"""The month as the hero.

"MONATSZIEL · September 2026" over the month quota bar, large like the week layout's, with
the hours against the target range and the percent. Under it every day of the month as a
small bar (from the session history; days still to come are empty ticks, today's tick is
thicker), labelled 1, 10, 20 and the last day. Then the days left in the month, today
included, and what is still needed per day to reach the minimum. The footer compares the
month with the previous one and - once there is a year of data - with the same month a
year ago.
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
    draw_quota_bar,
    draw_text,
    ellipsize,
    finish,
    format_decimal,
    load_fonts,
    new_canvas,
    text_width,
)
from studylife_display.layouts.common import TEXT as COMMON_TEXT
from studylife_display.model import DashboardData, WeekQuota, days_in_month

TEXT: dict[str, dict[str, str]] = {
    "de": {
        "month_label": "MONATSZIEL",
        "months": (
            "Januar,Februar,März,April,Mai,Juni,Juli,August,September,Oktober,November,Dezember"
        ),
        "days_left_label": "RESTTAGE",
        "per_day": "noch {hours} h/Tag für das Minimum",
        "minimum_reached": "Minimum erreicht",
        "previous_month": "Vormonat {hours} h",
        "last_year": "Vorjahr {hours} h",
        "delta": "{sign}{hours} h",
    },
    "en": {
        "month_label": "MONTH TARGET",
        "months": (
            "January,February,March,April,May,June,July,August,September,October,November,December"
        ),
        "days_left_label": "DAYS LEFT",
        "per_day": "{hours} h/day for the minimum",
        "minimum_reached": "minimum reached",
        "previous_month": "Last month {hours} h",
        "last_year": "Last year {hours} h",
        "delta": "{sign}{hours} h",
    },
}

QUOTA_LABEL_BASELINE = 86
QUOTA_BAR_TOP = 98
QUOTA_BAR_HEIGHT = 36
QUOTA_TEXT_BASELINE = 178
# The filled part of the bar: the render test checks that it is solid black.
QUOTA_BAR_BOX = (MARGIN, QUOTA_BAR_TOP, WIDTH - MARGIN, QUOTA_BAR_TOP + QUOTA_BAR_HEIGHT)

STRIP_TOP = 204
STRIP_BOTTOM = 318
BAR_GAP = 3
DAY_LABEL_BASELINE = 340
# Where the day bars stand: the render test checks that a month without sessions leaves it
# (nearly) white and the sample month does not.
STRIP_BOX = (MARGIN, STRIP_TOP, WIDTH - MARGIN, STRIP_BOTTOM)

DAYS_LEFT_LABEL_BASELINE = 372
DAYS_LEFT_VALUE_BASELINE = 410
PER_DAY_X = MARGIN + 110

FOOTER_RULE_Y = 428


def month_title(data: DashboardData, t: dict[str, str], c: dict[str, str]) -> str:
    """ "MONATSZIEL · September 2026" - the month name from the table, never strftime."""
    month = t["months"].split(",")[data.now.month - 1]
    return f"{t['month_label']}{c['separator']}{month} {data.now.year}"


def days_left(data: DashboardData) -> int:
    """Days left in the current month, today included."""
    today = data.now.date()
    return days_in_month(today) - today.day + 1


def per_day_line(data: DashboardData, t: dict[str, str], c: dict[str, str]) -> str:
    """ "noch 1,4 h/Tag für das Minimum" or "Minimum erreicht"."""
    quota = data.month_quota
    missing = max(0.0, quota.target_min - quota.hours)
    if missing < 0.05:
        return t["minimum_reached"]
    per_day = missing / max(1, days_left(data))
    return t["per_day"].format(hours=format_decimal(per_day, c["decimal"]))


def format_signed_hours(delta: float, t: dict[str, str], c: dict[str, str]) -> str:
    """ "+5 h", "−11,5 h" or "±0 h"."""
    rounded = round(delta, 1)
    if rounded > 0:
        sign = "+"
    elif rounded < 0:
        sign = "−"
    else:
        sign = "±"
    return t["delta"].format(sign=sign, hours=format_decimal(abs(rounded), c["decimal"]))


def comparison_line(data: DashboardData, t: dict[str, str], c: dict[str, str]) -> str:
    """ "Vormonat 52,5 h · −11,5 h · Vorjahr 36 h · +5 h" (the year part only with a year
    of data)."""
    comparison = data.month_comparison
    parts = [
        t["previous_month"].format(
            hours=format_decimal(comparison.previous_month_hours, c["decimal"])
        ),
        format_signed_hours(comparison.delta_vs_previous_month, t, c),
    ]
    if comparison.has_year_data and comparison.same_month_last_year_hours is not None:
        parts.append(
            t["last_year"].format(
                hours=format_decimal(comparison.same_month_last_year_hours, c["decimal"])
            )
        )
        parts.append(format_signed_hours(comparison.delta_vs_last_year or 0.0, t, c))
    return c["separator"].join(parts)


def _quota_value(quota: WeekQuota, c: dict[str, str]) -> str:
    return c["quota_value"].format(
        hours=format_decimal(quota.hours, c["decimal"]),
        minimum=format_decimal(quota.target_min, c["decimal"]),
        maximum=format_decimal(quota.target_max, c["decimal"]),
    )


def _draw_quota(
    draw: ImageDraw.ImageDraw,
    data: DashboardData,
    fonts: Fonts,
    t: dict[str, str],
    c: dict[str, str],
) -> None:
    quota = data.month_quota
    draw_text(draw, (MARGIN, QUOTA_LABEL_BASELINE), month_title(data, t, c), fonts.label)
    draw_quota_bar(
        draw, quota, MARGIN, QUOTA_BAR_TOP, WIDTH - MARGIN, QUOTA_BAR_HEIGHT, tick_overhang=8
    )
    draw_text(draw, (MARGIN, QUOTA_TEXT_BASELINE), _quota_value(quota, c), fonts.body)
    percent = c["quota_percent"].format(percent=int(round(quota.percent)))
    draw_text(draw, (WIDTH - MARGIN, QUOTA_TEXT_BASELINE), percent, fonts.value, anchor="rs")


def _draw_strip(
    draw: ImageDraw.ImageDraw, data: DashboardData, fonts: Fonts, t: dict[str, str]
) -> None:
    days = data.month_days
    count = len(days)
    if count == 0:
        return
    pitch = (WIDTH - 2 * MARGIN) / count
    bar_width = max(2, int(pitch) - BAR_GAP)
    scale = max(days)
    height = STRIP_BOTTOM - STRIP_TOP
    today_index = data.now.day - 1
    labelled = {0, 9, 19, count - 1}
    for index, hours in enumerate(days):
        left = int(MARGIN + index * pitch)
        right = left + bar_width
        centre = (left + right) / 2
        # A baseline tick for every day, the bar grows out of it; today's tick is thicker.
        draw.line(
            [(left, STRIP_BOTTOM), (right, STRIP_BOTTOM)],
            fill=BLACK,
            width=4 if index == today_index else 1,
        )
        if hours > 0 and scale > 0:
            bar = max(2, int((height - 6) * hours / scale))
            draw.rectangle((left, STRIP_BOTTOM - bar, right, STRIP_BOTTOM), fill=BLACK)
        if index in labelled:
            draw_text(draw, (centre, DAY_LABEL_BASELINE), str(index + 1), fonts.small, anchor="ms")


def _draw_days_left(
    draw: ImageDraw.ImageDraw,
    data: DashboardData,
    fonts: Fonts,
    t: dict[str, str],
    c: dict[str, str],
) -> None:
    draw_text(draw, (MARGIN, DAYS_LEFT_LABEL_BASELINE), t["days_left_label"], fonts.label)
    number = str(days_left(data))
    draw_text(draw, (MARGIN, DAYS_LEFT_VALUE_BASELINE), number, fonts.value)
    x = max(PER_DAY_X, MARGIN + text_width(number, fonts.value) + 24)
    line = ellipsize(per_day_line(data, t, c), fonts.body, WIDTH - MARGIN - x)
    draw_text(draw, (x, DAYS_LEFT_VALUE_BASELINE), line, fonts.body)


def render(data: DashboardData, language: str) -> Image.Image:
    t = TEXT[language]
    c = COMMON_TEXT[language]
    fonts = load_fonts()
    canvas, draw = new_canvas()
    draw_header(draw, data, fonts, c)
    _draw_quota(draw, data, fonts, t, c)
    _draw_strip(draw, data, fonts, t)
    _draw_days_left(draw, data, fonts, t, c)
    footer = ellipsize(comparison_line(data, t, c), fonts.body, WIDTH - 2 * MARGIN)
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
    """The month title, the quota bar with its value line and percent, and the days-left /
    per-day lines."""
    t = TEXT[language]
    c = COMMON_TEXT[language]
    left, top, right, _ = box
    width = right - left
    quota = data.month_quota

    title = ellipsize(month_title(data, t, c), fonts.small, width)
    draw_text(draw, (left, top + 18), title, fonts.small)
    draw_quota_bar(draw, quota, left, top + 30, right, 24)
    draw_text(
        draw,
        (left, top + 92),
        ellipsize(_quota_value(quota, c), fonts.body, width - 90),
        fonts.body,
    )
    percent = c["quota_percent"].format(percent=int(round(quota.percent)))
    draw_text(draw, (right, top + 96), percent, fonts.value, anchor="rs")

    draw_text(draw, (left, top + 150), t["days_left_label"], fonts.label)
    draw_text(draw, (left, top + 190), str(days_left(data)), fonts.value)
    line = ellipsize(per_day_line(data, t, c), fonts.body, width)
    draw_text(draw, (left, top + 236), line, fonts.body)
