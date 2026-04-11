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
from homeassistant.helpers.update_coordinator import CoordinatorEntity
import homeassistant.util.dt as dt_util

from .const import (
    DOMAIN,
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
    CONF_PEAK_INTERVAL,
    CONF_OFF_PEAK_INTERVAL,
    CONF_PEAK_WINDOWS,
    DEFAULT_PEAK_INTERVAL,
    DEFAULT_OFF_PEAK_INTERVAL,
    DEFAULT_PEAK_WINDOWS,
)
from .normalization import coerce_positive_int, coerce_time_offset, split_csv, parse_time_windows
from .sensor_helpers import (
    build_default_sensor_name,
)
from .rtt_api import RealtimeTrainsApiClient
from .coordinator import RealtimeTrainsUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOFFSET = timedelta(minutes=0)

ATTR_ATCOCODE = "atcocode"
ATTR_LOCALITY = "locality"
ATTR_REQUEST_TIME = "request_time"
ATTR_JOURNEY_START = "journey_start"
ATTR_JOURNEY_END = "journey_end"
ATTR_NEXT_TRAINS = "next_trains"
ATTR_PLATFORMS_OF_INTEREST = "platforms_of_interest"
ATTR_CURRENT_POLLING_INTERVAL = "current_polling_interval"
ATTR_NEXT_UPDATE_AT = "next_update_at"

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

    journey_data = coerce_positive_int(raw_query.get(CONF_JOURNEYDATA, 0))
    time_offset = coerce_time_offset(raw_query.get(CONF_TIMEOFFSET, DEFAULT_TIMEOFFSET), DEFAULT_TIMEOFFSET)
    platforms = split_csv(raw_query.get(CONF_PLATFORMS_OF_INTEREST, []))

    return {
        CONF_SENSORNAME: sensor_name,
        CONF_START: origin,
        CONF_END: destination,
        CONF_JOURNEYDATA: journey_data,
        CONF_TIMEOFFSET: time_offset,
        CONF_PLATFORMS_OF_INTEREST: platforms,
    }


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Get the realtime_train sensor."""
    interval = timedelta(seconds=DEFAULT_PEAK_INTERVAL)
    token = config.get(RTT_CONF_API_TOKEN)
    refresh_token = config.get(RTT_CONF_REFRESH_TOKEN)

    if not token and not refresh_token:
        _LOGGER.error("Realtime Trains API entry is missing credentials.")
        return

    queries = config[CONF_QUERIES]

    client = async_get_clientsession(hass)
    api_client = RealtimeTrainsApiClient(client, token, refresh_token)
    
    coordinator = RealtimeTrainsUpdateCoordinator(
        hass=hass,
        logger=_LOGGER,
        name="RTT YAML",
        update_interval=interval,
        api=api_client,
        queries=queries,
        peak_interval=DEFAULT_PEAK_INTERVAL,
        off_peak_interval=DEFAULT_OFF_PEAK_INTERVAL,
        peak_windows=parse_time_windows(DEFAULT_PEAK_WINDOWS)
    )
    
    await coordinator.async_refresh()

    sensors: list[SensorEntity] = []
    seen_names: set[str] = set()

    for idx, raw_query in enumerate(queries or []):
        try:
            query = _normalize_query(raw_query)
        except ValueError as err:
            _LOGGER.warning("Skipping RTT query configuration: %s", err)
            continue
            
        origin = query[CONF_START]
        destination = query[CONF_END]
        platforms = query[CONF_PLATFORMS_OF_INTEREST]
        time_offset = query[CONF_TIMEOFFSET]
        
        platforms_str = "_".join(sorted(platforms)) if platforms else "all"
        dest_str = destination if destination else "all"
        offset_str = f"{int(time_offset.total_seconds())}" if time_offset.total_seconds() > 0 else "0"
        query_key = f"{origin}_{dest_str}_{platforms_str}_{offset_str}"

        sensor = RealtimeTrainLiveTrainTimeSensor(
            coordinator,
            query.get(CONF_SENSORNAME),
            query_key,
            origin,
            destination,
            time_offset,
            platforms,
            None,
            idx,
        )

        if sensor.name in seen_names:
            _LOGGER.warning("Duplicate sensor name '%s' - skipping duplicate entry", sensor.name)
            continue

        seen_names.add(sensor.name)
        sensors.append(sensor)

    sensors.append(RealtimeTrainRateLimitSensor(coordinator, None))

    if sensors:
        async_add_entities(sensors, True)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors from a config entry."""
    coordinator = hass.data[DOMAIN].get(entry.entry_id)
    if not coordinator:
        _LOGGER.error("Coordinator not found for entry %s", entry.entry_id)
        return

    queries = entry.options.get(CONF_QUERIES) or entry.data.get(CONF_QUERIES, [])

    if not queries:
        _LOGGER.warning("Realtime Trains API entry %s has no queries configured", entry.entry_id)
        return

    sensors: list[SensorEntity] = []
    seen_names: set[str] = set()
    seen_unique_ids: set[str] = set()

    for idx, raw_query in enumerate(queries):
        try:
            query = _normalize_query(raw_query)
        except ValueError as err:
            _LOGGER.warning("Skipping RTT query configuration: %s", err)
            continue

        origin = query[CONF_START]
        destination = query[CONF_END]
        platforms = query[CONF_PLATFORMS_OF_INTEREST]
        time_offset = query[CONF_TIMEOFFSET]
        
        platforms_str = "_".join(sorted(platforms)) if platforms else "all"
        dest_str = destination if destination else "all"
        offset_str = f"{int(time_offset.total_seconds())}" if time_offset.total_seconds() > 0 else "0"
        query_key = f"{origin}_{dest_str}_{platforms_str}_{offset_str}"

        sensor = RealtimeTrainLiveTrainTimeSensor(
            coordinator,
            query.get(CONF_SENSORNAME),
            query_key,
            origin,
            destination,
            time_offset,
            platforms,
            entry.entry_id,
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

    sensors.append(RealtimeTrainRateLimitSensor(coordinator, entry.entry_id))

    if sensors:
        async_add_entities(sensors, True)
    else:
        _LOGGER.warning(
            "Realtime Trains API entry %s produced no sensors after validation", entry.entry_id
        )


class RealtimeTrainLiveTrainTimeSensor(CoordinatorEntity, SensorEntity):
    """
    Sensor that reads the rtt API via coordinator.
    """

    _attr_icon = "mdi:train"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(
        self,
        coordinator: RealtimeTrainsUpdateCoordinator,
        sensor_name: str | None,
        query_key: str,
        journey_start: str,
        journey_end: str | None,
        timeoffset: timedelta,
        platforms_of_interest: list[str],
        entry_id: str | None = None,
        query_index: int = 0,
    ) -> None:
        """Construct a live train time sensor."""
        super().__init__(coordinator)

        default_sensor_name = build_default_sensor_name(
            journey_start,
            journey_end,
            timeoffset,
            platforms_of_interest,
        )

        self._query_key = query_key
        self._journey_start = journey_start
        self._journey_end = journey_end
        self._timeoffset = timeoffset
        self._platforms_of_interest = {
            platform.strip()
            for platform in platforms_of_interest
            if isinstance(platform, str) and platform.strip()
        }
        self._entry_id = entry_id

        self._attr_name = default_sensor_name if sensor_name is None else sensor_name
        
        if entry_id:
            platforms_str = "_".join(sorted(self._platforms_of_interest)) if self._platforms_of_interest else "all"
            dest_str = journey_end if journey_end else "all"
            offset_str = f"{int(timeoffset.total_seconds())}" if timeoffset.total_seconds() > 0 else "0"
            self._attr_unique_id = f"{entry_id}_{journey_start}_{dest_str}_{platforms_str}_{offset_str}_{query_index}"
        else:
            self._attr_unique_id = None

    @property
    def native_value(self):
        """Return the state of the sensor."""
        if not self.coordinator.data or self._query_key not in self.coordinator.data:
            return None
        return self.coordinator.data[self._query_key].get("state")

    @property
    def extra_state_attributes(self):
        """Return other details about the sensor state."""
        attrs = {}
        
        if self.coordinator.data and self._query_key in self.coordinator.data:
            data = self.coordinator.data[self._query_key]
            attrs[ATTR_JOURNEY_START] = data.get("journey_start")
            if data.get("journey_end"):
                attrs[ATTR_JOURNEY_END] = data.get("journey_end")
                
            next_trains = data.get("next_trains", [])
            if next_trains:
                attrs[ATTR_NEXT_TRAINS] = next_trains
                
            if data.get("platforms_of_interest"):
                attrs[ATTR_PLATFORMS_OF_INTEREST] = list(data["platforms_of_interest"])
                
        attrs[ATTR_CURRENT_POLLING_INTERVAL] = self.coordinator.current_polling_interval
        if getattr(self.coordinator, "last_update_time", None):
            next_update = self.coordinator.last_update_time + self.coordinator.update_interval
            attrs[ATTR_NEXT_UPDATE_AT] = next_update.isoformat()
            
        return attrs


class RealtimeTrainRateLimitSensor(CoordinatorEntity, SensorEntity):
    """Sensor tracking API rate limits via coordinator."""

    _attr_icon = "mdi:speedometer"
    _attr_native_unit_of_measurement = "requests"

    def __init__(
        self,
        coordinator: RealtimeTrainsUpdateCoordinator,
        entry_id: str | None = None,
    ) -> None:
        """Initialize the rate limit sensor."""
        super().__init__(coordinator)
        self._entry_id = entry_id
        
        base_name = "Realtime Trains Rate Limit"
        self._attr_name = base_name
        
        if entry_id:
            self._attr_unique_id = f"{entry_id}_rate_limit"
        else:
            self._attr_unique_id = None

    @property
    def native_value(self):
        """Return the lowest remaining rate limit."""
        lowest = None
        for limits in self.coordinator.api.rate_limits.values():
            if limits["remaining"] is not None:
                if lowest is None or limits["remaining"] < lowest:
                    lowest = limits["remaining"]
        return lowest

    @property
    def extra_state_attributes(self):
        """Return rate limit attributes."""
        attrs = {}
        for dim, limits in self.coordinator.api.rate_limits.items():
            if limits["limit"] is not None:
                attrs[f"{dim}_limit"] = limits["limit"]
            if limits["remaining"] is not None:
                attrs[f"{dim}_remaining"] = limits["remaining"]
                
        # Also include polling interval in rate limit sensor for convenience
        attrs[ATTR_CURRENT_POLLING_INTERVAL] = self.coordinator.current_polling_interval
        if getattr(self.coordinator, "last_update_time", None):
            next_update = self.coordinator.last_update_time + self.coordinator.update_interval
            attrs[ATTR_NEXT_UPDATE_AT] = next_update.isoformat()
            
        return attrs
