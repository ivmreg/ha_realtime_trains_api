"""Smoke test against a real Home Assistant instance.

The main suite (custom_components/realtime_trains_api/test) runs against a
stubbed homeassistant module tree, which is fast but can't catch drift in
HA's actual APIs. This suite uses pytest-homeassistant-custom-component to
set up a genuine config entry end to end. It lives outside the stub
conftest's directory on purpose and runs as its own CI job:

    pip install -r requirements-test-ha.txt
    pytest tests_ha -q
"""
from __future__ import annotations

import re

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant

DOMAIN = "realtime_trains_api"

LOCATION_URL = re.compile(r"https://data\.rtt\.io/gb-nr/location.*")

# Far-future departure so the service stays "upcoming" whenever the test runs
SERVICES_RESPONSE = {
    "services": [
        {
            "scheduleMetadata": {
                "identity": "P12345",
                "inPassengerService": True,
                "departureDate": "2099-01-01",
                "trainReportingIdentity": "2A69",
                "modeType": "TRAIN",
                "operator": {"name": "Southeastern"},
            },
            "locationMetadata": {
                "platform": {"actual": "1"},
                "numberOfVehicles": 8,
            },
            "origin": [{"location": {"description": "Dartford"}}],
            "destination": [
                {"location": {"description": "London Cannon Street"}}
            ],
            "temporalData": {
                "departure": {"scheduleAdvertised": "2099-01-01T12:00:00"}
            },
        }
    ]
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow HA to load custom_components from this repo."""
    yield


async def test_config_entry_sets_up_sensors(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """A config entry sets up and produces a departure sensor with data."""
    aioclient_mock.get(LOCATION_URL, json=SERVICES_RESPONSE)

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "token": "test-token",
            "refresh_token": "test-refresh-token",
            "queries": [{"origin": "DFD", "destination": "CST"}],
            "peak_interval": 60,
            "off_peak_interval": 300,
            "peak_windows": "07:00-09:30",
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.next_train_from_dfd_to_cst")
    assert state is not None
    # Far-future departure -> large positive minutes value
    assert float(state.state) > 0

    trains = state.attributes.get("next_trains")
    assert trains and len(trains) == 1
    assert trains[0]["destination_name"] == "London Cannon Street"
    assert trains[0]["platform"] == "1"
    assert state.attributes.get("journey_start") == "DFD"
    assert state.attributes.get("data_stale") is False

    rate_limit = hass.states.get("sensor.realtime_trains_rate_limit")
    assert rate_limit is not None

    # And it unloads cleanly
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
