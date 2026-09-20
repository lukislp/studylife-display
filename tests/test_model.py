from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from studylife_display.model import (
    HEATMAP_COLUMNS,
    HEATMAP_ROWS,
    WeeklyReport,
    build_dashboard,
)
from studylife_display.times import day_bounds, parse_local


def session(start: str, end: str) -> dict[str, Any]:
    return {"id": 1, "courseName": "X", "startTime": start, "endTime": end, "isCompleted": True}


class TestTodayHours:
    def test_session_across_midnight_counts_half_an_hour_on_each_day(self, tz: ZoneInfo) -> None:
        history = [session("2026-09-16T23:30:00", "2026-09-17T00:30:00")]
        now = datetime(2026, 9, 17, 10, 0, tzinfo=tz)
        data = build_dashboard({}, history, {}, now, tz)
        assert data.today_hours == 0.5
        assert data.heatmap[-1][-1] == 0.5  # today
        assert data.heatmap[-1][-2] == 0.5  # yesterday

    def test_today_is_the_local_day_of_the_zone_not_of_now_s_own_zone(self, tz: ZoneInfo) -> None:
        # 22:30 UTC on the 16th is already 00:30 on the 17th in Berlin: a session logged at
        # 00:00-01:00 Berlin time on the 17th is "today", not "tomorrow".
        now_utc = datetime(2026, 9, 16, 22, 30, tzinfo=ZoneInfo("UTC"))
        history = [session("2026-09-17T00:00:00", "2026-09-17T01:00:00")]
        data = build_dashboard({}, history, {}, now_utc, tz)
        assert data.today_hours == 1.0

    def test_sums_several_sessions_and_ignores_incomplete_ones(self, tz: ZoneInfo) -> None:
        history = [
            session("2026-09-17T09:00:00", "2026-09-17T10:15:00"),
            session("2026-09-17T14:00:00", "2026-09-17T14:45:00"),
            {"startTime": "2026-09-17T16:00:00", "endTime": None},
            {"startTime": "2026-09-17T16:00:00"},
        ]
        now = datetime(2026, 9, 17, 18, 0, tzinfo=tz)
        assert build_dashboard({}, history, {}, now, tz).today_hours == 2.0

    def test_empty_history_gives_zeros(self, tz: ZoneInfo, fixed_now: datetime) -> None:
        data = build_dashboard({}, [], {}, fixed_now, tz)
        assert data.today_hours == 0.0
        assert all(hours == 0.0 for row in data.heatmap for hours in row)


class TestHeatmap:
    def test_shape_and_last_cell_is_today(self, tz: ZoneInfo, fixed_now: datetime) -> None:
        history = [session("2026-09-17T09:00:00", "2026-09-17T11:00:00")]
        data = build_dashboard({}, history, {}, fixed_now, tz)
        assert len(data.heatmap) == HEATMAP_ROWS
        assert all(len(row) == HEATMAP_COLUMNS for row in data.heatmap)
        assert data.heatmap[HEATMAP_ROWS - 1][HEATMAP_COLUMNS - 1] == 2.0

    def test_first_cell_is_27_days_ago_and_older_sessions_drop_out(
        self, tz: ZoneInfo, fixed_now: datetime
    ) -> None:
        first_day = fixed_now.date() - timedelta(days=27)
        too_old = first_day - timedelta(days=1)
        history = [
            session(f"{first_day}T08:00:00", f"{first_day}T09:30:00"),
            session(f"{too_old}T08:00:00", f"{too_old}T09:30:00"),
        ]
        data = build_dashboard({}, history, {}, fixed_now, tz)
        assert data.heatmap[0][0] == 1.5
        assert sum(sum(row) for row in data.heatmap) == 1.5
        assert data.heatmap_first_weekday == first_day.weekday()

    def test_rows_are_consecutive_weeks(self, tz: ZoneInfo, fixed_now: datetime) -> None:
        eight_days_ago = fixed_now.date() - timedelta(days=8)
        history = [session(f"{eight_days_ago}T08:00:00", f"{eight_days_ago}T09:00:00")]
        data = build_dashboard({}, history, {}, fixed_now, tz)
        # 8 days ago = second-to-last row, second-to-last column.
        assert data.heatmap[HEATMAP_ROWS - 2][HEATMAP_COLUMNS - 2] == 1.0


class TestMetrics:
    def test_next_goal_is_the_first_upcoming_goal(self, tz: ZoneInfo, fixed_now: datetime) -> None:
        metrics = {
            "upcomingCourseGoals": [
                {
                    "courseId": 1,
                    "courseName": "Betriebssysteme",
                    "targetDate": "2026-09-26T00:00:00",
                    "daysLeft": 9,
                },
                {
                    "courseId": 2,
                    "courseName": "Algebra",
                    "targetDate": "2026-10-10T00:00:00",
                    "daysLeft": 23,
                },
            ]
        }
        goal = build_dashboard(metrics, [], {}, fixed_now, tz).next_goal
        assert goal is not None
        assert goal.course_name == "Betriebssysteme"
        assert goal.days_left == 9
        assert goal.target_date == date(2026, 9, 26)

    def test_missing_sections_are_tolerated(self, tz: ZoneInfo, fixed_now: datetime) -> None:
        data = build_dashboard({}, [], {}, fixed_now, tz)
        assert data.next_goal is None
        assert data.streak_days == 0
        assert data.week_hours == 0.0
        assert data.week_quota.target_max == 0.0
        assert data.timer is None
        assert data.program_name is None

    def test_reads_streak_week_quota_and_program(
        self, tz: ZoneInfo, fixed_now: datetime, sample: Any
    ) -> None:
        metrics, history, timer, sessions = sample
        data = build_dashboard(metrics, history, timer, fixed_now, tz)
        assert data.streak_days == 12
        assert data.week_hours == 12.5
        assert data.week_quota.hours == 12.5
        assert data.week_quota.target_min == 15.0
        assert data.week_quota.target_max == 20.0
        assert data.week_quota.percent == 62.5
        assert data.program_name == "B.Sc. Informatik"

    def test_wrong_types_fall_back_to_zero(self, tz: ZoneInfo, fixed_now: datetime) -> None:
        metrics = {"streak": {"current": "12"}, "hours": {"week": True}, "weekQuota": "nope"}
        data = build_dashboard(metrics, [], {}, fixed_now, tz)
        assert data.streak_days == 0
        assert data.week_hours == 0.0
        assert data.week_quota.hours == 0.0


class TestTimer:
    def test_running_focus_phase(self, tz: ZoneInfo, fixed_now: datetime) -> None:
        timer = {"isRunning": True, "isBreak": False, "phaseEndsAt": "2026-09-17T17:10:00"}
        info = build_dashboard({}, [], timer, fixed_now, tz).timer
        assert info is not None
        assert info.is_running and not info.is_break
        assert info.phase_ends_at == datetime(2026, 9, 17, 17, 10, tzinfo=tz)

    def test_not_running_is_none(self, tz: ZoneInfo, fixed_now: datetime) -> None:
        assert build_dashboard({}, [], {"isRunning": False}, fixed_now, tz).timer is None
        assert build_dashboard({}, [], {}, fixed_now, tz).timer is None


class TestWeeklyReport:
    def test_server_report_is_read(self, tz: ZoneInfo, fixed_now: datetime, sample: Any) -> None:
        metrics, history, timer, _ = sample
        data = build_dashboard(metrics, history, timer, fixed_now, tz)
        assert data.weekly_report == WeeklyReport("2026-W37", 12.5, 2.5, "Betriebssysteme", 7)

    def test_missing_report_is_zeros(self, tz: ZoneInfo, fixed_now: datetime) -> None:
        data = build_dashboard({"weeklyReport": {"hours": "x"}}, [], {}, fixed_now, tz)
        assert data.weekly_report == WeeklyReport("", 0.0, 0.0, None, 0)

    def test_this_week_is_summed_from_the_history(self, tz: ZoneInfo, fixed_now: datetime) -> None:
        # FIXED_NOW is Thursday 2026-09-17; the week runs Mon 14th to Sun 20th.
        history = [
            {**session("2026-09-14T09:00:00", "2026-09-14T10:00:00"), "courseName": "A"},
            {**session("2026-09-17T09:00:00", "2026-09-17T11:30:00"), "courseName": "B"},
            {**session("2026-09-13T09:00:00", "2026-09-13T10:00:00"), "courseName": "A"},  # Sun
            {**session("2026-09-07T09:00:00", "2026-09-07T12:00:00"), "courseName": "A"},  # last
        ]
        data = build_dashboard({}, history, {}, fixed_now, tz)
        assert data.week_strip == (1.0, 0.0, 0.0, 2.5, 0.0, 0.0, 0.0)
        assert data.this_week == WeeklyReport("2026-W38", 3.5, 3.5 - 4.0, "B", 2)

    def test_this_week_without_sessions(self, tz: ZoneInfo, fixed_now: datetime) -> None:
        data = build_dashboard({}, [], {}, fixed_now, tz)
        assert data.this_week == WeeklyReport("2026-W38", 0.0, 0.0, None, 0)
        assert data.week_strip == (0.0,) * 7

    def test_week_id_follows_the_iso_week_of_now(self, tz: ZoneInfo) -> None:
        sunday = datetime(2026, 9, 20, 22, 0, tzinfo=tz)
        assert build_dashboard({}, [], {}, sunday, tz).this_week.week_id == "2026-W38"
        monday = datetime(2026, 9, 21, 0, 30, tzinfo=tz)
        assert build_dashboard({}, [], {}, monday, tz).this_week.week_id == "2026-W39"


