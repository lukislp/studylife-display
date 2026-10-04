"""The achievements (GET /api/metrics/achievements, scope Metrics.GetAchievements): how many
of all tiers are unlocked as a big number with a progress bar, the tier most recently earned
and the next one within reach with its own small bar on the right, and every category with
its unlocked/total tiers as a compact two-column list underneath. Needs the scope; without
it the layout says so. Never picked by "auto"."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from studylife_display.layouts.common import (
    BLACK,
    MARGIN,
    WIDTH,
    Fonts,
    draw_footer_line,
    draw_header,
    draw_text,
    ellipsize,
    finish,
    format_decimal,
    format_hours_clock,
    format_streak,
    load_fonts,
    new_canvas,
    text_width,
)
from studylife_display.layouts.common import TEXT as COMMON_TEXT
from studylife_display.model import Achievements, AchievementTier, DashboardData

TEXT: dict[str, dict[str, str]] = {
    "de": {
        "label": "FREIGESCHALTET",
        "of": "von {total}",
        "latest_label": "ZULETZT ERREICHT",
        "next_label": "NÄCHSTES ZIEL",
        "tier": "{category} · {threshold}",
        "progress": "{category} · {current} von {threshold}",
        "count": "{category} {unlocked}/{total}",
        "none": "keine Achievements",
        "all_done": "alle erreicht",
        "unavailable": "Schlüssel ohne Scope Metrics.GetAchievements",
        "category_hours": "Lernstunden",
        "category_streak": "Serie",
        "category_sessions": "Sessions",
        "category_courses": "Kurse",
        "category_allcourses": "Alle Kurse",
        "category_earlybird": "Frühaufsteher",
        "category_nightowl": "Nachteule",
        "category_weekend": "Wochenende",
        "category_marathon": "Marathon",
        "category_perfectweek": "Perfekte Woche",
        "category_notes": "Notizen",
        "category_coursediversity": "Kursvielfalt",
        "category_programs": "Studiengänge",
    },
    "en": {
        "label": "UNLOCKED",
        "of": "of {total}",
        "latest_label": "LATEST",
        "next_label": "NEXT UP",
        "tier": "{category} · {threshold}",
        "progress": "{category} · {current} of {threshold}",
        "count": "{category} {unlocked}/{total}",
        "none": "no achievements",
        "all_done": "all unlocked",
        "unavailable": "key lacks scope Metrics.GetAchievements",
        "category_hours": "Study hours",
        "category_streak": "Streak",
        "category_sessions": "Sessions",
        "category_courses": "Courses",
        "category_allcourses": "All courses",
        "category_earlybird": "Early bird",
        "category_nightowl": "Night owl",
        "category_weekend": "Weekend",
        "category_marathon": "Marathon",
        "category_perfectweek": "Perfect week",
        "category_notes": "Notes",
        "category_coursediversity": "Course variety",
        "category_programs": "Programmes",
    },
}

LABEL_BASELINE = 86
HERO_BASELINE = 190
HERO_RIGHT = 430
BAR_TOP = 210
BAR_HEIGHT = 22
# The filled part of the bar: the render test checks it against the unlocked fraction.
BAR_BOX = (MARGIN, BAR_TOP, HERO_RIGHT, BAR_TOP + BAR_HEIGHT)
EMPTY_BASELINE = 132

RIGHT_COLUMN_X = 480
LATEST_LABEL_BASELINE = 86
LATEST_VALUE_BASELINE = 132
NEXT_LABEL_BASELINE = 178
NEXT_VALUE_BASELINE = 224
NEXT_BAR_TOP = 240
NEXT_BAR_HEIGHT = 12

LIST_TOP_BASELINE = 266
LIST_LINE_HEIGHT = 23
LIST_COLUMN_WIDTH = 228
LIST_ROWS = 7

PANE_HERO_BASELINE = 100
PANE_BAR_TOP = 118
PANE_BAR_HEIGHT = 16
PANE_NEXT_LABEL_BASELINE = 178
PANE_NEXT_VALUE_BASELINE = 208
PANE_NEXT_BAR_TOP = 222
PANE_NEXT_BAR_HEIGHT = 10
PANE_LATEST_LABEL_BASELINE = 282
PANE_LATEST_VALUE_BASELINE = 312
PANE_EMPTY_BASELINE = 30

FOOTER_RULE_Y = 428


def category_name(key: str, t: dict[str, str]) -> str:
    """The display name of a catalog category; an unknown key is shown as is."""
    return t.get(f"category_{key}", key)


def category_counts(achievements: Achievements) -> list[tuple[str, int, int]]:
    """(category, unlocked tiers, tiers) per category, in order of first appearance."""
    counts: dict[str, list[int]] = {}
    for tier in achievements.tiers:
        entry = counts.setdefault(tier.category, [0, 0])
        entry[0] += int(tier.unlocked)
        entry[1] += 1
    return [(category, unlocked, total) for category, (unlocked, total) in counts.items()]


def tier_line(tier: AchievementTier, t: dict[str, str], ct: dict[str, str]) -> str:
    return t["tier"].format(
        category=category_name(tier.category, t),
        threshold=format_decimal(tier.threshold, ct["decimal"]),
    )


def progress_line(tier: AchievementTier, t: dict[str, str], ct: dict[str, str]) -> str:
    return t["progress"].format(
        category=category_name(tier.category, t),
        current=format_decimal(min(tier.current, tier.threshold), ct["decimal"]),
        threshold=format_decimal(tier.threshold, ct["decimal"]),
    )


def _draw_bar(
    draw: ImageDraw.ImageDraw, left: int, top: int, right: int, height: int, fraction: float
) -> None:
    """An outlined bar filled to `fraction` (clamped to 0..1)."""
    bottom = top + height
    draw.rectangle((left, top, right, bottom), outline=BLACK, width=2)
    fill_right = left + int((right - left) * max(0.0, min(1.0, fraction)))
    if fill_right > left + 2:
        draw.rectangle((left + 2, top + 2, fill_right - 1, bottom - 2), fill=BLACK)


def _empty_text(data: DashboardData, t: dict[str, str]) -> str | None:
    """The one-line placeholder when there is nothing to show, None otherwise."""
    if "achievements" in data.unavailable:
        return t["unavailable"]
    if data.achievements.total <= 0:
        return t["none"]
    return None


def _draw_hero_number(
    draw: ImageDraw.ImageDraw,
    achievements: Achievements,
    fonts: Fonts,
    t: dict[str, str],
    left: int,
    baseline: int,
    max_right: int,
    number_font: ImageFont.FreeTypeFont,
    narrow_font: ImageFont.FreeTypeFont,
) -> None:
    number = str(achievements.unlocked)
    rest = t["of"].format(total=achievements.total)
    font = number_font
    if left + text_width(number, font) + 12 + text_width(rest, fonts.big_unit) > max_right:
        font = narrow_font
    end_x = draw_text(draw, (left, baseline), number, font)
    draw_text(draw, (end_x + 12, baseline - 4), rest, fonts.big_unit)


def _draw_hero(
    draw: ImageDraw.ImageDraw,
    data: DashboardData,
    fonts: Fonts,
    t: dict[str, str],
    ct: dict[str, str],
) -> None:
    achievements = data.achievements
    draw_text(draw, (MARGIN, LABEL_BASELINE), t["label"], fonts.label)
    _draw_hero_number(
        draw, achievements, fonts, t, MARGIN, HERO_BASELINE, HERO_RIGHT, fonts.big, fonts.big_narrow
    )
    fraction = achievements.unlocked / achievements.total if achievements.total > 0 else 0.0
    _draw_bar(draw, MARGIN, BAR_TOP, HERO_RIGHT, BAR_HEIGHT, fraction)
    percent = ct["ects_percent"].format(percent=int(round(fraction * 100)))
    draw_text(draw, (HERO_RIGHT, BAR_TOP - 8), percent, fonts.small, anchor="rs")


def _draw_latest_and_next(
    draw: ImageDraw.ImageDraw,
    data: DashboardData,
    fonts: Fonts,
    t: dict[str, str],
    ct: dict[str, str],
) -> None:
    x = RIGHT_COLUMN_X
    max_width = WIDTH - MARGIN - x
    achievements = data.achievements
    draw_text(draw, (x, LATEST_LABEL_BASELINE), t["latest_label"], fonts.label)
    latest = achievements.latest_unlocked()
    latest_text = t["none"] if latest is None else tier_line(latest, t, ct)
    draw_text(
        draw, (x, LATEST_VALUE_BASELINE), ellipsize(latest_text, fonts.body, max_width), fonts.body
    )

    draw_text(draw, (x, NEXT_LABEL_BASELINE), t["next_label"], fonts.label)
    upcoming = achievements.next_tier()
    if upcoming is None:
        draw_text(draw, (x, NEXT_VALUE_BASELINE), t["all_done"], fonts.body)
        return
    line = ellipsize(progress_line(upcoming, t, ct), fonts.body, max_width)
    draw_text(draw, (x, NEXT_VALUE_BASELINE), line, fonts.body)
    fraction = upcoming.current / upcoming.threshold if upcoming.threshold > 0 else 0.0
    _draw_bar(draw, x, NEXT_BAR_TOP, WIDTH - MARGIN, NEXT_BAR_HEIGHT, fraction)


def _draw_category_list(
    draw: ImageDraw.ImageDraw, data: DashboardData, fonts: Fonts, t: dict[str, str]
) -> None:
    counts = category_counts(data.achievements)[: 2 * LIST_ROWS]
    for index, (category, unlocked, total) in enumerate(counts):
        column, row = divmod(index, LIST_ROWS)
        x = MARGIN + column * LIST_COLUMN_WIDTH
        baseline = LIST_TOP_BASELINE + row * LIST_LINE_HEIGHT
        text = t["count"].format(
            category=category_name(category, t), unlocked=unlocked, total=total
        )
        draw_text(
            draw, (x, baseline), ellipsize(text, fonts.small, LIST_COLUMN_WIDTH - 16), fonts.small
        )


def render(data: DashboardData, language: str) -> Image.Image:
    t = TEXT[language]
    ct = COMMON_TEXT[language]
    fonts = load_fonts()
    canvas, draw = new_canvas()
    draw_header(draw, data, fonts, ct)
    empty = _empty_text(data, t)
    if empty is not None:
        draw_text(draw, (MARGIN, LABEL_BASELINE), t["label"], fonts.label)
        draw_text(
            draw,
            (MARGIN, EMPTY_BASELINE),
            ellipsize(empty, fonts.body, WIDTH - 2 * MARGIN),
            fonts.body,
        )
    else:
        _draw_hero(draw, data, fonts, t, ct)
        _draw_latest_and_next(draw, data, fonts, t, ct)
        _draw_category_list(draw, data, fonts, t)
    footer = ct["separator"].join(
        [
            ct["today_line"].format(hours=format_hours_clock(data.today_hours)),
            f"{ct['streak_label']} {format_streak(data.streak_days, ct)}",
        ]
    )
    draw_footer_line(draw, footer, fonts, FOOTER_RULE_Y)
    return finish(canvas)


def render_pane(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    data: DashboardData,
    fonts: Fonts,
    language: str,
    box: tuple[int, int, int, int],
) -> None:
    """The unlocked count with its bar, the next tier with a small bar and the latest one."""
    t = TEXT[language]
    ct = COMMON_TEXT[language]
    left, top, right, _ = box
    max_width = right - left
    empty = _empty_text(data, t)
    if empty is not None:
        draw_text(
            draw,
            (left, top + PANE_EMPTY_BASELINE),
            ellipsize(empty, fonts.body, max_width),
            fonts.body,
        )
        return
    achievements = data.achievements
    _draw_hero_number(
        draw,
        achievements,
        fonts,
        t,
        left,
        top + PANE_HERO_BASELINE,
        right,
        fonts.big_narrow,
        fonts.value,
    )
    fraction = achievements.unlocked / achievements.total if achievements.total > 0 else 0.0
    _draw_bar(draw, left, top + PANE_BAR_TOP, right, PANE_BAR_HEIGHT, fraction)

    draw_text(draw, (left, top + PANE_NEXT_LABEL_BASELINE), t["next_label"], fonts.label)
    upcoming = achievements.next_tier()
    if upcoming is None:
        draw_text(draw, (left, top + PANE_NEXT_VALUE_BASELINE), t["all_done"], fonts.body)
    else:
        line = ellipsize(progress_line(upcoming, t, ct), fonts.body, max_width)
        draw_text(draw, (left, top + PANE_NEXT_VALUE_BASELINE), line, fonts.body)
        next_fraction = upcoming.current / upcoming.threshold if upcoming.threshold > 0 else 0.0
        _draw_bar(draw, left, top + PANE_NEXT_BAR_TOP, right, PANE_NEXT_BAR_HEIGHT, next_fraction)

    draw_text(draw, (left, top + PANE_LATEST_LABEL_BASELINE), t["latest_label"], fonts.label)
    latest = achievements.latest_unlocked()
    latest_text = t["none"] if latest is None else tier_line(latest, t, ct)
    draw_text(
        draw,
        (left, top + PANE_LATEST_VALUE_BASELINE),
        ellipsize(latest_text, fonts.body, max_width),
        fonts.body,
    )
