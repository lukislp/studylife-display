"""The time-centred layouts (today, timer, month, quiet) and the panes of focus, classic,
week and milestone: full frames in both languages, their empty and alternative states, one
cheap geometry check each, and the duo pairs."""

from dataclasses import replace
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from PIL import Image, ImageChops

from studylife_display.layouts import LAYOUTS, month, quiet, timer, today
from studylife_display.layouts.common import HEIGHT, MARGIN, WIDTH
from studylife_display.layouts.common import TEXT as COMMON_TEXT
from studylife_display.layouts.duo import render_pair
from studylife_display.layouts.panes import pane_boxes
from studylife_display.model import (
    DashboardData,
    MonthComparison,
    TimerInfo,
    TodayStats,
    WeekQuota,
    build_dashboard,
)
from studylife_display.sample import sample_extras

DE = COMMON_TEXT["de"]
EN = COMMON_TEXT["en"]


@pytest.fixture
def data(sample: Any, fixed_now: datetime, tz: ZoneInfo) -> DashboardData:
    metrics, history, timer_payload, sessions = sample
    goals, achievements, notes = sample_extras(fixed_now, tz)
    return build_dashboard(
        metrics,
        history,
        timer_payload,
        fixed_now,
        tz,
        sessions=sessions,
        goals=goals,
        achievements_payload=achievements,
        notes_payload=notes,
    )


def render(data: DashboardData, language: str, layout: str) -> Image.Image:
    return LAYOUTS[layout].render(data, language)


def black_fraction(image: Image.Image, box: tuple[int, int, int, int]) -> float:
    region = image.crop(box).convert("L")
    black = region.histogram()[0]
    return black / (region.width * region.height)


def differing_fraction(a: Image.Image, b: Image.Image) -> float:
    diff = ImageChops.difference(a.convert("L"), b.convert("L"))
    differing = sum(diff.histogram()[1:])
    return differing / (a.width * a.height)


def assert_full_frame(image: Image.Image) -> None:
    assert image.size == (WIDTH, HEIGHT)
    assert image.mode == "1"


class TestToday:
    def test_renders_in_both_languages(self, data: DashboardData) -> None:
        for language in ("de", "en"):
            assert_full_frame(render(data, language, "today"))

    def test_hero_number_is_drawn(self, data: DashboardData) -> None:
        image = render(data, "de", "today")
        assert black_fraction(image, today.HERO_BOX) > 0.1
        # A two-digit hour still fits between the margins.
        wide = render(replace(data, today_hours=10.5), "de", "today")
        assert black_fraction(wide, today.HERO_BOX) > 0.1
        assert black_fraction(wide, (0, 60, MARGIN - 4, HEIGHT - 60)) == 0.0

    def test_target_line_states(self, data: DashboardData) -> None:
        # Sample: 3:45 today against a 15 h/week minimum (2.14 h/day) -> reached.
        reached = render(data, "de", "today")
        left = render(replace(data, today_hours=0.5), "de", "today")
        none = render(replace(data, week_quota=WeekQuota(0.0, 0.0, 0.0, 0.0)), "de", "today")
        assert black_fraction(reached, today.TARGET_BOX) > 0.0
        assert black_fraction(left, today.TARGET_BOX) > 0.0
        assert black_fraction(none, today.TARGET_BOX) == 0.0
        assert differing_fraction(reached, left) > 0.0

    def test_target_line_text(self, data: DashboardData) -> None:
        t = today.TEXT["de"]
        assert today.format_target_line(data, t, DE) == "Tagesziel erreicht"
        half = replace(data, today_hours=0.5)
        assert today.format_target_line(half, t, DE) == "noch 1,6 h bis zum Tagesziel"
        assert today.format_target_line(half, today.TEXT["en"], EN) == "1.6 h to the daily target"
        no_target = replace(data, week_quota=WeekQuota(0.0, 0.0, 0.0, 0.0))
        assert today.format_target_line(no_target, t, DE) == ""

    def test_sessions_and_footer_text(self, data: DashboardData) -> None:
        assert today.format_sessions(1, today.TEXT["de"]) == "1 Session"
        assert today.format_sessions(3, today.TEXT["en"]) == "3 sessions"
        assert today.format_week_quota_line(data, DE) == "WOCHENZIEL 12,5 h von 15–20 h"

    def test_without_sessions_today(self, data: DashboardData) -> None:
        bare = replace(data, today_hours=0.0, today_stats=TodayStats(0, 0.0, 0.0, None, None))
        image = render(bare, "en", "today")
        assert_full_frame(image)
        assert differing_fraction(image, render(data, "en", "today")) > 0.0


