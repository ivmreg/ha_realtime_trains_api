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
    CONF_API_PASSWORD,
    CONF_API_USERNAME,
    CONF_AUTOADJUSTSCANS,
    CONF_END,
    CONF_JOURNEYDATA,
    CONF_PLATFORMS_OF_INTEREST,
    CONF_QUERIES,
    CONF_SENSORNAME,
    CONF_START,
    CONF_STOPS_OF_INTEREST,
    CONF_TIMEOFFSET,
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
        vol.Optional(CONF_STOPS_OF_INTEREST): [cv.string],
        vol.Optional(CONF_PLATFORMS_OF_INTEREST): [cv.string],
    }
)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_AUTOADJUSTSCANS, default=False): cv.boolean,
        vol.Required(CONF_API_USERNAME): cv.string,
        vol.Required(CONF_API_PASSWORD): cv.string,
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
    destination_raw = raw_query.get(CONF_END, "")
    destination = str(destination_raw).strip().upper() if destination_raw else None

    journey_data = _coerce_positive_int(raw_query.get(CONF_JOURNEYDATA, 0))
    time_offset = _coerce_time_offset(raw_query.get(CONF_TIMEOFFSET, DEFAULT_TIMEOFFSET))
    stops = _ensure_list(raw_query.get(CONF_STOPS_OF_INTEREST, []))
    stops = [stop.upper() for stop in stops]
    platforms = _ensure_list(raw_query.get(CONF_PLATFORMS_OF_INTEREST, []))

    return {
        CONF_SENSORNAME: sensor_name,
        CONF_START: origin,
        CONF_END: destination,
        CONF_JOURNEYDATA: journey_data,
        CONF_TIMEOFFSET: time_offset,
        CONF_STOPS_OF_INTEREST: stops,
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
            query[CONF_STOPS_OF_INTEREST],
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
    username = config[CONF_API_USERNAME]
    password = config[CONF_API_PASSWORD]
    queries = config[CONF_QUERIES]


    client = async_get_clientsession(hass)
    api_client = RealtimeTrainsApiClient(client, username, password)

    sensors = _create_sensors(api_client, autoadjustscans, interval, queries)

    if sensors:
        async_add_entities(sensors, True)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors from a config entry."""
    username = entry.data.get(CONF_API_USERNAME)
    password = entry.data.get(CONF_API_PASSWORD)

    if not username or not password:
        _LOGGER.error("Realtime Trains API entry %s is missing credentials", entry.entry_id)
        return

    interval = _coerce_scan_interval(entry.options.get(CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)))
    autoadjustscans = entry.options.get(CONF_AUTOADJUSTSCANS, entry.data.get(CONF_AUTOADJUSTSCANS, False))
    queries = entry.options.get(CONF_QUERIES) or entry.data.get(CONF_QUERIES, [])

    if not queries:
        _LOGGER.warning("Realtime Trains API entry %s has no queries configured", entry.entry_id)
        return

    client = async_get_clientsession(hass)
    api_client = RealtimeTrainsApiClient(client, username, password)

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
        stops_of_interest: list[str],
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
        self._stops_of_interest = [stop.upper() for stop in stops_of_interest]
        self._platforms_of_interest = {
            platform.strip()
            for platform in platforms_of_interest
            if isinstance(platform, str) and platform.strip()
        }
        self._interval = interval

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
        await self._load_departures()
        self._next_trains = []
        departureCount = 0
        now = cast(datetime, dt_util.now()).astimezone(TIMEZONE)

        nextDepartureEstimatedTs : (datetime | None) = None

        services = self._data.get("services") if isinstance(self._data, dict) else None
        departures = services or []

        for departure in departures:
            if not departure["isPassenger"] :
                continue

            platform = departure["locationDetail"].get("platform", None)
            platform_key = platform.strip() if isinstance(platform, str) else platform
            if self._platforms_of_interest and platform_key not in self._platforms_of_interest:
                continue

            departuredate = TIMEZONE.localize(datetime.fromisoformat(departure["runDate"]))

            scheduled_str = _to_colonseparatedtime(
                departure["locationDetail"].get("gbttBookedDeparture")
            )
            estimated_str = _to_colonseparatedtime(
                departure["locationDetail"].get("realtimeDeparture")
            )

            if scheduled_str is not None:
                scheduledTs = _timestamp(scheduled_str, departuredate)
            elif estimated_str is not None:
                scheduledTs = _timestamp(estimated_str, departuredate)
            else:
                continue

            if estimated_str is not None:
                estimatedTs = _timestamp(estimated_str, departuredate)
            else:
                estimatedTs = scheduledTs

            if _delta_secs(estimatedTs, now) < self._timeoffset.total_seconds():
                continue

            if nextDepartureEstimatedTs is None:
                nextDepartureEstimatedTs = estimatedTs
            else:
                nextDepartureEstimatedTs = min(nextDepartureEstimatedTs, estimatedTs)

            departureCount += 1

            train = {
                    "origin_name": departure["locationDetail"]["origin"][0]["description"],
                    "destination_name": departure["locationDetail"]["destination"][0]["description"],
                    #"service_date": departure["runDate"],
                    "service_uid": departure["serviceUid"],
                    "scheduled": scheduledTs.strftime(STRFFORMAT),
                    "estimated": estimatedTs.strftime(STRFFORMAT),
                    "minutes": _delta_secs(estimatedTs, now) // 60,
                    "platform": platform,
                    "operator_name": departure["atocName"],
                }
            if departureCount > self._journey_data_for_next_X_trains:
                break;
            
            await self._add_journey_data(train, scheduledTs, estimatedTs)
            self._next_trains.append(train)

        if nextDepartureEstimatedTs is None:
            self._state = None
        else:
            self._state = _delta_secs(nextDepartureEstimatedTs, now) // 60

        if self._autoadjustscans:
            if nextDepartureEstimatedTs is None:
                self.async_update = Throttle(timedelta(minutes=30))(self._async_update)
            else:
                self.async_update = self._async_update


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

    async def _load_departures(self):
        try:
            self._data = await self._api.fetch_location_services(
                self._journey_start,
                self._journey_end,
            )
        except RealtimeTrainsApiAuthError:
            self._state = "Credentials invalid"
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
        except RealtimeTrainsApiNotFoundError:
            _LOGGER.warning(
                "Could not find %s in stops for service %s.",
                self._journey_end,
                train['service_uid'],
            )
            return
        except RealtimeTrainsApiAuthError:
            self._state = "Credentials invalid"
            return
        except RealtimeTrainsApiError as err:
            _LOGGER.warning("Could not populate arrival times: %s", err)
            return

        stopsOfInterest = []
        stopCount = -1  # origin counts as first stop in the returned json
        found = False
        for stop in data['locations']:
            if self._journey_end and stop['crs'] == self._journey_end and stop['displayAs'] != 'ORIGIN':
                scheduled_arrival_str = _to_colonseparatedtime(stop.get('gbttBookedArrival'))
                estimated_arrival_str = _to_colonseparatedtime(stop.get('realtimeArrival'))

                if scheduled_arrival_str is not None:
                    scheduled_arrival = _timestamp(scheduled_arrival_str, scheduled_departure)
                elif estimated_arrival_str is not None:
                    scheduled_arrival = _timestamp(estimated_arrival_str, scheduled_departure)
                else:
                    scheduled_arrival = scheduled_departure

                if estimated_arrival_str is not None:
                    estimated_arrival = _timestamp(estimated_arrival_str, scheduled_departure)
                else:
                    estimated_arrival = scheduled_arrival

                status = "OK"
                if 'CANCELLED' in stop['displayAs']:
                    status = "Cancelled"
                elif estimated_arrival > scheduled_arrival:
                    status = "Delayed"

                newtrain = {
                    "stops_of_interest": stopsOfInterest,
                    "scheduled_arrival": scheduled_arrival.strftime(STRFFORMAT),
                    "estimate_arrival": estimated_arrival.strftime(STRFFORMAT),
                    "journey_time_mins": _delta_secs(estimated_arrival, estimated_departure) // 60,
                    "stops": stopCount,
                    "status": status,
                }
                train.update(newtrain)
                found = True
                break
            elif stop['crs'] in self._stops_of_interest and stop['isPublicCall']:
                scheduled_stop_str = _to_colonseparatedtime(stop.get('gbttBookedArrival'))
                estimated_stop_str = _to_colonseparatedtime(stop.get('realtimeArrival'))

                if scheduled_stop_str is not None:
                    scheduled_stop = _timestamp(scheduled_stop_str, scheduled_departure)
                elif estimated_stop_str is not None:
                    scheduled_stop = _timestamp(estimated_stop_str, scheduled_departure)
                else:
                    scheduled_stop = scheduled_departure

                if estimated_stop_str is not None:
                    estimated_stop = _timestamp(estimated_stop_str, scheduled_departure)
                else:
                    estimated_stop = scheduled_stop

                stopsOfInterest.append(
                    {
                        "stop": stop['crs'],
                        "name": stop['description'],
                        "scheduled_stop": scheduled_stop.strftime(STRFFORMAT),
                        "estimate_stop": estimated_stop.strftime(STRFFORMAT),
                        "journey_time_mins": _delta_secs(estimated_stop, estimated_departure) // 60,
                        "stops": stopCount,
                    }
                )
            stopCount += 1
        
        # If no destination specified, just add stops of interest without destination arrival info
        if not self._journey_end:
            found = True
            train["stops_of_interest"] = stopsOfInterest
            train["stops"] = stopCount
        elif not found:
            _LOGGER.warning(
                "Could not find %s in stops for service %s.",
                self._journey_end,
                train['service_uid'],
            )

    @property
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

def _to_colonseparatedtime(hhmm_time_str: str | None) -> str | None:
    if not hhmm_time_str:
        return None
    clean = hhmm_time_str.strip()
    if len(clean) < 4:
        return None
    return clean[:2] + ":" + clean[2:4]

def _timestamp(hhmm_time_str : str, date : datetime | None = None) -> datetime:
    now = cast(datetime, dt_util.now()).astimezone(TIMEZONE) if date is None else date
    hhmm_time_a = datetime.strptime(hhmm_time_str, "%H:%M")
    hhmm_datetime = now.replace(hour=hhmm_time_a.hour, minute=hhmm_time_a.minute, second=0, microsecond=0)
    if hhmm_datetime < now:
        hhmm_datetime += timedelta(days=1)
    return hhmm_datetime

def _delta_secs(hhmm_datetime_a : datetime, hhmm_datetime_b : datetime) -> float:
    """Calculate time delta in minutes to a time in hh:mm format."""
    return (hhmm_datetime_a - hhmm_datetime_b).total_seconds()
