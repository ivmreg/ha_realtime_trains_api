"""Business logic helpers for api.rtt.io responses."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

import pytz

TIMEZONE = pytz.timezone("Europe/London")
STRFFORMAT = "%d-%m-%Y %H:%M"

REASON_MISSING_ORIGIN = "missing_origin"
REASON_DESTINATION_NOT_REACHED = "destination_not_reached"
REASON_WRONG_DIRECTION = "wrong_direction"


class JourneyParseResult(NamedTuple):
    """Represents the outcome of parsing a journey detail response."""

    success: bool
    data: Optional[Dict[str, Any]]
    reason: Optional[str]


def to_colon_separated_time(time_str: Optional[str]) -> Optional[str]:
    """Return an HH:MM string from an HHMM string or None if invalid."""
    if not time_str or len(time_str) < 4:
        return None
    return f"{time_str[:2]}:{time_str[2:4]}"


def timestamp_from_reference(reference: datetime, hhmm: Optional[str]) -> Optional[datetime]:
    """Build a timezone-aware datetime using a reference date and an HH:MM string."""
    if hhmm is None:
        return None
    try:
        hour = int(hhmm[:2])
        minute = int(hhmm[3:5])
    except (ValueError, TypeError):
        return None

    candidate = reference.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate < reference:
        candidate += timedelta(days=1)
    return candidate


def delta_secs(later: datetime, earlier: datetime) -> float:
    """Return the difference between two datetimes in seconds."""
    return (later - earlier).total_seconds()


def parse_departure(
    departure: Dict[str, Any],
    now: datetime,
    time_offset: timedelta,
) -> Optional[Tuple[Dict[str, Any], datetime, datetime]]:
    """Parse a single departure entry into a train dictionary.

    Returns None when the service should be skipped.
    """
    if not departure.get("isPassenger", False):
        return None

    location_detail = departure.get("locationDetail") or {}
    run_date_str = departure.get("runDate")
    scheduled_raw = to_colon_separated_time(location_detail.get("gbttBookedDeparture"))
    realtime_raw = to_colon_separated_time(
        location_detail.get("realtimeDeparture")
        or location_detail.get("gbttBookedDeparture")
    )

    if not run_date_str or scheduled_raw is None or realtime_raw is None:
        return None

    departure_date = TIMEZONE.localize(datetime.fromisoformat(run_date_str))
    scheduled_ts = timestamp_from_reference(departure_date, scheduled_raw)
    estimated_ts = timestamp_from_reference(departure_date, realtime_raw)
    if scheduled_ts is None or estimated_ts is None:
        return None

    if delta_secs(estimated_ts, now) < time_offset.total_seconds():
        return None

    origin_name = None
    destination_name = None
    origins = location_detail.get("origin") or []
    destinations = location_detail.get("destination") or []

    if origins:
        origin_name = origins[0].get("description")
    if destinations:
        destination_name = destinations[0].get("description")

    train = {
        "origin_name": origin_name,
        "destination_name": destination_name,
        "service_uid": departure.get("serviceUid"),
        "scheduled": scheduled_ts.strftime(STRFFORMAT),
        "estimated": estimated_ts.strftime(STRFFORMAT),
        "delay": int(delta_secs(estimated_ts, scheduled_ts) // 60),
        "minutes": int(delta_secs(estimated_ts, now) // 60),
        "platform": location_detail.get("platform"),
        "operator_name": departure.get("atocName"),
    }

    return train, scheduled_ts, estimated_ts


def _first_valid_time(stop: Dict[str, Any], keys: List[str]) -> Optional[str]:
    for key in keys:
        candidate = stop.get(key)
        result = to_colon_separated_time(candidate)
        if result is not None:
            return result
    return None


def parse_service_detail(
    service_data: Dict[str, Any],
    journey_start: str,
    journey_end: str,
    scheduled_departure: datetime,
    estimated_departure: datetime,
    stops_of_interest: List[str],
) -> JourneyParseResult:
    """Validate and enrich train information using a journey detail response."""
    locations = service_data.get("locations") or []

    boarding_idx = None
    for idx, stop in enumerate(locations):
        if _code_matches(stop, journey_start):
            boarding_idx = idx
            break

    if boarding_idx is None:
        return JourneyParseResult(False, None, REASON_MISSING_ORIGIN)

    # If the destination has already been visited earlier in the journey, the
    # train is moving away from the requested destination (e.g. circular
    # services that originate and finish at the same station). Skip these to
    # avoid presenting the wrong direction.
    for previous_stop in locations[:boarding_idx]:
        if _code_matches(previous_stop, journey_end):
            return JourneyParseResult(False, None, REASON_WRONG_DIRECTION)

    stops_info: List[Dict[str, Any]] = []
    stop_count = -1

    for idx, stop in enumerate(locations):
        if idx <= boarding_idx:
            continue

        crs = stop.get("crs")
        if _code_matches(stop, journey_end):
            scheduled_arrival_raw = _first_valid_time(stop, [
                "gbttBookedArrival",
                "gbttBookedDeparture",
            ])
            estimated_arrival_raw = _first_valid_time(stop, [
                "realtimeArrival",
                "realtimeDeparture",
                "gbttBookedArrival",
                "gbttBookedDeparture",
            ])

            scheduled_arrival = timestamp_from_reference(
                scheduled_departure, scheduled_arrival_raw
            )
            estimated_arrival = timestamp_from_reference(
                scheduled_departure, estimated_arrival_raw
            )

            if scheduled_arrival is None or estimated_arrival is None:
                return JourneyParseResult(False, None, REASON_DESTINATION_NOT_REACHED)

            status = "OK"
            display_as = stop.get("displayAs", "")
            if isinstance(display_as, str) and "CANCELLED" in display_as:
                status = "Cancelled"
            elif estimated_arrival > scheduled_arrival:
                status = "Delayed"

            journey_data = {
                "stops_of_interest": stops_info,
                "scheduled_arrival": scheduled_arrival.strftime(STRFFORMAT),
                "estimate_arrival": estimated_arrival.strftime(STRFFORMAT),
                "arrival_delay": int(delta_secs(estimated_arrival, scheduled_arrival) // 60),
                "journey_time_mins": int(delta_secs(estimated_arrival, estimated_departure) // 60),
                "stops": stop_count,
                "status": status,
            }
            return JourneyParseResult(True, journey_data, None)

        if crs in stops_of_interest and stop.get("isPublicCall", False):
            scheduled_stop_raw = _first_valid_time(stop, [
                "gbttBookedArrival",
                "gbttBookedDeparture",
            ])
            estimated_stop_raw = _first_valid_time(stop, [
                "realtimeArrival",
                "realtimeDeparture",
                "gbttBookedArrival",
                "gbttBookedDeparture",
            ])
            scheduled_stop = timestamp_from_reference(
                scheduled_departure, scheduled_stop_raw
            )
            estimated_stop = timestamp_from_reference(
                scheduled_departure, estimated_stop_raw
            )

            if scheduled_stop and estimated_stop:
                stops_info.append(
                    {
                        "stop": crs,
                        "name": stop.get("description"),
                        "scheduled_stop": scheduled_stop.strftime(STRFFORMAT),
                        "estimate_stop": estimated_stop.strftime(STRFFORMAT),
                        "stop_delay": int(delta_secs(estimated_stop, scheduled_stop) // 60),
                        "journey_time_mins": int(delta_secs(estimated_stop, estimated_departure) // 60),
                        "stops": stop_count,
                    }
                )

        stop_count += 1

    return JourneyParseResult(False, None, REASON_DESTINATION_NOT_REACHED)


def _code_matches(stop: Dict[str, Any], code: str) -> bool:
    """Return True if the stop's CRS or TIPLOC matches the provided code."""
    if not code:
        return False
    upper_code = code.upper()
    crs = stop.get("crs")
    if isinstance(crs, str) and crs.upper() == upper_code:
        return True
    tiploc = stop.get("tiploc")
    if isinstance(tiploc, str) and tiploc.upper() == upper_code:
        return True
    return False
