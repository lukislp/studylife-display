"""Hours per course over the last 28 days as the whole frame: the same bar chart `exam`
squeezes under its countdown block, given the full canvas and more rows, plus the summed
total as a small hero line up top."""

from __future__ import annotations

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
