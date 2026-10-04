"""Turns the raw API payloads into the one immutable value the renderer draws.

Everything that touches a StudyLife field name lives in this module and is listed in
USED_FIELDS. The server silently drops unknown fields on input and simply never sends a
field that does not exist on output, so a typo here would not raise - the feature would just
render as zero forever. tests/test_wire_fields.py pins the list against the verified wire
format and against what `build_dashboard` actually reads.

Four payloads are the dashboard proper (metrics, history, timer, sessions); three more are
optional extras for single layouts (course goals, achievements, notes) - each needs its own
scope, a key without it leaves that list empty and the layout says so (`unavailable`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from studylife_display.times import day_bounds, local_day, overlap_hours, parse_optional

HEATMAP_ROWS = 4
HEATMAP_COLUMNS = 7
HEATMAP_DAYS = HEATMAP_ROWS * HEATMAP_COLUMNS

# The year view: 53 Monday-to-Sunday columns ending in the current week; 52 full weeks back
# plus today's partial one covers a whole year whatever weekday it is.
YEAR_WEEKS = 53

# How many notes the note layout keeps (newest first) and how long its excerpt is.
NOTES_KEPT = 3
NOTE_EXCERPT_CHARS = 400

# Every JSON field read from each endpoint, in dotted notation ("[]" = each list element).
# Keep in sync with build_dashboard; the test fails if the two disagree.
USED_FIELDS: dict[str, frozenset[str]] = {
    "metrics": frozenset(
        {
            "streak",
            "streak.current",
            "streak.longest",
            "hours",
            "hours.week",
            "hours.month",
            "weekQuota",
            "weekQuota.hours",
            "weekQuota.targetMin",
            "weekQuota.targetMax",
            "weekQuota.percent",
            "monthQuota",
            "monthQuota.hours",
            "monthQuota.targetMin",
            "monthQuota.targetMax",
            "monthQuota.percent",
            "monthComparison",
            "monthComparison.currentMonthHours",
            "monthComparison.previousMonthHours",
            "monthComparison.deltaVsPreviousMonth",
            "monthComparison.hasYearData",
            "monthComparison.sameMonthLastYearHours",
            "monthComparison.deltaVsLastYear",
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
            "courseHours",
            "courseHours[].courseName",
            "courseHours[].hours",
            "courseHours[].sessionCount",
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
    # GET /api/coursegoals (CourseGoals.GetAll): the goals layout.
    "goals": frozenset({"[].courseName", "[].targetDate", "[].completedAt", "[].grade", "[].tag"}),
    # GET /api/metrics/achievements (Metrics.GetAchievements): the achievements layout.
    "achievements": frozenset(
        {
            "unlocked",
            "total",
            "tiers",
            "tiers[].category",
            "tiers[].threshold",
            "tiers[].unlocked",
            "tiers[].current",
        }
    ),
    # GET /api/notes (Notes.GetAll): the note layout.
    "notes": frozenset({"[].title", "[].content", "[].summary", "[].updatedAt"}),
}

# The optional payloads a key may lack the scope for; `DashboardData.unavailable` holds the
# ones that could not be fetched this time.
OPTIONAL_PAYLOADS = ("sessions", "goals", "achievements", "notes")


@dataclass(frozen=True)
class NextGoal:
    course_name: str
    days_left: int
    target_date: date | None


@dataclass(frozen=True)
class WeekQuota:
    """`metrics/summary` -> `weekQuota` / `monthQuota` (the same shape, StudyMetrics.CalcQuota)."""

    hours: float
    target_min: float
    target_max: float
    percent: float


@dataclass(frozen=True)
class MonthComparison:
    """`metrics/summary` -> `monthComparison`: this month against the previous one and, when
    there is a year of data, against the same month a year ago."""

    current_month_hours: float
    previous_month_hours: float
    delta_vs_previous_month: float
    has_year_data: bool
    same_month_last_year_hours: float | None
    delta_vs_last_year: float | None


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
    """One session planned for a day (from `GET /api/sessions`), for the agenda layouts."""

    start: datetime
    end: datetime
    course_name: str
    topic: str | None
    is_completed: bool
    # The wall clock is inside [start, end) right now.
    is_running_now: bool


@dataclass(frozen=True)
class CourseShare:
    """`metrics/summary` -> `courseHours[]`: one selected course with at least one studied
    session - its all-time hours and session count (the balance layout)."""

    course_name: str
    hours: float
    session_count: int


@dataclass(frozen=True)
class TodayStats:
    """Today's completed sessions from the history (the timer layout)."""

    session_count: int
    hours: float
    longest_hours: float
    first_start: datetime | None
    last_end: datetime | None


@dataclass(frozen=True)
class YearData:
    """A year of the history as 53 Monday-to-Sunday columns (the year layout). Every column
    is 7 hours-per-day values, Monday first; days still to come are 0."""

    weeks: tuple[tuple[float, ...], ...]
    first_monday: date
    total_hours: float
    session_count: int
    active_days: int


@dataclass(frozen=True)
class CourseGoal:
    """`GET /api/coursegoals` -> one course's goal: the target date and, once reached, when it
    was completed and with which grade."""

    course_name: str
    target_date: date | None
    completed_at: datetime | None
    grade: float | None
    tag: str | None

    @property
    def is_completed(self) -> bool:
        return self.completed_at is not None


@dataclass(frozen=True)
class AchievementTier:
    """`GET /api/metrics/achievements` -> `tiers[]`: one tier of one category, with how far
    along the figure behind it is."""

    category: str
    threshold: float
    unlocked: bool
    current: float


@dataclass(frozen=True)
class Achievements:
    unlocked: int
    total: int
    tiers: tuple[AchievementTier, ...]

    def next_tier(self) -> AchievementTier | None:
        """The locked tier closest to being unlocked (highest current/threshold)."""
        locked = [tier for tier in self.tiers if not tier.unlocked and tier.threshold > 0]
        if not locked:
            return None
        return max(locked, key=lambda tier: (tier.current / tier.threshold, -tier.threshold))

    def latest_unlocked(self) -> AchievementTier | None:
        """The unlocked tier whose threshold the figure behind it exceeds by the least - the
        best proxy for "most recently earned" without timestamps."""
        unlocked = [tier for tier in self.tiers if tier.unlocked and tier.threshold > 0]
        if not unlocked:
            return None
        return min(unlocked, key=lambda tier: (tier.current / tier.threshold, -tier.threshold))


@dataclass(frozen=True)
class Note:
    """`GET /api/notes` -> one note, newest first: its title and a plain-text excerpt (the
    server's one-sentence summary when there is one, else the start of the content)."""

    title: str
    excerpt: str
    updated_at: datetime | None


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
    # The degree figures (the "degree" layout): ECTS, average grade (None without a
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
    # -- added with the second batch of layouts; defaults keep older call sites working --
    # The month figures (the "month" layout): the server's quota, hours per calendar day of
    # the current month from the history (day 1 first, days to come 0) and the comparison.
    month_quota: WeekQuota = WeekQuota(0.0, 0.0, 0.0, 0.0)
    month_days: tuple[float, ...] = ()
    month_hours: float = 0.0
    month_comparison: MonthComparison = MonthComparison(0.0, 0.0, 0.0, False, None, None)
    # Every upcoming course goal the server lists (soonest first, max 5): the "exams" layout.
    # next_goal is upcoming_goals[0].
    upcoming_goals: tuple[NextGoal, ...] = ()
    # All-time hours per selected course from the server (the "balance" layout).
    course_shares: tuple[CourseShare, ...] = ()
    # Today's completed sessions (the "timer" layout).
    today_stats: TodayStats = TodayStats(0, 0.0, 0.0, None, None)
    # Tomorrow's planned sessions (the "tomorrow" layout), sorted by start.
    tomorrow: tuple[AgendaItem, ...] = ()
    # A year of the history (the "year" layout) and the longest streak ever.
    year: YearData = YearData((), date(1970, 1, 5), 0.0, 0, 0)
    longest_streak_days: int = 0
    # The optional extras: course goals (open ones first, soonest first), achievements and
    # the newest notes. Empty when not fetched - `unavailable` says which ones failed.
    goals: tuple[CourseGoal, ...] = ()
    achievements: Achievements = Achievements(0, 0, ())
    notes: tuple[Note, ...] = ()
    unavailable: frozenset[str] = frozenset()

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


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


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


def _goal(raw: dict[str, Any], tz: ZoneInfo) -> NextGoal:
    name = raw.get("courseName")
    target = parse_optional(raw.get("targetDate"), tz)
    return NextGoal(
        course_name=str(name) if name else "",
        days_left=_as_int(raw.get("daysLeft")),
        target_date=target.date() if target else None,
    )


def _upcoming_goals(metrics: dict[str, Any], tz: ZoneInfo) -> tuple[NextGoal, ...]:
    goals = metrics.get("upcomingCourseGoals")
    if not isinstance(goals, list):
        return ()
    return tuple(_goal(_as_dict(goal), tz) for goal in goals)


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)


def _quota(raw: dict[str, Any]) -> WeekQuota:
    return WeekQuota(
        hours=_as_float(raw.get("hours")),
        target_min=_as_float(raw.get("targetMin")),
        target_max=_as_float(raw.get("targetMax")),
        percent=_as_float(raw.get("percent")),
    )


def _month_comparison(metrics: dict[str, Any]) -> MonthComparison:
    raw = _as_dict(metrics.get("monthComparison"))
    return MonthComparison(
        current_month_hours=_as_float(raw.get("currentMonthHours")),
        previous_month_hours=_as_float(raw.get("previousMonthHours")),
        delta_vs_previous_month=_as_float(raw.get("deltaVsPreviousMonth")),
        has_year_data=bool(raw.get("hasYearData")),
        same_month_last_year_hours=_optional_float(raw.get("sameMonthLastYearHours")),
        delta_vs_last_year=_optional_float(raw.get("deltaVsLastYear")),
    )


def _course_shares(metrics: dict[str, Any]) -> tuple[CourseShare, ...]:
    shares = []
    for raw in _as_list(metrics.get("courseHours")):
        entry = _as_dict(raw)
        name = entry.get("courseName")
        shares.append(
            CourseShare(
                course_name=str(name) if name else "",
                hours=_as_float(entry.get("hours")),
                session_count=_as_int(entry.get("sessionCount")),
            )
        )
    shares.sort(key=lambda share: (-share.hours, share.course_name))
    return tuple(shares)


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


def today_stats(history: list[dict[str, Any]], today: date, tz: ZoneInfo) -> TodayStats:
    """Today's completed sessions: how many, their hours inside the day, the longest one,
    and when the first began / the last ended."""
    window_start, window_end = day_bounds(today, tz)
    count = 0
    hours = 0.0
    longest = 0.0
    first: datetime | None = None
    last: datetime | None = None
    for session in history:
        start = parse_optional(session.get("startTime"), tz)
        end = parse_optional(session.get("endTime"), tz)
        if start is None or end is None or end <= start:
            continue
        inside = overlap_hours(start, end, window_start, window_end)
        if inside <= 0:
            continue
        count += 1
        hours += inside
        longest = max(longest, inside)
        first = start if first is None or start < first else first
        last = end if last is None or end > last else last
    return TodayStats(count, hours, longest, first, last)


def year_data(history: list[dict[str, Any]], today: date, tz: ZoneInfo) -> YearData:
    """YEAR_WEEKS Monday-to-Sunday columns ending in today's week."""
    this_monday = today - timedelta(days=today.weekday())
    first_monday = this_monday - timedelta(weeks=YEAR_WEEKS - 1)
    days = [first_monday + timedelta(days=offset) for offset in range(YEAR_WEEKS * 7)]
    per_day = hours_per_day(history, days, tz)
    weeks = tuple(tuple(per_day[week * 7 : (week + 1) * 7]) for week in range(YEAR_WEEKS))
    window_start = day_bounds(days[0], tz)[0]
    window_end = day_bounds(today, tz)[1]
    return YearData(
        weeks=weeks,
        first_monday=first_monday,
        total_hours=sum(per_day),
        session_count=count_sessions(history, window_start, window_end, tz),
        active_days=sum(1 for hours in per_day if hours > 0),
    )


def days_in_month(day: date) -> int:
    first_next = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return (first_next - day.replace(day=1)).days


def agenda_items(
    sessions: list[dict[str, Any]], now: datetime, tz: ZoneInfo, day_offset: int = 0
) -> list[AgendaItem]:
    """The sessions that start on the local calendar day of `now` plus `day_offset` days,
    sorted by start. A session without a usable start or end (or that ends before it
    starts) is skipped."""
    day = local_day(now, tz) + timedelta(days=day_offset)
    items: list[AgendaItem] = []
    for session in sessions:
        start = parse_optional(session.get("startTime"), tz)
        end = parse_optional(session.get("endTime"), tz)
        if start is None or end is None or end <= start or local_day(start, tz) != day:
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


def course_goals(raw_goals: list[dict[str, Any]], tz: ZoneInfo) -> tuple[CourseGoal, ...]:
    """Open goals first (dated ones soonest first, then undated), completed ones last with
    the most recent first."""
    goals: list[CourseGoal] = []
    for raw in raw_goals:
        entry = _as_dict(raw)
        name = entry.get("courseName")
        target = parse_optional(entry.get("targetDate"), tz)
        tag = entry.get("tag")
        goals.append(
            CourseGoal(
                course_name=str(name) if name else "",
                target_date=target.date() if target else None,
                completed_at=parse_optional(entry.get("completedAt"), tz),
                grade=_optional_float(entry.get("grade")),
                tag=str(tag) if tag else None,
            )
        )

    def order(goal: CourseGoal) -> tuple[int, float, str]:
        if goal.completed_at is not None:
            return (1, -goal.completed_at.timestamp(), goal.course_name)
        target = goal.target_date or date.max
        return (0, float(target.toordinal()), goal.course_name)

    goals.sort(key=order)
    return tuple(goals)


def achievements(raw: dict[str, Any]) -> Achievements:
    tiers = []
    for tier in _as_list(raw.get("tiers")):
        entry = _as_dict(tier)
        category = entry.get("category")
        tiers.append(
            AchievementTier(
                category=str(category) if category else "",
                threshold=_as_float(entry.get("threshold")),
                unlocked=bool(entry.get("unlocked")),
                current=_as_float(entry.get("current")),
            )
        )
    return Achievements(
        unlocked=_as_int(raw.get("unlocked")),
        total=_as_int(raw.get("total")),
        tiers=tuple(tiers),
    )


_MARKDOWN_NOISE = re.compile(r"[#*_`>\[\]()!|~]+|^-+\s", re.MULTILINE)
_WHITESPACE = re.compile(r"\s+")


def note_excerpt(text: str, limit: int = NOTE_EXCERPT_CHARS) -> str:
    """Plain text for the panel: markdown punctuation and line breaks collapsed, cut to
    `limit` characters with an ellipsis."""
    plain = _WHITESPACE.sub(" ", _MARKDOWN_NOISE.sub(" ", text)).strip()
    if len(plain) <= limit:
        return plain
    return plain[:limit].rstrip() + "…"


def notes(
    raw_notes: list[dict[str, Any]], tz: ZoneInfo, keep: int = NOTES_KEPT
) -> tuple[Note, ...]:
    """The newest `keep` notes, newest first, each with a plain-text excerpt."""
    items: list[Note] = []
    for raw in raw_notes:
        entry = _as_dict(raw)
        title = entry.get("title")
        summary = entry.get("summary")
        content = entry.get("content")
        source = str(summary) if summary else str(content) if content else ""
        items.append(
            Note(
                title=str(title) if title else "",
                excerpt=note_excerpt(source),
                updated_at=parse_optional(entry.get("updatedAt"), tz),
            )
        )
    items.sort(
        key=lambda note: note.updated_at.timestamp() if note.updated_at else 0.0, reverse=True
    )
    return tuple(items[:keep])


def build_dashboard(
    metrics: dict[str, Any],
    history: list[dict[str, Any]],
    timer: dict[str, Any],
    now: datetime,
    tz: ZoneInfo,
    fetched_at: datetime | None = None,
    sessions: list[dict[str, Any]] | None = None,
    goals: list[dict[str, Any]] | None = None,
    achievements_payload: dict[str, Any] | None = None,
    notes_payload: list[dict[str, Any]] | None = None,
    unavailable: frozenset[str] = frozenset(),
) -> DashboardData:
    """Pure: no clock, no I/O. `now` decides what "today" is (in `tz`); `fetched_at` is when
    the payloads were obtained and defaults to `now` for a fresh fetch; `sessions`, `goals`,
    `achievements_payload` and `notes_payload` are the optional lists (None or empty = that
    layout is empty); `unavailable` names the optional payloads whose fetch failed."""
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
    month_first = today.replace(day=1)
    month_days = tuple(
        hours_per_day(
            history,
            [month_first + timedelta(days=offset) for offset in range(days_in_month(today))],
            tz,
        )
    )

    streak = _as_dict(metrics.get("streak"))
    hours = _as_dict(metrics.get("hours"))
    quota = _as_dict(metrics.get("weekQuota"))
    month_quota = _as_dict(metrics.get("monthQuota"))
    program = _as_dict(metrics.get("program"))
    program_name = program.get("name")
    ects = _as_dict(metrics.get("ects"))
    topics = _as_dict(metrics.get("topics"))
    upcoming = _upcoming_goals(metrics, tz)

    obtained = fetched_at if fetched_at is not None else now
    stale_seconds = now.timestamp() - obtained.timestamp()

    return DashboardData(
        today_hours=per_day[-1],
        week_hours=_as_float(hours.get("week")),
        streak_days=_as_int(streak.get("current")),
        next_goal=upcoming[0] if upcoming else None,
        heatmap=heatmap,
        heatmap_first_weekday=days[0].weekday(),
        course_hours=course_hours,
        week_quota=_quota(quota),
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
        month_quota=_quota(month_quota),
        month_days=month_days,
        month_hours=_as_float(hours.get("month")),
        month_comparison=_month_comparison(metrics),
        upcoming_goals=upcoming,
        course_shares=_course_shares(metrics),
        today_stats=today_stats(history, today, tz),
        tomorrow=tuple(agenda_items(sessions or [], now, tz, day_offset=1)),
        year=year_data(history, today, tz),
        longest_streak_days=_as_int(streak.get("longest")),
        goals=course_goals(goals or [], tz),
        achievements=achievements(achievements_payload or {}),
        notes=notes(notes_payload or [], tz),
        unavailable=frozenset(unavailable) & frozenset(OPTIONAL_PAYLOADS),
    )
