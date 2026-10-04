"""The list layouts (exams, goals, tomorrow, note), their empty and scope-less states, the
note layout's word wrap, and the pane forms of the layouts that got a real one."""

from dataclasses import replace
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from PIL import Image, ImageChops

from studylife_display.layouts import exams, goals, note, tomorrow
from studylife_display.layouts.common import HEIGHT, WIDTH, load_fonts, text_width
from studylife_display.layouts.duo import render_pair
from studylife_display.layouts.panes import pane_boxes
from studylife_display.model import DashboardData, Note, build_dashboard
from studylife_display.sample import sample_extras

LAYOUTS = {
    "exams": exams.render,
    "goals": goals.render,
    "tomorrow": tomorrow.render,
    "note": note.render,
}
# Which optional payload each layout depends on (the scope hint it shows when missing).
PAYLOAD_OF = {"exams": None, "goals": "goals", "tomorrow": "sessions", "note": "notes"}


@pytest.fixture
def data(sample: Any, fixed_now: datetime, tz: ZoneInfo) -> DashboardData:
    metrics, history, timer, sessions = sample
    extra_goals, achievements, notes = sample_extras(fixed_now, tz)
    return build_dashboard(
        metrics,
        history,
        timer,
        fixed_now,
        tz,
        sessions=sessions,
        goals=extra_goals,
        achievements_payload=achievements,
        notes_payload=notes,
    )


def black_fraction(image: Image.Image, box: tuple[int, int, int, int]) -> float:
    region = image.crop(box).convert("L")
    histogram = region.histogram()
    return histogram[0] / (region.width * region.height)


def differing_fraction(a: Image.Image, b: Image.Image) -> float:
    diff = ImageChops.difference(a.convert("L"), b.convert("L"))
    differing = sum(diff.histogram()[1:])
    return differing / (a.width * a.height)


def emptied(data: DashboardData, layout: str) -> DashboardData:
    """`data` without the content the layout lists."""
    if layout == "exams":
        return replace(data, upcoming_goals=(), next_goal=None)
    if layout == "goals":
        return replace(data, goals=())
    if layout == "tomorrow":
        return replace(data, tomorrow=())
    return replace(data, notes=())


class TestFrames:
    @pytest.mark.parametrize("layout", sorted(LAYOUTS))
    @pytest.mark.parametrize("language", ["de", "en"])
    def test_full_frame_in_both_languages(
        self, data: DashboardData, layout: str, language: str
    ) -> None:
        image = LAYOUTS[layout](data, language)
        assert image.size == (WIDTH, HEIGHT)
        assert image.mode == "1"

    @pytest.mark.parametrize("layout", sorted(LAYOUTS))
    def test_empty_state_renders_and_differs(self, data: DashboardData, layout: str) -> None:
        full = LAYOUTS[layout](data, "de")
        empty = LAYOUTS[layout](emptied(data, layout), "de")
        assert empty.size == (WIDTH, HEIGHT)
        assert differing_fraction(full, empty) > 0.01
        assert LAYOUTS[layout](emptied(data, layout), "en").size == (WIDTH, HEIGHT)

    @pytest.mark.parametrize("layout", ["goals", "tomorrow", "note"])
    def test_unavailable_scope_shows_a_hint(self, data: DashboardData, layout: str) -> None:
        payload = PAYLOAD_OF[layout]
        assert payload is not None
        empty = emptied(data, layout)
        without_scope = replace(empty, unavailable=frozenset({payload}))
        hint = LAYOUTS[layout](without_scope, "de")
        assert hint.size == (WIDTH, HEIGHT)
        # The hint is a different line than the plain empty state, and both differ from full.
        assert differing_fraction(hint, LAYOUTS[layout](empty, "de")) > 0.001
        assert differing_fraction(hint, LAYOUTS[layout](data, "de")) > 0.01
        assert LAYOUTS[layout](without_scope, "en").size == (WIDTH, HEIGHT)

    @pytest.mark.parametrize("module", [exams, goals, tomorrow, note])
    def test_language_tables_have_the_same_keys(self, module: Any) -> None:
        assert module.TEXT["de"].keys() == module.TEXT["en"].keys()
        assert module.TEXT["de"]


