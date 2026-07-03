"""Disruption binary sensors for pinned trains."""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    CONF_END,
    CONF_PINNED_DEPARTURE,
    CONF_PLATFORMS_OF_INTEREST,
    CONF_QUERIES,
    CONF_START,
    CONF_TIMEOFFSET,
    PINNED_DELAY_THRESHOLD_MINUTES,
)
from .coordinator import RealtimeTrainsUpdateCoordinator
from .sensor import _normalize_query
from .sensor_helpers import build_query_key, evaluate_pinned_disruption

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up disruption binary sensors for queries with a pinned train."""
    coordinator = hass.data[DOMAIN].get(entry.entry_id)
    if not coordinator:
        _LOGGER.error("Coordinator not found for entry %s", entry.entry_id)
        return

    queries = entry.options.get(CONF_QUERIES) or entry.data.get(CONF_QUERIES, [])

    sensors: list[BinarySensorEntity] = []
    for idx, raw_query in enumerate(queries):
        try:
            query = _normalize_query(raw_query)
        except ValueError as err:
            _LOGGER.warning("Skipping RTT query configuration: %s", err)
            continue

        pinned_time = query.get(CONF_PINNED_DEPARTURE)
        if not pinned_time:
            continue

        query_key = build_query_key(
            query[CONF_START],
            query[CONF_END],
            query[CONF_PLATFORMS_OF_INTEREST],
            query[CONF_TIMEOFFSET],  # already a timedelta via _normalize_query
        )
        sensors.append(
            RealtimeTrainPinnedDisruptionSensor(
                coordinator,
                query_key,
                query[CONF_START],
                query[CONF_END],
                pinned_time,
                entry.entry_id,
                idx,
            )
        )

    if sensors:
        async_add_entities(sensors, True)


class RealtimeTrainPinnedDisruptionSensor(CoordinatorEntity, BinarySensorEntity):
    """On when the pinned train is cancelled or delayed beyond the threshold."""

    _attr_icon = "mdi:train-car-passenger-variant"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(
        self,
        coordinator: RealtimeTrainsUpdateCoordinator,
        query_key: str,
        origin: str,
        destination: str | None,
        pinned_time: str,
        entry_id: str,
        query_index: int,
    ) -> None:
        super().__init__(coordinator)
        self._query_key = query_key
        self._pinned_time = pinned_time

        destination_str = f" to {destination}" if destination else ""
        self._attr_name = f"Your {pinned_time} from {origin}{destination_str} disrupted"
        self._attr_unique_id = f"{entry_id}_{query_key}_{pinned_time}_pinned_{query_index}"

    def _pinned_train(self) -> dict | None:
        data = self.coordinator.data or {}
        result = data.get(self._query_key)
        if not result:
            return None
        return result.get("pinned_train")

    @property
    def is_on(self) -> bool | None:
        train = self._pinned_train()
        disrupted, _ = evaluate_pinned_disruption(
            train, PINNED_DELAY_THRESHOLD_MINUTES
        )
        return disrupted

    @property
    def extra_state_attributes(self):
        train = self._pinned_train()
        disrupted, reason = evaluate_pinned_disruption(
            train, PINNED_DELAY_THRESHOLD_MINUTES
        )
        attrs = {
            "pinned_departure_time": self._pinned_time,
            "reason": reason,
        }
        if train:
            attrs.update(
                {
                    "scheduled": train.get("scheduled"),
                    "estimated": train.get("estimated"),
                    "platform": train.get("platform"),
                    "is_cancelled": train.get("is_cancelled"),
                    "destination_name": train.get("destination_name"),
                    "service_uid": train.get("service_uid"),
                    "disruption_reason": train.get("reason"),
                }
            )
        else:
            attrs["train_found"] = False
        return attrs
