"""The chart layouts (year, balance, achievements) and the panes of degree and courses."""

from dataclasses import replace
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from PIL import Image, ImageChops

from studylife_display.layouts import achievements, balance, year
from studylife_display.layouts.achievements import category_counts, category_name
from studylife_display.layouts.balance import is_short, neglected_line
from studylife_display.layouts.common import HEIGHT, WIDTH, load_fonts
from studylife_display.layouts.common import TEXT as COMMON_TEXT
from studylife_display.layouts.duo import render_pair
from studylife_display.layouts.panes import pane_boxes
from studylife_display.layouts.year import GRID_BOX, pack_lines, padded_weeks
from studylife_display.model import (
    Achievements,
    CourseShare,
    DashboardData,
    NeglectedCourse,
    YearData,
    build_dashboard,
)
from studylife_display.sample import sample_extras


@pytest.fixture
def data(sample: Any, fixed_now: datetime, tz: ZoneInfo) -> DashboardData:
    metrics, history, timer, sessions = sample
    goals, achievements_payload, notes = sample_extras(fixed_now, tz)
    return build_dashboard(
        metrics,
        history,
        timer,
        fixed_now,
        tz,
        sessions=sessions,
        goals=goals,
        achievements_payload=achievements_payload,
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


def assert_full_frame(image: Image.Image) -> None:
    assert image.size == (WIDTH, HEIGHT)
    assert image.mode == "1"


class TestLanguageTables:
    @pytest.mark.parametrize("module", [year, balance, achievements])
    def test_both_languages_have_the_same_keys(self, module: Any) -> None:
        assert module.TEXT["de"].keys() == module.TEXT["en"].keys()
        for language in ("de", "en"):
            assert all(module.TEXT[language].values())


class TestYear:
    def test_renders_in_both_languages(self, data: DashboardData) -> None:
        for language in ("de", "en"):
            assert_full_frame(year.render(data, language))

    def test_grid_follows_the_history(self, data: DashboardData) -> None:
        assert len(data.year.weeks) == 53
        filled = year.render(data, "de")
        empty = year.render(replace(data, year=replace(data.year, weeks=((0.0,) * 7,) * 53)), "de")
        assert 0.05 < black_fraction(filled, GRID_BOX) < 0.9
        # Empty cells are outlines only: far less ink than the sample, but not none.
        assert 0.0 < black_fraction(empty, GRID_BOX) < black_fraction(filled, GRID_BOX)
        assert differing_fraction(filled, empty) > 0.0

    def test_empty_state_renders_and_differs(self, data: DashboardData) -> None:
        bare = replace(
            data, year=YearData((), data.year.first_monday, 0.0, 0, 0), longest_streak_days=0
        )
        image = year.render(bare, "en")
        assert_full_frame(image)
        assert differing_fraction(image, year.render(data, "en")) > 0.0

    def test_padded_weeks_always_has_53_full_columns(self, data: DashboardData) -> None:
        assert len(padded_weeks(data.year)) == 53
        short = YearData(((1.0, 2.0),), data.year.first_monday, 3.0, 2, 2)
        weeks = padded_weeks(short)
        assert len(weeks) == 53
        assert all(len(week) == 7 for week in weeks)
        assert weeks[-1] == (1.0, 2.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        assert weeks[0] == (0.0,) * 7

    def test_pack_lines_never_splits_an_item(self) -> None:
        fonts = load_fonts()
        items = ["312,5 h gesamt", "187 aktive Tage", "240 Sessions", "längste Serie 23 Tage"]
        wide = pack_lines(items, " · ", fonts.body, 2000)
        assert wide == [" · ".join(items)]
        narrow = pack_lines(items, " · ", fonts.body, 250)
        assert [part for line in narrow for part in line.split(" · ")] == items
        assert len(narrow) > 1

    def test_stale_marker_still_in_the_header(self, data: DashboardData) -> None:
        fresh = year.render(data, "de")
        stale = year.render(replace(data, stale_minutes=35), "de")
        header_right = (WIDTH // 2, 0, WIDTH, 50)
        assert black_fraction(stale, header_right) > black_fraction(fresh, header_right)


class TestBalance:
    def test_renders_in_both_languages(self, data: DashboardData) -> None:
        assert len(data.course_shares) == 4
        for language in ("de", "en"):
            assert_full_frame(balance.render(data, language))

    def test_empty_state_renders_and_differs(self, data: DashboardData) -> None:
        empty = balance.render(replace(data, course_shares=()), "de")
        assert_full_frame(empty)
        assert differing_fraction(empty, balance.render(data, "de")) > 0.0

    def test_tiny_share_gets_the_short_tag(self, data: DashboardData) -> None:
        shares = data.course_shares
        # Sample: 128.5 / 112 / 48 / 24 of 312.5 h; the even share is 25 %, half of it 12.5 %.
        assert [is_short(share, shares) for share in shares] == [False, False, False, True]
        balanced = tuple(replace(share, hours=50.0) for share in shares)
        assert not any(is_short(share, balanced) for share in balanced)
        with_tag = balance.render(data, "de")
        without = balance.render(replace(data, course_shares=balanced), "de")
        assert differing_fraction(with_tag, without) > 0.0
        # A single tiny course against three equal ones: tagged in both languages.
        skewed = balanced[:3] + (CourseShare("Theoretische Informatik", 2.0, 1),)
        assert is_short(skewed[-1], skewed)
        for language in ("de", "en"):
            assert_full_frame(balance.render(replace(data, course_shares=skewed), language))

    def test_neglected_footer_wording(self, data: DashboardData) -> None:
        t = COMMON_TEXT["de"]
        assert neglected_line(data, t) == "Datenbanken · seit 12 Tagen"
        assert neglected_line(replace(data, neglected_course=None), t) == t["neglected_none"]
        one = NeglectedCourse(1, "Algebra", None, 1)
        assert neglected_line(replace(data, neglected_course=one), t) == "Algebra · seit 1 Tag"
        never = NeglectedCourse(1, "", None, None)
        assert neglected_line(replace(data, neglected_course=never), COMMON_TEXT["en"]) == (
            "(no course) · never"
        )

    def test_many_courses_stay_inside_the_frame(self, data: DashboardData) -> None:
        many = tuple(
            CourseShare(f"Kurs mit einem sehr langen Namen Nummer {index}", 10.0 + index, 3)
            for index in range(12)
        )
        image = balance.render(replace(data, course_shares=many), "de")
        assert_full_frame(image)


class TestAchievements:
    def test_renders_in_both_languages(self, data: DashboardData) -> None:
        assert data.achievements.total > 0
        for language in ("de", "en"):
            assert_full_frame(achievements.render(data, language))

    def test_sample_picks_sensible_tiers(self, data: DashboardData) -> None:
        latest = data.achievements.latest_unlocked()
        upcoming = data.achievements.next_tier()
        assert latest is not None and latest.category == "programs" and latest.threshold == 1
        assert upcoming is not None and upcoming.category == "streak"
        assert (upcoming.current, upcoming.threshold) == (12, 14)
        counts = dict(
            (category, (done, total))
            for category, done, total in category_counts(data.achievements)
        )
        assert counts["hours"] == (4, 6)
        assert counts["nightowl"] == (0, 3)
        assert [c for c, _, _ in category_counts(data.achievements)][:3] == [
            "hours",
            "streak",
            "sessions",
        ]

    def test_category_names_fall_back_to_the_key(self) -> None:
        assert category_name("hours", achievements.TEXT["de"]) == "Lernstunden"
        assert category_name("hours", achievements.TEXT["en"]) == "Study hours"
        assert category_name("mystery", achievements.TEXT["en"]) == "mystery"

    def test_empty_and_unavailable_states_differ(self, data: DashboardData) -> None:
        filled = achievements.render(data, "de")
        empty = achievements.render(replace(data, achievements=Achievements(0, 0, ())), "de")
        hint = achievements.render(
            replace(
                data, achievements=Achievements(0, 0, ()), unavailable=frozenset({"achievements"})
            ),
            "de",
        )
        for image in (empty, hint):
            assert_full_frame(image)
        assert differing_fraction(filled, empty) > 0.0
        assert differing_fraction(empty, hint) > 0.0
        assert_full_frame(
            achievements.render(replace(data, unavailable=frozenset({"achievements"})), "en")
        )

    def test_bar_is_filled_to_the_unlocked_fraction(self, data: DashboardData) -> None:
        image = achievements.render(data, "de")
        left, top, right, bottom = achievements.BAR_BOX
        fraction = data.achievements.unlocked / data.achievements.total
        filled = (left + 4, top + 4, left + int((right - left) * fraction) - 8, bottom - 4)
        empty = (left + int((right - left) * fraction) + 8, top + 4, right - 4, bottom - 4)
        assert black_fraction(image, filled) > 0.95
        assert black_fraction(image, empty) == 0.0

    def test_everything_unlocked_has_no_next_tier(self, data: DashboardData) -> None:
        done = tuple(
            replace(tier, unlocked=True, current=tier.threshold) for tier in data.achievements.tiers
        )
        all_done = replace(data, achievements=Achievements(len(done), len(done), done))
        assert all_done.achievements.next_tier() is None
        assert_full_frame(achievements.render(all_done, "de"))


class TestPanes:
    @pytest.mark.parametrize(
        "pair",
        [
            ("year", "degree"),
            ("balance", "courses"),
            ("achievements", "year"),
            ("degree", "balance"),
            ("courses", "achievements"),
        ],
    )
    def test_every_pane_paints_inside_its_box(
        self, data: DashboardData, pair: tuple[str, str]
    ) -> None:
        for language in ("de", "en"):
            image = render_pair(data, language, pair)
            assert_full_frame(image)
            for box in pane_boxes():
                left, top, right, bottom = box
                # Below the pane title: only the pane itself draws here.
                assert black_fraction(image, (left, top + 2, right, bottom)) > 0.0

    def test_panes_keep_the_gutter_clean(self, data: DashboardData) -> None:
        image = render_pair(data, "de", ("year", "achievements"))
        (_, top, left_right, bottom), (right_left, _, _, _) = pane_boxes()
        # The divider sits in the middle of the gutter; both sides of it stay white.
        gutter_left = (left_right + 1, top, WIDTH // 2 - 1, bottom)
        gutter_right = (WIDTH // 2 + 1, top, right_left - 1, bottom)
        assert black_fraction(image, gutter_left) == 0.0
        assert black_fraction(image, gutter_right) == 0.0

    def test_empty_panes_render(self, data: DashboardData) -> None:
        bare = replace(
            data,
            course_shares=(),
            course_hours=(),
            achievements=Achievements(0, 0, ()),
            year=YearData((), data.year.first_monday, 0.0, 0, 0),
        )
        for pair in (("year", "degree"), ("balance", "courses"), ("achievements", "balance")):
            assert_full_frame(render_pair(bare, "en", pair))
