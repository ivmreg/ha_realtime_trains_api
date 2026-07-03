import asyncio
from datetime import datetime, timedelta
import logging
from typing import Any, cast
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_START,
    CONF_END,
    CONF_JOURNEYDATA,
    CONF_MAXTRAINS,
    CONF_TIMEOFFSET,
    CONF_PLATFORMS_OF_INTEREST,
    CONF_LOOKBACK,
    DEFAULT_LOOKBACK_MINUTES,
    DEFAULT_MAX_TRAINS,
    NO_TRAINS_BACKOFF_SECONDS,
)
from .sensor_helpers import (
    build_query_key,
    retry_with_auth_refresh,
    parse_rtt_datetime,
    find_last_report,
    subsequent_stop_start_index,
    collect_subsequent_stops,
)
from .normalization import coerce_time_offset, split_csv
from .rtt_api import (
    RealtimeTrainsApiAuthError,
    RealtimeTrainsApiClient,
    RealtimeTrainsApiError,
    RealtimeTrainsApiRateLimitError,
    RealtimeTrainsApiNotFoundError,
)

_LOGGER = logging.getLogger(__name__)
TIMEZONE = ZoneInfo('Europe/London')
STRFFORMAT = "%d-%m-%Y %H:%M"

# How many journey-detail requests may be in flight at once. Kept low so a
# board with journey data for several trains stays well inside the RTT
# per-minute rate budget.
JOURNEY_DATA_CONCURRENCY = 2

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
        auto_adjust_scans: bool = False,
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
        self.auto_adjust_scans = auto_adjust_scans
        self.current_polling_interval = None
        self.last_update_time = None
        self.last_successful_update: datetime | None = None
        self.data_stale = False

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

    def _is_peak(self, now: datetime) -> bool:
        """Return True when now falls inside a peak window (or none are set)."""
        if not self.peak_windows:
            return True
        current_time = now.time()
        return any(start <= current_time <= end for start, end in self.peak_windows)

    def _set_polling_interval(self, seconds: int) -> None:
        if self.current_polling_interval != seconds:
            self.current_polling_interval = seconds
            self.update_interval = timedelta(seconds=seconds)
            _LOGGER.debug("Adjusted polling interval to %s seconds", seconds)

    @staticmethod
    def _parse_departure_times(
        departure: dict[str, Any], now: datetime
    ) -> tuple[datetime, datetime] | None:
        """Extract (scheduled, estimated) timestamps from a service, or None."""
        temporal_data = departure.get("temporalData", {}).get("departure", {})
        scheduled_str = temporal_data.get("scheduleAdvertised") or temporal_data.get("scheduleInternal")
        estimated_str = temporal_data.get("realtimeActual") or temporal_data.get("realtimeForecast") or temporal_data.get("realtimeEstimate")

        if not scheduled_str:
            return None

        try:
            scheduledTs = parse_rtt_datetime(scheduled_str, TIMEZONE)
        except ValueError:
            return None

        estimatedTs = scheduledTs
        if estimated_str:
            try:
                estimatedTs = parse_rtt_datetime(estimated_str, TIMEZONE)
            except ValueError:
                estimatedTs = scheduledTs

        return scheduledTs, estimatedTs

    @staticmethod
    def _build_train(
        departure: dict[str, Any],
        scheduledTs: datetime,
        estimatedTs: datetime,
        now: datetime,
        platform: Any,
    ) -> dict[str, Any]:
        """Assemble the next_trains entry for a single service."""
        schedule_metadata = departure.get("scheduleMetadata", {})
        loc_metadata = departure.get("locationMetadata", {})
        temporal_data = departure.get("temporalData", {}).get("departure", {})

        origins = departure.get("origin", [])
        origin_name = origins[0].get("location", {}).get("description", "") if origins else ""

        destinations = departure.get("destination", [])
        destination_name = destinations[0].get("location", {}).get("description", "") if destinations else ""

        return {
            "origin_name": origin_name,
            "destination_name": destination_name,
            "service_uid": schedule_metadata.get("identity"),
            "headcode": schedule_metadata.get("trainReportingIdentity"),
            "type": schedule_metadata.get("modeType"),
            "operator_name": schedule_metadata.get("operator", {}).get("name", ""),
            "scheduled": scheduledTs.strftime(STRFFORMAT),
            "estimated": estimatedTs.strftime(STRFFORMAT),
            "minutes": _delta_seconds(estimatedTs, now) // 60,
            "lateness": temporal_data.get("realtimeAdvertisedLateness"),
            "is_cancelled": temporal_data.get("isCancelled", False),
            "platform": platform,
            "length": loc_metadata.get("numberOfVehicles"),
            "stock": loc_metadata.get("stockBranding"),
        }

    async def _fetch_one_query(
        self, query: dict[str, Any], now: datetime
    ) -> tuple[str, dict[str, Any]]:
        """Fetch, filter and enrich the departures for a single configured query."""
        origin = query.get(CONF_START)
        destination = query.get(CONF_END)
        platforms = query.get(CONF_PLATFORMS_OF_INTEREST, [])
        time_offset = coerce_time_offset(query.get(CONF_TIMEOFFSET, timedelta()), timedelta())
        journey_data_count = query.get(CONF_JOURNEYDATA, 0) or 0
        lookback_mins = query.get(CONF_LOOKBACK, DEFAULT_LOOKBACK_MINUTES)
        query_dt = now - timedelta(minutes=lookback_mins)

        # Backwards compatibility: before max_trains existed, the board length
        # was (buggily) capped at journey_data_count, so keep that as the
        # default when journey data is requested.
        max_trains = query.get(CONF_MAXTRAINS) or (
            journey_data_count if journey_data_count > 0 else DEFAULT_MAX_TRAINS
        )

        if isinstance(platforms, str):
            platforms = split_csv(platforms)
        platforms_of_interest = set(platforms)

        query_key = build_query_key(origin, destination, platforms_of_interest, time_offset)

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
                query_dt.date(),
                query_dt.strftime("%H%M"),
                time_window=lookback_mins + 120,
            ),
            self._async_refresh_token,
        )

        services = data.get("services") if data and isinstance(data, dict) else None
        departures = services or []

        next_trains: list[dict[str, Any]] = []
        enrichment: list[tuple[dict[str, Any], datetime, datetime]] = []
        nextDepartureEstimatedTs = None

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

            if not schedule_metadata.get("departureDate"):
                continue

            times = self._parse_departure_times(departure, now)
            if times is None:
                continue
            scheduledTs, estimatedTs = times

            if _delta_seconds(estimatedTs, now) < time_offset.total_seconds():
                continue

            if nextDepartureEstimatedTs is None:
                nextDepartureEstimatedTs = estimatedTs
            else:
                nextDepartureEstimatedTs = min(nextDepartureEstimatedTs, estimatedTs)

            train = self._build_train(departure, scheduledTs, estimatedTs, now, platform)
            next_trains.append(train)
            if len(next_trains) <= journey_data_count:
                enrichment.append((train, scheduledTs, estimatedTs))
            if len(next_trains) >= max_trains:
                break

        # Enrichment failures no longer replace the numeric state; they are
        # surfaced separately so the sensor stays usable in automations.
        error = await self._enrich_journey_data(enrichment, origin, destination)

        state = None
        if nextDepartureEstimatedTs is not None:
            state = _delta_seconds(nextDepartureEstimatedTs, now) // 60

        return query_key, {
            "state": state,
            "error": error,
            "next_trains": next_trains,
            "journey_start": origin,
            "journey_end": destination,
            "platforms_of_interest": platforms_of_interest,
        }

    async def _enrich_journey_data(
        self,
        trains: list[tuple[dict[str, Any], datetime, datetime]],
        origin: str,
        destination: str | None,
    ) -> str | None:
        """Fetch journey details for the given trains with bounded concurrency.

        Returns the first error state encountered, if any. _add_journey_data
        swallows API errors into these state strings, so gather cannot raise
        anything except auth failures surfaced by the last retry.
        """
        if not trains:
            return None

        semaphore = asyncio.Semaphore(JOURNEY_DATA_CONCURRENCY)

        async def enrich(train, scheduledTs, estimatedTs):
            async with semaphore:
                return await self._add_journey_data(
                    train, scheduledTs, estimatedTs, origin, destination
                )

        results = await asyncio.gather(
            *(enrich(train, sched, est) for train, sched, est in trains)
        )
        return next((err for err in results if err), None)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API endpoint."""
        now = cast(datetime, dt_util.now()).astimezone(TIMEZONE)

        target_interval = self.peak_interval if self._is_peak(now) else self.off_peak_interval
        self._set_polling_interval(target_interval)

        self.last_update_time = now

        try:
            result_data = {}
            for query in self.queries:
                query_key, query_result = await self._fetch_one_query(query, now)
                result_data[query_key] = query_result
        except RealtimeTrainsApiAuthError as err:
            raise ConfigEntryAuthFailed(err) from err
        except RealtimeTrainsApiRateLimitError as err:
            stale = self._serve_stale_data(f"Rate limit hit: {err}")
            if stale is not None:
                return stale
            raise UpdateFailed(f"Rate limit hit: {err}") from err
        except RealtimeTrainsApiError as err:
            stale = self._serve_stale_data(f"Error communicating with API: {err}")
            if stale is not None:
                return stale
            raise UpdateFailed(f"Error communicating with API: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected error: {err}") from err

        self.data_stale = False
        self.last_successful_update = now

        if (
            self.auto_adjust_scans
            and all(not result["next_trains"] for result in result_data.values())
        ):
            self._set_polling_interval(max(NO_TRAINS_BACKOFF_SECONDS, target_interval))

        return result_data

    def _serve_stale_data(self, reason: str) -> dict[str, Any] | None:
        """Return the last-known data marked stale, or None when there is none."""
        last_known = getattr(self, "data", None)
        if not last_known:
            return None
        self.data_stale = True
        _LOGGER.warning("Serving last-known train data: %s", reason)
        return last_known
