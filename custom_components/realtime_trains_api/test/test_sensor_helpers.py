from datetime import datetime, timedelta, timezone

from custom_components.realtime_trains_api.sensor_helpers import (
    collect_subsequent_stops,
    build_default_sensor_name,
    find_last_report,
    parse_rtt_datetime,
    retry_with_auth_refresh,
    subsequent_stop_start_index,
)
from custom_components.realtime_trains_api.rtt_api import (
    RealtimeTrainsApiAuthError,
    RealtimeTrainsApiError,
)


class FakeTimezone:
    def __init__(self) -> None:
        self.localize_calls: list[datetime] = []

    def localize(self, value: datetime) -> datetime:
        self.localize_calls.append(value)
        return value.replace(tzinfo=timezone.utc)


def test_build_default_sensor_name_with_destination_and_platforms() -> None:
    result = build_default_sensor_name(
        "WAT",
        "BAS",
        timedelta(minutes=20),
        ["11", "10"],
    )

    assert result == "Next train from WAT platform 10, 11 to BAS (0:20:00)"


def test_build_default_sensor_name_without_destination() -> None:
    result = build_default_sensor_name("WAT", None, timedelta(), [])

    assert result == "Trains from WAT"


def test_parse_rtt_datetime_localizes_naive_value() -> None:
    tz = FakeTimezone()

    result = parse_rtt_datetime("2025-11-04T17:30:00", tz)

    assert result.tzinfo is timezone.utc
    assert tz.localize_calls == [datetime(2025, 11, 4, 17, 30)]


def test_parse_rtt_datetime_preserves_aware_value() -> None:
    tz = FakeTimezone()

    result = parse_rtt_datetime("2025-11-04T17:30:00+00:00", tz)

    assert result.tzinfo is not None
    assert tz.localize_calls == []


async def test_retry_with_auth_refresh_returns_first_result() -> None:
    refresh_called = False

    async def fetch() -> str:
        return "ok"

    async def refresh() -> bool:
        nonlocal refresh_called
        refresh_called = True
        return True

    result = await retry_with_auth_refresh(fetch, refresh)

    assert result == "ok"
    assert refresh_called is False


async def test_retry_with_auth_refresh_retries_after_auth_error() -> None:
    attempts = 0

    async def fetch() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RealtimeTrainsApiAuthError("auth")
        return "ok"

    async def refresh() -> bool:
        return True

    result = await retry_with_auth_refresh(fetch, refresh)

    assert result == "ok"
    assert attempts == 2


async def test_retry_with_auth_refresh_raises_when_refresh_fails() -> None:
    async def fetch() -> str:
        raise RealtimeTrainsApiAuthError("auth")

    async def refresh() -> bool:
        return False

    try:
        await retry_with_auth_refresh(fetch, refresh)
    except RealtimeTrainsApiAuthError:
        pass
    else:
        raise AssertionError("Expected RealtimeTrainsApiAuthError")


async def test_retry_with_auth_refresh_reports_retry_failure() -> None:
    retry_errors: list[Exception] = []

    async def refresh() -> bool:
        return True

    attempts = 0

    async def fetch_with_two_attempts() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RealtimeTrainsApiAuthError("auth")
        raise RealtimeTrainsApiError("boom")

    result = await retry_with_auth_refresh(
        fetch_with_two_attempts,
        refresh,
        retry_errors.append,
    )

    assert result is None
    assert attempts == 2
    assert len(retry_errors) == 1
    assert isinstance(retry_errors[0], RealtimeTrainsApiError)


def test_find_last_report_returns_latest_actual_report() -> None:
    locations = [
        {
            "location": {"shortCodes": ["AAA"], "description": "Alpha"},
            "temporalData": {
                "displayAs": "CALL",
                "departure": {"realtimeActual": "2026-04-01T10:00:00"},
            },
        },
        {
            "location": {"shortCodes": ["BBB"], "description": "Beta"},
            "temporalData": {
                "displayAs": "CALL",
                "arrival": {"realtimeActual": "2026-04-01T10:05:00"},
            },
        },
        {
            "location": {"shortCodes": ["CCC"], "description": "Gamma"},
            "temporalData": {
                "displayAs": "CALL",
                "pass": {"realtimeActual": "2026-04-01T10:10:00"},
            },
        },
    ]

    last_report_idx, last_report_type, last_report_station, last_report_time = find_last_report(
        locations,
        timezone.utc,
    )

    assert last_report_idx == 2
    assert last_report_type == "Pass"
    assert last_report_station == "CCC"
    assert last_report_time == datetime(2026, 4, 1, 10, 10, tzinfo=timezone.utc)


def test_subsequent_stop_start_index_steps_back_after_arrival() -> None:
    assert subsequent_stop_start_index(4, "Arrival") == 3
    assert subsequent_stop_start_index(4, "Departure") == 4
    assert subsequent_stop_start_index(-1, None) == 0


def test_collect_subsequent_stops_uses_expected_time_source_and_filters() -> None:
    locations = [
        {
            "location": {"shortCodes": ["AAA"], "description": "Alpha"},
            "temporalData": {
                "displayAs": "CALL",
                "arrival": {},
                "departure": {
                    "scheduleAdvertised": "2026-04-01T10:00:00",
                    "realtimeForecast": "2026-04-01T10:02:00",
                },
            },
        },
        {
            "location": {"shortCodes": ["BBB"], "description": "Beta"},
            "temporalData": {
                "displayAs": "CALL",
                "arrival": {
                    "scheduleAdvertised": "2026-04-01T10:10:00",
                    "realtimeActual": "2026-04-01T10:12:00",
                },
            },
        },
        {
            "location": {"shortCodes": ["CCC"], "description": "Gamma"},
            "temporalData": {
                "displayAs": "PASS",
                "arrival": {
                    "scheduleAdvertised": "2026-04-01T10:20:00",
                    "realtimeEstimate": "2026-04-01T10:21:00",
                },
            },
        },
        {
            "location": {"shortCodes": ["DDD"], "description": "Delta"},
            "temporalData": {
                "displayAs": "DEST",
                "arrival": {
                    "scheduleAdvertised": "2026-04-01T10:30:00",
                    "realtimeActual": "2026-04-01T10:31:00",
                },
            },
        },
    ]

    result = collect_subsequent_stops(locations, 0, timezone.utc)

    assert result == [
        {
            "stop": "AAA",
            "name": "Alpha",
            "scheduled": "01-04-2026 10:00",
            "estimated": "01-04-2026 10:02",
        },
        {
            "stop": "BBB",
            "name": "Beta",
            "scheduled": "01-04-2026 10:10",
            "estimated": "01-04-2026 10:12",
        },
        {
            "stop": "DDD",
            "name": "Delta",
            "scheduled": "01-04-2026 10:30",
            "estimated": "01-04-2026 10:31",
        },
    ]