class TestExams:
    def test_row_count_follows_upcoming_goals(self, data: DashboardData) -> None:
        assert len(data.upcoming_goals) == 3
        three = exams.render(data, "de")
        one = exams.render(replace(data, upcoming_goals=data.upcoming_goals[:1]), "de")
        # The first row carries the hero block in both frames.
        assert black_fraction(three, exams.row_box(0)) > 0.1
        assert black_fraction(one, exams.row_box(0)) > 0.1
        # Rows two and three exist only with three goals.
        assert black_fraction(three, exams.row_box(1)) > 0.05
        assert black_fraction(three, exams.row_box(2)) > 0.05
        assert black_fraction(one, exams.row_box(1)) == 0.0
        assert black_fraction(one, exams.row_box(2)) == 0.0
        assert black_fraction(three, exams.row_box(3)) == 0.0

    def test_course_hours_line(self, data: DashboardData) -> None:
        from studylife_display.layouts.common import TEXT

        goal = data.upcoming_goals[0]
        line = exams.course_hours_line(goal, data, TEXT["de"], exams.TEXT["de"])
        assert line.endswith("h · 28 d")
        unknown = replace(goal, course_name="Nie gelernt")
        assert exams.course_hours_line(unknown, data, TEXT["en"], exams.TEXT["en"]) == "0 h"


class TestGoals:
    def test_completed_group_is_drawn(self, data: DashboardData) -> None:
        open_only = tuple(goal for goal in data.goals if not goal.is_completed)
        assert 0 < len(open_only) < len(data.goals)
        full = goals.render(data, "de")
        without_done = goals.render(replace(data, goals=open_only), "de")
        # The rows under the open group are white without the completed goals.
        lower = (
            0,
            goals.ROW_TOP + len(open_only) * goals.ROW_HEIGHT,
            WIDTH,
            goals.FOOTER_RULE_Y - 2,
        )
        assert black_fraction(full, lower) > 0.01
        assert black_fraction(without_done, lower) == 0.0

    def test_counts_line(self, data: DashboardData) -> None:
        from studylife_display.layouts.common import TEXT

        assert goals.counts_line(data, TEXT["de"], goals.TEXT["de"]) == "3 offen · 2 erledigt"
        assert goals.counts_line(data, TEXT["en"], goals.TEXT["en"]) == "3 open · 2 done"

    def test_days_left_counts_from_today(self, data: DashboardData) -> None:
        first = data.goals[0]
        assert goals.days_left(first, data) == 9
        assert goals.days_left(replace(first, target_date=None), data) is None

    def test_more_than_the_row_limit_still_fits(self, data: DashboardData) -> None:
        many = data.goals * 3
        image = goals.render(replace(data, goals=many), "de")
        assert image.size == (WIDTH, HEIGHT)
        # Nothing spills over the footer rule.
        assert (
            black_fraction(image, (0, goals.FOOTER_RULE_Y - 4, WIDTH, goals.FOOTER_RULE_Y - 1))
            == 0.0
        )


class TestTomorrow:
    def test_summary_line(self, data: DashboardData) -> None:
        from studylife_display.layouts.common import TEXT

        assert len(data.tomorrow) == 3
        assert tomorrow.summary_line(data, TEXT["de"], tomorrow.TEXT["de"]) == (
            "3 Sessions · 3,5 h geplant"
        )
        assert tomorrow.summary_line(data, TEXT["en"], tomorrow.TEXT["en"]) == (
            "3 sessions · 3.5 h planned"
        )

    def test_label_names_tomorrow(self, data: DashboardData) -> None:
        from studylife_display.layouts.common import TEXT

        # FIXED_NOW is Thursday 2026-09-17.
        assert tomorrow.label_line(data, TEXT["de"], tomorrow.TEXT["de"]) == "MORGEN · Fr 18.09."
        assert tomorrow.label_line(data, TEXT["en"], tomorrow.TEXT["en"]) == "TOMORROW · Fri 18 Sep"

    def test_many_sessions_show_the_more_line(self, data: DashboardData) -> None:
        many = data.tomorrow * 4
        image = tomorrow.render(replace(data, tomorrow=many), "de")
        assert image.size == (WIDTH, HEIGHT)
        assert (
            black_fraction(
                image, (0, tomorrow.FOOTER_RULE_Y - 4, WIDTH, tomorrow.FOOTER_RULE_Y - 1)
            )
            == 0.0
        )


