import pytest
from custom_components.realtime_trains_api.sensor import _to_colonseparatedtime

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
