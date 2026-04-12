"""The realtime_trains_api component."""

from __future__ import annotations
from datetime import timedelta
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

import logging
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .const import (
    DOMAIN, 
    PLATFORMS, 
    CONF_QUERIES, 
    CONF_API_TOKEN, 
    CONF_REFRESH_TOKEN, 
    CONF_PEAK_INTERVAL, 
    CONF_OFF_PEAK_INTERVAL, 
    CONF_PEAK_WINDOWS, 
    DEFAULT_PEAK_INTERVAL, 
    DEFAULT_OFF_PEAK_INTERVAL,
    DEFAULT_PEAK_WINDOWS
)
from .rtt_api import RealtimeTrainsApiClient
from .coordinator import RealtimeTrainsUpdateCoordinator
from .normalization import parse_time_windows

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
	"""Set up the integration via YAML (deprecated)."""
	hass.data.setdefault(DOMAIN, {})
	return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
	"""Set up Realtime Trains API from a config entry."""
	hass.data.setdefault(DOMAIN, {})

	token = entry.data.get(CONF_API_TOKEN)
	refresh_token = entry.data.get(CONF_REFRESH_TOKEN)

	queries = entry.options.get(CONF_QUERIES, entry.data.get(CONF_QUERIES, []))
	peak_interval = entry.options.get(CONF_PEAK_INTERVAL, entry.data.get(CONF_PEAK_INTERVAL, DEFAULT_PEAK_INTERVAL))
	off_peak_interval = entry.options.get(CONF_OFF_PEAK_INTERVAL, entry.data.get(CONF_OFF_PEAK_INTERVAL, DEFAULT_OFF_PEAK_INTERVAL))
	peak_windows_str = entry.options.get(CONF_PEAK_WINDOWS, entry.data.get(CONF_PEAK_WINDOWS, DEFAULT_PEAK_WINDOWS))

	client = async_get_clientsession(hass)
	api_client = RealtimeTrainsApiClient(client, token, refresh_token)

	# Fix: convert integer to float/int directly since it's already an int based on schema
	interval = timedelta(seconds=int(peak_interval))

	coordinator = RealtimeTrainsUpdateCoordinator(
		hass=hass,
		logger=_LOGGER,
		name=f"RTT {entry.entry_id}",
		update_interval=interval,
		api=api_client,
		queries=queries,
		peak_interval=int(peak_interval),
		off_peak_interval=int(off_peak_interval),
		peak_windows=parse_time_windows(peak_windows_str)
	)

	await coordinator.async_config_entry_first_refresh()

	hass.data[DOMAIN][entry.entry_id] = coordinator

	await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
	return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
	"""Unload a config entry."""
	unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
	if unload_ok:
		hass.data[DOMAIN].pop(entry.entry_id, None)
	return unload_ok
