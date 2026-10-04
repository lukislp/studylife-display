"""The week calendar layout: where a session lands (column and rows, computed from the
geometry and checked on the rendered pixels), the now-line, the exam markers, the footer
texts, the empty and unavailable states and the pane form in the duo."""

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from studylife_display.config import CONCRETE_LAYOUTS, LAYOUT_CHOICES
from studylife_display.layouts import LAYOUTS, calendar
from studylife_display.layouts.calendar import FRAME, block_box, marker_box, now_y, pane_geometry
from studylife_display.layouts.common import HEIGHT, MARGIN, WIDTH
from studylife_display.layouts.common import TEXT as COMMON_TEXT
from studylife_display.layouts.duo import render_pair
from studylife_display.layouts.panes import DIVIDER_X, PANES, pane_boxes
from studylife_display.model import AgendaItem, DashboardData, build_dashboard
from studylife_display.sample import sample_extras

DE = calendar.TEXT["de"]
EN = calendar.TEXT["en"]
COMMON_DE = COMMON_TEXT["de"]
COMMON_EN = COMMON_TEXT["en"]
# FIXED_NOW is Thursday 2026-09-17 16:45, so Monday is the 14th.
MONDAY = datetime(2026, 9, 14)


@pytest.fixture
def data(sample: Any, fixed_now: datetime, tz: ZoneInfo) -> DashboardData:
    metrics, history, timer, sessions = sample
    goals, achievements, notes = sample_extras(fixed_now, tz)
    return build_dashboard(
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


def item(
    tz: ZoneInfo,
    weekday: int,
    start: tuple[int, int],
    end: tuple[int, int],
    completed: bool = False,
    end_day_offset: int = 0,
) -> AgendaItem:
    day = MONDAY + timedelta(days=weekday)
    first = day.replace(hour=start[0], minute=start[1], tzinfo=tz)
    last = (day + timedelta(days=end_day_offset)).replace(hour=end[0], minute=end[1], tzinfo=tz)
    return AgendaItem(first, last, "Algebra", None, completed, False)


def only(data: DashboardData, *items: AgendaItem) -> DashboardData:
    """A week with just these sessions and no exam."""
    return replace(data, week_calendar=tuple(items), week_goal_days=(), unavailable=frozenset())


def render(data: DashboardData, language: str = "de") -> Image.Image:
    return LAYOUTS["calendar"].render(data, language)


def black_fraction(image: Image.Image, box: tuple[int, int, int, int]) -> float:
    region = image.crop(box).convert("L")
    return region.histogram()[0] / (region.width * region.height)


def inner(box: tuple[int, int, int, int], by: int = 3) -> tuple[int, int, int, int]:
    left, top, right, bottom = box
    return left + by, top + by, right - by, bottom - by


class TestRegistration:
    def test_is_a_choice_a_layout_and_a_pane(self) -> None:
        assert "calendar" in LAYOUT_CHOICES
        assert "calendar" in CONCRETE_LAYOUTS
        assert "calendar" in LAYOUTS
        assert "calendar" in PANES

    def test_names(self) -> None:
        assert LAYOUTS["calendar"].name == {"de": "Wochenkalender", "en": "Week calendar"}

    def test_text_tables_have_the_same_keys(self) -> None:
        assert set(DE) == set(EN)


class TestGeometry:
    def test_the_grid_fits_between_the_header_and_the_footer(self) -> None:
        assert FRAME.grid_top > FRAME.marker_top + FRAME.marker_height
        assert FRAME.grid_bottom < calendar.FOOTER_RULE_Y
        assert FRAME.grid_right <= WIDTH - MARGIN

    def test_a_session_lands_in_its_column_and_rows(
        self, data: DashboardData, tz: ZoneInfo
    ) -> None:
        # Monday 09:00-11:00: the column is the first, the rows start three hours and end
        # five hours under the 06:00 line.
        monday = item(tz, 0, (9, 0), (11, 0), completed=True)
        box = block_box(monday, data, FRAME)
        assert box == (
            FRAME.grid_left + FRAME.block_inset,
            FRAME.grid_top + 3 * FRAME.hour_height,
            FRAME.grid_left + FRAME.col_width - FRAME.block_inset,
            FRAME.grid_top + 5 * FRAME.hour_height,
        )
        image = render(only(data, monday))
        assert black_fraction(image, box) == 1.0
        # The same rows in Wednesday's column hold nothing but the dotted grid.
        wednesday = (box[0] + 2 * FRAME.col_width, box[1], box[2] + 2 * FRAME.col_width, box[3])
        assert black_fraction(image, wednesday) < 0.1
        # And above and below the block in its own column.
        assert black_fraction(image, (box[0], box[1] - 12, box[2], box[1] - 3)) < 0.1
        assert black_fraction(image, (box[0], box[3] + 3, box[2], box[3] + 12)) < 0.1

    def test_each_weekday_has_its_own_column(self, data: DashboardData, tz: ZoneInfo) -> None:
        for weekday in range(7):
            block = block_box(item(tz, weekday, (8, 0), (10, 0), completed=True), data, FRAME)
            assert block[0] == FRAME.column_left(weekday) + FRAME.block_inset
            assert block[2] == FRAME.column_left(weekday + 1) - FRAME.block_inset

    def test_planned_is_hatched_and_outlined_completed_is_solid(
        self, data: DashboardData, tz: ZoneInfo
    ) -> None:
        planned = item(tz, 1, (9, 0), (12, 0))
        done = item(tz, 2, (9, 0), (12, 0), completed=True)
        image = render(only(data, planned, done))
        planned_box = block_box(planned, data, FRAME)
        done_box = block_box(done, data, FRAME)
        hatch = black_fraction(image, inner(planned_box, 3))
        assert 0.02 < hatch < 0.2
        assert black_fraction(image, inner(done_box, 1)) == 1.0
        # The outline of the planned block is solid along its top edge.
        left, top, right, _ = planned_box
        assert black_fraction(image, (left, top, right, top + 1)) == 1.0

    def test_sessions_outside_the_grid_hours_are_clamped_to_the_edges(
        self, data: DashboardData, tz: ZoneInfo
    ) -> None:
        early = block_box(item(tz, 0, (4, 30), (6, 30)), data, FRAME)
        assert early[1] == FRAME.grid_top
        assert early[3] == FRAME.y_at(6.5)
        late = block_box(item(tz, 0, (21, 0), (23, 30)), data, FRAME)
        assert late[3] == FRAME.grid_bottom
        night = block_box(item(tz, 0, (22, 30), (23, 30)), data, FRAME)
        assert night[3] == FRAME.grid_bottom
        assert night[3] - night[1] == calendar.MIN_BLOCK_HEIGHT
        dawn = block_box(item(tz, 0, (3, 0), (4, 0)), data, FRAME)
        assert dawn[1] == FRAME.grid_top
        assert dawn[3] - dawn[1] == calendar.MIN_BLOCK_HEIGHT
        past_midnight = block_box(item(tz, 0, (21, 0), (1, 0), end_day_offset=1), data, FRAME)
        assert past_midnight[3] == FRAME.grid_bottom
        assert past_midnight[0] == FRAME.column_left(0) + FRAME.block_inset  # stays in Monday

    def test_a_very_short_session_keeps_a_minimum_height(
        self, data: DashboardData, tz: ZoneInfo
    ) -> None:
        box = block_box(item(tz, 3, (10, 0), (10, 5)), data, FRAME)
        assert box[3] - box[1] == calendar.MIN_BLOCK_HEIGHT


class TestHeaderAndNow:
    def test_todays_header_is_inverted(self, data: DashboardData) -> None:
        image = render(only(data))
        thursday = FRAME.column_left(3)
        today = (
            thursday + 2,
            FRAME.header_top,
            thursday + FRAME.col_width - 2,
            FRAME.header_top + FRAME.header_height,
        )
        wednesday = FRAME.column_left(2)
        other = (
            wednesday + 2,
            FRAME.header_top,
            wednesday + FRAME.col_width - 2,
            FRAME.header_top + FRAME.header_height,
        )
        assert black_fraction(image, today) > 0.5
        assert black_fraction(image, other) < 0.3

    def test_now_line_in_todays_column_only(self, data: DashboardData) -> None:
        image = render(only(data))
        y = now_y(data, FRAME)
        assert y is not None
        assert y == FRAME.y_at(16.75)
        left = FRAME.column_left(3)
        assert black_fraction(image, (left + 4, y - 1, left + FRAME.col_width - 4, y + 2)) == 1.0
        # Not in the neighbouring columns.
        right = FRAME.column_left(4)
        assert black_fraction(image, (right + 4, y - 1, right + FRAME.col_width - 4, y + 2)) < 0.5

    def test_the_line_stays_visible_over_a_solid_block(
        self, data: DashboardData, tz: ZoneInfo
    ) -> None:
        running = item(tz, 3, (16, 0), (17, 30), completed=True)
        image = render(only(data, running))
        y = now_y(data, FRAME)
        assert y is not None
        left = FRAME.column_left(3)
        # White halo above and below the black line inside the black block.
        assert black_fraction(image, (left + 8, y - 2, left + 40, y - 1)) == 0.0
        assert black_fraction(image, (left + 8, y + 2, left + 40, y + 3)) == 0.0
        assert black_fraction(image, (left + 8, y - 1, left + 40, y + 2)) == 1.0

    def test_no_line_outside_the_grid_hours(self, data: DashboardData) -> None:
        for hour in (5, 23):
            late = replace(only(data), now=data.now.replace(hour=hour, minute=30))
            assert now_y(late, FRAME) is None
        assert now_y(only(data), FRAME) is not None


class TestExamMarkers:
    def test_marker_under_the_header_of_the_exam_day(self, data: DashboardData) -> None:
        exam = replace(only(data), week_goal_days=((4, "Betriebssysteme"),))
        image = render(exam)
        assert black_fraction(image, marker_box(4, FRAME)) > 0.5
        for other in (0, 3, 5):
            assert black_fraction(image, marker_box(other, FRAME)) == 0.0

    def test_no_marker_without_an_exam(self, data: DashboardData) -> None:
        image = render(only(data))
        row = (
            FRAME.grid_left,
            FRAME.marker_top,
            FRAME.grid_right,
            FRAME.marker_top + FRAME.marker_height,
        )
        assert black_fraction(image, row) == 0.0

    def test_two_exams_on_one_day_share_a_marker(self, data: DashboardData) -> None:
        both = replace(only(data), week_goal_days=((2, "A"), (2, "B")))
        assert black_fraction(render(both), marker_box(2, FRAME)) > 0.5

    def test_the_footer_lists_the_exams(self, data: DashboardData) -> None:
        exam = replace(only(data), week_goal_days=((4, "Betriebssysteme"),))
        assert calendar.exams_line(exam, COMMON_DE, DE) == "Prüfung: Betriebssysteme Fr 18.09."
        assert calendar.exams_line(exam, COMMON_EN, EN) == "Exam: Betriebssysteme Fri 18 Sep"
        two = replace(only(data), week_goal_days=((4, "A"), (6, "B")))
        assert calendar.exams_line(two, COMMON_DE, DE) == "Prüfung: A Fr 18.09., B So 20.09."
        assert calendar.exams_line(only(data), COMMON_DE, DE) == ""
        right = (500, calendar.FOOTER_RULE_Y + 6, WIDTH - MARGIN, HEIGHT - 6)
        assert black_fraction(render(exam), right) > 0.0
        assert black_fraction(render(only(data)), right) == 0.0


class TestFooterAndStates:
    def test_summary_counts_sessions_and_sums_the_hours(self, data: DashboardData) -> None:
        # Sample week: 08:00-09:30, 16:00-17:30, 18:00-19:00, 20:00-21:00 on Thursday and
        # 09:00-10:30, 13:00-14:00, 18:00-19:00 on Friday.
        assert len(data.week_calendar) == 7
        assert calendar.summary_line(data, COMMON_DE, DE) == "7 Sessions · 8,5 h geplant"
        assert calendar.summary_line(data, COMMON_EN, EN) == "7 sessions · 8.5 h planned"

    def test_one_session(self, data: DashboardData, tz: ZoneInfo) -> None:
        one = only(data, item(tz, 0, (9, 0), (10, 0)))
        assert calendar.summary_line(one, COMMON_DE, DE) == "1 Session · 1 h geplant"
        assert calendar.summary_line(one, COMMON_EN, EN) == "1 session · 1 h planned"

    def test_empty_week_still_draws_the_grid(self, data: DashboardData) -> None:
        empty = only(data)
        assert calendar.summary_line(empty, COMMON_DE, DE) == "keine Sessions diese Woche"
        assert calendar.summary_line(empty, COMMON_EN, EN) == "no sessions this week"
        image = render(empty)
        grid = (FRAME.grid_left, FRAME.grid_top, FRAME.grid_right, FRAME.grid_bottom)
        assert 0.0 < black_fraction(image, grid) < 0.1
        assert render(empty, "en").size == (WIDTH, HEIGHT)

    def test_unavailable_sessions_name_the_scope(self, data: DashboardData) -> None:
        missing = replace(only(data), unavailable=frozenset({"sessions"}))
        assert (
            calendar.summary_line(missing, COMMON_DE, DE) == "Schlüssel ohne Scope Sessions.GetAll"
        )
        assert (
            calendar.summary_line(missing, COMMON_EN, EN) == "key lacks the scope Sessions.GetAll"
        )
        footer = (MARGIN, calendar.FOOTER_RULE_Y + 6, WIDTH - MARGIN, HEIGHT - 6)
        differs = (
            render(missing).convert("L").tobytes() != render(only(data)).convert("L").tobytes()
        )
        assert differs
        assert black_fraction(render(missing), footer) > 0.0

    def test_full_frame_in_both_languages(self, data: DashboardData) -> None:
        for language in ("de", "en"):
            image = render(data, language)
            assert image.size == (WIDTH, HEIGHT)
            assert image.mode == "1"

    def test_the_axis_is_labelled_every_four_hours(self, data: DashboardData) -> None:
        image = render(only(data))
        for hour in (6, 10, 14, 18, 22):
            y = FRAME.y_at(hour)
            assert black_fraction(image, (MARGIN, y - 6, FRAME.grid_left - 4, y + 7)) > 0.05
        # Nothing in between.
        y = FRAME.y_at(8)
        assert black_fraction(image, (MARGIN, y - 6, FRAME.grid_left - 4, y + 7)) == 0.0


class TestPane:
    @pytest.mark.parametrize("pair", [("calendar", "focus"), ("agenda", "calendar")])
    def test_pairs_render_and_the_calendar_pane_draws(
        self, data: DashboardData, pair: tuple[str, str]
    ) -> None:
        for language in ("de", "en"):
            image = render_pair(data, language, pair)
            assert image.size == (WIDTH, HEIGHT)
            assert black_fraction(image, pane_boxes()[pair.index("calendar")]) > 0.03

    def test_the_pane_grid_stays_inside_its_box(self) -> None:
        for box in pane_boxes():
            geometry = pane_geometry(box)
            left, top, right, bottom = box
            assert geometry.axis_left >= left
            assert geometry.grid_right <= right
            assert geometry.grid_bottom <= bottom
            assert geometry.col_width >= 40

    def test_a_session_lands_in_the_pane_column(self, data: DashboardData, tz: ZoneInfo) -> None:
        box = pane_boxes()[0]
        geometry = pane_geometry(box)
        friday = item(tz, 4, (10, 0), (12, 0), completed=True)
        block = block_box(friday, data, geometry)
        image = render_pair(only(data, friday), "de", ("calendar", "focus"))
        assert black_fraction(image, inner(block, 1)) == 1.0
        assert block[0] > geometry.column_left(4)
        assert block[2] < geometry.column_left(5)

    def test_no_footer_and_no_spill_into_the_gutter(self, data: DashboardData) -> None:
        image = render_pair(data, "de", ("calendar", "focus"))
        left, top, right, bottom = pane_boxes()[0]
        assert black_fraction(image, (right + 2, top, DIVIDER_X - 2, bottom)) == 0.0
        assert black_fraction(image, (MARGIN, bottom + 2, DIVIDER_X - 2, HEIGHT)) == 0.0
