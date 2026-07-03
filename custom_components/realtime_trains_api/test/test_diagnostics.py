from unittest.mock import MagicMock

import pytest

from custom_components.realtime_trains_api.diagnostics import (
    async_get_config_entry_diagnostics,
)


@pytest.mark.asyncio
async def test_diagnostics_redacts_tokens(hass, config_entry):
    config_entry.data = {"token": "secret-token", "refresh_token": "secret-refresh"}
    config_entry.options = {"queries": [{"origin": "DFD", "destination": "CST"}]}

    coordinator = MagicMock()
    coordinator.current_polling_interval = 60
    coordinator.peak_interval = 60
    coordinator.off_peak_interval = 300
    coordinator.auto_adjust_scans = False
    coordinator.data_stale = False
    coordinator.last_update_time = None
    coordinator.last_successful_update = None
    coordinator.last_update_success = True
    coordinator.api.rate_limits = {"minute": {"limit": 60, "remaining": 59}}
    coordinator.data = {
        "DFD_CST_all_0": {
            "state": 4,
            "next_trains": [{"service_uid": "S1"}],
            "journey_start": "DFD",
            "journey_end": "CST",
            "platforms_of_interest": set(),
        }
    }
    hass.data = {"realtime_trains_api": {config_entry.entry_id: coordinator}}

    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)

    assert diagnostics["entry"]["data"]["token"] == "**REDACTED**"
    assert diagnostics["entry"]["data"]["refresh_token"] == "**REDACTED**"
    assert diagnostics["queries"][0]["train_count"] == 1
    assert diagnostics["queries"][0]["state"] == 4
    assert diagnostics["rate_limits"]["minute"]["remaining"] == 59


@pytest.mark.asyncio
async def test_diagnostics_without_coordinator(hass, config_entry):
    config_entry.data = {"token": "secret-token"}
    config_entry.options = {}
    hass.data = {}

    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)

    assert diagnostics["coordinator"] is None
    assert diagnostics["entry"]["data"]["token"] == "**REDACTED**"
