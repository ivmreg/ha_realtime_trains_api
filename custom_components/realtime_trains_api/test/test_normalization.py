from datetime import timedelta

from custom_components.realtime_trains_api.normalization import (
    coerce_positive_int,
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


from datetime import time
import pytest
from custom_components.realtime_trains_api.normalization import parse_time_windows

def test_parse_time_windows_valid():
    windows_str = "07:00-09:30, 16:30-19:00"
    windows = parse_time_windows(windows_str)
    assert len(windows) == 2
    assert windows[0] == (time(7, 0), time(9, 30))
    assert windows[1] == (time(16, 30), time(19, 0))

def test_parse_time_windows_empty():
    assert parse_time_windows("") == []
    assert parse_time_windows("  ") == []

def test_parse_time_windows_invalid():
    with pytest.raises(ValueError):
        parse_time_windows("07:00-09:30, invalid")
    with pytest.raises(ValueError):
        parse_time_windows("09:30-07:00") # end before start
