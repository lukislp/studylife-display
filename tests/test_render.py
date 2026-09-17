from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from PIL import Image, ImageChops

from studylife_display.model import DashboardData, build_dashboard
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


class TestGoldens:
    @pytest.mark.parametrize("language", ["de", "en"])
    def test_matches_golden(self, data: DashboardData, language: str, update_goldens: bool) -> None:
        image = render(data, language)
        golden_path = GOLDEN_DIR / f"dashboard_{language}.png"
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
