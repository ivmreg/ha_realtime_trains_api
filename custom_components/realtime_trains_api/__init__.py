"""The realtime_trains_api component."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

import logging
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .const import (
    DOMAIN, 
    PLATFORMS, 
    CONF_QUERIES, 
    DEFAULT_SCAN_INTERVAL, 
    CONF_API_TOKEN, 
    CONF_REFRESH_TOKEN, 
    CONF_PEAK_INTERVAL, 
    CONF_OFF_PEAK_INTERVAL, 
    CONF_PEAK_WINDOWS, 
    DEFAULT_PEAK_INTERVAL, 
    DEFAULT_OFF_PEAK_INTERVAL
)
from .rtt_api import RealtimeTrainsApiClient
from .coordinator import RealtimeTrainsUpdateCoordinator
from .normalization import coerce_scan_interval, parse_time_windows

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
	"""Set up the integration via YAML (deprecated)."""
	hass.data.setdefault(DOMAIN, {})
	return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
	"""Set up Realtime Trains API from a config entry."""
	hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {}
	await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
	return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
	"""Unload a config entry."""
	unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
	if unload_ok:
		hass.data[DOMAIN].pop(entry.entry_id, None)
	return unload_ok