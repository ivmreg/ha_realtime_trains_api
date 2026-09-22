from datetime import datetime, timedelta, timezone

from custom_components.realtime_trains_api.sensor_helpers import (
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


async def test_retry_with_auth_refresh_reraises_auth_error_on_retry() -> None:
    """Auth errors after a successful refresh+retry must propagate, not be swallowed."""
    async def refresh() -> bool:
        return True

    attempts = 0

    async def fetch() -> str:
        nonlocal attempts
        attempts += 1
        raise RealtimeTrainsApiAuthError("still bad")

    try:
        await retry_with_auth_refresh(fetch, refresh)
    except RealtimeTrainsApiAuthError:
        pass
    else:
        raise AssertionError("Expected RealtimeTrainsApiAuthError to be re-raised")

    assert attempts == 2


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


def test_query_scheme_and_normalization_origin_only():
    from custom_components.realtime_trains_api.sensor import _QUERY_SCHEME, _normalize_query

    # Origin-only query validates successfully without requiring destination
    raw = {"origin": "BKH"}
    validated = _QUERY_SCHEME(raw)
    assert "destination" not in validated

    normalized = _normalize_query({"origin": "BKH"})
    assert normalized["origin"] == "BKH"
    assert normalized["destination"] is None


def test_sensor_unrecorded_attributes_and_empty_next_trains():
    from unittest.mock import MagicMock
    from datetime import timedelta
    from custom_components.realtime_trains_api.sensor import (
        RealtimeTrainLiveTrainTimeSensor,
        ATTR_NEXT_TRAINS,
        ATTR_PINNED_TRAIN,
        ATTR_NEXT_UPDATE_AT,
        ATTR_LAST_SUCCESSFUL_UPDATE,
        ATTR_DATA_STALE,
        ATTR_ERROR,
    )

    expected_unrecorded = frozenset(
        {
            ATTR_NEXT_TRAINS,
            ATTR_PINNED_TRAIN,
            ATTR_NEXT_UPDATE_AT,
            ATTR_LAST_SUCCESSFUL_UPDATE,
        }
    )
    assert RealtimeTrainLiveTrainTimeSensor._unrecorded_attributes == expected_unrecorded
    assert ATTR_DATA_STALE not in RealtimeTrainLiveTrainTimeSensor._unrecorded_attributes
    assert ATTR_ERROR not in RealtimeTrainLiveTrainTimeSensor._unrecorded_attributes

    coordinator = MagicMock()
    coordinator.data = {
        "BKH_all_all_0_0": {
            "journey_start": "BKH",
            "journey_end": None,
            "next_trains": [],
            "error": None,
            "pinned_train": None,
        }
    }
    coordinator.current_polling_interval = 60
    coordinator.last_update_time = None
    coordinator.data_stale = False
    coordinator.last_successful_update = None

    sensor = RealtimeTrainLiveTrainTimeSensor(
        coordinator=coordinator,
        sensor_name=None,
        query_key="BKH_all_all_0_0",
        journey_start="BKH",
        journey_end=None,
        timeoffset=timedelta(),
        platforms_of_interest=[],
        entry_id="test_entry",
        query_index=0,
    )
    attrs = sensor.extra_state_attributes
    assert ATTR_NEXT_TRAINS in attrs
    assert attrs["contract_version"] == 2
    assert "schema_version" not in attrs


def test_calculate_service_status_cases() -> None:
    from custom_components.realtime_trains_api.sensor_helpers import calculate_service_status

    sched = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)

    # Cancelled
    assert calculate_service_status(sched, sched, True) == (
        None, "cancelled", "cancelled", "Cancelled", None
    )

    # Missing estimate defaults to on_time
    assert calculate_service_status(sched, None, False) == (
        0, "on_time", "on-time", "On Time", None
    )

    # Exact on-time
    assert calculate_service_status(sched, sched, False) == (
        0, "on_time", "on-time", "On Time", None
    )

    # Within 1 min tolerance (+1 min is on time)
    est_plus_1 = datetime(2026, 4, 1, 10, 1, tzinfo=timezone.utc)
    assert calculate_service_status(sched, est_plus_1, False) == (
        1, "on_time", "on-time", "On Time", None
    )

    # Within 1 min tolerance (-1 min is on time)
    est_minus_1 = datetime(2026, 4, 1, 9, 59, tzinfo=timezone.utc)
    assert calculate_service_status(sched, est_minus_1, False) == (
        -1, "on_time", "on-time", "On Time", None
    )

    # Delayed 5 mins
    est_plus_5 = datetime(2026, 4, 1, 10, 5, tzinfo=timezone.utc)
    assert calculate_service_status(sched, est_plus_5, False) == (
        5, "delayed", "delayed", "Exp 10:05", "+5m"
    )

    # Early 4 mins
    est_minus_4 = datetime(2026, 4, 1, 9, 56, tzinfo=timezone.utc)
    assert calculate_service_status(sched, est_minus_4, False) == (
        -4, "early", "early", "Early 09:56", "-4m"
    )


