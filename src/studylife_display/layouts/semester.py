"""The semester as the hero: ECTS earned of total as a big number with a progress bar,
the average grade and the graduation forecast on the right, the course that has gone
longest without a session and the topic progress underneath, and the programme name with
today's hours at the bottom. Never picked by "auto": it is a view to switch to on purpose."""

from __future__ import annotations

from PIL import Image, ImageDraw

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
    load_fonts,
    new_canvas,
    text_width,
)
from studylife_display.model import DashboardData

ECTS_LABEL_BASELINE = 86
ECTS_BASELINE = 190
ECTS_BAR_TOP = 210
ECTS_BAR_HEIGHT = 22
ECTS_BAR_RIGHT = 430
# The filled part of the bar: the render test checks that it is solid black.
ECTS_BAR_BOX = (MARGIN, ECTS_BAR_TOP, ECTS_BAR_RIGHT, ECTS_BAR_TOP + ECTS_BAR_HEIGHT)

RIGHT_COLUMN_X = 480
GRADE_LABEL_BASELINE = 86
GRADE_VALUE_BASELINE = 132
FORECAST_LABEL_BASELINE = 178
FORECAST_VALUE_BASELINE = 224

NEGLECTED_LABEL_BASELINE = 300
NEGLECTED_VALUE_BASELINE = 340
TOPICS_LABEL_BASELINE = 300
TOPICS_VALUE_BASELINE = 340
TOPICS_BAR_TOP = 356
TOPICS_BAR_HEIGHT = 12

FOOTER_RULE_Y = 428


def _draw_bar(
    draw: ImageDraw.ImageDraw, left: int, top: int, right: int, height: int, fraction: float
) -> None:
    """An outlined bar filled to `fraction` (clamped to 0..1)."""
    bottom = top + height
    draw.rectangle((left, top, right, bottom), outline=BLACK, width=2)
    fill_right = left + int((right - left) * max(0.0, min(1.0, fraction)))
    if fill_right > left + 2:
        draw.rectangle((left + 2, top + 2, fill_right - 1, bottom - 2), fill=BLACK)


def _draw_ects(
    draw: ImageDraw.ImageDraw, data: DashboardData, fonts: Fonts, t: dict[str, str]
) -> None:
    ects = data.ects
    draw_text(draw, (MARGIN, ECTS_LABEL_BASELINE), t["ects_label"], fonts.label)
    number = format_decimal(ects.earned, t["decimal"])
    rest = t["ects_value"].format(earned="", total=format_decimal(ects.total, t["decimal"]))
    rest = rest.strip()
    font = fonts.big
    if MARGIN + text_width(number, font) + 12 + text_width(rest, fonts.big_unit) > ECTS_BAR_RIGHT:
        font = fonts.big_narrow
    end_x = draw_text(draw, (MARGIN, ECTS_BASELINE), number, font)
    draw_text(draw, (end_x + 12, ECTS_BASELINE - 4), rest, fonts.big_unit)
    fraction = ects.earned / ects.total if ects.total > 0 else 0.0
    _draw_bar(draw, MARGIN, ECTS_BAR_TOP, ECTS_BAR_RIGHT, ECTS_BAR_HEIGHT, fraction)
    percent = t["ects_percent"].format(percent=int(round(fraction * 100)))
    draw_text(draw, (ECTS_BAR_RIGHT, ECTS_BAR_TOP - 8), percent, fonts.small, anchor="rs")


def _draw_grade_and_forecast(
    draw: ImageDraw.ImageDraw, data: DashboardData, fonts: Fonts, t: dict[str, str]
) -> None:
    x = RIGHT_COLUMN_X
    max_width = WIDTH - MARGIN - x
    draw_text(draw, (x, GRADE_LABEL_BASELINE), t["grade_label"], fonts.label)
    if data.average_grade is None:
        draw_text(draw, (x, GRADE_VALUE_BASELINE), t["grade_none"], fonts.body)
    else:
        grade = f"{data.average_grade:.1f}".replace(".", t["decimal"])
        draw_text(draw, (x, GRADE_VALUE_BASELINE), grade, fonts.value)

    draw_text(
        draw,
        (x, FORECAST_LABEL_BASELINE),
        ellipsize(t["forecast_label"], fonts.label, max_width),
        fonts.label,
    )
    forecast = data.forecast
    if forecast.already_done:
        draw_text(draw, (x, FORECAST_VALUE_BASELINE), t["forecast_done"], fonts.value)
    elif forecast.available and forecast.date is not None:
        date = forecast.date.strftime(t["goal_date_format"])
        draw_text(draw, (x, FORECAST_VALUE_BASELINE), date, fonts.value)
    else:
        draw_text(draw, (x, FORECAST_VALUE_BASELINE), t["forecast_none"], fonts.body)


def _neglected_line(data: DashboardData, t: dict[str, str]) -> str:
    course = data.neglected_course
    if course is None:
        return t["neglected_none"]
    if course.days_since is None:
        return t["neglected_never"].format(course=course.course_name)
    if course.days_since == 1:
        return t["neglected_one"].format(course=course.course_name)
    return t["neglected_days"].format(course=course.course_name, days=course.days_since)


def _draw_neglected_and_topics(
    draw: ImageDraw.ImageDraw, data: DashboardData, fonts: Fonts, t: dict[str, str]
) -> None:
    draw_text(draw, (MARGIN, NEGLECTED_LABEL_BASELINE), t["neglected_label"], fonts.label)
    line = ellipsize(_neglected_line(data, t), fonts.body, RIGHT_COLUMN_X - MARGIN - 24)
    draw_text(draw, (MARGIN, NEGLECTED_VALUE_BASELINE), line, fonts.body)

    x = RIGHT_COLUMN_X
    topics = data.topics
    draw_text(draw, (x, TOPICS_LABEL_BASELINE), t["topics_label"], fonts.label)
    value = t["topics_value"].format(completed=topics.completed, total=topics.total)
    draw_text(draw, (x, TOPICS_VALUE_BASELINE), value, fonts.body)
    fraction = topics.completed / topics.total if topics.total > 0 else 0.0
    _draw_bar(draw, x, TOPICS_BAR_TOP, WIDTH - MARGIN, TOPICS_BAR_HEIGHT, fraction)


def render(data: DashboardData, language: str) -> Image.Image:
    t = TEXT[language]
    fonts = load_fonts()
    canvas, draw = new_canvas()
    draw_header(draw, data, fonts, t)
    _draw_ects(draw, data, fonts, t)
    _draw_grade_and_forecast(draw, data, fonts, t)
    _draw_neglected_and_topics(draw, data, fonts, t)
    parts = [t["today_line"].format(hours=format_hours_clock(data.today_hours))]
    if data.program_name:
        parts.insert(0, data.program_name)
    footer = ellipsize(t["separator"].join(parts), fonts.body, WIDTH - 2 * MARGIN)
    draw_footer_line(draw, footer, fonts, FOOTER_RULE_Y)
    return finish(canvas)
