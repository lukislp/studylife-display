"""Turns the four raw API payloads into the one immutable value the renderer draws.

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
            "ects",
            "ects.earned",
            "ects.total",
            "averageGrade",
            "forecast",
            "forecast.available",
            "forecast.alreadyDone",
            "forecast.date",
            "forecast.recentWeeklyHours",
            "neglectedCourse",
            "neglectedCourse.courseId",
            "neglectedCourse.courseName",
            "neglectedCourse.lastStudied",
            "neglectedCourse.daysSince",
            "topics",
            "topics.completed",
            "topics.total",
            "weeklyReport",
            "weeklyReport.weekId",
            "weeklyReport.hours",
            "weeklyReport.deltaVsPreviousWeek",
            "weeklyReport.topCourseName",
            "weeklyReport.sessionCount",
        }
    ),
    "history": frozenset({"[].startTime", "[].endTime", "[].courseName"}),
    "timer": frozenset({"isRunning", "isBreak", "phaseEndsAt", "currentRound"}),
    "sessions": frozenset(
        {"[].startTime", "[].endTime", "[].courseName", "[].topic", "[].isCompleted"}
    ),
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
    current_round: int | None = None


@dataclass(frozen=True)
class Ects:
    earned: float
    total: float


@dataclass(frozen=True)
class Forecast:
    # `available` False: not enough data yet; `already_done`: every course is completed.
    available: bool
    already_done: bool
    date: date | None
    recent_weekly_hours: float


@dataclass(frozen=True)
class NeglectedCourse:
    course_id: int
    course_name: str
    # Both None when the course was never studied inside the server's lookback window.
    last_studied: datetime | None
    days_since: int | None


@dataclass(frozen=True)
class Topics:
    completed: int
    total: int


@dataclass(frozen=True)
class WeeklyReport:
    """`metrics/summary` -> `weeklyReport`: the server's figures for the current week."""

    week_id: str
    hours: float
    delta_vs_previous_week: float
    top_course_name: str | None
    session_count: int


@dataclass(frozen=True)
class AgendaItem:
    """One session planned for today (from `GET /api/sessions`), for the agenda layout."""

    start: datetime
    end: datetime
    course_name: str
    topic: str | None
    is_completed: bool
    # The wall clock is inside [start, end) right now.
    is_running_now: bool


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
    # Hours per course over the heatmap window, most first; "" is a session without
    # a course name.
    course_hours: tuple[tuple[str, float], ...]
    week_quota: WeekQuota
    timer: TimerInfo | None
    program_name: str | None
    # The semester figures (the "semester" layout): ECTS, average grade (None without a
    # graded course), graduation forecast, the neglected course (None when the server's
    # gate is not met) and topic progress.
    ects: Ects
    average_grade: float | None
    forecast: Forecast
    neglected_course: NeglectedCourse | None
    topics: Topics
    # The server's `weeklyReport` - always the last COMPLETED Monday-to-Sunday week (the
    # review layout's footer); zeros when the section is missing.
    weekly_report: WeeklyReport
    # The current Monday-to-Sunday week, summed from the history on the Pi: what the review
    # layout shows large. Delta against the week before, top course by hours.
    this_week: WeeklyReport
    # Hours per day of the current Monday-to-Sunday week from the history, Monday first;
    # days still to come are 0.
    week_strip: tuple[float, ...]
    # Today's planned sessions (the "agenda" layout), sorted by start; empty when the
    # session list could not be fetched.
    agenda: tuple[AgendaItem, ...]
    fetched_at: datetime
    # `now` in the server's zone: the wall clock every layout and the auto rules work with.
    now: datetime
    stale_minutes: int

    @property
    def next_agenda_item(self) -> AgendaItem | None:
        """The running or next session of today: the first whose end is still ahead."""
        for item in self.agenda:
            if item.end > self.now:
                return item
        return None


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


