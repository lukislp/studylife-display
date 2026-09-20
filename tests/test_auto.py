from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from studylife_display.config import Settings
from studylife_display.layouts import LAYOUTS
from studylife_display.layouts.auto import (
    DEFAULT_RULES,
    EXAM_SOON_DAYS,
    AutoRules,
    resolve_layout,
    rules_from_settings,
)
from studylife_display.model import (
    AgendaItem,
    DashboardData,
    NextGoal,
    TimerInfo,
    build_dashboard,
)

# September 2026: the 17th (FIXED_NOW) is a Thursday, the 20th a Sunday, the 21st a Monday.
SUNDAY = datetime(2026, 9, 20, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))


@pytest.fixture
def data(sample: Any, fixed_now: datetime, tz: ZoneInfo) -> DashboardData:
    """Sample data: Thursday 16:45, timer running, next goal in 9 days, a session running
    now and two more ahead today."""
    metrics, history, timer, sessions = sample
    return build_dashboard(metrics, history, timer, fixed_now, tz, sessions=sessions)


@pytest.fixture
def quiet(data: DashboardData) -> DashboardData:
    """Nothing going on: no timer, no goal, no session ahead - `classic` territory."""
    return replace(data, timer=None, next_goal=None, agenda=())


def with_goal(data: DashboardData, days_left: int) -> DashboardData:
    assert data.next_goal is not None
    return replace(data, next_goal=replace(data.next_goal, days_left=days_left))


def at(data: DashboardData, moment: datetime) -> DashboardData:
    return replace(data, now=moment)


def planned(data: DashboardData, start_in: timedelta, length: timedelta) -> DashboardData:
    start = data.now + start_in
    item = AgendaItem(start, start + length, "Algebra", None, False, start <= data.now)
    return replace(data, agenda=(item,))


class TestExamAndFocus:
    def test_exam_when_the_goal_is_within_seven_days(self, data: DashboardData) -> None:
        assert resolve_layout("auto", with_goal(data, EXAM_SOON_DAYS)) == "exam"
        assert resolve_layout("auto", with_goal(data, 1)) == "exam"
        assert resolve_layout("auto", with_goal(data, 0)) == "exam"

    def test_overdue_goal_still_counts_as_exam(self, data: DashboardData) -> None:
        assert resolve_layout("auto", with_goal(data, -1)) == "exam"

    def test_eight_days_is_not_soon(self, data: DashboardData) -> None:
        # The boundary: 7 -> exam, 8 -> whatever the timer says (running in the sample).
        assert resolve_layout("auto", with_goal(data, EXAM_SOON_DAYS + 1)) == "focus"

    def test_exam_beats_a_running_timer(self, data: DashboardData) -> None:
        assert data.timer is not None and data.timer.is_running
        assert resolve_layout("auto", with_goal(data, 3)) == "exam"

    def test_focus_while_the_timer_runs_and_no_exam_is_near(self, data: DashboardData) -> None:
        assert resolve_layout("auto", data) == "focus"
        assert resolve_layout("auto", replace(data, next_goal=None)) == "focus"

    def test_stopped_timer_does_not_count(self, quiet: DashboardData) -> None:
        stopped = TimerInfo(is_running=False, is_break=False, phase_ends_at=None)
        assert resolve_layout("auto", replace(quiet, timer=stopped)) == "classic"

    def test_classic_otherwise(self, quiet: DashboardData) -> None:
        assert resolve_layout("auto", quiet) == "classic"
        far = replace(quiet, next_goal=NextGoal("Algebra", 30, None))
        assert resolve_layout("auto", far) == "classic"


class TestReviewRule:
    def test_sunday_evening_is_the_review(self, quiet: DashboardData) -> None:
        assert resolve_layout("auto", at(quiet, SUNDAY.replace(hour=18, minute=0))) == "review"
        assert resolve_layout("auto", at(quiet, SUNDAY.replace(hour=23, minute=59))) == "review"

    def test_boundaries(self, quiet: DashboardData) -> None:
        assert resolve_layout("auto", at(quiet, SUNDAY.replace(hour=17, minute=59))) == "classic"
        monday = SUNDAY + timedelta(days=1)
        assert resolve_layout("auto", at(quiet, monday.replace(hour=0, minute=0))) == "classic"
        assert resolve_layout("auto", at(quiet, monday.replace(hour=19))) == "classic"
        saturday = SUNDAY - timedelta(days=1)
        assert resolve_layout("auto", at(quiet, saturday.replace(hour=19))) == "classic"

    def test_review_beats_every_other_rule(self, data: DashboardData) -> None:
        busy = planned(
            with_goal(at(data, SUNDAY.replace(hour=19)), 2), timedelta(0), timedelta(hours=1)
        )
        assert busy.timer is not None and busy.timer.is_running
        assert resolve_layout("auto", busy) == "review"

    def test_custom_window(self, quiet: DashboardData) -> None:
        rules = AutoRules(review_window="fri 20-22")
        friday = SUNDAY - timedelta(days=2)
        assert resolve_layout("auto", at(quiet, friday.replace(hour=20)), rules) == "review"
        assert resolve_layout("auto", at(quiet, friday.replace(hour=22)), rules) == "classic"
        assert resolve_layout("auto", at(quiet, SUNDAY.replace(hour=19)), rules) == "classic"

    def test_empty_window_disables_the_rule(self, quiet: DashboardData) -> None:
        rules = AutoRules(review_window="")
        assert resolve_layout("auto", at(quiet, SUNDAY.replace(hour=19)), rules) == "classic"


