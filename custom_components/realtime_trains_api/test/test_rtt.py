"""Unit tests for business logic helpers in rtt.py."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from custom_components.realtime_trains_api import rtt

SAMPLE_PATH = Path(__file__).parent / "sample.json"
NOW = rtt.TIMEZONE.localize(datetime(2025, 11, 4, 17, 0))
SCHEDULED_DEPARTURE = rtt.TIMEZONE.localize(datetime(2025, 11, 4, 17, 35))
ESTIMATED_DEPARTURE = rtt.TIMEZONE.localize(datetime(2025, 11, 4, 17, 38))


@pytest.fixture(scope="module")
def sample_departures() -> dict:
    """Load the example departures response shipped with the integration."""
    with SAMPLE_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def test_parse_departure_returns_expected_service(sample_departures: dict) -> None:
    service = sample_departures["services"][0]

    parsed = rtt.parse_departure(service, NOW, timedelta())
    assert parsed is not None

    train, scheduled_ts, estimated_ts = parsed

    assert train["service_uid"] == "P18676"
    assert train["delay"] == 3
    assert train["minutes"] == 38
    assert train["scheduled"] == scheduled_ts.strftime(rtt.STRFFORMAT)
    assert train["estimated"] == estimated_ts.strftime(rtt.STRFFORMAT)


def test_parse_departure_respects_time_offset(sample_departures: dict) -> None:
    service = sample_departures["services"][0]

    parsed = rtt.parse_departure(service, NOW, timedelta(hours=1))
    assert parsed is None


def test_parse_service_detail_enriches_train_information() -> None:
    service_detail = {
        "locations": [
            {"crs": "HNH", "description": "Hither Green"},
            {
                "crs": "BKH",
                "description": "Blackheath",
                "gbttBookedDeparture": "1735",
                "realtimeDeparture": "1738",
            },
            {
                "crs": "LEW",
                "description": "Lewisham",
                "gbttBookedArrival": "1740",
                "realtimeArrival": "1742",
                "isPublicCall": True,
            },
            {
                "crs": "CST",
                "description": "London Cannon Street",
                "gbttBookedArrival": "1753",
                "realtimeArrival": "1756",
                "displayAs": "CALL",
            },
        ]
    }

    result = rtt.parse_service_detail(
        service_detail,
        "BKH",
        "CST",
        SCHEDULED_DEPARTURE,
        ESTIMATED_DEPARTURE,
        ["LEW"],
    )

    assert result.success is True
    assert result.data is not None

    journey = result.data
    assert journey["status"] == "Delayed"
    assert journey["arrival_delay"] == 3
    assert journey["journey_time_mins"] == 18
    assert journey["stops"] == 0
    assert len(journey["stops_of_interest"]) == 1

    stop_info = journey["stops_of_interest"][0]
    assert stop_info["stop"] == "LEW"
    assert stop_info["stop_delay"] == 2
    assert stop_info["journey_time_mins"] == 4


def test_parse_service_detail_handles_missing_origin() -> None:
    result = rtt.parse_service_detail(
        {"locations": []},
        "BKH",
        "CST",
        SCHEDULED_DEPARTURE,
        ESTIMATED_DEPARTURE,
        [],
    )

    assert result.success is False
    assert result.reason == rtt.REASON_MISSING_ORIGIN


def test_parse_service_detail_handles_missing_destination() -> None:
    detail = {
        "locations": [
            {
                "crs": "BKH",
                "description": "Blackheath",
                "gbttBookedDeparture": "1735",
                "realtimeDeparture": "1738",
            },
            {
                "crs": "LEW",
                "description": "Lewisham",
                "gbttBookedArrival": "1740",
                "realtimeArrival": "1742",
                "isPublicCall": True,
            },
        ]
    }

    result = rtt.parse_service_detail(
        detail,
        "BKH",
        "CST",
        SCHEDULED_DEPARTURE,
        ESTIMATED_DEPARTURE,
        [],
    )

    assert result.success is False
    assert result.reason == rtt.REASON_DESTINATION_NOT_REACHED


def test_parse_service_detail_rejects_wrong_direction() -> None:
    detail = {
        "locations": [
            {
                "crs": "CST",
                "gbttBookedDeparture": "1700",
                "displayAs": "ORIGIN",
            },
            {
                "crs": "BKH",
                "gbttBookedDeparture": "1735",
                "realtimeDeparture": "1738",
            },
            {
                "crs": "STJ",
                "gbttBookedArrival": "1740",
            },
            {
                "crs": "CST",
                "gbttBookedArrival": "1851",
                "displayAs": "DESTINATION",
            },
        ]
    }

    result = rtt.parse_service_detail(
        detail,
        "BKH",
        "CST",
        SCHEDULED_DEPARTURE,
        ESTIMATED_DEPARTURE,
        [],
    )

    assert result.success is False
    assert result.reason == rtt.REASON_WRONG_DIRECTION