def hours_per_course(
    history: list[dict[str, Any]], window_start: datetime, window_end: datetime, tz: ZoneInfo
) -> list[tuple[str, float]]:
    """Session time per course name inside [window_start, window_end), most first (name as
    the tie-break so equal totals keep a stable order between refreshes)."""
    totals: dict[str, float] = {}
    for session in history:
        start = parse_optional(session.get("startTime"), tz)
        end = parse_optional(session.get("endTime"), tz)
        if start is None or end is None or end <= start:
            continue
        hours = overlap_hours(start, end, window_start, window_end)
        if hours <= 0:
            continue
        name = session.get("courseName")
        key = str(name) if name else ""
        totals[key] = totals.get(key, 0.0) + hours
    return sorted(totals.items(), key=lambda item: (-item[1], item[0]))


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


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)


def _forecast(metrics: dict[str, Any], tz: ZoneInfo) -> Forecast:
    forecast = _as_dict(metrics.get("forecast"))
    moment = parse_optional(forecast.get("date"), tz)
    return Forecast(
        available=bool(forecast.get("available")),
        already_done=bool(forecast.get("alreadyDone")),
        date=moment.date() if moment else None,
        recent_weekly_hours=_as_float(forecast.get("recentWeeklyHours")),
    )


def _neglected_course(metrics: dict[str, Any], tz: ZoneInfo) -> NeglectedCourse | None:
    raw = metrics.get("neglectedCourse")
    if not isinstance(raw, dict):
        return None
    name = raw.get("courseName")
    return NeglectedCourse(
        course_id=_as_int(raw.get("courseId")),
        course_name=str(name) if name else "",
        last_studied=parse_optional(raw.get("lastStudied"), tz),
        days_since=_optional_int(raw.get("daysSince")),
    )


def count_sessions(
    history: list[dict[str, Any]], window_start: datetime, window_end: datetime, tz: ZoneInfo
) -> int:
    """Sessions that start inside [window_start, window_end) and have a usable end."""
    count = 0
    for session in history:
        start = parse_optional(session.get("startTime"), tz)
        end = parse_optional(session.get("endTime"), tz)
        if start is None or end is None or end <= start:
            continue
        if window_start <= start < window_end:
            count += 1
    return count


def this_week_report(history: list[dict[str, Any]], monday: date, tz: ZoneInfo) -> WeeklyReport:
    """The week starting on `monday`, summed from the history like the heatmap is: hours,
    the change against the week before, the course with the most hours, session count."""
    days = [monday + timedelta(days=offset) for offset in range(7)]
    previous = [monday - timedelta(days=7 - offset) for offset in range(7)]
    hours = sum(hours_per_day(history, days, tz))
    previous_hours = sum(hours_per_day(history, previous, tz))
    window_start = day_bounds(days[0], tz)[0]
    window_end = day_bounds(days[-1], tz)[1]
    courses = hours_per_course(history, window_start, window_end, tz)
    iso = monday.isocalendar()
    return WeeklyReport(
        week_id=f"{iso.year}-W{iso.week:02d}",
        hours=hours,
        delta_vs_previous_week=hours - previous_hours,
        top_course_name=(courses[0][0] or None) if courses else None,
        session_count=count_sessions(history, window_start, window_end, tz),
    )


def _weekly_report(metrics: dict[str, Any]) -> WeeklyReport:
    report = _as_dict(metrics.get("weeklyReport"))
    week_id = report.get("weekId")
    top = report.get("topCourseName")
    return WeeklyReport(
        week_id=str(week_id) if week_id else "",
        hours=_as_float(report.get("hours")),
        delta_vs_previous_week=_as_float(report.get("deltaVsPreviousWeek")),
        top_course_name=str(top) if top else None,
        session_count=_as_int(report.get("sessionCount")),
    )


def agenda_items(sessions: list[dict[str, Any]], now: datetime, tz: ZoneInfo) -> list[AgendaItem]:
    """The sessions that start on the local calendar day of `now`, sorted by start. A session
    without a usable start or end (or that ends before it starts) is skipped."""
    today = local_day(now, tz)
    items: list[AgendaItem] = []
    for session in sessions:
        start = parse_optional(session.get("startTime"), tz)
        end = parse_optional(session.get("endTime"), tz)
        if start is None or end is None or end <= start or local_day(start, tz) != today:
            continue
        name = session.get("courseName")
        topic = session.get("topic")
        items.append(
            AgendaItem(
                start=start,
                end=end,
                course_name=str(name) if name else "",
                topic=str(topic) if topic else None,
                is_completed=bool(session.get("isCompleted")),
                is_running_now=start <= now < end,
            )
        )
    items.sort(key=lambda item: (item.start, item.end))
    return items