class TestAgendaRule:
    def test_agenda_in_the_morning_with_a_session_ahead(self, quiet: DashboardData) -> None:
        morning = at(quiet, quiet.now.replace(hour=8, minute=0))
        assert (
            resolve_layout("auto", planned(morning, timedelta(hours=2), timedelta(hours=1)))
            == "agenda"
        )
        running = planned(morning, timedelta(minutes=-10), timedelta(hours=1))
        assert resolve_layout("auto", running) == "agenda"

    def test_window_boundaries(self, quiet: DashboardData) -> None:
        for hour, minute, expected in (
            (5, 59, "classic"),
            (6, 0, "agenda"),
            (11, 59, "agenda"),
            (12, 0, "classic"),
        ):
            moment = at(quiet, quiet.now.replace(hour=hour, minute=minute))
            assert (
                resolve_layout("auto", planned(moment, timedelta(hours=1), timedelta(hours=1)))
                == expected
            )

    def test_no_agenda_without_a_session_still_ahead(self, quiet: DashboardData) -> None:
        morning = at(quiet, quiet.now.replace(hour=8, minute=0))
        assert resolve_layout("auto", morning) == "classic"
        over = planned(morning, timedelta(hours=-2), timedelta(hours=1))
        assert resolve_layout("auto", over) == "classic"

    def test_next_agenda_item_is_the_first_still_ahead(self, data: DashboardData) -> None:
        # The sample: 08:00-09:30 done, 16:00-17:30 running at 16:45, two more later.
        assert data.next_agenda_item is not None
        assert data.next_agenda_item.is_running_now
        assert data.next_agenda_item.course_name == "Betriebssysteme"
        assert replace(data, agenda=()).next_agenda_item is None

    def test_focus_and_exam_beat_the_agenda(self, data: DashboardData) -> None:
        morning = planned(
            at(data, data.now.replace(hour=8)), timedelta(hours=1), timedelta(hours=1)
        )
        assert morning.timer is not None and morning.timer.is_running
        assert resolve_layout("auto", morning) == "focus"
        assert resolve_layout("auto", with_goal(morning, 1)) == "exam"
        assert resolve_layout("auto", replace(morning, timer=None)) == "agenda"

    def test_custom_and_disabled_window(self, quiet: DashboardData) -> None:
        afternoon = planned(quiet, timedelta(hours=1), timedelta(hours=1))  # 16:45
        assert resolve_layout("auto", afternoon) == "classic"
        assert resolve_layout("auto", afternoon, AutoRules(agenda_window="06-22")) == "agenda"
        assert resolve_layout("auto", afternoon, AutoRules(agenda_window="")) == "classic"


class TestRulesFromSettings:
    def test_defaults_match(self) -> None:
        settings = Settings(studylife_base_url="https://studylife.test")  # type: ignore[arg-type]
        assert rules_from_settings(settings) == DEFAULT_RULES
        assert AutoRules("sun 18-24", "06-12") == DEFAULT_RULES

    def test_reads_the_two_windows(self) -> None:
        settings = Settings(
            studylife_base_url="https://studylife.test",  # type: ignore[arg-type]
            display_auto_review="sat 20-23",
            display_auto_agenda="",
        )
        assert rules_from_settings(settings) == AutoRules("sat 20-23", "")


class TestConcreteKeys:
    @pytest.mark.parametrize("key", sorted(LAYOUTS))
    def test_a_concrete_key_returns_itself(self, data: DashboardData, key: str) -> None:
        assert resolve_layout(key, with_goal(at(data, SUNDAY.replace(hour=19)), 0)) == key

    def test_unknown_key_raises(self, data: DashboardData) -> None:
        with pytest.raises(ValueError):
            resolve_layout("holographic", data)
