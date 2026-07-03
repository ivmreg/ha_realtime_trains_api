from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from freezegun import freeze_time

from custom_components.realtime_trains_api.binary_sensor import (
    RealtimeTrainPinnedDisruptionSensor,
    async_setup_entry,
)
from custom_components.realtime_trains_api.coordinator import (
    RealtimeTrainsUpdateCoordinator,
)
from custom_components.realtime_trains_api.sensor_helpers import (
    evaluate_pinned_disruption,
)


class TestEvaluatePinnedDisruption:
    def test_none_train_not_disrupted(self):
        assert evaluate_pinned_disruption(None, 5) == (False, None)

    def test_cancelled_is_disrupted(self):
        train = {"is_cancelled": True, "scheduled": "x", "estimated": "x"}
        assert evaluate_pinned_disruption(train, 5) == (True, "Cancelled")

    def test_journey_status_cancelled_is_disrupted(self):
        train = {"status": "Cancelled", "scheduled": "x", "estimated": "x"}
        assert evaluate_pinned_disruption(train, 5) == (True, "Cancelled")

    def test_delay_beyond_threshold(self):
        train = {
            "scheduled": "07-04-2026 07:42",
            "estimated": "07-04-2026 07:49",
        }
        assert evaluate_pinned_disruption(train, 5) == (True, "Delayed 7 min")

    def test_small_delay_not_disrupted(self):
        train = {
            "scheduled": "07-04-2026 07:42",
            "estimated": "07-04-2026 07:45",
        }
        assert evaluate_pinned_disruption(train, 5) == (False, None)

    def test_unparseable_times_not_disrupted(self):
        train = {"scheduled": "garbage", "estimated": "07:49"}
        assert evaluate_pinned_disruption(train, 5) == (False, None)


def _service(uid: str, sched: str) -> dict:
    return {
        "scheduleMetadata": {
            "identity": uid,
            "inPassengerService": True,
            "departureDate": "2026-04-07",
        },
        "temporalData": {"departure": {"scheduleAdvertised": sched}},
    }


@pytest.mark.asyncio
async def test_coordinator_marks_pinned_train():
    api = MagicMock()
    api.fetch_location_services = AsyncMock(return_value={
        "services": [
            _service("S1", "2026-04-07T12:05:00Z"),
            _service("S2", "2026-04-07T12:42:00Z"),
        ]
    })

    coordinator = RealtimeTrainsUpdateCoordinator(
        hass=MagicMock(),
        logger=MagicMock(),
        name="test",
        update_interval=timedelta(minutes=1),
        api=api,
        queries=[{
            "origin": "WAL",
            "destination": "WAT",
            "pinned_departure_time": "12:42",
        }],
    )

    with freeze_time("2026-04-07 12:00:00"):
        data = await coordinator._async_update_data()

    result = data["WAL_WAT_all_0"]
    assert result["pinned_train"] is not None
    assert result["pinned_train"]["service_uid"] == "S2"
    assert result["pinned_train"]["is_pinned"] is True
    assert "is_pinned" not in result["next_trains"][0]


@pytest.mark.asyncio
async def test_coordinator_pinned_train_absent():
    api = MagicMock()
    api.fetch_location_services = AsyncMock(return_value={
        "services": [_service("S1", "2026-04-07T12:05:00Z")]
    })

    coordinator = RealtimeTrainsUpdateCoordinator(
        hass=MagicMock(),
        logger=MagicMock(),
        name="test",
        update_interval=timedelta(minutes=1),
        api=api,
        queries=[{
            "origin": "WAL",
            "destination": "WAT",
            "pinned_departure_time": "23:59",
        }],
    )

    with freeze_time("2026-04-07 12:00:00"):
        data = await coordinator._async_update_data()

    assert data["WAL_WAT_all_0"]["pinned_train"] is None


def _make_binary_sensor(pinned_train):
    coordinator = MagicMock()
    coordinator.data = {"DFD_CST_all_0": {"pinned_train": pinned_train}}
    return RealtimeTrainPinnedDisruptionSensor(
        coordinator, "DFD_CST_all_0", "DFD", "CST", "07:42", "entry-1", 0
    )


def test_binary_sensor_on_when_cancelled():
    sensor = _make_binary_sensor({
        "scheduled": "07-04-2026 07:42",
        "estimated": "07-04-2026 07:42",
        "is_cancelled": True,
        "platform": "2",
        "destination_name": "London Cannon Street",
        "service_uid": "S2",
    })
    assert sensor.is_on is True
    attrs = sensor.extra_state_attributes
    assert attrs["reason"] == "Cancelled"
    assert attrs["pinned_departure_time"] == "07:42"


def test_binary_sensor_off_when_on_time():
    sensor = _make_binary_sensor({
        "scheduled": "07-04-2026 07:42",
        "estimated": "07-04-2026 07:43",
        "is_cancelled": False,
    })
    assert sensor.is_on is False


def test_binary_sensor_off_when_train_missing():
    sensor = _make_binary_sensor(None)
    assert sensor.is_on is False
    assert sensor.extra_state_attributes["train_found"] is False


@pytest.mark.asyncio
async def test_setup_entry_creates_sensor_only_for_pinned_queries(hass, config_entry):
    coordinator = MagicMock()
    hass.data = {"realtime_trains_api": {config_entry.entry_id: coordinator}}
    config_entry.options = {}
    config_entry.data = {
        "queries": [
            {"origin": "DFD", "destination": "CST"},
            {"origin": "DFD", "destination": "CST", "pinned_departure_time": "07:42"},
        ]
    }

    added = []
    async_setup = lambda entities, update: added.extend(entities)
    await async_setup_entry(hass, config_entry, async_setup)

    assert len(added) == 1
    assert added[0]._pinned_time == "07:42"
