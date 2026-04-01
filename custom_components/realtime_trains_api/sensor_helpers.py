"""Pure helpers for realtime_trains_api sensor formatting and parsing."""

from __future__ import annotations

from datetime import datetime, timedelta
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, cast

from .rtt_api import RealtimeTrainsApiAuthError, RealtimeTrainsApiError

T = TypeVar("T")


def build_default_sensor_name(
    journey_start: str,
    journey_end: str | None,
    timeoffset: timedelta,
    platforms_of_interest: list[str],
) -> str:
    """Build the default sensor name used when a custom name is not provided."""
    has_offset = timeoffset.total_seconds() > 0
    platform_str = f" platform {', '.join(sorted(platforms_of_interest))}" if platforms_of_interest else ""
    offset_str = f" ({timeoffset})" if has_offset else ""

    if journey_end:
        return f"Next train from {journey_start}{platform_str} to {journey_end}{offset_str}"
    return f"Trains from {journey_start}{platform_str}{offset_str}"


def _localize_datetime(value: datetime, fallback_tz: Any) -> datetime:
    """Attach a timezone to a naive datetime, using pytz localize when available."""
    if value.tzinfo is not None:
        return value

    localize = getattr(fallback_tz, "localize", None)
    if callable(localize):
        return cast(datetime, localize(value))

    return value.replace(tzinfo=fallback_tz)


def parse_rtt_datetime(value: str, fallback_tz: Any) -> datetime:
    """Parse an RTT ISO datetime string and apply the fallback timezone if needed."""
    parsed = datetime.fromisoformat(value)
    return _localize_datetime(parsed, fallback_tz)


async def retry_with_auth_refresh(
    fetch: Callable[[], Awaitable[T]],
    refresh: Callable[[], Awaitable[bool]],
    on_retry_error: Callable[[RealtimeTrainsApiError], None] | None = None,
) -> T | None:
    """Retry a fetch once after refreshing auth."""
    try:
        return await fetch()
    except RealtimeTrainsApiAuthError:
        if not await refresh():
            raise

        try:
            return await fetch()
        except RealtimeTrainsApiError as err:
            if on_retry_error is not None:
                on_retry_error(err)
            return None