class TestTimer:
    def test_renders_in_both_languages(self, data: DashboardData) -> None:
        for language in ("de", "en"):
            assert_full_frame(render(data, language, "timer"))

    def test_hero_with_and_without_a_timer(self, data: DashboardData) -> None:
        assert data.timer is not None and data.timer.is_running
        running = render(data, "de", "timer")
        stopped = render(replace(data, timer=None), "de", "timer")
        assert black_fraction(running, timer.HERO_BOX) > 0.1
        assert black_fraction(stopped, timer.HERO_BOX) > 0.1
        assert differing_fraction(running, stopped) > 0.0
        # The hero stays left of the column rule.
        rule_gap = (timer.HERO_RIGHT + 2, 70, timer.COLUMN_RULE_X - 1, timer.FOOTER_RULE_Y - 20)
        assert black_fraction(running, rule_gap) == 0.0
        assert black_fraction(stopped, rule_gap) == 0.0

    def test_timer_without_an_end_time_renders(self, data: DashboardData) -> None:
        open_ended = replace(data, timer=TimerInfo(True, True, None, 1))
        assert_full_frame(render(open_ended, "en", "timer"))

    def test_tally_text(self, data: DashboardData) -> None:
        t = timer.TEXT["de"]
        rows = timer.tally_rows(data, t)
        assert [label for label, _ in rows] == [
            "SESSIONS HEUTE",
            "GELERNT",
            "LÄNGSTE SESSION",
            "ERSTE – LETZTE",
        ]
        # Sample: one 3:45 session today starting at 09:00.
        assert rows[0][1] == "1"
        assert rows[1][1] == "3:45 h"
        assert rows[3][1] == "09:00–12:45"
        empty = TodayStats(0, 0.0, 0.0, None, None)
        assert timer.format_span(empty, data, t) == "–"
        bare = render(replace(data, today_stats=empty), "de", "timer")
        assert differing_fraction(bare, render(data, "de", "timer")) > 0.0


class TestMonth:
    def test_renders_in_both_languages(self, data: DashboardData) -> None:
        for language in ("de", "en"):
            assert_full_frame(render(data, language, "month"))

    def test_quota_bar_is_filled_to_the_hours(self, data: DashboardData) -> None:
        image = render(data, "de", "month")
        left, top, right, bottom = month.QUOTA_BAR_BOX
        fraction = data.month_quota.hours / data.month_quota.target_max
        filled = (left + 4, top + 4, left + int((right - left) * fraction) - 8, bottom - 4)
        empty = (left + int((right - left) * fraction) + 8, top + 4, right - 4, bottom - 4)
        assert black_fraction(image, filled) > 0.95
        assert black_fraction(image, empty) < 0.05

    def test_day_strip_follows_the_month(self, data: DashboardData) -> None:
        assert len(data.month_days) == 30 and sum(data.month_days) > 0
        image = render(data, "de", "month")
        assert black_fraction(image, month.STRIP_BOX) > 0.02
        quiet_month = replace(data, month_days=(0.0,) * 30)
        assert black_fraction(render(quiet_month, "de", "month"), month.STRIP_BOX) < 0.02
        assert_full_frame(render(replace(data, month_days=()), "en", "month"))

    def test_days_left_and_per_day(self, data: DashboardData) -> None:
        t = month.TEXT["de"]
        # 17 September 2026: 14 days left including today; 19 h missing -> 1.36 h/day.
        assert month.days_left(data) == 14
        assert month.per_day_line(data, t, DE) == "noch 1,4 h/Tag für das Minimum"
        reached = replace(data, month_quota=WeekQuota(62.0, 60.0, 80.0, 77.5))
        assert month.per_day_line(reached, t, DE) == "Minimum erreicht"
        assert month.per_day_line(reached, month.TEXT["en"], EN) == "minimum reached"
        assert differing_fraction(render(reached, "de", "month"), render(data, "de", "month")) > 0

    def test_title_and_comparison_text(self, data: DashboardData) -> None:
        assert month.month_title(data, month.TEXT["de"], DE) == "MONATSZIEL · September 2026"
        assert month.month_title(data, month.TEXT["en"], EN) == "MONTH TARGET · September 2026"
        t = month.TEXT["de"]
        assert (
            month.comparison_line(data, t, DE) == "Vormonat 52,5 h · −11,5 h · Vorjahr 36 h · +5 h"
        )
        no_year = replace(
            data, month_comparison=MonthComparison(41.0, 52.5, -11.5, False, None, None)
        )
        assert month.comparison_line(no_year, t, DE) == "Vormonat 52,5 h · −11,5 h"
        assert month.format_signed_hours(0.0, t, DE) == "±0 h"
        assert month.format_signed_hours(2.5, month.TEXT["en"], EN) == "+2.5 h"
        assert differing_fraction(render(no_year, "de", "month"), render(data, "de", "month")) > 0


