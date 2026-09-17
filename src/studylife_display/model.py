"""Turns the three raw API payloads into the one immutable value the renderer draws.

Everything that touches a StudyLife field name lives in this module and is listed in
USED_FIELDS. The server silently drops unknown fields on input and simply never sends a
field that does not exist on output, so a typo here would not raise - the feature would just
render as zero forever. tests/test_wire_fields.py pins the list against the verified wire
format and against what `build_dashboard` actually reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from studylife_display.times import day_bounds, local_day, overlap_hours, parse_optional

HEATMAP_ROWS = 4
HEATMAP_COLUMNS = 7
HEATMAP_DAYS = HEATMAP_ROWS * HEATMAP_COLUMNS

# Every JSON field read from each endpoint, in dotted notation ("[]" = each list element).
# Keep in sync with build_dashboard; the test fails if the two disagree.
USED_FIELDS: dict[str, frozenset[str]] = {
    "metrics": frozenset(
        {
            "streak",
            "streak.current",
            "hours",
            "hours.week",
            "weekQuota",
            "weekQuota.hours",
            "weekQuota.targetMin",
            "weekQuota.targetMax",
            "weekQuota.percent",
            "program",
            "program.name",
            "upcomingCourseGoals",
            "upcomingCourseGoals[].courseName",
            "upcomingCourseGoals[].daysLeft",
            "upcomingCourseGoals[].targetDate",
        }
    ),
    "history": frozenset({"[].startTime", "[].endTime"}),
    "timer": frozenset({"isRunning", "isBreak", "phaseEndsAt"}),
}


@dataclass(frozen=True)
class NextGoal:
    course_name: str
    days_left: int
    target_date: date | None


@dataclass(frozen=True)
class WeekQuota:
    hours: float
    target_min: float
    target_max: float
    percent: float


@dataclass(frozen=True)
class TimerInfo:
    is_running: bool
    is_break: bool
    phase_ends_at: datetime | None


@dataclass(frozen=True)
class DashboardData:
    today_hours: float
    week_hours: float
    streak_days: int
    next_goal: NextGoal | None
    # HEATMAP_ROWS rows of HEATMAP_COLUMNS hours-per-day, oldest first, last cell = today.
    heatmap: tuple[tuple[float, ...], ...]
    # Weekday (0 = Monday) of the first heatmap cell, so the renderer can label the columns.
    heatmap_first_weekday: int
    week_quota: WeekQuota
    timer: TimerInfo | None
    program_name: str | None
    fetched_at: datetime
    now: datetime
    stale_minutes: int


def _as_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value)


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return int(value)


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def hours_per_day(history: list[dict[str, Any]], days: list[date], tz: ZoneInfo) -> list[float]:
    """Sum of session time per local calendar day. A session crossing midnight is split
    between the two days it touches; anything outside `days` is ignored."""
    totals = [0.0] * len(days)
    bounds = [day_bounds(day, tz) for day in days]
    for session in history:
        start = parse_optional(session.get("startTime"), tz)
        end = parse_optional(session.get("endTime"), tz)
        if start is None or end is None or end <= start:
            continue
        for index, (window_start, window_end) in enumerate(bounds):
            if end <= window_start or start >= window_end:
                continue
            totals[index] += overlap_hours(start, end, window_start, window_end)
    return totals


def _next_goal(metrics: dict[str, Any], tz: ZoneInfo) -> NextGoal | None:
    goals = metrics.get("upcomingCourseGoals")
    if not isinstance(goals, list) or not goals:
        return None
    first = _as_dict(goals[0])
    name = first.get("courseName")
    target = parse_optional(first.get("targetDate"), tz)
    return NextGoal(
        course_name=str(name) if name else "",
        days_left=_as_int(first.get("daysLeft")),
        target_date=target.date() if target else None,
    )


def _timer(timer: dict[str, Any], tz: ZoneInfo) -> TimerInfo | None:
    if not timer.get("isRunning"):
        return None
    return TimerInfo(
        is_running=True,
        is_break=bool(timer.get("isBreak")),
        phase_ends_at=parse_optional(timer.get("phaseEndsAt"), tz),
    )


def build_dashboard(
    metrics: dict[str, Any],
    history: list[dict[str, Any]],
    timer: dict[str, Any],
    now: datetime,
    tz: ZoneInfo,
    fetched_at: datetime | None = None,
) -> DashboardData:
    """Pure: no clock, no I/O. `now` decides what "today" is (in `tz`); `fetched_at` is when
    the payloads were obtained and defaults to `now` for a fresh fetch."""
    today = local_day(now, tz)
    days = [today - timedelta(days=offset) for offset in range(HEATMAP_DAYS - 1, -1, -1)]
    per_day = hours_per_day(history, days, tz)
    heatmap = tuple(
        tuple(per_day[row * HEATMAP_COLUMNS : (row + 1) * HEATMAP_COLUMNS])
        for row in range(HEATMAP_ROWS)
    )

    streak = _as_dict(metrics.get("streak"))
    hours = _as_dict(metrics.get("hours"))
    quota = _as_dict(metrics.get("weekQuota"))
    program = _as_dict(metrics.get("program"))
    program_name = program.get("name")

    obtained = fetched_at if fetched_at is not None else now
    stale_seconds = now.timestamp() - obtained.timestamp()

    return DashboardData(
        today_hours=per_day[-1],
        week_hours=_as_float(hours.get("week")),
        streak_days=_as_int(streak.get("current")),
        next_goal=_next_goal(metrics, tz),
        heatmap=heatmap,
        heatmap_first_weekday=days[0].weekday(),
        week_quota=WeekQuota(
            hours=_as_float(quota.get("hours")),
            target_min=_as_float(quota.get("targetMin")),
            target_max=_as_float(quota.get("targetMax")),
            percent=_as_float(quota.get("percent")),
        ),
        timer=_timer(timer, tz),
        program_name=str(program_name) if program_name else None,
        fetched_at=obtained,
        now=now,
        stale_minutes=max(0, int(stale_seconds // 60)),
    )
