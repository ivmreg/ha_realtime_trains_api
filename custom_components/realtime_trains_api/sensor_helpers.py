"""Pure helpers for realtime_trains_api sensor formatting and parsing."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any, TypeVar, cast

from .rtt_api import RealtimeTrainsApiAuthError, RealtimeTrainsApiError

T = TypeVar("T")

RTT_TIME_FORMAT = "%d-%m-%Y %H:%M"
SUBSEQUENT_STOP_DISPLAY_AS = frozenset({"CALL", "DEST"})


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


def _primary_short_code(location: Mapping[str, Any]) -> str | None:
    short_codes = location.get("shortCodes")
    if not short_codes:
        return None
    return cast(str | None, short_codes[0])


def _select_time_source(temporal_data: Mapping[str, Any]) -> Mapping[str, Any]:
    arrival_data = temporal_data.get("arrival", {})
    return arrival_data if arrival_data else temporal_data.get("departure", {})


def find_last_report(
    locations: Sequence[Mapping[str, Any]],
    fallback_tz: Any,
) -> tuple[int, str | None, str | None, datetime | None]:
    """Return the last actual report found in a service detail payload."""
    last_report_idx = -1
    last_report_type = None
    last_report_station = None
    last_report_time = None

    for idx, stop in enumerate(locations):
        temporal_data = stop.get("temporalData", {})
        pass_actual = temporal_data.get("pass", {}).get("realtimeActual")
        departure_actual = temporal_data.get("departure", {}).get("realtimeActual")
        arrival_actual = temporal_data.get("arrival", {}).get("realtimeActual")

        if not (pass_actual or departure_actual or arrival_actual):
            continue

        last_report_idx = idx
        last_report_time = parse_rtt_datetime(pass_actual or departure_actual or arrival_actual, fallback_tz)
        last_report_station = _primary_short_code(stop.get("location", {}))

        if pass_actual:
            last_report_type = "Pass"
        elif departure_actual:
            last_report_type = "Departure"
        else:
            last_report_type = "Arrival"

    return last_report_idx, last_report_type, last_report_station, last_report_time


def subsequent_stop_start_index(last_report_idx: int, last_report_type: str | None) -> int:
    """Return the index where subsequent stops should start."""
    if last_report_idx == -1:
        return 0
    if last_report_type == "Arrival":
        return max(0, last_report_idx - 1)
    return last_report_idx


def build_subsequent_stop(stop: Mapping[str, Any], fallback_tz: Any) -> dict[str, Any] | None:
    """Build a single subsequent stop entry from a service location."""
    temporal_data = stop.get("temporalData", {})
    display_as = temporal_data.get("displayAs") or ""
    if display_as not in SUBSEQUENT_STOP_DISPLAY_AS:
        return None

    location = stop.get("location", {})
    time_source = _select_time_source(temporal_data)

    scheduled_str = time_source.get("scheduleAdvertised") or time_source.get("scheduleInternal")
    if not scheduled_str:
        return None

    estimated_str = (
        time_source.get("realtimeActual")
        or time_source.get("realtimeForecast")
        or time_source.get("realtimeEstimate")
    )

    scheduled_dt = parse_rtt_datetime(scheduled_str, fallback_tz)
    estimated_dt = parse_rtt_datetime(estimated_str, fallback_tz) if estimated_str else scheduled_dt

    return {
        "stop": _primary_short_code(location),
        "name": location.get("description", ""),
        "scheduled": scheduled_dt.strftime(RTT_TIME_FORMAT),
        "estimated": estimated_dt.strftime(RTT_TIME_FORMAT),
    }


def collect_subsequent_stops(
    locations: Sequence[Mapping[str, Any]],
    start_index: int,
    fallback_tz: Any,
) -> list[dict[str, Any]]:
    """Collect subsequent stop entries starting from a given index."""
    subsequent_stops: list[dict[str, Any]] = []

    for idx, stop in enumerate(locations):
        if idx < start_index:
            continue

        stop_data = build_subsequent_stop(stop, fallback_tz)
        if stop_data is not None:
            subsequent_stops.append(stop_data)

    return subsequent_stops


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
