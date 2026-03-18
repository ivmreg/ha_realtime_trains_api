"""Support for UK train data provided by api.rtt.io."""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
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
    CONF_JOURNEYDATA,
    CONF_QUERIES,
    CONF_SENSORNAME,
    CONF_START,
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
        vol.Optional(CONF_JOURNEYDATA, default=0): cv.positive_int,
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
    if not CRS_CODE_PATTERN.match(origin):
        raise ValueError(f"Invalid origin CRS code: {origin}")

    journey_data = _coerce_positive_int(raw_query.get(CONF_JOURNEYDATA, 0))

    return {
        CONF_SENSORNAME: sensor_name,
        CONF_START: origin,
        CONF_JOURNEYDATA: journey_data,
    }


def _query_unique_key(query: dict[str, Any]) -> str:
    stable_json = json.dumps(query, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(stable_json.encode("utf-8")).hexdigest()[:12]


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

    for raw_query in queries or []:
        try:
            query = _normalize_query(raw_query)
        except ValueError as err:
            _LOGGER.warning("Skipping RTT query configuration: %s", err)
            continue

        sensor = RealtimeTrainLiveTrainTimeSensor(
            query.get(CONF_SENSORNAME),
            api_client,
            query[CONF_START],
            query[CONF_JOURNEYDATA],
            autoadjustscans,
            interval,
            entry_id,
            _query_unique_key(query),
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
        journey_data_for_next_X_trains: int,
        autoadjustscans: bool,
        interval: timedelta,
        entry_id: str | None = None,
        query_key: str | None = None,
    ) -> None:
        """Construct a live train time sensor."""

        default_sensor_name = f"Trains from {journey_start}"

        self._journey_start = journey_start
        self._journey_data_for_next_X_trains = journey_data_for_next_X_trains
        self._next_trains = []
        self._data = {}
        self._api = api_client
        self._autoadjustscans = autoadjustscans
        self._interval = interval

        self._name = default_sensor_name if sensor_name is None else sensor_name
        self._state = None
        
        # Generate a stable unique_id for config entry sensors
        if entry_id and query_key:
            self._attr_unique_id = f"{entry_id}_{query_key}"
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

            location_detail = departure.get("locationDetail", {})
            platform = location_detail.get("platform", None)

            departuredate = TIMEZONE.localize(datetime.fromisoformat(departure["runDate"]))

            scheduled_str = _to_colonseparatedtime(location_detail.get("gbttBookedDeparture"))
            estimated_str = _to_colonseparatedtime(location_detail.get("realtimeDeparture"))

            if scheduled_str is None and estimated_str is None:
                scheduled_str = _to_colonseparatedtime(location_detail.get("gbttBookedArrival"))
                estimated_str = _to_colonseparatedtime(location_detail.get("realtimeArrival"))

            scheduledTs, estimatedTs = _parse_times(
                scheduled_str, estimated_str, departuredate
            )
            if scheduledTs is None or estimatedTs is None:
                continue

            if estimated_str is not None:
                estimatedTs = _timestamp(estimated_str, departuredate)
            else:
                estimatedTs = scheduledTs

            if nextDepartureEstimatedTs is None:
                nextDepartureEstimatedTs = estimatedTs
            else:
                nextDepartureEstimatedTs = min(nextDepartureEstimatedTs, estimatedTs)

            departureCount += 1

            train = {
                    "origin_name": departure["locationDetail"]["origin"][0]["description"],
                    "destination_name": departure["locationDetail"]["destination"][0]["description"],
                    "service_uid": departure["serviceUid"],
                    "scheduled": scheduledTs.strftime(STRFFORMAT),
                    "estimated": estimatedTs.strftime(STRFFORMAT),
                    "minutes": _delta_seconds(estimatedTs, now) // 60,
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
            self._state = _delta_seconds(nextDepartureEstimatedTs, now) // 60

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
            self._data = await self._api.fetch_location_services(self._journey_start)
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
                "Could not find service %s.",
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
        found_start = False

        last_report_station = None
        last_report_station_name = None
        last_report_type = None
        last_report_time_str = None
        last_report_index = 0

        for stop in data['locations']:
            if stop.get('realtimePassActual') and stop.get('realtimePass'):
                last_report_station = stop.get('crs')
                last_report_station_name = stop.get('description')
                last_report_type = 'Pass'
                last_report_time_str = _to_colonseparatedtime(stop.get('realtimePass'))
                last_report_index = max(0, stopCount)
            elif stop.get('realtimeDepartureActual') and stop.get('realtimeDeparture'):
                last_report_station = stop.get('crs')
                last_report_station_name = stop.get('description')
                last_report_type = 'Departure'
                last_report_time_str = _to_colonseparatedtime(stop.get('realtimeDeparture'))
                last_report_index = max(0, stopCount)
            elif stop.get('realtimeArrivalActual') and stop.get('realtimeArrival'):
                last_report_station = stop.get('crs')
                last_report_station_name = stop.get('description')
                last_report_type = 'Arrival'
                last_report_time_str = _to_colonseparatedtime(stop.get('realtimeArrival'))
                last_report_index = max(0, stopCount)

            if stop['crs'] == self._journey_start:
                found_start = True

            if found_start and stop['crs'] != self._journey_start:
                if stop.get('isPublicCall'):
                    scheduled_stop_str = _to_colonseparatedtime(stop.get('gbttBookedArrival'))
                    estimated_stop_str = _to_colonseparatedtime(stop.get('realtimeArrival'))

                    scheduled_stop, estimated_stop = _parse_times(
                        scheduled_stop_str, estimated_stop_str, scheduled_departure, scheduled_departure
                    )

                    stopsOfInterest.append(
                        {
                            "stop": stop.get('crs'),
                            "name": stop.get('description'),
                            "scheduled_stop": scheduled_stop.strftime(STRFFORMAT),
                            "estimate_stop": estimated_stop.strftime(STRFFORMAT),
                            "journey_time_mins": _delta_seconds(estimated_stop, estimated_departure) // 60,
                            "stops": stopCount,
                        }
                    )
            stopCount += 1
        
        train["stops_of_interest"] = stopsOfInterest
        train["stops"] = stopCount

        if last_report_station is not None:
            if last_report_time_str:
                # Use the service run date (midnight of the scheduled departure date)
                # as the baseline so that times earlier than the journey-start
                # departure time are not incorrectly rolled to the next day.
                service_run_date = scheduled_departure.replace(hour=0, minute=0, second=0, microsecond=0)
                last_report_time = _timestamp(last_report_time_str, service_run_date)
            else:
                last_report_time = None
            train["last_report_station"] = last_report_station
            train["last_report_station_name"] = last_report_station_name
            train["last_report_type"] = last_report_type
            train["last_report_time"] = last_report_time.strftime(STRFFORMAT) if last_report_time else None
            train["last_report_index"] = last_report_index

    @property
    def extra_state_attributes(self):
        """Return other details about the sensor state."""
        attrs = {}
        if self._data is not None:
            attrs[ATTR_JOURNEY_START] = self._journey_start
            if self._next_trains:
                attrs[ATTR_NEXT_TRAINS] = self._next_trains
            return attrs

def _to_colonseparatedtime(hhmm_time_str: str | None) -> str | None:
    if not hhmm_time_str:
        return None
    clean = hhmm_time_str.strip()
    if len(clean) != 4:
        return None
    return clean[:2] + ":" + clean[2:4]


def _parse_times(
    scheduled_str: str | None,
    estimated_str: str | None,
    date: datetime,
    fallback_ts: datetime | None = None,
) -> tuple[datetime | None, datetime | None]:
    """Parse scheduled and estimated times, applying fallbacks."""
    if scheduled_str is not None:
        scheduled_ts = _timestamp(scheduled_str, date)
    elif estimated_str is not None:
        scheduled_ts = _timestamp(estimated_str, date)
    else:
        scheduled_ts = fallback_ts

    if estimated_str is not None:
        estimated_ts = _timestamp(estimated_str, date)
    else:
        estimated_ts = scheduled_ts

    return scheduled_ts, estimated_ts

def _timestamp(hhmm_time_str: str, date: datetime | None = None) -> datetime:
    """Convert a time string to a datetime on or after the given date.

    Accepts either "HH:MM" or "HHMM" formats. Raises ValueError on invalid input.
    """
    now = cast(datetime, dt_util.now()).astimezone(TIMEZONE) if date is None else date
    clean = hhmm_time_str.strip()

    if ":" in clean:
        parts = clean.split(":")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(f"Invalid time format (expected HH:MM): {hhmm_time_str!r}")
        if not parts[0].isdigit() or not parts[1].isdigit():
            raise ValueError(f"Invalid time value (non-numeric): {hhmm_time_str!r}")
        hour = int(parts[0])
        minute = int(parts[1])
    else:
        if len(clean) != 4 or not clean.isdigit():
            raise ValueError(f"Invalid time format (expected HHMM or HH:MM): {hhmm_time_str!r}")
        hour = int(clean[:2])
        minute = int(clean[2:])

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid time value (out of range): {hhmm_time_str!r}")

    hhmm_datetime = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if hhmm_datetime < now:
        hhmm_datetime += timedelta(days=1)
    return hhmm_datetime

def _delta_seconds(hhmm_datetime_a : datetime, hhmm_datetime_b : datetime) -> float:
    """Calculate time delta in seconds between two datetime objects."""
    return (hhmm_datetime_a - hhmm_datetime_b).total_seconds()
