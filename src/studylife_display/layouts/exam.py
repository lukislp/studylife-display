"""The countdown as the hero: a big inverted "in N days" block with the course and the
target date, a horizontal bar chart of hours per course over the last 28 days underneath,
and a small streak/today line at the bottom."""

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
from studylife_display.model import DashboardData

# The hero block: the render test looks for majority-black pixels here.
HERO_BOX = (MARGIN, 72, MARGIN + 400, 192)
HERO_BASELINE = 154
COURSE_X = HERO_BOX[2] + 24
COURSE_BASELINE = 122
DATE_BASELINE = 166

CHART_LABEL_BASELINE = 234
CHART_TOP = 248
ROW_HEIGHT = 34
BAR_HEIGHT = 20
BAR_LEFT = MARGIN + 220
BAR_RIGHT = WIDTH - MARGIN - 90
TOP_COURSES = 5

FOOTER_RULE_Y = 428


def _draw_hero(
    draw: ImageDraw.ImageDraw, data: DashboardData, fonts: Fonts, t: dict[str, str]
) -> None:
    goal = data.next_goal
    if goal is None:
        draw_inverted_block(draw, HERO_BOX, t["goal_none"], fonts.value, HERO_BASELINE, 12)
        draw_text(draw, (COURSE_X, COURSE_BASELINE), t["goal_label"], fonts.label)
        return
    countdown = format_countdown(goal.days_left, t)
    inner_width = HERO_BOX[2] - HERO_BOX[0] - 32
    font = fonts.title if text_width(countdown, fonts.title) <= inner_width else fonts.value
    draw_inverted_block(draw, HERO_BOX, countdown, font, HERO_BASELINE, 12)

    max_width = WIDTH - MARGIN - COURSE_X
    draw_text(draw, (COURSE_X, COURSE_BASELINE - 34), t["goal_label"], fonts.label)
    name = ellipsize(goal.course_name, fonts.value, max_width)
    draw_text(draw, (COURSE_X, COURSE_BASELINE + 8), name, fonts.value)
    if goal.target_date is not None:
        date = t["goal_date"].format(date=goal.target_date.strftime(t["goal_date_format"]))
        draw_text(draw, (COURSE_X, DATE_BASELINE + 14), date, fonts.body)


def _draw_course_chart(
    draw: ImageDraw.ImageDraw, data: DashboardData, fonts: Fonts, t: dict[str, str]
) -> None:
    draw_text(draw, (MARGIN, CHART_LABEL_BASELINE), t["courses_label"], fonts.label)
    courses = data.course_hours[:TOP_COURSES]
    if not courses:
        draw_text(draw, (MARGIN, CHART_TOP + ROW_HEIGHT - 10), t["courses_none"], fonts.body)
        return
    scale = max(hours for _, hours in courses) or 1.0
    for index, (name, hours) in enumerate(courses):
        top = CHART_TOP + index * ROW_HEIGHT
        baseline = top + BAR_HEIGHT - 3
        label = ellipsize(name or t["course_unknown"], fonts.small, BAR_LEFT - MARGIN - 12)
        draw_text(draw, (MARGIN, baseline), label, fonts.small)
        width = int((BAR_RIGHT - BAR_LEFT) * hours / scale)
        draw.rectangle((BAR_LEFT, top, BAR_LEFT + max(width, 2), top + BAR_HEIGHT), fill=BLACK)
        value = t["hours_short"].format(hours=format_decimal(hours, t["decimal"]))
        draw_text(draw, (WIDTH - MARGIN, baseline), value, fonts.small, anchor="rs")


def render(data: DashboardData, language: str) -> Image:
    t = TEXT[language]
    fonts = load_fonts()
    canvas, draw = new_canvas()
    draw_header(draw, data, fonts, t)
    _draw_hero(draw, data, fonts, t)
    _draw_course_chart(draw, data, fonts, t)
    footer = t["separator"].join(
        [
            f"{t['streak_label']} {format_streak(data.streak_days, t)}",
            t["today_line"].format(hours=format_hours_clock(data.today_hours)),
        ]
    )
    draw_footer_line(draw, footer, fonts, FOOTER_RULE_Y)
    return finish(canvas)
