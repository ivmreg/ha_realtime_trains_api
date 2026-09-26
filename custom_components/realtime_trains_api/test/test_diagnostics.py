from unittest.mock import MagicMock

import pytest

from custom_components.realtime_trains_api.diagnostics import (
    async_get_config_entry_diagnostics,
    TO_REDACT,
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


@pytest.mark.asyncio
async def test_diagnostics_redacts_credentials_in_data_and_options(hass, config_entry):
    config_entry.data = {
        "token": "secret-rtt-token",
        "refresh_token": "secret-rtt-refresh",
        "openldbws_token": "secret-openldbws-token",
        "kb_username": "secret-kb-user",
        "kb_password": "secret-kb-password",
        "unrelated_field": "visible_value",
    }
    config_entry.options = {
        "token": "secret-opt-rtt-token",
        "refresh_token": "secret-opt-rtt-refresh",
        "openldbws_token": "secret-opt-openldbws-token",
        "kb_username": "secret-opt-kb-user",
        "kb_password": "secret-opt-kb-password",
        "unrelated_option": "visible_option",
    }
    hass.data = {}

    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)

    data = diagnostics["entry"]["data"]
    options = diagnostics["entry"]["options"]

    # Verify all credential keys are redacted in entry.data
    assert data["token"] == "**REDACTED**"
    assert data["refresh_token"] == "**REDACTED**"
    assert data["openldbws_token"] == "**REDACTED**"
    assert data["kb_username"] == "**REDACTED**"
    assert data["kb_password"] == "**REDACTED**"
    assert data["unrelated_field"] == "visible_value"

    # Verify all credential keys are redacted in entry.options
    assert options["token"] == "**REDACTED**"
    assert options["refresh_token"] == "**REDACTED**"
    assert options["openldbws_token"] == "**REDACTED**"
    assert options["kb_username"] == "**REDACTED**"
    assert options["kb_password"] == "**REDACTED**"
    assert options["unrelated_option"] == "visible_option"


def test_to_redact_contains_all_credential_keys():
    assert "openldbws_token" in TO_REDACT
    assert "kb_username" in TO_REDACT
    assert "kb_password" in TO_REDACT
    assert "token" in TO_REDACT
    assert "refresh_token" in TO_REDACT
