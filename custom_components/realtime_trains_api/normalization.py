"""Shared parsing and coercion helpers for the Realtime Trains integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Any


def split_csv(value: Any) -> list[str]:
    """Return a normalized list of values from CSV-like input."""
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.replace("\n", ",")
        return [item.strip() for item in cleaned.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        iterable = value
    else:
        iterable = [value]

    result: list[str] = []
    for item in iterable:
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def coerce_positive_int(value: Any) -> int:
    """Coerce a value to a non-negative integer."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def coerce_time_offset(value: Any, default: timedelta) -> timedelta:
    """Coerce a value to a non-negative timedelta in minutes."""
    if isinstance(value, timedelta):
        return value
    if isinstance(value, dict):
        try:
            return timedelta(**{key: int(val) for key, val in value.items()})
        except (TypeError, ValueError):
            return default
    if isinstance(value, (int, float)):
        minutes = int(value)
        if minutes < 0:
            minutes = 0
        return timedelta(minutes=minutes)
    if isinstance(value, str):
        try:
            minutes = int(float(value.strip()))
        except (TypeError, ValueError):
            return default
        return timedelta(minutes=max(0, minutes))
    return default


def coerce_scan_interval(value: Any, default: timedelta) -> timedelta:
    """Coerce a value to a scan interval timedelta."""
    if isinstance(value, timedelta):
        return value
    if isinstance(value, dict):
        try:
            return timedelta(**{key: int(val) for key, val in value.items()})
        except (TypeError, ValueError):
            return default
    if isinstance(value, (int, float)):
        seconds = int(value)
        if seconds <= 0:
            return default
        return timedelta(seconds=seconds)
    if isinstance(value, str):
        try:
            seconds = int(float(value))
        except (TypeError, ValueError):
            return default
        if seconds <= 0:
            return default
        return timedelta(seconds=seconds)
    return default


def coerce_scan_interval_seconds(value: Any, default: timedelta, minimum_seconds: int) -> int:
    """Coerce a value to integer scan interval seconds."""
    interval = coerce_scan_interval(value, default)
    return max(minimum_seconds, int(interval.total_seconds()))
