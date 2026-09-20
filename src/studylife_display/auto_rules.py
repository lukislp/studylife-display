"""Time windows for two of the "auto" rules: the weekly review and the agenda.

`DISPLAY_AUTO_REVIEW=sun 18-24` and `DISPLAY_AUTO_AGENDA=06-12` follow the quiet-hours
notation (`HH-HH` or `HH:MM-HH:MM`, start inclusive, end exclusive) with two differences: an
optional list of weekdays in front (`sun`, `sat,sun`, `mon-fri`) restricts the window to
those days, and `24` (or `24:00`) is allowed as the end so that "until midnight" can be
written down. A window may not wrap past midnight - with a weekday in front it would be
unclear which day the part after midnight belongs to, and neither rule needs it. An empty
value switches the rule off. Everything here is pure: the caller passes the moment to test,
in the zone the panel lives in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MINUTES_PER_DAY = 24 * 60


@dataclass(frozen=True)
class RuleWindow:
    # Weekdays (0 = Monday) the window applies on; None = every day.
    weekdays: frozenset[int] | None
    # Minutes since midnight, start inclusive, end exclusive; end may be 1440 (midnight).
    start_minute: int
    end_minute: int


def _parse_minutes(text: str, allow_midnight: bool) -> int:
    """`7`, `07`, `7:30`, `07:30` -> minutes since midnight; `24`/`24:00` only as an end."""
    text = text.strip()
    hours, sep, minutes = text.partition(":")
    if not hours.isdigit() or (sep and not minutes.isdigit()):
        raise ValueError(f"{text!r} is not a time of day (HH or HH:MM)")
    hour = int(hours)
    minute = int(minutes) if sep else 0
    if minute > 59 or hour > 24 or (hour == 24 and (minute != 0 or not allow_midnight)):
        raise ValueError(f"{text!r} is not a time of day (HH or HH:MM)")
    return hour * 60 + minute


def _parse_weekdays(text: str) -> frozenset[int]:
    """`sun`, `sat,sun` or `mon-fri` -> weekday numbers (0 = Monday)."""
    days: set[int] = set()
    for part in text.lower().split(","):
        part = part.strip()
        first, sep, last = part.partition("-")
        if first not in WEEKDAYS or (sep and last not in WEEKDAYS):
            raise ValueError(f"{part!r} is not a weekday ({', '.join(WEEKDAYS)})")
        start = WEEKDAYS.index(first)
        end = WEEKDAYS.index(last) if sep else start
        if end < start:
            raise ValueError(f"weekday range {part!r} runs backwards")
        days.update(range(start, end + 1))
    return frozenset(days)


def parse_rule_window(spec: str) -> RuleWindow | None:
    """`sun 18-24` -> Sundays 18:00 to midnight, `06-12` -> every day 06:00 to 12:00; empty
    or blank -> None (the rule is off). ValueError for anything malformed, an empty window
    or one that would wrap past midnight."""
    spec = spec.strip()
    if not spec:
        return None
    days_text, _, window_text = spec.rpartition(" ")
    weekdays = _parse_weekdays(days_text) if days_text.strip() else None
    start_text, sep, end_text = window_text.partition("-")
    if not sep:
        raise ValueError(f"window {spec!r} must be [weekdays] HH-HH or HH:MM-HH:MM")
    start = _parse_minutes(start_text, allow_midnight=False)
    end = _parse_minutes(end_text, allow_midnight=True)
    if end <= start:
        raise ValueError(f"window {spec!r} must end after it starts (no wrap past midnight)")
    return RuleWindow(weekdays=weekdays, start_minute=start, end_minute=end)


def in_rule_window(now: datetime, spec: str) -> bool:
    """Whether `now` (by its own wall clock and weekday) falls inside the window; False when
    the rule is off."""
    window = parse_rule_window(spec)
    if window is None:
        return False
    if window.weekdays is not None and now.weekday() not in window.weekdays:
        return False
    minute = now.hour * 60 + now.minute
    return window.start_minute <= minute < window.end_minute
