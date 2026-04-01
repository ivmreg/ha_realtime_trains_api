from datetime import datetime, timedelta, timezone

from custom_components.realtime_trains_api.sensor_helpers import (
    build_default_sensor_name,
    parse_rtt_datetime,
    retry_with_auth_refresh,
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
