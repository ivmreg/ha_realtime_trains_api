from datetime import timedelta

from custom_components.realtime_trains_api.normalization import (
    coerce_positive_int,
    coerce_scan_interval,
    coerce_scan_interval_seconds,
    coerce_time_offset,
    split_csv,
)


def test_split_csv_normalizes_strings_and_iterables() -> None:
    assert split_csv("A, B\nC") == ["A", "B", "C"]
    assert split_csv(["A", " B ", 3]) == ["A", "B", "3"]
    assert split_csv(None) == []


def test_coerce_positive_int_clamps_invalid_values() -> None:
    assert coerce_positive_int("4") == 4
    assert coerce_positive_int(-2) == 0
    assert coerce_positive_int("bad") == 0


def test_coerce_time_offset_handles_multiple_input_types() -> None:
    default = timedelta(minutes=5)

    assert coerce_time_offset(15, default) == timedelta(minutes=15)
    assert coerce_time_offset({"hours": 1}, default) == timedelta(hours=1)
    assert coerce_time_offset("20", default) == timedelta(minutes=20)
    assert coerce_time_offset("bad", default) == default


def test_coerce_scan_interval_handles_multiple_input_types() -> None:
    default = timedelta(minutes=1)

    assert coerce_scan_interval(90, default) == timedelta(seconds=90)
    assert coerce_scan_interval({"minutes": 2}, default) == timedelta(minutes=2)
    assert coerce_scan_interval("120", default) == timedelta(seconds=120)
    assert coerce_scan_interval("bad", default) == default


def test_coerce_scan_interval_seconds_clamps_to_minimum() -> None:
    default = timedelta(minutes=1)

    assert coerce_scan_interval_seconds(10, default, 30) == 30
    assert coerce_scan_interval_seconds({"seconds": 90}, default, 30) == 90
