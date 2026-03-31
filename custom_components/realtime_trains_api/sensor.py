"""Support for UK train data provided by api.rtt.io."""
from __future__ import annotations

from datetime import datetime, timedelta
import logging
import pytz

import voluptuous as vol
from typing import Any, cast

from homeassistant.components.sensor import PLATFORM_SCHEMA, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import Throttle
import homeassistant.util.dt as dt_util

from .const import (
    CONF_API_TOKEN as RTT_CONF_API_TOKEN,
    CONF_REFRESH_TOKEN as RTT_CONF_REFRESH_TOKEN,
    CONF_AUTOADJUSTSCANS,
    CONF_END,
    CONF_JOURNEYDATA,
    CONF_PLATFORMS_OF_INTEREST,
    CONF_QUERIES,
    CONF_SENSORNAME,
    CONF_START,
    CONF_TIMEOFFSET,
    CRS_CODE_PATTERN,
    DEFAULT_SCAN_INTERVAL,
)
from .rtt_api import (
    RealtimeTrainsApiAuthError,
    RealtimeTrainsApiClient,
    RealtimeTrainsApiError,
    RealtimeTrainsApiNotFoundError,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOFFSET = timedelta(minutes=0)

ATTR_ATCOCODE = "atcocode"
ATTR_LOCALITY = "locality"
ATTR_REQUEST_TIME = "request_time"
ATTR_JOURNEY_START = "journey_start"
ATTR_JOURNEY_END = "journey_end"
ATTR_NEXT_TRAINS = "next_trains"
ATTR_PLATFORMS_OF_INTEREST = "platforms_of_interest"

TIMEZONE = pytz.timezone('Europe/London')
STRFFORMAT = "%d-%m-%Y %H:%M"

_QUERY_SCHEME = vol.Schema(
    {
        vol.Optional(CONF_SENSORNAME): cv.string,
        vol.Required(CONF_START): cv.string,
        vol.Required(CONF_END): cv.string,
        vol.Optional(CONF_JOURNEYDATA, default=0): cv.positive_int,
        vol.Optional(CONF_TIMEOFFSET, default=DEFAULT_TIMEOFFSET):
            vol.All(cv.time_period, cv.positive_timedelta),
        vol.Optional(CONF_PLATFORMS_OF_INTEREST): [cv.string],
    }
)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_AUTOADJUSTSCANS, default=False): cv.boolean,
        vol.Optional(RTT_CONF_API_TOKEN): cv.string,
        vol.Optional(RTT_CONF_REFRESH_TOKEN): cv.string,
        vol.Required(CONF_QUERIES): [_QUERY_SCHEME],
    }
)


def _coerce_scan_interval(value: Any) -> timedelta:
    if isinstance(value, timedelta):
        return value
    if isinstance(value, dict):
        try:
            return timedelta(**{key: int(val) for key, val in value.items()})
        except (TypeError, ValueError):
            return DEFAULT_SCAN_INTERVAL
    if isinstance(value, (int, float)):
        seconds = int(value)
        if seconds <= 0:
            return DEFAULT_SCAN_INTERVAL
        return timedelta(seconds=seconds)
    if isinstance(value, str):
        try:
            seconds = int(float(value))
        except (TypeError, ValueError):
            return DEFAULT_SCAN_INTERVAL
        if seconds <= 0:
            return DEFAULT_SCAN_INTERVAL
        return timedelta(seconds=seconds)
    return DEFAULT_SCAN_INTERVAL


def _coerce_positive_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def _coerce_time_offset(value: Any) -> timedelta:
    if isinstance(value, timedelta):
        return value
    if isinstance(value, dict):
        try:
            return timedelta(**{key: int(val) for key, val in value.items()})
        except (TypeError, ValueError):
            return DEFAULT_TIMEOFFSET
    if isinstance(value, (int, float)):
        minutes = int(value)
        if minutes < 0:
            minutes = 0
        return timedelta(minutes=minutes)
    if isinstance(value, str):
        try:
            minutes = int(float(value.strip()))
        except (TypeError, ValueError):
            return DEFAULT_TIMEOFFSET
        return timedelta(minutes=max(0, minutes))
    return DEFAULT_TIMEOFFSET


