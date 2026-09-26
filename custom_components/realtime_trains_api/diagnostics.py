"""Diagnostics support for the Realtime Trains API integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    DOMAIN,
    CONF_API_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_OPENLDBWS_TOKEN,
    CONF_KB_USERNAME,
    CONF_KB_PASSWORD,
)

TO_REDACT = {
    CONF_API_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_OPENLDBWS_TOKEN,
    CONF_KB_USERNAME,
    CONF_KB_PASSWORD,
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)

    diagnostics: dict[str, Any] = {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
    }

    if coordinator is None:
        diagnostics["coordinator"] = None
        return diagnostics

    def _isoformat(value):
        return value.isoformat() if value is not None else None

    diagnostics["coordinator"] = {
        "current_polling_interval": coordinator.current_polling_interval,
        "peak_interval": coordinator.peak_interval,
        "off_peak_interval": coordinator.off_peak_interval,
        "auto_adjust_scans": coordinator.auto_adjust_scans,
        "data_stale": coordinator.data_stale,
        "last_update_time": _isoformat(coordinator.last_update_time),
        "last_successful_update": _isoformat(coordinator.last_successful_update),
        "last_update_success": getattr(coordinator, "last_update_success", None),
    }
    diagnostics["rate_limits"] = coordinator.api.rate_limits

    # Summarize per-query results without dumping the full departure payloads.
    results = getattr(coordinator, "data", None) or {}
    diagnostics["queries"] = [
        {
            "query_key": query_key,
            "state": result.get("state"),
            "train_count": len(result.get("next_trains", [])),
            "journey_start": result.get("journey_start"),
            "journey_end": result.get("journey_end"),
            "platforms_of_interest": sorted(result.get("platforms_of_interest") or []),
        }
        for query_key, result in results.items()
    ]

    return diagnostics
