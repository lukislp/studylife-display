"""The recap layout: the full frame in both languages, the empty state, long names, the
wording of the ended line and the footer, and the pane form in the duo."""

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from studylife_display.config import CONCRETE_LAYOUTS, LAYOUT_CHOICES
from studylife_display.layouts import LAYOUTS, recap
from studylife_display.layouts.common import HEIGHT, MARGIN, WIDTH
from studylife_display.layouts.common import TEXT as COMMON_TEXT
from studylife_display.layouts.duo import render_pair
from studylife_display.layouts.panes import DIVIDER_X, PANES, pane_boxes
from studylife_display.model import (
    DashboardData,
    FinishedSession,
    WeekQuota,
    build_dashboard,
)
from studylife_display.sample import sample_extras

DE = recap.TEXT["de"]
EN = recap.TEXT["en"]
COMMON_DE = COMMON_TEXT["de"]
COMMON_EN = COMMON_TEXT["en"]


def recent_session(now: datetime) -> FinishedSession:
    """A session of 1:26 h that ended four minutes before `now` (16:41 at FIXED_NOW)."""
    end = now - timedelta(minutes=4)
    return FinishedSession(end - timedelta(minutes=86), end, "Betriebssysteme", "Scheduling", 4)


@pytest.fixture
def data(sample: Any, fixed_now: datetime, tz: ZoneInfo) -> DashboardData:
    """The sample with a session that ended four minutes ago (the sample history itself
    ends hours earlier, which the goldens of the other layouts depend on)."""
    metrics, history, timer, sessions = sample
    goals, achievements, notes = sample_extras(fixed_now, tz)
    built = build_dashboard(
        metrics,
        history,
        timer,
        fixed_now,
        tz,
        sessions=sessions,
        goals=goals,
        achievements_payload=achievements,
        notes_payload=notes,
    )
    return replace(built, last_session=recent_session(fixed_now))


def render(data: DashboardData, language: str) -> Image.Image:
    return LAYOUTS["recap"].render(data, language)


def black_fraction(image: Image.Image, box: tuple[int, int, int, int]) -> float:
    region = image.crop(box).convert("L")
    return region.histogram()[0] / (region.width * region.height)


def differing(a: Image.Image, b: Image.Image) -> bool:
    return a.convert("L").tobytes() != b.convert("L").tobytes()


class TestRegistration:
    def test_is_a_choice_a_layout_and_a_pane(self) -> None:
        assert "recap" in LAYOUT_CHOICES
        assert "recap" in CONCRETE_LAYOUTS
        assert "recap" in LAYOUTS
        assert "recap" in PANES

    def test_names_and_description(self) -> None:
        spec = LAYOUTS["recap"]
        assert spec.name == {"de": "Sitzungs-Abschluss", "en": "Session recap"}
        assert "right after a session ends" in spec.description["en"]

    def test_text_tables_have_the_same_keys(self) -> None:
        assert set(DE) == set(EN)


class TestFullFrame:
    def test_renders_in_both_languages(self, data: DashboardData) -> None:
        for language in ("de", "en"):
            image = render(data, language)
            assert image.size == (WIDTH, HEIGHT)
            assert image.mode == "1"
        assert differing(render(data, "de"), render(data, "en"))

    def test_hero_duration_is_drawn(self, data: DashboardData) -> None:
        assert black_fraction(render(data, "de"), recap.HERO_BOX) > 0.1

    def test_empty_state_has_no_hero_but_still_the_figures(self, data: DashboardData) -> None:
        empty = render(replace(data, last_session=None), "de")
        # Only the one line of text where the big duration would stand.
        assert black_fraction(empty, recap.HERO_BOX) < black_fraction(
            render(data, "de"), recap.HERO_BOX
        )
        assert black_fraction(empty, (MARGIN, 90, WIDTH - MARGIN, 150)) == 0.0
        stats = (MARGIN, recap.STATS_LABEL_BASELINE - 20, WIDTH - MARGIN, recap.BAR_TOP)
        assert black_fraction(empty, stats) > 0.0
        assert differing(empty, render(data, "de"))
        assert render(replace(data, last_session=None), "en").size == (WIDTH, HEIGHT)

    def test_a_long_course_name_and_topic_stay_inside_the_margins(
        self, data: DashboardData
    ) -> None:
        session = replace(
            data.last_session,  # type: ignore[type-var]
            course_name="Grundlagen der Theoretischen Informatik und Berechenbarkeit " * 2,
            topic="Ein außergewöhnlich langer Themenname, der nie in eine Zeile passt " * 2,
        )
        image = render(replace(data, last_session=session), "de")
        assert (
            black_fraction(image, (WIDTH - MARGIN + 2, 60, WIDTH, recap.STATS_LABEL_BASELINE - 20))
            == 0.0
        )

    def test_course_and_topic_are_optional(self, data: DashboardData) -> None:
        assert data.last_session is not None
        bare = replace(data.last_session, course_name="", topic=None)
        for language in ("de", "en"):
            image = render(replace(data, last_session=bare), language)
            assert black_fraction(image, recap.HERO_BOX) > 0.1

    def test_a_wide_duration_still_fits(self, data: DashboardData) -> None:
        assert data.last_session is not None
        long = replace(
            data.last_session, start=data.last_session.end - timedelta(hours=11, minutes=59)
        )
        image = render(replace(data, last_session=long), "de")
        assert black_fraction(image, (WIDTH - MARGIN + 2, 60, WIDTH, HEIGHT - 60)) == 0.0

    def test_the_week_bar_follows_the_quota(self, data: DashboardData) -> None:
        empty = render(replace(data, week_quota=WeekQuota(0.0, 15.0, 20.0, 0.0)), "de")
        full = render(replace(data, week_quota=WeekQuota(20.0, 15.0, 20.0, 133.0)), "de")
        column = (WIDTH - 2 * MARGIN) // 3
        bar = (
            MARGIN + 2 * column + 4,
            recap.BAR_TOP + 3,
            MARGIN + 3 * column - 28,
            recap.BAR_TOP + recap.BAR_HEIGHT - 3,
        )
        assert black_fraction(empty, bar) == 0.0
        assert black_fraction(full, bar) == 1.0


