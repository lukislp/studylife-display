"""Time-zone helpers.

StudyLife serialises every DateTime as naive local time of the server (Europe/Berlin in
production) without an offset. Nothing in this package ever calls `datetime.now()` without a
zone or `fromisoformat` without attaching one: the Pi's own clock is UTC after a fresh image,
and "today" has to mean the same calendar day the web app shows.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


def zone(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def parse_local(text: str, tz: ZoneInfo) -> datetime:
    """Parse a naive StudyLife timestamp and attach `tz`.

    A timestamp that unexpectedly carries an offset (a future server change, a proxy that
    rewrites JSON) is honoured and converted rather than double-shifted.
    """
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def parse_optional(text: object, tz: ZoneInfo) -> datetime | None:
    """`parse_local` for values that may be missing, null or malformed."""
    if not isinstance(text, str) or not text:
        return None
    try:
        return parse_local(text, tz)
    except ValueError:
        return None


def local_day(moment: datetime, tz: ZoneInfo) -> date:
    """The calendar day of `moment` in `tz`."""
    return moment.astimezone(tz).date()


def day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Start (inclusive) and end (exclusive) of a local calendar day, DST-safe: the end is
    "the next calendar day at 00:00", not "start + 24h"."""
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    return start, end


def overlap_hours(
    start: datetime, end: datetime, window_start: datetime, window_end: datetime
) -> float:
    """Hours of [start, end) that fall inside [window_start, window_end).

    Measured in absolute time (`timestamp()`), not wall clock: two aware datetimes in the
    same zone subtract by wall clock in Python, which would be an hour off across a DST
    change."""
    latest_start = max(start, window_start)
    earliest_end = min(end, window_end)
    seconds = earliest_end.timestamp() - latest_start.timestamp()
    return max(0.0, seconds) / 3600.0