def _ensure_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.replace("\n", ",")
        return [item.strip() for item in cleaned.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        iterable = value
    else:
        iterable = [value]
    result: list[str] = []
    for item in iterable:
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def _normalize_query(raw_query: Any) -> dict[str, Any]:
    if not isinstance(raw_query, dict):
        raise ValueError("Query configuration must be a mapping")

    if CONF_START not in raw_query:
        raise ValueError("Query must define origin station")

    sensor_name = raw_query.get(CONF_SENSORNAME)
    if isinstance(sensor_name, str):
        sensor_name = sensor_name.strip() or None
    elif sensor_name is not None:
        sensor_name = str(sensor_name).strip() or None

    origin = str(raw_query[CONF_START]).strip().upper()
    if not CRS_CODE_PATTERN.match(origin):
        raise ValueError(f"Invalid origin CRS code: {origin}")

    destination_raw = raw_query.get(CONF_END, "")
    destination = str(destination_raw).strip().upper() if destination_raw else None
    if destination and not CRS_CODE_PATTERN.match(destination):
        raise ValueError(f"Invalid destination CRS code: {destination}")

    journey_data = _coerce_positive_int(raw_query.get(CONF_JOURNEYDATA, 0))
    time_offset = _coerce_time_offset(raw_query.get(CONF_TIMEOFFSET, DEFAULT_TIMEOFFSET))
    platforms = _ensure_list(raw_query.get(CONF_PLATFORMS_OF_INTEREST, []))

    return {
        CONF_SENSORNAME: sensor_name,
        CONF_START: origin,
        CONF_END: destination,
        CONF_JOURNEYDATA: journey_data,
        CONF_TIMEOFFSET: time_offset,
        CONF_PLATFORMS_OF_INTEREST: platforms,
    }


def _create_sensors(
    api_client: RealtimeTrainsApiClient,
    autoadjustscans: bool,
    interval: timedelta,
    queries: list[Any],
    entry_id: str | None = None,
) -> list[RealtimeTrainLiveTrainTimeSensor]:
    sensors: list[RealtimeTrainLiveTrainTimeSensor] = []
    seen_names: set[str] = set()
    seen_unique_ids: set[str] = set()

    for idx, raw_query in enumerate(queries or []):
        try:
            query = _normalize_query(raw_query)
        except ValueError as err:
            _LOGGER.warning("Skipping RTT query configuration: %s", err)
            continue

        sensor = RealtimeTrainLiveTrainTimeSensor(
            query.get(CONF_SENSORNAME),
            api_client,
            query[CONF_START],
            query[CONF_END],
            query[CONF_JOURNEYDATA],
            query[CONF_TIMEOFFSET],
            autoadjustscans,
            query[CONF_PLATFORMS_OF_INTEREST],
            interval,
            entry_id,
            idx,
        )

        if sensor.name in seen_names:
            _LOGGER.warning("Duplicate sensor name '%s' - skipping duplicate entry", sensor.name)
            continue

        if sensor.unique_id and sensor.unique_id in seen_unique_ids:
            _LOGGER.warning("Duplicate unique ID '%s' - skipping duplicate entry", sensor.unique_id)
            continue

        seen_names.add(sensor.name)
        if sensor.unique_id:
            seen_unique_ids.add(sensor.unique_id)
        sensors.append(sensor)

    return sensors


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Get the realtime_train sensor."""
    interval = _coerce_scan_interval(config.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
    autoadjustscans = config[CONF_AUTOADJUSTSCANS]
    token = config.get(RTT_CONF_API_TOKEN)
    refresh_token = config.get(RTT_CONF_REFRESH_TOKEN)

    if not token and not refresh_token:
        _LOGGER.error("Realtime Trains API entry is missing credentials.")
        return

    queries = config[CONF_QUERIES]


    client = async_get_clientsession(hass)
    api_client = RealtimeTrainsApiClient(client, token, refresh_token)

    sensors = _create_sensors(api_client, autoadjustscans, interval, queries)

    if sensors:
        async_add_entities(sensors, True)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors from a config entry."""
    token = entry.data.get(RTT_CONF_API_TOKEN)
    refresh_token = entry.data.get(RTT_CONF_REFRESH_TOKEN)

    if not token and not refresh_token:
        _LOGGER.error("Realtime Trains API entry %s is missing credentials", entry.entry_id)
        return

    interval = _coerce_scan_interval(entry.options.get(CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)))
    autoadjustscans = entry.options.get(CONF_AUTOADJUSTSCANS, entry.data.get(CONF_AUTOADJUSTSCANS, False))
    queries = entry.options.get(CONF_QUERIES) or entry.data.get(CONF_QUERIES, [])

    if not queries:
        _LOGGER.warning("Realtime Trains API entry %s has no queries configured", entry.entry_id)
        return

    client = async_get_clientsession(hass)
    api_client = RealtimeTrainsApiClient(client, token, refresh_token)

    sensors = _create_sensors(api_client, autoadjustscans, interval, queries, entry.entry_id)

    if sensors:
        async_add_entities(sensors, True)
    else:
        _LOGGER.warning(
            "Realtime Trains API entry %s produced no sensors after validation", entry.entry_id
        )


class RealtimeTrainLiveTrainTimeSensor(SensorEntity):
    """
    Sensor that reads the rtt API.

    api.rtt.io provides free comprehensive train data for UK trains
    across the UK via simple JSON API. Subclasses of this
    base class can be used to access specific types of information.
    """

    _attr_icon = "mdi:train"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(
        self,
        sensor_name: str | None,
        api_client: RealtimeTrainsApiClient,
        journey_start: str,
        journey_end: str | None,
        journey_data_for_next_X_trains: int,
        timeoffset: timedelta,
        autoadjustscans: bool,
        platforms_of_interest: list[str],
        interval: timedelta,
        entry_id: str | None = None,
        query_index: int = 0,
    ) -> None:
        """Construct a live train time sensor."""

        if journey_end:
            if platforms_of_interest:
                platform_str = ", ".join(sorted(platforms_of_interest))
                default_sensor_name = (
                    f"Next train from {journey_start} platform {platform_str} to {journey_end} ({timeoffset})" 
                    if (timeoffset.total_seconds() > 0)
                    else f"Next train from {journey_start} platform {platform_str} to {journey_end}"
                )
            else:
                default_sensor_name = (
                    f"Next train from {journey_start} to {journey_end} ({timeoffset})" 
                    if (timeoffset.total_seconds() > 0)
                    else f"Next train from {journey_start} to {journey_end}"
                )
        else:
            if platforms_of_interest:
                platform_str = ", ".join(sorted(platforms_of_interest))
                default_sensor_name = (
                    f"Trains from {journey_start} platform {platform_str} ({timeoffset})" 
                    if (timeoffset.total_seconds() > 0)
                    else f"Trains from {journey_start} platform {platform_str}"
                )
            else:
                default_sensor_name = (
                    f"Trains from {journey_start} ({timeoffset})" 
                    if (timeoffset.total_seconds() > 0)
                    else f"Trains from {journey_start}"
                )

        self._journey_start = journey_start
        self._journey_end = journey_end
        self._journey_data_for_next_X_trains = journey_data_for_next_X_trains
        self._next_trains = []
        self._data = {}
        self._api = api_client
        self._timeoffset = timeoffset
        self._autoadjustscans = autoadjustscans
        self._platforms_of_interest = {
            platform.strip()
            for platform in platforms_of_interest
            if isinstance(platform, str) and platform.strip()
        }
        self._interval = interval
        self._entry_id = entry_id

        self._name = default_sensor_name if sensor_name is None else sensor_name
        self._state = None
        
        # Generate a stable unique_id for config entry sensors
        if entry_id:
            # Create unique ID based on entry_id, origin, destination, platforms, and time offset
            platforms_str = "_".join(sorted(platforms_of_interest)) if platforms_of_interest else "all"
            dest_str = journey_end if journey_end else "all"
            offset_str = f"{int(timeoffset.total_seconds())}" if timeoffset.total_seconds() > 0 else "0"
            self._attr_unique_id = f"{entry_id}_{journey_start}_{dest_str}_{platforms_str}_{offset_str}_{query_index}"
        else:
            self._attr_unique_id = None

        self.async_update = self._async_update

    async def _async_update(self):
        """Get the latest live departure data for the specified stop."""
        _LOGGER.debug("Updating Realtime Trains sensor: %s", self._name)
        now = cast(datetime, dt_util.now()).astimezone(TIMEZONE)
        await self._load_departures(now)
        self._next_trains = []
        departureCount = 0
        
        nextDepartureEstimatedTs : (datetime | None) = None

        services = self._data.get("services") if isinstance(self._data, dict) else None
        departures = services or []
        _LOGGER.debug("Found %d services for %s", len(departures), self._journey_start)

        for departure in departures:
            schedule_metadata = departure.get("scheduleMetadata", {})
            if not schedule_metadata.get("inPassengerService", False):
                continue

            loc_metadata = departure.get("locationMetadata", {})
            platform_dict = loc_metadata.get("platform", {})
            # Use actual platform if available, else planned
            platform = platform_dict.get("actual") or platform_dict.get("planned")
            platform_key = platform.strip() if isinstance(platform, str) else platform
            if self._platforms_of_interest and platform_key not in self._platforms_of_interest:
                continue

            departuredate_str = schedule_metadata.get("departureDate")
            if not departuredate_str:
                continue

            # temporalData handles datetimes as ISO strings
            temporal_data = departure.get("temporalData", {}).get("departure", {})

            scheduled_str = temporal_data.get("scheduleAdvertised") or temporal_data.get("scheduleInternal")

            estimated_str = temporal_data.get("realtimeActual") or temporal_data.get("realtimeForecast") or temporal_data.get("realtimeEstimate")

            if not scheduled_str:
                continue

            try:
                scheduledTs = datetime.fromisoformat(scheduled_str)
                if scheduledTs.tzinfo is None:
                    scheduledTs = TIMEZONE.localize(scheduledTs)
            except ValueError:
                continue

            if estimated_str:
                try:
                    estimatedTs = datetime.fromisoformat(estimated_str)
                    if estimatedTs.tzinfo is None:
                        estimatedTs = TIMEZONE.localize(estimatedTs)
                except ValueError:
                    estimatedTs = scheduledTs
            else:
                estimatedTs = scheduledTs

            if _delta_seconds(estimatedTs, now) < self._timeoffset.total_seconds():
                continue

            if nextDepartureEstimatedTs is None:
                nextDepartureEstimatedTs = estimatedTs
            else:
                nextDepartureEstimatedTs = min(nextDepartureEstimatedTs, estimatedTs)

            departureCount += 1

            # Get origin/destination
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
            if departureCount > self._journey_data_for_next_X_trains:
                break;
            
            await self._add_journey_data(train, scheduledTs, estimatedTs)
            self._next_trains.append(train)

        if nextDepartureEstimatedTs is None:
            self._state = None
        else:
            self._state = _delta_seconds(nextDepartureEstimatedTs, now) // 60

        if self._autoadjustscans:
            if nextDepartureEstimatedTs is None:
                self.async_update = Throttle(timedelta(minutes=30))(self._async_update)
            else:
                self.async_update = self._async_update

    async def _async_refresh_token(self) -> bool:
        """Refresh the access token."""
        try:
            await self._api.async_get_access_token()
            if self._entry_id:
                entry = self.hass.config_entries.async_get_entry(self._entry_id)
                if entry:
                    new_data = dict(entry.data)
                    new_data[RTT_CONF_API_TOKEN] = self._api.token
                    self.hass.config_entries.async_update_entry(entry, data=new_data)
            return True
        except RealtimeTrainsApiAuthError as err:
            _LOGGER.error("Failed to refresh RTT access token: %s", err)
            return False

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return self._attr_unique_id

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._state

    async def _load_departures(self, now: datetime):
        try:
            _LOGGER.debug("Fetching location services for %s to %s at %s", self._journey_start, self._journey_end, now.strftime("%H%M"))
            self._data = await self._api.fetch_location_services(
                self._journey_start,
                self._journey_end,
                now.date(),
                now.strftime("%H%M")
            )
        except RealtimeTrainsApiAuthError:
            if not await self._async_refresh_token():
                self._state = "Credentials invalid"
                self._data = {}
                return
            
            # Retry
            try:
                self._data = await self._api.fetch_location_services(
                    self._journey_start,
                    self._journey_end,
                    now.date(),
                    now.strftime("%H%M")
                )
            except RealtimeTrainsApiError:
                self._data = {}
        except RealtimeTrainsApiNotFoundError:
            self._data = {}
        except RealtimeTrainsApiError as err:
            _LOGGER.warning("Invalid response from API: %s", err)
            self._data = {}

    async def _add_journey_data(self, train, scheduled_departure, estimated_departure):
        """Populate journey data using service details."""
        try:
            data = await self._api.fetch_service_details(
                train['service_uid'],
                scheduled_departure,
            )
        except RealtimeTrainsApiAuthError:
            if not await self._async_refresh_token():
                self._state = "Credentials invalid"
                return
            
            # Retry
            try:
                data = await self._api.fetch_service_details(
                    train['service_uid'],
                    scheduled_departure,
                )
            except RealtimeTrainsApiError as err:
                _LOGGER.warning("Could not populate arrival times after retry: %s", err)
                return
        except RealtimeTrainsApiNotFoundError:
            _LOGGER.warning(
                "Could not find %s in stops for service %s.",
                self._journey_end,
                train['service_uid'],
            )
            return
        except RealtimeTrainsApiError as err:
            _LOGGER.warning("Could not populate arrival times: %s", err)
            return

        last_report_station = None
        last_report_type = None
        last_report_time = None

        service = data.get("service", {})
        locations = service.get("locations", [])
        
        # Extract reasons if available
        reasons = service.get("reasons", [])
        if reasons:
            # We'll take the first reason for simplicity, usually it's the primary one
            train["reason"] = reasons[0].get("shortText")

        found = False
        found_start = False
        stopCount = -1

        for i, stop in enumerate(locations):
            stop_location = stop.get("location", {})
            crs = stop_location.get("shortCodes", [None])[0] if stop_location.get("shortCodes") else None
            
            temporal = stop.get("temporalData", {})
            pass_act = temporal.get("pass", {}).get("realtimeActual")
            dep_act = temporal.get("departure", {}).get("realtimeActual")
            arr_act = temporal.get("arrival", {}).get("realtimeActual")
            
            if pass_act or dep_act or arr_act:
                last_report_time_str = pass_act or dep_act or arr_act
                last_report_time = datetime.fromisoformat(last_report_time_str)
                last_report_station = crs
                if pass_act:
                    last_report_type = "Pass"
                elif dep_act:
                    last_report_type = "Departure"
                else:
                    last_report_type = "Arrival"

            if crs == self._journey_start:
                found_start = True

            if crs == self._journey_end and found_start:
                display_as = temporal.get("displayAs") or ""
                if display_as != 'ORIGIN':
                    arr_data = temporal.get("arrival", {})
                    scheduled_arrival_str = arr_data.get("scheduleAdvertised") or arr_data.get("scheduleInternal")
                    estimated_arrival_str = arr_data.get("realtimeActual") or arr_data.get("realtimeForecast") or arr_data.get("realtimeEstimate")

                    if scheduled_arrival_str:
                        scheduled_arrival = datetime.fromisoformat(scheduled_arrival_str)
                        if scheduled_arrival.tzinfo is None:
                            scheduled_arrival = TIMEZONE.localize(scheduled_arrival)

                        if estimated_arrival_str:
                            estimated_arrival = datetime.fromisoformat(estimated_arrival_str)
                            if estimated_arrival.tzinfo is None:
                                estimated_arrival = TIMEZONE.localize(estimated_arrival)
                        else:
                            estimated_arrival = scheduled_arrival

                        status = "OK"
                        if 'CANCEL' in display_as or temporal.get("status") == "CANCELLED":
                            status = "Cancelled"
                        elif estimated_arrival > scheduled_arrival:
                            status = "Delayed"

                        newtrain = {
                            "scheduled_arrival": scheduled_arrival.strftime(STRFFORMAT),
                            "estimate_arrival": estimated_arrival.strftime(STRFFORMAT),
                            "journey_time_mins": _delta_seconds(estimated_arrival, estimated_departure) // 60,
                            "stops": stopCount,
                            "status": status,
                        }
                        train.update(newtrain)
                        found = True
            
            stopCount += 1

        if self._journey_end and not found:
            _LOGGER.warning(
                "Could not find %s in stops for service %s.",
                self._journey_end,
                train['service_uid'],
            )

        if not self._journey_end:
            train["stops"] = stopCount

        if last_report_station is not None:
            train["last_report_station"] = last_report_station
            train["last_report_type"] = last_report_type
            train["last_report_time"] = last_report_time.strftime(STRFFORMAT) if last_report_time else None

    @property    @property
    def extra_state_attributes(self):
        """Return other details about the sensor state."""
        attrs = {}
        if self._data is not None:
            attrs[ATTR_JOURNEY_START] = self._journey_start
            if self._journey_end:
                attrs[ATTR_JOURNEY_END] = self._journey_end
            if self._platforms_of_interest:
                attrs[ATTR_PLATFORMS_OF_INTEREST] = sorted(self._platforms_of_interest)
            if self._next_trains:
                attrs[ATTR_NEXT_TRAINS] = self._next_trains
            return attrs

def _delta_seconds(hhmm_datetime_a : datetime, hhmm_datetime_b : datetime) -> float:
    """Calculate time delta in seconds between two datetime objects."""
    return (hhmm_datetime_a - hhmm_datetime_b).total_seconds()