def _timer(timer: dict[str, Any], tz: ZoneInfo) -> TimerInfo | None:
    if not timer.get("isRunning"):
        return None
    return TimerInfo(
        is_running=True,
        is_break=bool(timer.get("isBreak")),
        phase_ends_at=parse_optional(timer.get("phaseEndsAt"), tz),
        current_round=_as_int(timer.get("currentRound")) or None,
    )


def build_dashboard(
    metrics: dict[str, Any],
    history: list[dict[str, Any]],
    timer: dict[str, Any],
    now: datetime,
    tz: ZoneInfo,
    fetched_at: datetime | None = None,
    sessions: list[dict[str, Any]] | None = None,
) -> DashboardData:
    """Pure: no clock, no I/O. `now` decides what "today" is (in `tz`); `fetched_at` is when
    the payloads were obtained and defaults to `now` for a fresh fetch; `sessions` is the
    optional session list (None or empty = no agenda)."""
    now = now.astimezone(tz)
    today = local_day(now, tz)
    days = [today - timedelta(days=offset) for offset in range(HEATMAP_DAYS - 1, -1, -1)]
    per_day = hours_per_day(history, days, tz)
    monday = today - timedelta(days=today.weekday())
    week_days = [monday + timedelta(days=offset) for offset in range(7)]
    week_strip = tuple(hours_per_day(history, week_days, tz))
    window_start = day_bounds(days[0], tz)[0]
    window_end = day_bounds(days[-1], tz)[1]
    course_hours = tuple(hours_per_course(history, window_start, window_end, tz))
    heatmap = tuple(
        tuple(per_day[row * HEATMAP_COLUMNS : (row + 1) * HEATMAP_COLUMNS])
        for row in range(HEATMAP_ROWS)
    )

    streak = _as_dict(metrics.get("streak"))
    hours = _as_dict(metrics.get("hours"))
    quota = _as_dict(metrics.get("weekQuota"))
    program = _as_dict(metrics.get("program"))
    program_name = program.get("name")
    ects = _as_dict(metrics.get("ects"))
    topics = _as_dict(metrics.get("topics"))

    obtained = fetched_at if fetched_at is not None else now
    stale_seconds = now.timestamp() - obtained.timestamp()

    return DashboardData(
        today_hours=per_day[-1],
        week_hours=_as_float(hours.get("week")),
        streak_days=_as_int(streak.get("current")),
        next_goal=_next_goal(metrics, tz),
        heatmap=heatmap,
        heatmap_first_weekday=days[0].weekday(),
        course_hours=course_hours,
        week_quota=WeekQuota(
            hours=_as_float(quota.get("hours")),
            target_min=_as_float(quota.get("targetMin")),
            target_max=_as_float(quota.get("targetMax")),
            percent=_as_float(quota.get("percent")),
        ),
        timer=_timer(timer, tz),
        program_name=str(program_name) if program_name else None,
        ects=Ects(earned=_as_float(ects.get("earned")), total=_as_float(ects.get("total"))),
        average_grade=_optional_float(metrics.get("averageGrade")),
        forecast=_forecast(metrics, tz),
        neglected_course=_neglected_course(metrics, tz),
        topics=Topics(
            completed=_as_int(topics.get("completed")), total=_as_int(topics.get("total"))
        ),
        weekly_report=_weekly_report(metrics),
        this_week=this_week_report(history, monday, tz),
        week_strip=week_strip,
        agenda=tuple(agenda_items(sessions or [], now, tz)),
        fetched_at=obtained,
        now=now,
        stale_minutes=max(0, int(stale_seconds // 60)),
    )
