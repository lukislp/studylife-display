"""The exam schedule: every upcoming course goal the server lists (soonest first, at most
five) as one row each - an inverted countdown block on the left, wider and bolder for the
nearest exam, then the course and its date, and at the right edge the hours spent on that
course over the last 28 days from the session history. The streak and today's hours sit in
the footer line."""

from __future__ import annotations

from PIL import Image, ImageDraw

import studylife_display.layouts.common as common
from studylife_display.layouts.common import (
    MARGIN,
    WIDTH,
    Fonts,
    draw_footer_line,
    draw_header,
    draw_inverted_block,
    draw_text,
    ellipsize,
    finish,
    format_countdown,
    format_decimal,
    format_hours_clock,
    format_streak,
    load_fonts,
    new_canvas,
    text_width,
)
from studylife_display.model import DashboardData, NextGoal

# This layout's own strings; everything else comes from the shared table in common.py.
TEXT: dict[str, dict[str, str]] = {
    "de": {
        "exams_label": "PRÜFUNGSPLAN",
        "exams_hours": "{hours} h · 28 d",
        "exams_hours_none": "0 h",
    },
    "en": {
        "exams_label": "EXAM SCHEDULE",
        "exams_hours": "{hours} h · 28 d",
        "exams_hours_none": "0 h",
    },
}

LABEL_BASELINE = 86
ROW_TOP = 100
ROW_HEIGHT = 62
MAX_ROWS = 5
# The nearest exam gets the wide block in fonts.value, the others a narrower one in body type.
HERO_BLOCK_WIDTH = 240
BLOCK_WIDTH = 180
TEXT_X = MARGIN + HERO_BLOCK_WIDTH + 20
# The right-aligned hours column; the course name stops before it.
HOURS_COLUMN_WIDTH = 130
NAME_RIGHT = WIDTH - MARGIN - HOURS_COLUMN_WIDTH - 12

FOOTER_RULE_Y = 428

# The pane form.
PANE_ROW_HEIGHT = 66
PANE_MAX_ROWS = 4
PANE_BLOCK_WIDTH = 150
PANE_TEXT_X = PANE_BLOCK_WIDTH + 14


def row_box(index: int) -> tuple[int, int, int, int]:
    """The box of exam row `index` (0-based) in the full frame; the tests look for the
    inverted block inside it."""
    top = ROW_TOP + index * ROW_HEIGHT
    return (MARGIN, top, WIDTH - MARGIN, top + ROW_HEIGHT)


def course_hours_line(
    goal: NextGoal, data: DashboardData, t: dict[str, str], s: dict[str, str]
) -> str:
    """ "12,5 h · 28 d" for the goal's course, "0 h" when it was not studied in the window."""
    for name, hours in data.course_hours:
        if name == goal.course_name and hours > 0:
            return s["exams_hours"].format(hours=format_decimal(hours, t["decimal"]))
    return s["exams_hours_none"]


def _date_line(goal: NextGoal, t: dict[str, str]) -> str:
    if goal.target_date is None:
        return ""
    return t["goal_date"].format(date=goal.target_date.strftime(t["goal_date_format"]))


def _draw_rows(
    draw: ImageDraw.ImageDraw,
    data: DashboardData,
    fonts: Fonts,
    t: dict[str, str],
    s: dict[str, str],
) -> None:
    draw_text(draw, (MARGIN, LABEL_BASELINE), s["exams_label"], fonts.label)
    if not data.upcoming_goals:
        draw_text(draw, (MARGIN, ROW_TOP + 31), t["goal_none"], fonts.body)
        return
    for index, goal in enumerate(data.upcoming_goals[:MAX_ROWS]):
        left, top, right, _ = row_box(index)
        countdown = format_countdown(goal.days_left, t)
        if index == 0:
            block = (left, top + 2, left + HERO_BLOCK_WIDTH, top + 56)
            fits = text_width(countdown, fonts.value) <= HERO_BLOCK_WIDTH - 24
            font, baseline = (fonts.value, top + 43) if fits else (fonts.body, top + 38)
            draw_inverted_block(draw, block, countdown, font, baseline, 10)
            name_font, name_baseline, date_baseline = fonts.value, top + 36, top + 57
        else:
            block = (left, top + 10, left + BLOCK_WIDTH, top + 50)
            fits = text_width(countdown, fonts.body) <= BLOCK_WIDTH - 20
            font, baseline = (fonts.body, top + 39) if fits else (fonts.small, top + 37)
            draw_inverted_block(draw, block, countdown, font, baseline, 8)
            name_font, name_baseline, date_baseline = fonts.body, top + 30, top + 51
        name = ellipsize(goal.course_name or t["course_unknown"], name_font, NAME_RIGHT - TEXT_X)
        draw_text(draw, (TEXT_X, name_baseline), name, name_font)
        date = _date_line(goal, t)
        if date:
            draw_text(draw, (TEXT_X, date_baseline), date, fonts.small)
        hours = course_hours_line(goal, data, t, s)
        draw_text(draw, (right, name_baseline), hours, fonts.small, anchor="rs")


def render(data: DashboardData, language: str) -> Image.Image:
    t = common.TEXT[language]
    fonts = load_fonts()
    canvas, draw = new_canvas()
    draw_header(draw, data, fonts, t)
    _draw_rows(draw, data, fonts, t, TEXT[language])
    footer = t["separator"].join(
        [
            f"{t['streak_label']} {format_streak(data.streak_days, t)}",
            t["today_line"].format(hours=format_hours_clock(data.today_hours)),
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
    """Up to PANE_MAX_ROWS exams: countdown block, course and date per row."""
    t = common.TEXT[language]
    left, top, right, _ = box
    if not data.upcoming_goals:
        draw_text(draw, (left, top + 31), t["goal_none"], fonts.body)
        return
    text_x = left + PANE_TEXT_X
    for index, goal in enumerate(data.upcoming_goals[:PANE_MAX_ROWS]):
        row_top = top + index * PANE_ROW_HEIGHT
        countdown = format_countdown(goal.days_left, t)
        block = (left, row_top + 4, left + PANE_BLOCK_WIDTH, row_top + 44)
        fits = text_width(countdown, fonts.body) <= PANE_BLOCK_WIDTH - 16
        font, baseline = (fonts.body, row_top + 33) if fits else (fonts.small, row_top + 31)
        draw_inverted_block(draw, block, countdown, font, baseline, 8)
        name = ellipsize(goal.course_name or t["course_unknown"], fonts.body, right - text_x)
        draw_text(draw, (text_x, row_top + 30), name, fonts.body)
        date = _date_line(goal, t)
        if date:
            draw_text(draw, (text_x, row_top + 54), date, fonts.small)
    hidden = len(data.upcoming_goals) - PANE_MAX_ROWS
    if hidden > 0:
        more = t["agenda_more"].format(count=hidden)
        draw_text(draw, (left, top + PANE_MAX_ROWS * PANE_ROW_HEIGHT + 16), more, fonts.small)