class TestNoteWrap:
    def test_short_text_is_one_line(self) -> None:
        fonts = load_fonts()
        assert note.wrap_lines("kurz", fonts.body, 400, 7) == ["kurz"]
        assert note.wrap_lines("", fonts.body, 400, 7) == []

    def test_long_text_is_cut_with_an_ellipsis(self) -> None:
        fonts = load_fonts()
        text = " ".join(f"Wort{index}" for index in range(200))
        lines = note.wrap_lines(text, fonts.body, 300, 3)
        assert len(lines) == 3
        assert lines[-1].endswith("…")
        assert all(text_width(line, fonts.body) <= 300 for line in lines)

    def test_fitting_text_has_no_ellipsis(self) -> None:
        fonts = load_fonts()
        text = "eins zwei drei vier fünf sechs sieben acht neun zehn"
        lines = note.wrap_lines(text, fonts.body, 200, 10)
        assert 1 < len(lines) < 10
        assert "…" not in "".join(lines)
        assert " ".join(lines) == text
        assert all(text_width(line, fonts.body) <= 200 for line in lines)

    def test_overlong_word_is_ellipsized_alone(self) -> None:
        fonts = load_fonts()
        lines = note.wrap_lines("Donaudampfschifffahrtsgesellschaftskapitän ja", fonts.body, 150, 7)
        assert lines[0].endswith("…")
        assert all(text_width(line, fonts.body) <= 150 for line in lines)

    def test_long_excerpt_fills_more_lines(self, data: DashboardData) -> None:
        first = data.notes[0]
        long_note = Note(first.title, "Lorem ipsum dolor sit amet " * 40, first.updated_at)
        short_note = Note(first.title, "Kurz.", first.updated_at)
        long_image = note.render(replace(data, notes=(long_note,) + data.notes[1:]), "de")
        short_image = note.render(replace(data, notes=(short_note,) + data.notes[1:]), "de")
        excerpt_box = (0, note.TITLE_BASELINE + 10, WIDTH, note.OTHERS_RULE_Y - 2)
        assert black_fraction(long_image, excerpt_box) > 3 * black_fraction(
            short_image, excerpt_box
        )

    def test_untitled_note_without_timestamp_renders(self, data: DashboardData) -> None:
        bare = Note("", "nur Text", None)
        image = note.render(replace(data, notes=(bare,)), "en")
        assert image.size == (WIDTH, HEIGHT)


class TestPanes:
    PAIRS = [
        ("exams", "agenda"),
        ("goals", "exam"),
        ("tomorrow", "review"),
        ("note", "exams"),
        ("agenda", "goals"),
        ("review", "tomorrow"),
        ("exam", "note"),
    ]

    @pytest.mark.parametrize("pair", PAIRS)
    @pytest.mark.parametrize("language", ["de", "en"])
    def test_every_pane_paints_inside_its_box(
        self, data: DashboardData, pair: tuple[str, str], language: str
    ) -> None:
        image = render_pair(data, language, pair)
        assert image.size == (WIDTH, HEIGHT)
        assert image.mode == "1"
        for box in pane_boxes():
            assert black_fraction(image, box) > 0.0

    @pytest.mark.parametrize("pair", PAIRS)
    def test_panes_stay_inside_their_boxes(
        self, data: DashboardData, pair: tuple[str, str]
    ) -> None:
        image = render_pair(data, "de", pair)
        left, right = pane_boxes()
        # The gutter between the panes carries only the divider line.
        gutter = (left[2] + 1, left[1], right[0] - 1, left[3])
        divider = WIDTH // 2
        gutter_left = (gutter[0], gutter[1], divider - 1, gutter[3])
        gutter_right = (divider + 1, gutter[1], gutter[2], gutter[3])
        assert black_fraction(image, gutter_left) == 0.0
        assert black_fraction(image, gutter_right) == 0.0
        # Nothing below the pane bottom.
        assert black_fraction(image, (0, left[3] + 1, WIDTH, HEIGHT)) == 0.0

    @pytest.mark.parametrize("pair", PAIRS)
    def test_empty_data_panes_render(self, data: DashboardData, pair: tuple[str, str]) -> None:
        bare = replace(
            data,
            upcoming_goals=(),
            next_goal=None,
            goals=(),
            tomorrow=(),
            notes=(),
            agenda=(),
            week_strip=(0.0,) * 7,
            unavailable=frozenset({"goals", "notes", "sessions"}),
        )
        image = render_pair(bare, "de", pair)
        assert image.size == (WIDTH, HEIGHT)
        for box in pane_boxes():
            assert black_fraction(image, box) > 0.0
