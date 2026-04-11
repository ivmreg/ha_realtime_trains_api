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

from datetime import datetime, time

def parse_time_windows(windows_str: str) -> list[tuple[time, time]]:
    """Parse a comma-separated string of time windows (e.g., '07:00-09:30, 16:30-19:00')."""
    if not windows_str or not windows_str.strip():
        return []
    
    windows = []
    parts = [p.strip() for p in windows_str.split(",") if p.strip()]
    for part in parts:
        try:
            start_str, end_str = part.split("-")
            start_time = datetime.strptime(start_str.strip(), "%H:%M").time()
            end_time = datetime.strptime(end_str.strip(), "%H:%M").time()
            if end_time <= start_time:
                raise ValueError(f"End time {end_time} must be after start time {start_time}")
            windows.append((start_time, end_time))
        except ValueError as err:
            raise ValueError(f"Invalid time window format: {part}. Expected HH:MM-HH:MM") from err
    return windows