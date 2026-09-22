"""Pure helpers for realtime_trains_api sensor formatting and parsing."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any, TypeVar, cast

from .rtt_api import RealtimeTrainsApiAuthError, RealtimeTrainsApiError

T = TypeVar("T")
SUBSEQUENT_STOP_DISPLAY_AS = frozenset({"CALL", "DEST"})


def build_query_key(
    origin: str,
    destination: str | None,
    platforms_of_interest: Iterable[str],
    time_offset: timedelta,
) -> str:
    """Build the key linking a configured query to its coordinator data."""
    platforms = sorted(platforms_of_interest)
    platforms_str = "_".join(platforms) if platforms else "all"
    dest_str = destination if destination else "all"
    offset_str = f"{int(time_offset.total_seconds())}" if time_offset.total_seconds() > 0 else "0"
    return f"{origin}_{dest_str}_{platforms_str}_{offset_str}"


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
    """Attach a timezone to a naive datetime (pytz-style localize supported for back-compat)."""
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
    if not isinstance(location, Mapping):
        return None
    crs = location.get("crs")
    if crs is not None and str(crs).strip():
        return str(crs).strip()
    short_codes = location.get("shortCodes")
    if isinstance(short_codes, (list, tuple)) and len(short_codes) > 0:
        val = short_codes[0]
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


def _primary_long_code(location: Mapping[str, Any]) -> str | None:
    if not isinstance(location, Mapping):
        return None
    tiploc = location.get("tiploc")
    if tiploc is not None and str(tiploc).strip():
        return str(tiploc).strip()
    long_codes = location.get("longCodes")
    if isinstance(long_codes, (list, tuple)) and len(long_codes) > 0:
        val = long_codes[0]
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


def calculate_service_status(
    scheduled_dt: datetime,
    estimated_dt: datetime | None,
    is_cancelled: bool,
) -> tuple[int | None, str, str, str, str | None]:
    """Calculate (delay_minutes, status, status_class, status_label, offset_label)."""
    if is_cancelled:
        return None, "cancelled", "cancelled", "Cancelled", None

    if estimated_dt is None:
        return 0, "on_time", "on-time", "On Time", None

    delay_seconds = (
        estimated_dt.replace(second=0, microsecond=0)
        - scheduled_dt.replace(second=0, microsecond=0)
    ).total_seconds()
    delay_minutes = int(round(delay_seconds / 60.0))
    est_time = estimated_dt.strftime("%H:%M")

    if abs(delay_minutes) <= 1:
        return delay_minutes, "on_time", "on-time", "On Time", None
    if delay_minutes < 0:
        return delay_minutes, "early", "early", f"Early {est_time}", f"{delay_minutes}m"
    return delay_minutes, "delayed", "delayed", f"Exp {est_time}", f"+{delay_minutes}m"


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


def build_calling_points(
    locations: Sequence[Mapping[str, Any]],
    start_index: int,
    last_report_station: str | None,
    last_report_type: str | None,
    last_report_time: datetime | None,
    fallback_tz: Any,
) -> list[dict[str, Any]]:
    """Build the display-ready list of calling points with status and tracking."""
    calling_points: list[dict[str, Any]] = []

    for idx, stop in enumerate(locations):
        if idx < start_index:
            continue

        temporal_data = stop.get("temporalData", {})
        display_as = temporal_data.get("displayAs") or ""
        if display_as not in SUBSEQUENT_STOP_DISPLAY_AS:
            continue

        location = stop.get("location", {})
        time_source = _select_time_source(temporal_data)

        scheduled_str = time_source.get("scheduleAdvertised") or time_source.get("scheduleInternal")
        if not scheduled_str:
            continue

        estimated_str = (
            time_source.get("realtimeActual")
            or time_source.get("realtimeForecast")
            or time_source.get("realtimeEstimate")
        )

        scheduled_dt = parse_rtt_datetime(scheduled_str, fallback_tz)
        estimated_dt = parse_rtt_datetime(estimated_str, fallback_tz) if estimated_str else scheduled_dt

        display_as_clean = str(display_as).lower()
        status_clean = str(temporal_data.get("status") or time_source.get("status") or "").lower()
        is_stop_cancelled = bool(
            "cancel" in display_as_clean
            or "cancel" in status_clean
            or time_source.get("isCancelled")
            or temporal_data.get("isCancelled")
        )

        est_time_str = estimated_dt.strftime("%H:%M")
        sched_time_str = scheduled_dt.strftime("%H:%M")

        if is_stop_cancelled:
            stop_status = "cancelled"
            stop_status_class = "cancelled"
            stop_status_label = "Cancelled"
            stop_delay_mins = None
        else:
            stop_delay_mins = int(round((estimated_dt - scheduled_dt).total_seconds() / 60.0))
            if abs(stop_delay_mins) <= 1:
                stop_status = "on_time"
                stop_status_class = "on-time"
                stop_status_label = "On time"
            elif stop_delay_mins < 0:
                stop_status = "early"
                stop_status_class = "early"
                stop_status_label = f"Early {est_time_str}"
            else:
                stop_status = "delayed"
                stop_status_class = "delayed"
                stop_status_label = f"Exp {est_time_str}"

        calling_points.append(
            {
                "station_name": str(location.get("description") or ""),
                "crs": _primary_short_code(location),
                "tiploc": _primary_long_code(location),
                "scheduled": scheduled_dt.isoformat(),
                "estimated": estimated_dt.isoformat() if estimated_str else None,
                "time": sched_time_str,
                "delay_minutes": stop_delay_mins,
                "status": stop_status,
                "status_class": stop_status_class,
                "status_label": stop_status_label,
                "is_passed": False,
                "is_current": False,
                "is_between_previous": False,
                "_timestamp": scheduled_dt.timestamp(),
            }
        )

    # Sort calling points chronologically
    calling_points.sort(key=lambda p: p["_timestamp"])

    # Apply real-time position tracking if report data is available
    exact_match_idx = -1
    if last_report_station and last_report_type:
        for i, point in enumerate(calling_points):
            if (
                point["crs"] == last_report_station
                or point["tiploc"] == last_report_station
                or point["station_name"] == last_report_station
            ):
                exact_match_idx = i
                break

        if exact_match_idx != -1:
            for i in range(exact_match_idx):
                calling_points[i]["is_passed"] = True
            if last_report_type == "Arrival":
                calling_points[exact_match_idx]["is_current"] = True
                calling_points[exact_match_idx]["is_passed"] = False
            else:
                calling_points[exact_match_idx]["is_passed"] = True
                if exact_match_idx + 1 < len(calling_points):
                    calling_points[exact_match_idx + 1]["is_between_previous"] = True
        elif last_report_time is not None:
            last_passed_idx = -1
            report_ts = last_report_time.timestamp()
            for i, point in enumerate(calling_points):
                if point["_timestamp"] <= report_ts:
                    point["is_passed"] = True
                    last_passed_idx = i
                else:
                    break
            if last_passed_idx != -1 and last_passed_idx + 1 < len(calling_points):
                calling_points[last_passed_idx + 1]["is_between_previous"] = True
            elif last_passed_idx == -1 and len(calling_points) > 0:
                calling_points[0]["is_between_previous"] = True

        if (
            len(calling_points) > 0
            and calling_points[0]["is_between_previous"]
            and exact_match_idx == -1
            and last_report_station
            and last_report_time is not None
        ):
            report_time_str = last_report_time.strftime("%H:%M")
            report_iso = last_report_time.isoformat()
            calling_points.insert(
                0,
                {
                    "station_name": last_report_station,
                    "crs": last_report_station,
                    "tiploc": None,
                    "scheduled": report_iso,
                    "estimated": report_iso,
                    "time": report_time_str,
                    "delay_minutes": 0,
                    "status": "on_time",
                    "status_class": "on-time",
                    "status_label": "On time",
                    "is_passed": True,
                    "is_current": False,
                    "is_between_previous": False,
                },
            )

    # Clean up temporary sort key
    for point in calling_points:
        point.pop("_timestamp", None)

    return calling_points


def evaluate_pinned_disruption(
    train: Mapping[str, Any] | None,
    threshold_minutes: int,
) -> tuple[bool, str | None]:
    """Judge whether a pinned train is disrupted; returns (disrupted, reason)."""
    if train is None:
        return False, None

    if train.get("is_cancelled") or "cancel" in str(train.get("status") or "").lower():
        return True, "Cancelled"

    delay_minutes = train.get("delay_minutes")
    if delay_minutes is not None:
        try:
            delay_int = int(delay_minutes)
            if delay_int >= threshold_minutes:
                return True, f"Delayed {delay_int} min"
        except (TypeError, ValueError):
            pass

    return False, None


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
        except RealtimeTrainsApiAuthError:
            raise
        except RealtimeTrainsApiError as err:
            if on_retry_error is not None:
                on_retry_error(err)
            return None
