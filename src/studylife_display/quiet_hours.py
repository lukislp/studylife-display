"""Quiet hours: a daily window in which the scheduled refresh does nothing.

`DISPLAY_QUIET_HOURS` is `HH-HH` or `HH:MM-HH:MM`; the window may wrap past midnight
(`23-7` = 23:00 to 07:00). The start is inclusive and the end exclusive, so `23-7` skips the
refreshes at 23:00 and 06:55 and runs the one at 07:00. Everything here is pure: the caller
passes the moment to test, in the zone the panel lives in.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

Window = tuple[time, time]


def parse_clock(text: str) -> time:
    """`7`, `07`, `7:30` or `07:30` -> time; ValueError for anything else."""
    text = text.strip()
    hours, sep, minutes = text.partition(":")
    if not hours.isdigit() or (sep and not minutes.isdigit()):
        raise ValueError(f"{text!r} is not a time of day (HH or HH:MM)")
    hour = int(hours)
    minute = int(minutes) if sep else 0
    if hour > 23 or minute > 59:
        raise ValueError(f"{text!r} is not a time of day (HH or HH:MM)")
    return time(hour, minute)


def parse_quiet_hours(spec: str) -> Window | None:
    """`23-7` -> (23:00, 07:00); empty or blank -> None (quiet hours off). ValueError for a
    malformed spec or a window with the same start and end (which would be either nothing
    or the whole day, and nobody means either)."""
    spec = spec.strip()
    if not spec:
        return None
    start_text, sep, end_text = spec.partition("-")
    if not sep:
        raise ValueError(f"quiet hours {spec!r} must be HH-HH or HH:MM-HH:MM")
    start, end = parse_clock(start_text), parse_clock(end_text)
    if start == end:
        raise ValueError(f"quiet hours {spec!r} start and end at the same time")
    return start, end


def in_quiet_hours(now: datetime, spec: str) -> bool:
    """Whether `now` (by its wall clock) falls inside the window; False when off."""
    window = parse_quiet_hours(spec)
    if window is None:
        return False
    start, end = window
    moment = now.time().replace(second=0, microsecond=0)
    if start < end:
        return start <= moment < end
    # Wraps past midnight: inside when after the start or before the end.
    return moment >= start or moment < end


def quiet_hours_end(now: datetime, spec: str) -> datetime | None:
    """When the window that contains `now` ends, or None when `now` is outside it."""
    if not in_quiet_hours(now, spec):
        return None
    window = parse_quiet_hours(spec)
    assert window is not None
    end = window[1]
    candidate = now.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate
