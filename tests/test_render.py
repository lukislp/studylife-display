from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from PIL import Image, ImageChops

from studylife_display.layouts import LAYOUTS
from studylife_display.layouts.classic import HEATMAP_BOX
from studylife_display.layouts.common import format_minutes_seconds
from studylife_display.layouts.exam import HERO_BOX
from studylife_display.layouts.focus import remaining_seconds
from studylife_display.layouts.semester import ECTS_BAR_BOX
from studylife_display.layouts.week import QUOTA_BAR_BOX
from studylife_display.model import DashboardData, Forecast, TimerInfo, build_dashboard
from studylife_display.render import (
    COUNTDOWN_BOX,
    HEIGHT,
    TEXT,
    WIDTH,
    format_decimal,
    format_hours_clock,
    heatmap_level,
    load_fonts,
    render,
    text_width,
)

GOLDEN_DIR = Path(__file__).parent / "golden"
# Fraction of pixels allowed to differ (FreeType hinting differences between builds).
# FreeType rasterises the vendored fonts slightly differently per platform/version (the goldens
# were written on Windows, CI runs Ubuntu: ~1.3% of pixels differ along glyph edges). A layout
# regression moves whole blocks and lands far above this; anti-aliasing noise stays well below.
GOLDEN_TOLERANCE = 0.03


@pytest.fixture
def data(sample: Any, fixed_now: datetime, tz: ZoneInfo) -> DashboardData:
    metrics, history, timer = sample
    return build_dashboard(metrics, history, timer, fixed_now, tz)


def black_fraction(image: Image.Image, box: tuple[int, int, int, int]) -> float:
    region = image.crop(box).convert("L")
    histogram = region.histogram()
    black = histogram[0]
    return black / (region.width * region.height)


def differing_fraction(a: Image.Image, b: Image.Image) -> float:
    diff = ImageChops.difference(a.convert("L"), b.convert("L"))
    differing = sum(diff.histogram()[1:])
    return differing / (a.width * a.height)


class TestFrame:
    def test_size_and_mode(self, data: DashboardData) -> None:
        image = render(data, "de")
        assert image.size == (WIDTH, HEIGHT)
        assert image.mode == "1"

    def test_countdown_block_is_inverted(self, data: DashboardData) -> None:
        image = render(data, "de")
        assert black_fraction(image, COUNTDOWN_BOX) > 0.5

    def test_no_goal_shows_placeholder_instead_of_the_block(self, data: DashboardData) -> None:
        image = render(replace(data, next_goal=None), "de")
        assert black_fraction(image, COUNTDOWN_BOX) < 0.3

    def test_timer_line_only_when_running(self, data: DashboardData) -> None:
        with_timer = render(data, "de")
        without = render(replace(data, timer=None), "de")
        bottom = (0, HEIGHT - 50, WIDTH, HEIGHT)
        assert black_fraction(with_timer, bottom) > black_fraction(without, bottom)
        assert black_fraction(without, bottom) == 0.0

    def test_stale_marker_widens_the_header_right_text(self, data: DashboardData) -> None:
        fresh = render(data, "de")
        stale = render(replace(data, stale_minutes=35), "de")
        header_right = (WIDTH // 2, 0, WIDTH, 50)
        assert black_fraction(stale, header_right) > black_fraction(fresh, header_right)

    def test_english_renders_too(self, data: DashboardData) -> None:
        image = render(data, "en")
        assert image.size == (WIDTH, HEIGHT)

    def test_wide_number_stays_left_of_the_right_column(self, data: DashboardData) -> None:
        image = render(replace(data, today_hours=10.5), "de")
        # Nothing but white in the gap right before the streak label column.
        assert black_fraction(image, (416, 60, 428, 200)) == 0.0

    def test_language_tables_have_the_same_keys(self) -> None:
        assert set(TEXT["de"]) == set(TEXT["en"])


class TestLayouts:
    @pytest.mark.parametrize("layout", sorted(LAYOUTS))
    def test_every_layout_is_a_full_frame_in_both_languages(
        self, data: DashboardData, layout: str
    ) -> None:
        for language in ("de", "en"):
            image = render(data, language, layout)
            assert image.size == (WIDTH, HEIGHT)
            assert image.mode == "1"

    def test_unknown_layout_raises(self, data: DashboardData) -> None:
        with pytest.raises(ValueError):
            render(data, "de", "holographic")

    def test_default_layout_is_classic(self, data: DashboardData) -> None:
        assert differing_fraction(render(data, "de"), render(data, "de", "classic")) == 0.0

    def test_exam_hero_block_is_inverted(self, data: DashboardData) -> None:
        assert black_fraction(render(data, "de", "exam"), HERO_BOX) > 0.5
        assert black_fraction(render(replace(data, next_goal=None), "de", "exam"), HERO_BOX) > 0.5

    def test_focus_with_a_running_timer_has_no_heatmap(self, data: DashboardData) -> None:
        assert data.timer is not None and data.timer.is_running
        assert black_fraction(render(data, "de", "classic"), HEATMAP_BOX) > 0.05
        assert black_fraction(render(data, "de", "focus"), HEATMAP_BOX) == 0.0

    def test_focus_shows_the_remaining_time_as_a_snapshot(self, data: DashboardData) -> None:
        # Sample timer: phase ends 18 minutes after `now`.
        seconds = remaining_seconds(data)
        assert seconds is not None and format_minutes_seconds(seconds) == "18:00"
        stopped = replace(data, timer=TimerInfo(False, False, None))
        assert remaining_seconds(stopped) is None
        assert render(stopped, "de", "focus").size == (WIDTH, HEIGHT)

    def test_week_quota_bar_is_filled_to_the_hours(self, data: DashboardData) -> None:
        image = render(data, "de", "week")
        left, top, right, bottom = QUOTA_BAR_BOX
        fraction = data.week_quota.hours / data.week_quota.target_max
        filled = (left + 4, top + 4, left + int((right - left) * fraction) - 8, bottom - 4)
        empty = (left + int((right - left) * fraction) + 8, top + 4, right - 4, bottom - 4)
        assert black_fraction(image, filled) > 0.95
        # Only the target ticks cross the empty part.
        assert black_fraction(image, empty) < 0.05

    def test_semester_ects_bar_is_filled_to_the_earned_fraction(self, data: DashboardData) -> None:
        image = render(data, "de", "semester")
        left, top, right, bottom = ECTS_BAR_BOX
        fraction = data.ects.earned / data.ects.total
        filled = (left + 4, top + 4, left + int((right - left) * fraction) - 8, bottom - 4)
        empty = (left + int((right - left) * fraction) + 8, top + 4, right - 4, bottom - 4)
        assert black_fraction(image, filled) > 0.95
        assert black_fraction(image, empty) == 0.0

    def test_semester_placeholders_when_the_figures_are_missing(self, data: DashboardData) -> None:
        full = render(data, "de", "semester")
        bare = replace(
            data,
            average_grade=None,
            forecast=Forecast(False, False, None, 0.0),
            neglected_course=None,
        )
        image = render(bare, "de", "semester")
        assert image.size == (WIDTH, HEIGHT)
        assert differing_fraction(full, image) > 0.0
        done = replace(data, forecast=Forecast(True, True, None, 0.0))
        assert differing_fraction(render(done, "de", "semester"), image) > 0.0

    def test_exam_lists_the_courses_with_the_most_hours_first(self, data: DashboardData) -> None:
        names = [name for name, _ in data.course_hours]
        hours = [hours for _, hours in data.course_hours]
        assert names == ["Lineare Algebra", "Datenbanken", "Betriebssysteme"]
        assert hours == sorted(hours, reverse=True)


class TestGoldens:
    @pytest.mark.parametrize(
        ("layout", "language"),
        [
            ("classic", "de"),
            ("classic", "en"),
            ("focus", "de"),
            ("exam", "de"),
            ("week", "de"),
            ("semester", "de"),
        ],
    )
    def test_matches_golden(
        self, data: DashboardData, layout: str, language: str, update_goldens: bool
    ) -> None:
        image = render(data, language, layout)
        golden_path = GOLDEN_DIR / f"{layout}_{language}.png"
        if update_goldens:
            GOLDEN_DIR.mkdir(exist_ok=True)
            image.save(golden_path)
            pytest.skip(f"golden rewritten: {golden_path}")
        assert golden_path.exists(), "run pytest --update-goldens once"
        with Image.open(golden_path) as golden:
            fraction = differing_fraction(image, golden)
        assert fraction <= GOLDEN_TOLERANCE, f"{fraction:.4%} of pixels differ"


class TestHelpers:
    def test_format_hours_clock(self) -> None:
        assert format_hours_clock(3.75) == "3:45"
        assert format_hours_clock(0) == "0:00"
        assert format_hours_clock(10.5) == "10:30"
        assert format_hours_clock(0.999) == "1:00"
        assert format_hours_clock(-1) == "0:00"

    def test_format_decimal(self) -> None:
        assert format_decimal(12.5, ",") == "12,5"
        assert format_decimal(12.5, ".") == "12.5"
        assert format_decimal(15.0, ",") == "15"
        assert format_decimal(0.0, ",") == "0"

    def test_heatmap_levels(self) -> None:
        assert heatmap_level(0.0) == 0
        assert heatmap_level(0.5) == 1
        assert heatmap_level(1.0) == 2
        assert heatmap_level(2.5) == 3
        assert heatmap_level(9.0) == 3

    def test_digits_are_tabular(self) -> None:
        fonts = load_fonts()
        assert text_width("1111", fonts.big) == text_width("0000", fonts.big)
        assert text_width("1:11", fonts.big) == text_width("0:00", fonts.big)
