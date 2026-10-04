"""Hours per course over the last 28 days as the whole frame: the same bar chart `exam`
squeezes under its countdown block, given the full canvas and more rows, plus the summed
total as a small hero line up top."""

from __future__ import annotations

from PIL import Image as _PilImage
from PIL import ImageDraw
from PIL.Image import Image

from studylife_display.layouts.common import (
    BLACK,
    MARGIN,
    TEXT,
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
)
from studylife_display.model import DashboardData

HERO_BASELINE = 108
LABEL_BASELINE = 148
CHART_TOP = 164
ROW_HEIGHT = 30
BAR_HEIGHT = 18
BAR_LEFT = MARGIN + 260
BAR_RIGHT = WIDTH - MARGIN - 90
TOP_COURSES = 9

FOOTER_RULE_Y = 428

# Pane geometry, relative to the pane box's top edge.
PANE_LABEL_BASELINE = 22
PANE_ROWS_TOP = 40
PANE_ROW_HEIGHT = 34
PANE_BAR_HEIGHT = 18
PANE_NAME_WIDTH = 130
PANE_VALUE_WIDTH = 60
PANE_TOP_COURSES = 5


def _draw_hero(
    draw: ImageDraw.ImageDraw, data: DashboardData, fonts: Fonts, t: dict[str, str]
) -> None:
    total = sum(hours for _, hours in data.course_hours)
    text = t["courses_total"].format(hours=format_decimal(total, t["decimal"]))
    draw_text(draw, (MARGIN, HERO_BASELINE), text, fonts.title)
    draw_text(draw, (MARGIN, LABEL_BASELINE), t["courses_label"], fonts.label)


def _draw_chart(
    draw: ImageDraw.ImageDraw, data: DashboardData, fonts: Fonts, t: dict[str, str]
) -> None:
    courses = data.course_hours[:TOP_COURSES]
    if not courses:
        draw_text(draw, (MARGIN, CHART_TOP + ROW_HEIGHT - 8), t["courses_none"], fonts.body)
        return
    scale = max(hours for _, hours in courses) or 1.0
    for index, (name, hours) in enumerate(courses):
        top = CHART_TOP + index * ROW_HEIGHT
        baseline = top + BAR_HEIGHT - 2
        label = ellipsize(name or t["course_unknown"], fonts.body, BAR_LEFT - MARGIN - 12)
        draw_text(draw, (MARGIN, baseline), label, fonts.body)
        width = int((BAR_RIGHT - BAR_LEFT) * hours / scale)
        draw.rectangle((BAR_LEFT, top, BAR_LEFT + max(width, 2), top + BAR_HEIGHT), fill=BLACK)
        value = t["hours_short"].format(hours=format_decimal(hours, t["decimal"]))
        draw_text(draw, (WIDTH - MARGIN, baseline), value, fonts.body, anchor="rs")


def render(data: DashboardData, language: str) -> Image:
    t = TEXT[language]
    fonts = load_fonts()
    canvas, draw = new_canvas()
    draw_header(draw, data, fonts, t)
    _draw_hero(draw, data, fonts, t)
    _draw_chart(draw, data, fonts, t)
    footer = t["separator"].join(
        [
            f"{t['streak_label']} {format_streak(data.streak_days, t)}",
            t["today_line"].format(hours=format_hours_clock(data.today_hours)),
        ]
    )
    draw_footer_line(draw, footer, fonts, FOOTER_RULE_Y)
    return finish(canvas)


def render_pane(
    image: _PilImage.Image,
    draw: ImageDraw.ImageDraw,
    data: DashboardData,
    fonts: Fonts,
    language: str,
    box: tuple[int, int, int, int],
) -> None:
    """The label and the top PANE_TOP_COURSES courses as name, bar and hours, scaled to the
    box: a narrower name column and a value column at the right edge."""
    t = TEXT[language]
    left, top, right, _ = box
    draw_text(
        draw,
        (left, top + PANE_LABEL_BASELINE),
        ellipsize(t["courses_label"], fonts.label, right - left),
        fonts.label,
    )
    courses = data.course_hours[:PANE_TOP_COURSES]
    rows_top = top + PANE_ROWS_TOP
    if not courses:
        draw_text(draw, (left, rows_top + PANE_ROW_HEIGHT - 8), t["courses_none"], fonts.body)
        return
    bar_left = left + PANE_NAME_WIDTH + 10
    bar_right = right - PANE_VALUE_WIDTH - 10
    scale = max(hours for _, hours in courses) or 1.0
    for index, (name, hours) in enumerate(courses):
        row_top = rows_top + index * PANE_ROW_HEIGHT
        baseline = row_top + PANE_BAR_HEIGHT - 2
        label = ellipsize(name or t["course_unknown"], fonts.small, PANE_NAME_WIDTH)
        draw_text(draw, (left, baseline), label, fonts.small)
        width = int((bar_right - bar_left) * hours / scale)
        draw.rectangle(
            (bar_left, row_top, bar_left + max(width, 2), row_top + PANE_BAR_HEIGHT), fill=BLACK
        )
        value = t["hours_short"].format(hours=format_decimal(hours, t["decimal"]))
        draw_text(draw, (right, baseline), value, fonts.small, anchor="rs")
