from dataclasses import replace
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from studylife_display.layouts import LAYOUTS
from studylife_display.layouts.auto import EXAM_SOON_DAYS, resolve_layout
from studylife_display.model import DashboardData, NextGoal, TimerInfo, build_dashboard


@pytest.fixture
def data(sample: Any, fixed_now: datetime, tz: ZoneInfo) -> DashboardData:
    """Sample data: timer running, next goal in 9 days."""
    metrics, history, timer = sample
    return build_dashboard(metrics, history, timer, fixed_now, tz)


def with_goal(data: DashboardData, days_left: int) -> DashboardData:
    assert data.next_goal is not None
    return replace(data, next_goal=replace(data.next_goal, days_left=days_left))


class TestAutoRules:
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

    def test_stopped_timer_does_not_count(self, data: DashboardData) -> None:
        stopped = TimerInfo(is_running=False, is_break=False, phase_ends_at=None)
        assert resolve_layout("auto", replace(data, next_goal=None, timer=stopped)) == "classic"

    def test_classic_otherwise(self, data: DashboardData) -> None:
        quiet = replace(data, timer=None, next_goal=None)
        assert resolve_layout("auto", quiet) == "classic"
        far = replace(data, timer=None, next_goal=NextGoal("Algebra", 30, None))
        assert resolve_layout("auto", far) == "classic"


class TestConcreteKeys:
    @pytest.mark.parametrize("key", sorted(LAYOUTS))
    def test_a_concrete_key_returns_itself(self, data: DashboardData, key: str) -> None:
        assert resolve_layout(key, with_goal(data, 0)) == key

    def test_unknown_key_raises(self, data: DashboardData) -> None:
        with pytest.raises(ValueError):
            resolve_layout("holographic", data)