class TestWording:
    def finished(self, minutes: int) -> FinishedSession:
        now = datetime(2026, 9, 17, 16, 45)
        return FinishedSession(now, now, "X", None, minutes)

    def test_ended_line_in_minutes(self) -> None:
        assert recap.ended_line(self.finished(4), DE) == "vor 4 min beendet"
        assert recap.ended_line(self.finished(4), EN) == "ended 4 min ago"
        assert recap.ended_line(self.finished(0), DE) == "gerade eben beendet"
        assert recap.ended_line(self.finished(59), EN) == "ended 59 min ago"

    def test_ended_line_in_hours_and_days(self) -> None:
        assert recap.ended_line(self.finished(60), DE) == "vor 1 h beendet"
        assert recap.ended_line(self.finished(4 * 60 + 15), EN) == "ended 4 h ago"
        assert recap.ended_line(self.finished(24 * 60), DE) == "vor 1 Tag beendet"
        assert recap.ended_line(self.finished(3 * 24 * 60 + 5), DE) == "vor 3 Tagen beendet"
        assert recap.ended_line(self.finished(3 * 24 * 60), EN) == "ended 3 days ago"

    def test_duration_text(self, data: DashboardData) -> None:
        assert data.last_session is not None
        assert recap.duration_text(data.last_session) == "1:26"

    def test_stat_values(self, data: DashboardData) -> None:
        today, streak, week = recap.stat_values(data, COMMON_DE)
        assert today == "3:45 h"
        assert streak == "12 Tage"
        assert week == "62 %"  # the sample percent is 62.5, rounded half to even
        _, streak_en, _ = recap.stat_values(replace(data, streak_days=1), COMMON_EN)
        assert streak_en == "1 day"

    def test_footer_names_the_next_planned_session(self, data: DashboardData) -> None:
        # Sample: Betriebssysteme 16:00-17:30 runs at 16:45 and is the next one not over.
        assert recap.footer_text(data, COMMON_DE, DE) == "Als Nächstes 16:00 Betriebssysteme"
        assert recap.footer_text(data, COMMON_EN, EN) == "Up next 16:00 Betriebssysteme"

    def test_footer_falls_back_to_the_quota_line(self, data: DashboardData) -> None:
        line = recap.footer_text(replace(data, agenda=()), COMMON_DE, DE)
        assert line == "WOCHENZIEL 12,5 h von 15–20 h"

    def test_footer_without_a_course_name(self, data: DashboardData) -> None:
        item = replace(data.agenda[2], course_name="")
        line = recap.footer_text(replace(data, agenda=(item,)), COMMON_EN, EN)
        assert line == f"Up next {item.start.strftime('%H:%M')} (no course)"


class TestPane:
    @pytest.mark.parametrize("pair", [("recap", "focus"), ("agenda", "recap")])
    def test_pairs_render_and_the_recap_pane_draws(
        self, data: DashboardData, pair: tuple[str, str]
    ) -> None:
        for language in ("de", "en"):
            image = render_pair(data, language, pair)
            assert image.size == (WIDTH, HEIGHT)
            left, top, right, bottom = pane_boxes()[pair.index("recap")]
            assert black_fraction(image, (left, top, right, bottom)) > 0.05

    def test_pane_empty_state(self, data: DashboardData) -> None:
        pair = ("recap", "focus")
        full = render_pair(data, "de", pair)
        empty = render_pair(replace(data, last_session=None), "de", pair)
        left, top, right, bottom = pane_boxes()[0]
        assert differing(full, empty)
        # Only the one line "noch keine Sitzung" is left in the pane.
        assert 0.0 < black_fraction(empty, (left, top, right, bottom)) < 0.05

    def test_pane_with_a_long_course_name_stays_in_its_box(self, data: DashboardData) -> None:
        assert data.last_session is not None
        long = replace(data.last_session, course_name="Theoretische Informatik und mehr " * 4)
        image = render_pair(replace(data, last_session=long), "en", ("recap", "focus"))
        left, top, right, bottom = pane_boxes()[0]
        gutter = (right + 2, top, DIVIDER_X - 2, bottom)
        assert black_fraction(image, gutter) == 0.0
