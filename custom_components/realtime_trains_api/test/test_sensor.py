from datetime import datetime

from custom_components.realtime_trains_api.sensor import (
    TIMEZONE,
    _parse_times,
    _query_unique_key,
    _to_colonseparatedtime,
)

def test_to_colonseparatedtime_valid():
    assert _to_colonseparatedtime("1200") == "12:00"
    assert _to_colonseparatedtime("0930") == "09:30"
    assert _to_colonseparatedtime("2359") == "23:59"

def test_to_colonseparatedtime_none():
    assert _to_colonseparatedtime(None) is None

def test_to_colonseparatedtime_empty():
    assert _to_colonseparatedtime("") is None
    assert _to_colonseparatedtime("   ") is None

def test_to_colonseparatedtime_short():
    assert _to_colonseparatedtime("123") is None
    assert _to_colonseparatedtime("1") is None

def test_to_colonseparatedtime_whitespace():
    assert _to_colonseparatedtime(" 1200 ") == "12:00"
    assert _to_colonseparatedtime("\t1200\n") == "12:00"

def test_to_colonseparatedtime_longer_than_4():
    # Inputs longer than 4 characters should be treated as invalid
    # rather than silently truncated.
    assert _to_colonseparatedtime("12005") is None


def test_parse_times_falls_back_to_arrival_when_departure_missing():
    run_date = TIMEZONE.localize(datetime(2026, 3, 18, 0, 0))

    scheduled, estimated = _parse_times("10:05", "10:07", run_date)

    assert scheduled is not None
    assert estimated is not None
    assert scheduled.strftime("%Y-%m-%d %H:%M") == "2026-03-18 10:05"
    assert estimated.strftime("%Y-%m-%d %H:%M") == "2026-03-18 10:07"


def test_parse_times_rolls_to_next_day_when_baseline_near_midnight():
    baseline = TIMEZONE.localize(datetime(2026, 3, 18, 23, 58))

    scheduled, estimated = _parse_times("00:05", "00:07", baseline)

    assert scheduled is not None
    assert estimated is not None
    assert scheduled.strftime("%Y-%m-%d %H:%M") == "2026-03-19 00:05"
    assert estimated.strftime("%Y-%m-%d %H:%M") == "2026-03-19 00:07"


def test_query_unique_key_is_stable_for_same_query_data():
    query_a = {"origin": "KGX", "sensor_name": None, "journey_data_for_next_X_trains": 0}
    query_b = {"journey_data_for_next_X_trains": 0, "sensor_name": None, "origin": "KGX"}

    assert _query_unique_key(query_a) == _query_unique_key(query_b)