class TestAgenda:
    def planned(self, start: str, end: str, **extra: Any) -> dict[str, Any]:
        item = {"courseName": "Algebra", "startTime": start, "endTime": end, "isCompleted": False}
        item.update(extra)
        return item

    def test_todays_sessions_sorted_by_start(self, tz: ZoneInfo, fixed_now: datetime) -> None:
        sessions = [
            self.planned("2026-09-17T18:00:00", "2026-09-17T19:00:00", topic="Later"),
            self.planned("2026-09-17T08:00:00", "2026-09-17T09:30:00", isCompleted=True),
            self.planned("2026-09-18T08:00:00", "2026-09-18T09:00:00"),  # tomorrow
            self.planned("2026-09-16T23:00:00", "2026-09-17T00:30:00"),  # started yesterday
            self.planned("2026-09-17T16:30:00", "2026-09-17T17:00:00", courseName=None),
            self.planned("2026-09-17T12:00:00", None),
            self.planned("2026-09-17T12:00:00", "2026-09-17T11:00:00"),
        ]
        data = build_dashboard({}, [], {}, fixed_now, tz, sessions=sessions)
        assert [item.start.hour for item in data.agenda] == [8, 16, 18]
        first, running, later = data.agenda
        assert first.is_completed and not first.is_running_now and first.topic is None
        assert running.is_running_now and running.course_name == ""
        assert later.topic == "Later" and not later.is_running_now
        assert data.next_agenda_item is running

    def test_no_sessions_means_no_agenda(self, tz: ZoneInfo, fixed_now: datetime) -> None:
        assert build_dashboard({}, [], {}, fixed_now, tz).agenda == ()
        assert build_dashboard({}, [], {}, fixed_now, tz, sessions=[]).next_agenda_item is None

    def test_today_is_the_servers_day_and_now_is_stored_in_its_zone(self, tz: ZoneInfo) -> None:
        # 22:30 UTC on the 16th is 00:30 on the 17th in Berlin.
        now_utc = datetime(2026, 9, 16, 22, 30, tzinfo=ZoneInfo("UTC"))
        sessions = [self.planned("2026-09-17T09:00:00", "2026-09-17T10:00:00")]
        data = build_dashboard({}, [], {}, now_utc, tz, sessions=sessions)
        assert len(data.agenda) == 1
        assert data.now.tzinfo is tz and data.now.hour == 0


class TestStaleness:
    def test_fresh_fetch_is_not_stale(self, tz: ZoneInfo, fixed_now: datetime) -> None:
        assert build_dashboard({}, [], {}, fixed_now, tz).stale_minutes == 0

    def test_stale_minutes_from_fetched_at(self, tz: ZoneInfo, fixed_now: datetime) -> None:
        fetched = fixed_now - timedelta(minutes=12, seconds=40)
        data = build_dashboard({}, [], {}, fixed_now, tz, fetched_at=fetched)
        assert data.stale_minutes == 12
        assert data.fetched_at == fetched

    def test_clock_skew_never_goes_negative(self, tz: ZoneInfo, fixed_now: datetime) -> None:
        fetched = fixed_now + timedelta(minutes=3)
        assert build_dashboard({}, [], {}, fixed_now, tz, fetched_at=fetched).stale_minutes == 0


class TestTimes:
    def test_parse_local_attaches_the_zone(self, tz: ZoneInfo) -> None:
        parsed = parse_local("2026-09-17T23:30:00", tz)
        assert parsed.tzinfo is tz
        assert parsed.utcoffset() == timedelta(hours=2)

    def test_parse_local_converts_an_offset_aware_value(self, tz: ZoneInfo) -> None:
        parsed = parse_local("2026-09-17T21:30:00+00:00", tz)
        assert parsed.hour == 23

    def test_day_bounds_on_a_dst_change_day(self, tz: ZoneInfo) -> None:
        start, end = day_bounds(date(2026, 3, 29), tz)
        assert end.timestamp() - start.timestamp() == 23 * 3600

    def test_session_across_the_dst_change_counts_elapsed_time(self, tz: ZoneInfo) -> None:
        # 01:30 -> 03:30 wall clock on the night the clocks jump from 02:00 to 03:00 is one
        # real hour of studying, not two.
        history = [session("2026-03-29T01:30:00", "2026-03-29T03:30:00")]
        now = datetime(2026, 3, 29, 12, 0, tzinfo=tz)
        assert build_dashboard({}, history, {}, now, tz).today_hours == 1.0
