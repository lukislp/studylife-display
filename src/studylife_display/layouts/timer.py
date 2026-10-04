"""The timer with today's tally.

Left: the running phase ("Fokus · Runde 2 · endet 17:03") as a label and the remaining time
of the phase in the big font - a snapshot against `data.now`, like the focus layout, never a
live tick. Without a timer: "no timer running" and today's hours. Right: four label/value
pairs summed from today's completed sessions (`DashboardData.today_stats`) - how many, the
hours, the longest one, and when the first began and the last ended. Footer: streak and the
next exam.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

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
    format_goal_line,
    format_hours_clock,
    format_minutes_seconds,
    format_streak,
    format_timer_phase,
    load_fonts,
    new_canvas,
    text_width,
)
from studylife_display.layouts.common import TEXT as COMMON_TEXT
from studylife_display.layouts.focus import remaining_seconds
from studylife_display.model import DashboardData, TodayStats

TEXT: dict[str, dict[str, str]] = {
    "de": {
        "sessions_label": "SESSIONS HEUTE",
        "studied_label": "GELERNT",
        "longest_label": "LÄNGSTE SESSION",
        "span_label": "ERSTE – LETZTE",
        "span_value": "{first}–{last}",
        "span_none": "–",
        "hours_value": "{hours} h",
    },
    "en": {
        "sessions_label": "SESSIONS TODAY",
        "studied_label": "STUDIED",
        "longest_label": "LONGEST SESSION",
        "span_label": "FIRST – LAST",
        "span_value": "{first}–{last}",
        "span_none": "–",
        "hours_value": "{hours} h",
    },
}

COLUMN_RULE_X = 448
HERO_LABEL_BASELINE = 164
HERO_BASELINE = 298
HERO_RIGHT = COLUMN_RULE_X - 16
UNIT_GAP = 12
# The hero number: the render test checks that it is not white, with or without a timer.
HERO_BOX = (MARGIN, HERO_BASELINE - 90, HERO_RIGHT, HERO_BASELINE)

RIGHT_X = 472
TALLY_FIRST_LABEL_BASELINE = 100
TALLY_PITCH = 84
TALLY_VALUE_OFFSET = 38

FOOTER_RULE_Y = 428


def format_span(stats: TodayStats, data: DashboardData, t: dict[str, str]) -> str:
    """ "08:00–17:30" from the first start and the last end, "–" without sessions."""
    if stats.first_start is None or stats.last_end is None:
        return t["span_none"]
    zone = data.now.tzinfo
    return t["span_value"].format(
        first=stats.first_start.astimezone(zone).strftime("%H:%M"),
        last=stats.last_end.astimezone(zone).strftime("%H:%M"),
    )


def tally_rows(data: DashboardData, t: dict[str, str]) -> tuple[tuple[str, str], ...]:
    """The four (label, value) pairs of the tally column, in display order."""
    stats = data.today_stats
    hours = t["hours_value"].format(hours=format_hours_clock(stats.hours))
    longest = t["hours_value"].format(hours=format_hours_clock(stats.longest_hours))
    return (
        (t["sessions_label"], str(stats.session_count)),
        (t["studied_label"], hours),
        (t["longest_label"], longest),
        (t["span_label"], format_span(stats, data, t)),
    )


def _draw_hours_with_unit(
    draw: ImageDraw.ImageDraw,
    data: DashboardData,
    fonts: Fonts,
    c: dict[str, str],
    centre: float,
    baseline: float,
    max_width: float,
) -> None:
    number = format_hours_clock(data.today_hours)
    unit = c["today_unit"]
    unit_width = text_width(unit, fonts.big_unit)
    font = fonts.big
    if text_width(number, font) + UNIT_GAP + unit_width > max_width:
        font = fonts.big_narrow
    width = text_width(number, font) + UNIT_GAP + unit_width
    end_x = draw_text(draw, (centre - width / 2, baseline), number, font)
    draw_text(draw, (end_x + UNIT_GAP, baseline - 4), unit, fonts.big_unit)


def _draw_phase_and_number(
    draw: ImageDraw.ImageDraw,
    data: DashboardData,
    fonts: Fonts,
    c: dict[str, str],
    centre: float,
    label_baseline: float,
    hero_baseline: float,
    max_width: float,
) -> None:
    """The phase label over the remaining time, or the no-timer label over today's hours."""
    timer = data.timer
    if timer is not None and timer.is_running:
        label = ellipsize(format_timer_phase(data, c), fonts.body, max_width)
        draw_text(draw, (centre, label_baseline), label, fonts.body, anchor="ms")
        seconds = remaining_seconds(data)
        hero = "--:--" if seconds is None else format_minutes_seconds(seconds)
        draw_text(draw, (centre, hero_baseline), hero, fonts.big, anchor="ms")
        return
    draw_text(draw, (centre, label_baseline), c["timer_none"], fonts.body, anchor="ms")
    _draw_hours_with_unit(draw, data, fonts, c, centre, hero_baseline, max_width)


def _draw_tally(
    draw: ImageDraw.ImageDraw, data: DashboardData, fonts: Fonts, t: dict[str, str]
) -> None:
    draw.line([(COLUMN_RULE_X, 70), (COLUMN_RULE_X, FOOTER_RULE_Y - 16)], fill=BLACK)
    max_width = WIDTH - MARGIN - RIGHT_X
    for index, (label, value) in enumerate(tally_rows(data, t)):
        baseline = TALLY_FIRST_LABEL_BASELINE + index * TALLY_PITCH
        draw_text(draw, (RIGHT_X, baseline), label, fonts.label)
        value = ellipsize(value, fonts.value, max_width)
        draw_text(draw, (RIGHT_X, baseline + TALLY_VALUE_OFFSET), value, fonts.value)


def render(data: DashboardData, language: str) -> Image.Image:
    t = TEXT[language]
    c = COMMON_TEXT[language]
    fonts = load_fonts()
    canvas, draw = new_canvas()
    draw_header(draw, data, fonts, c)
    _draw_phase_and_number(
        draw,
        data,
        fonts,
        c,
        (MARGIN + HERO_RIGHT) / 2,
        HERO_LABEL_BASELINE,
        HERO_BASELINE,
        HERO_RIGHT - MARGIN,
    )
    _draw_tally(draw, data, fonts, t)
    footer = c["separator"].join(
        [
            f"{c['streak_label']} {format_streak(data.streak_days, c)}",
            format_goal_line(data, c),
        ]
    )
    draw_footer_line(draw, ellipsize(footer, fonts.body, WIDTH - 2 * MARGIN), fonts, FOOTER_RULE_Y)
    return finish(canvas)


def render_pane(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    data: DashboardData,
    fonts: Fonts,
    language: str,
    box: tuple[int, int, int, int],
) -> None:
    """Phase and remaining time (or today's hours) centred in the upper part of the box, the
    session count and the hours studied as two label/value lines underneath."""
    t = TEXT[language]
    c = COMMON_TEXT[language]
    left, top, right, _ = box
    _draw_phase_and_number(
        draw, data, fonts, c, (left + right) / 2, top + 30, top + 150, right - left
    )
    for index, (label, value) in enumerate(tally_rows(data, t)[:2]):
        baseline = top + 210 + index * 64
        draw_text(draw, (left, baseline), label, fonts.label)
        draw_text(draw, (right, baseline + 2), value, fonts.value, anchor="rs")