class TestQuiet:
    def test_renders_in_both_languages(self, data: DashboardData) -> None:
        for language in ("de", "en"):
            assert_full_frame(render(data, language, "quiet"))

    def test_no_footer_line(self, data: DashboardData) -> None:
        image = render(data, "de", "quiet")
        assert black_fraction(image, (0, quiet.TOMORROW_BOX[3] + 4, WIDTH, HEIGHT)) == 0.0

    def test_tomorrow_states(self, data: DashboardData) -> None:
        assert data.tomorrow
        filled = render(data, "de", "quiet")
        none = render(replace(data, tomorrow=()), "de", "quiet")
        unavailable = render(replace(data, unavailable=frozenset({"sessions"})), "de", "quiet")
        assert black_fraction(filled, quiet.TOMORROW_BOX) > 0.0
        assert black_fraction(none, quiet.TOMORROW_BOX) > 0.0
        assert black_fraction(unavailable, quiet.TOMORROW_BOX) == 0.0
        assert differing_fraction(filled, none) > 0.0
        assert differing_fraction(filled, unavailable) > 0.0

    def test_text_lines(self, data: DashboardData) -> None:
        assert quiet.date_line(data, DE) == "Do 17.09."
        assert quiet.date_line(data, EN) == "Thu 17 Sep"
        t = quiet.TEXT["de"]
        assert quiet.tomorrow_line(data, t, DE) == "Morgen 09:00 Betriebssysteme"
        assert quiet.tomorrow_line(replace(data, tomorrow=()), t, DE) == "morgen nichts geplant"
        hidden = replace(data, unavailable=frozenset({"sessions"}))
        assert quiet.tomorrow_line(hidden, t, DE) is None


class TestPanes:
    PAIRS = (("today", "focus"), ("timer", "classic"), ("month", "week"), ("quiet", "milestone"))

    @pytest.mark.parametrize("pair", PAIRS)
    def test_pairs_render_and_both_panes_draw(
        self, data: DashboardData, pair: tuple[str, str]
    ) -> None:
        for language in ("de", "en"):
            image = render_pair(data, language, pair)
            assert_full_frame(image)
            for box in pane_boxes():
                assert black_fraction(image, box) > 0.0

    def test_panes_in_their_alternative_states(self, data: DashboardData) -> None:
        stopped = replace(data, timer=None, today_hours=10.5)
        for pair in (("today", "focus"), ("timer", "classic")):
            image = render_pair(stopped, "de", pair)
            assert_full_frame(image)
            assert differing_fraction(image, render_pair(data, "de", pair)) > 0.0
        hidden = replace(data, unavailable=frozenset({"sessions"}), next_goal=None)
        for pair in (("quiet", "classic"), ("milestone", "week")):
            assert_full_frame(render_pair(hidden, "en", pair))


class TestText:
    @pytest.mark.parametrize("module", [today, timer, month, quiet])
    def test_language_tables_have_the_same_keys(self, module: Any) -> None:
        assert module.TEXT["de"].keys() == module.TEXT["en"].keys()
        assert not set(module.TEXT["de"]) & set(DE)
