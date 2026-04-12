from datetime import datetime, timedelta
import logging
from typing import Any, cast
import pytz

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import CONF_START, CONF_END, CONF_JOURNEYDATA, CONF_TIMEOFFSET, CONF_PLATFORMS_OF_INTEREST
from .sensor_helpers import (
    retry_with_auth_refresh,
    parse_rtt_datetime,
    find_last_report,
    subsequent_stop_start_index,
    collect_subsequent_stops,
)
from .normalization import coerce_time_offset
from .rtt_api import (
    RealtimeTrainsApiAuthError,
    RealtimeTrainsApiClient,
    RealtimeTrainsApiError,
    RealtimeTrainsApiRateLimitError,
    RealtimeTrainsApiNotFoundError,
)

_LOGGER = logging.getLogger(__name__)
TIMEZONE = pytz.timezone('Europe/London')
STRFFORMAT = "%d-%m-%Y %H:%M"

def _delta_seconds(hhmm_datetime_a: datetime, hhmm_datetime_b: datetime) -> float:
    a_trunc = hhmm_datetime_a.replace(second=0, microsecond=0)
    b_trunc = hhmm_datetime_b.replace(second=0, microsecond=0)
    return (a_trunc - b_trunc).total_seconds()

class RealtimeTrainsUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Class to manage fetching Realtime Trains data."""

    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        name: str,
        update_interval: timedelta,
        api: RealtimeTrainsApiClient,
        queries: list[dict[str, Any]],
        peak_interval: int = 60,
        off_peak_interval: int = 300,
        peak_windows: list = None,
    ) -> None:
        """Initialize."""
        super().__init__(
            hass=hass,
            logger=logger,
            name=name,
            update_interval=update_interval,
        )
        self.api = api
        self.queries = queries
        self.peak_interval = peak_interval
        self.off_peak_interval = off_peak_interval
        self.peak_windows = peak_windows or []
        self.current_polling_interval = None
        self.last_update_time = None

    async def _async_refresh_token(self) -> bool:
        """Refresh the access token."""
        try:
            await self.api.async_get_access_token()
            return True
        except RealtimeTrainsApiAuthError as err:
            _LOGGER.error("Failed to refresh RTT access token: %s", err)
            return False

    async def _add_journey_data(self, train, scheduled_departure, estimated_departure, journey_start, journey_end):
        """Populate journey data using service details."""
        try:
            data = await retry_with_auth_refresh(
                lambda: self.api.fetch_service_details(
                    train['service_uid'],
                    scheduled_departure,
                ),
                self._async_refresh_token,
                lambda err: _LOGGER.warning("Could not populate arrival times after retry: %s", err),
            )
        except RealtimeTrainsApiAuthError:
            return "Credentials invalid"
        except RealtimeTrainsApiRateLimitError as err:
            _LOGGER.debug("Rate limit hit or preemptively skipped for journey data: %s", err)
            return "Rate Limited"
        except RealtimeTrainsApiNotFoundError:
            _LOGGER.warning("Could not find %s in stops for service %s.", journey_end, train['service_uid'])
            return None
        except RealtimeTrainsApiError as err:
            _LOGGER.warning("Could not populate arrival times: %s", err)
            return None

        if data is None:
            return None

        service = data.get("service", {})
        locations = service.get("locations", [])

        reasons = service.get("reasons", [])
        if reasons:
            train["reason"] = reasons[0].get("shortText")

        last_report_idx, last_report_type, last_report_station, last_report_time = find_last_report(
            locations,
            TIMEZONE,
        )
        just_departed_idx = subsequent_stop_start_index(last_report_idx, last_report_type)

        found_dest = False
        found_start = False
        stopCount = -1
        subsequent_stops = collect_subsequent_stops(locations, just_departed_idx, TIMEZONE)

        for i, stop in enumerate(locations):
            stop_location = stop.get("location", {})
            crs = stop_location.get("shortCodes", [None])[0] if stop_location.get("shortCodes") else None
            temporal = stop.get("temporalData", {})
            display_as = temporal.get("displayAs") or ""

            if crs == journey_start:
                found_start = True

            if crs == journey_end and found_start:
                if display_as != 'ORIGIN':
                    arr_data = temporal.get("arrival", {})
                    sch_arr_str = arr_data.get("scheduleAdvertised") or arr_data.get("scheduleInternal")
                    est_arr_str = arr_data.get("realtimeActual") or arr_data.get("realtimeForecast") or arr_data.get("realtimeEstimate")

                    if sch_arr_str:
                        sch_arr_dt = parse_rtt_datetime(sch_arr_str, TIMEZONE)
                        est_arr_dt = parse_rtt_datetime(est_arr_str, TIMEZONE) if est_arr_str else sch_arr_dt

                        status = "OK"
                        if 'CANCEL' in display_as or temporal.get("status") == "CANCELLED":
                            status = "Cancelled"
                        elif est_arr_dt > sch_arr_dt:
                            status = "Delayed"

                        train.update({
                            "scheduled_arrival": sch_arr_dt.strftime(STRFFORMAT),
                            "estimate_arrival": est_arr_dt.strftime(STRFFORMAT),
                            "journey_time_mins": _delta_seconds(est_arr_dt, estimated_departure) // 60,
                            "stops": stopCount,
                            "status": status,
                        })
                        found_dest = True
            
            stopCount += 1

        train["subsequent_stops"] = subsequent_stops

        if journey_end and not found_dest:
            _LOGGER.warning("Could not find %s in stops for service %s.", journey_end, train['service_uid'])

        if not journey_end:
            train["stops"] = stopCount

        if last_report_station is not None:
            train["last_report_station"] = last_report_station
            train["last_report_type"] = last_report_type
            train["last_report_time"] = last_report_time.strftime(STRFFORMAT) if last_report_time else None
        return None

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API endpoint."""
        now = cast(datetime, dt_util.now()).astimezone(TIMEZONE)
        
        is_peak = False
        if not self.peak_windows:
            is_peak = True
        else:
            current_time = now.time()
            for start_time, end_time in self.peak_windows:
                if start_time <= current_time <= end_time:
                    is_peak = True
                    break
        
        target_interval = self.peak_interval if is_peak else self.off_peak_interval
        if self.current_polling_interval != target_interval:
            self.current_polling_interval = target_interval
            self.update_interval = timedelta(seconds=target_interval)
            _LOGGER.debug("Adjusted polling interval to %s seconds", target_interval)
            
        self.last_update_time = now
        result_data = {}

        try:
            for query in self.queries:
                origin = query.get(CONF_START)
                destination = query.get(CONF_END)
                platforms = query.get(CONF_PLATFORMS_OF_INTEREST, [])
                time_offset = coerce_time_offset(query.get(CONF_TIMEOFFSET, timedelta()), timedelta())
                journey_data_count = query.get(CONF_JOURNEYDATA, 0)
                
                if isinstance(platforms, str):
                    platforms = [p.strip() for p in platforms.split(',') if p.strip()]
                platforms_of_interest = set(platforms)

                platforms_str = "_".join(sorted(platforms_of_interest)) if platforms_of_interest else "all"
                dest_str = destination if destination else "all"
                offset_str = f"{int(time_offset.total_seconds())}" if time_offset.total_seconds() > 0 else "0"
                query_key = f"{origin}_{dest_str}_{platforms_str}_{offset_str}"

                _LOGGER.debug(
                    "Fetching location services for %s to %s at %s",
                    origin,
                    destination,
                    now.strftime("%H%M"),
                )
                
                data = await retry_with_auth_refresh(
                    lambda: self.api.fetch_location_services(
                        origin,
                        destination,
                        now.date(),
                        now.strftime("%H%M"),
                    ),
                    self._async_refresh_token,
                )
                
                services = data.get("services") if data and isinstance(data, dict) else None
                departures = services or []
                
                next_trains = []
                departureCount = 0
                nextDepartureEstimatedTs = None
                state = None

                for departure in departures:
                    schedule_metadata = departure.get("scheduleMetadata", {})
                    if not schedule_metadata.get("inPassengerService", False):
                        continue

                    loc_metadata = departure.get("locationMetadata", {})
                    platform_dict = loc_metadata.get("platform", {})
                    platform = platform_dict.get("actual") or platform_dict.get("planned")
                    platform_key = platform.strip() if isinstance(platform, str) else platform
                    if platforms_of_interest and platform_key not in platforms_of_interest:
                        continue

                    departuredate_str = schedule_metadata.get("departureDate")
                    if not departuredate_str:
                        continue

                    temporal_data = departure.get("temporalData", {}).get("departure", {})
                    scheduled_str = temporal_data.get("scheduleAdvertised") or temporal_data.get("scheduleInternal")
                    estimated_str = temporal_data.get("realtimeActual") or temporal_data.get("realtimeForecast") or temporal_data.get("realtimeEstimate")

                    if not scheduled_str:
                        continue

                    try:
                        scheduledTs = parse_rtt_datetime(scheduled_str, TIMEZONE)
                    except ValueError:
                        continue

                    if estimated_str:
                        try:
                            estimatedTs = parse_rtt_datetime(estimated_str, TIMEZONE)
                        except ValueError:
                            estimatedTs = scheduledTs
                    else:
                        estimatedTs = scheduledTs

                    if _delta_seconds(estimatedTs, now) < time_offset.total_seconds():
                        continue

                    if nextDepartureEstimatedTs is None:
                        nextDepartureEstimatedTs = estimatedTs
                    else:
                        nextDepartureEstimatedTs = min(nextDepartureEstimatedTs, estimatedTs)

                    departureCount += 1

                    origins = departure.get("origin", [])
                    origin_name = origins[0].get("location", {}).get("description", "") if origins else ""

                    destinations = departure.get("destination", [])
                    destination_name = destinations[0].get("location", {}).get("description", "") if destinations else ""

                    service_uid = schedule_metadata.get("identity")
                    headcode = schedule_metadata.get("trainReportingIdentity")
                    mode_type = schedule_metadata.get("modeType")
                    operator_name = schedule_metadata.get("operator", {}).get("name", "")
                    
                    length = loc_metadata.get("numberOfVehicles")
                    stock = loc_metadata.get("stockBranding")
                    
                    lateness = temporal_data.get("realtimeAdvertisedLateness")
                    is_cancelled = temporal_data.get("isCancelled", False)

                    train = {
                        "origin_name": origin_name,
                        "destination_name": destination_name,
                        "service_uid": service_uid,
                        "headcode": headcode,
                        "type": mode_type,
                        "operator_name": operator_name,
                        "scheduled": scheduledTs.strftime(STRFFORMAT),
                        "estimated": estimatedTs.strftime(STRFFORMAT),
                        "minutes": _delta_seconds(estimatedTs, now) // 60,
                        "lateness": lateness,
                        "is_cancelled": is_cancelled,
                        "platform": platform,
                        "length": length,
                        "stock": stock,
                    }
                    if departureCount > journey_data_count:
                        break
                    
                    err_state = await self._add_journey_data(train, scheduledTs, estimatedTs, origin, destination)
                    if err_state:
                        state = err_state
                    next_trains.append(train)

                if state is None:
                    if nextDepartureEstimatedTs is None:
                        state = None
                    else:
                        state = _delta_seconds(nextDepartureEstimatedTs, now) // 60
                
                result_data[query_key] = {
                    "state": state,
                    "next_trains": next_trains,
                    "journey_start": origin,
                    "journey_end": destination,
                    "platforms_of_interest": platforms_of_interest,
                }

            return result_data
        except RealtimeTrainsApiAuthError as err:
            raise ConfigEntryAuthFailed(err) from err
        except RealtimeTrainsApiRateLimitError as err:
            raise UpdateFailed(f"Rate limit hit: {err}") from err
        except RealtimeTrainsApiError as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected error: {err}") from err