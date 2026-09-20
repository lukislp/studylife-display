"""The weekly review: this week's hours, large, with the change against the week before
(sign and an up/down marker), the course studied most, the session count and the streak
on the right, the seven days of the week as small bars underneath, and the server's report
of the previous week in the footer.

The hero figures are summed on the Pi from the session history for the current
Monday-to-Sunday week (`DashboardData.this_week`), because StudyLife's own `weeklyReport`
always describes the last *completed* week - on the Sunday evening this layout is picked
for, that is the week before. That server report is what the footer shows.
"""

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
    format_streak,
    load_fonts,
    new_canvas,
    text_width,
)
from studylife_display.model import DashboardData, WeeklyReport

TITLE_BASELINE = 86
HOURS_BASELINE = 204
DELTA_BASELINE = 248
MARKER_SIZE = 14

RIGHT_X = 480
TOP_LABEL_BASELINE = 86
TOP_VALUE_BASELINE = 130
SESSIONS_LABEL_BASELINE = 178
SESSIONS_VALUE_BASELINE = 222
STREAK_LABEL_BASELINE = 270
STREAK_VALUE_BASELINE = 314

STRIP_LABEL_BASELINE = 292
STRIP_TOP = 304
STRIP_BOTTOM = 392
BAR_WIDTH = 40
BAR_GAP = 18
INITIALS_BASELINE = 414
# Where the seven bars stand: the render test checks that a week without sessions leaves
# it white and a week with sessions does not.
STRIP_BOX = (MARGIN, STRIP_TOP, MARGIN + 7 * (BAR_WIDTH + BAR_GAP) - BAR_GAP, STRIP_BOTTOM)

FOOTER_RULE_Y = 428


def format_week(week_id: str, t: dict[str, str]) -> str:
    """ "2026-W38" -> "KW 38" / "W38"; anything else is shown as it is."""
    _, sep, week = week_id.partition("-W")
    if not sep or not week.isdigit():
        return week_id
    return t["review_week"].format(week=int(week))


def format_delta(delta: float, t: dict[str, str]) -> str:
    """ "+2,5 h zur Vorwoche", "−1 h ..." or "±0 h ..."."""
    rounded = round(delta, 1)
    if rounded > 0:
        sign = "+"
    elif rounded < 0:
        sign = "−"
    else:
        sign = "±"
    return t["review_delta"].format(sign=sign, hours=format_decimal(abs(rounded), t["decimal"]))


def format_sessions(count: int, t: dict[str, str]) -> str:
    key = "review_sessions_one" if count == 1 else "review_sessions_many"
    return t[key].format(count=count)


def _draw_marker(draw: ImageDraw.ImageDraw, x: int, baseline: int, delta: float) -> int:
    """An up or down triangle (a dash for no change) in front of the delta line; returns the
    x where the text starts."""
    middle = baseline - 9
    if round(delta, 1) > 0:
        points = [(x, middle + MARKER_SIZE // 2), (x + MARKER_SIZE, middle + MARKER_SIZE // 2),
                  (x + MARKER_SIZE // 2, middle - MARKER_SIZE // 2)]  # fmt: skip
        draw.polygon(points, fill=BLACK)
    elif round(delta, 1) < 0:
        points = [(x, middle - MARKER_SIZE // 2), (x + MARKER_SIZE, middle - MARKER_SIZE // 2),
                  (x + MARKER_SIZE // 2, middle + MARKER_SIZE // 2)]  # fmt: skip
        draw.polygon(points, fill=BLACK)
    else:
        draw.line([(x, middle), (x + MARKER_SIZE, middle)], fill=BLACK, width=3)
    return x + MARKER_SIZE + 10


def _draw_hero(
    draw: ImageDraw.ImageDraw, data: DashboardData, fonts: Fonts, t: dict[str, str]
) -> None:
    week = data.this_week
    title = t["review_label"]
    if week.week_id:
        title += t["separator"] + format_week(week.week_id, t)
    draw_text(draw, (MARGIN, TITLE_BASELINE), title, fonts.label)

    number = format_decimal(week.hours, t["decimal"])
    unit = t["review_unit"]
    font = fonts.big
    if MARGIN + text_width(number, font) + 12 + text_width(unit, fonts.big_unit) > RIGHT_X - 12:
        font = fonts.big_narrow
    end_x = draw_text(draw, (MARGIN, HOURS_BASELINE), number, font)
    draw_text(draw, (end_x + 12, HOURS_BASELINE - 4), unit, fonts.big_unit)

    text_x = _draw_marker(draw, MARGIN, DELTA_BASELINE, week.delta_vs_previous_week)
    delta = ellipsize(
        format_delta(week.delta_vs_previous_week, t), fonts.body, RIGHT_X - 12 - text_x
    )
    draw_text(draw, (text_x, DELTA_BASELINE), delta, fonts.body)


def _draw_side(
    draw: ImageDraw.ImageDraw, data: DashboardData, fonts: Fonts, t: dict[str, str]
) -> None:
    week = data.this_week
    x = RIGHT_X
    max_width = WIDTH - MARGIN - x
    draw_text(draw, (x, TOP_LABEL_BASELINE), t["review_top_label"], fonts.label)
    top = week.top_course_name or t["review_top_none"]
    draw_text(draw, (x, TOP_VALUE_BASELINE), ellipsize(top, fonts.value, max_width), fonts.value)
    draw_text(draw, (x, SESSIONS_LABEL_BASELINE), t["review_sessions_label"], fonts.label)
    draw_text(
        draw, (x, SESSIONS_VALUE_BASELINE), format_sessions(week.session_count, t), fonts.value
    )
    draw_text(draw, (x, STREAK_LABEL_BASELINE), t["streak_label"], fonts.label)
    draw_text(draw, (x, STREAK_VALUE_BASELINE), format_streak(data.streak_days, t), fonts.value)


def _draw_strip(
    draw: ImageDraw.ImageDraw, data: DashboardData, fonts: Fonts, t: dict[str, str]
) -> None:
    draw_text(draw, (MARGIN, STRIP_LABEL_BASELINE), t["review_strip_label"], fonts.label)
    initials = t["weekday_initials"].split(",")
    scale = max(data.week_strip) if data.week_strip else 0.0
    height = STRIP_BOTTOM - STRIP_TOP
    today = data.now.weekday()
    for weekday, hours in enumerate(data.week_strip):
        left = MARGIN + weekday * (BAR_WIDTH + BAR_GAP)
        centre = left + BAR_WIDTH / 2
        # A baseline tick for every day; the bar grows out of it. Today's tick is thicker.
        draw.line(
            [(left, STRIP_BOTTOM), (left + BAR_WIDTH, STRIP_BOTTOM)],
            fill=BLACK,
            width=4 if weekday == today else 1,
        )
        if hours > 0 and scale > 0:
            bar = max(2, int((height - 22) * hours / scale))
            draw.rectangle((left, STRIP_BOTTOM - bar, left + BAR_WIDTH, STRIP_BOTTOM), fill=BLACK)
            label = format_decimal(hours, t["decimal"])
            draw_text(draw, (centre, STRIP_BOTTOM - bar - 6), label, fonts.small, anchor="ms")
        draw_text(draw, (centre, INITIALS_BASELINE), initials[weekday], fonts.small, anchor="ms")


def _previous_week_line(report: WeeklyReport, t: dict[str, str]) -> str:
    if not report.week_id and report.session_count == 0 and report.hours == 0:
        return t["review_previous_none"]
    parts = [
        t["review_previous"].format(
            week=format_week(report.week_id, t) if report.week_id else "?",
            hours=format_decimal(report.hours, t["decimal"]),
            sessions=format_sessions(report.session_count, t),
        )
    ]
    if report.top_course_name:
        parts.append(report.top_course_name)
    return t["separator"].join(parts)


def render(data: DashboardData, language: str) -> Image.Image:
    t = TEXT[language]
    fonts = load_fonts()
    canvas, draw = new_canvas()
    draw_header(draw, data, fonts, t)
    _draw_hero(draw, data, fonts, t)
    _draw_side(draw, data, fonts, t)
    _draw_strip(draw, data, fonts, t)
    footer = ellipsize(_previous_week_line(data.weekly_report, t), fonts.body, WIDTH - 2 * MARGIN)
    draw_footer_line(draw, footer, fonts, FOOTER_RULE_Y)
    return finish(canvas)