def test_build_calling_points_full_behavior() -> None:
    from custom_components.realtime_trains_api.sensor_helpers import build_calling_points

    locations = [
        {
            "location": {"shortCodes": ["LBG"], "longCodes": ["LONBDG"], "description": "London Bridge"},
            "temporalData": {
                "displayAs": "CALL",
                "arrival": {"scheduleAdvertised": "2026-04-01T10:08:00", "realtimeActual": "2026-04-01T10:08:00"},
            },
        },
        {
            "location": {"shortCodes": ["LEW"], "longCodes": ["LEWSHM"], "description": "Lewisham"},
            "temporalData": {
                "displayAs": "CALL",
                "arrival": {"scheduleAdvertised": "2026-04-01T10:15:00", "realtimeForecast": "2026-04-01T10:20:00"},
            },
        },
        {
            "location": {"shortCodes": ["BKH"], "longCodes": ["BLKHTH"], "description": "Blackheath"},
            "temporalData": {
                "displayAs": "DEST",
                "arrival": {"scheduleAdvertised": "2026-04-01T10:25:00", "isCancelled": True},
            },
        },
    ]

    # Test when train just departed LBG
    points = build_calling_points(
        locations=locations,
        start_index=0,
        last_report_station="LBG",
        last_report_type="Departure",
        last_report_time=datetime(2026, 4, 1, 10, 9, tzinfo=timezone.utc),
        fallback_tz=timezone.utc,
    )

    assert len(points) == 3
    # LBG
    assert points[0]["station_name"] == "London Bridge"
    assert points[0]["crs"] == "LBG"
    assert points[0]["tiploc"] == "LONBDG"
    assert points[0]["time"] == "10:08"
    assert points[0]["is_passed"] is True
    assert points[0]["is_current"] is False

    # LEW
    assert points[1]["station_name"] == "Lewisham"
    assert points[1]["status"] == "delayed"
    assert points[1]["status_class"] == "delayed"
    assert points[1]["status_label"] == "Exp 10:20"
    assert points[1]["delay_minutes"] == 5
    assert points[1]["is_between_previous"] is True
    assert points[1]["is_passed"] is False

    # BKH (cancelled)
    assert points[2]["station_name"] == "Blackheath"
    assert points[2]["status"] == "cancelled"
    assert points[2]["status_class"] == "cancelled"
    assert points[2]["status_label"] == "Cancelled"
    assert points[2]["delay_minutes"] is None


def test_build_calling_points_injects_unlisted_previous_station() -> None:
    from custom_components.realtime_trains_api.sensor_helpers import build_calling_points

    locations = [
        {
            "location": {"shortCodes": ["LEW"], "description": "Lewisham"},
            "temporalData": {
                "displayAs": "CALL",
                "arrival": {"scheduleAdvertised": "2026-04-01T10:15:00"},
            },
        },
    ]

    # Train departed LBG (which is not in the query's locations list)
    points = build_calling_points(
        locations=locations,
        start_index=0,
        last_report_station="LBG",
        last_report_type="Departure",
        last_report_time=datetime(2026, 4, 1, 10, 9, tzinfo=timezone.utc),
        fallback_tz=timezone.utc,
    )

    assert len(points) == 2
    assert points[0]["crs"] == "LBG"
    assert points[0]["station_name"] == "LBG"
    assert points[0]["is_passed"] is True
    assert isinstance(points[0]["scheduled"], str)
    assert points[0]["scheduled"] == "2026-04-01T10:09:00+00:00"
    assert points[0]["estimated"] == "2026-04-01T10:09:00+00:00"
    assert points[0]["time"] == "10:09"
    assert points[0]["status_label"] == "On time"
    assert points[1]["crs"] == "LEW"
    assert points[1]["is_between_previous"] is True


def test_build_calling_points_does_not_inject_without_last_report_time() -> None:
    from custom_components.realtime_trains_api.sensor_helpers import build_calling_points

    locations = [
        {
            "location": {"shortCodes": ["LEW"], "description": "Lewisham"},
            "temporalData": {
                "displayAs": "CALL",
                "arrival": {"scheduleAdvertised": "2026-04-01T10:15:00"},
            },
        },
    ]

    points = build_calling_points(
        locations=locations,
        start_index=0,
        last_report_station="LBG",
        last_report_type="Departure",
        last_report_time=None,
        fallback_tz=timezone.utc,
    )

    assert len(points) == 1
    assert points[0]["crs"] == "LEW"


def test_primary_codes_type_normalization() -> None:
    from custom_components.realtime_trains_api.sensor_helpers import (
        _primary_short_code,
        _primary_long_code,
    )

    # Integer values in crs or tiploc coerced to string
    assert _primary_short_code({"crs": 123}) == "123"
    assert _primary_long_code({"tiploc": 456}) == "456"

    # Integer values in shortCodes or longCodes array coerced to string
    assert _primary_short_code({"shortCodes": [789]}) == "789"
    assert _primary_long_code({"longCodes": [987]}) == "987"

    # None or whitespace
    assert _primary_short_code({}) is None
    assert _primary_long_code({}) is None
    assert _primary_short_code({"shortCodes": []}) is None
    assert _primary_long_code({"longCodes": [None]}) is None
    assert _primary_short_code({"crs": "  "}) is None
    assert _primary_long_code({"tiploc": ""}) is None
