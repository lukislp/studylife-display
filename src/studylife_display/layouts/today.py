"""Today's hours as the hero, readable across the room.

The number is `format_hours_clock(today_hours)` in the huge font, centred, with the unit
beside it; a label above, one line with today's session count and the streak underneath,
and the distance to the daily target - the week minimum spread over seven days - as the
actionable line. The footer is the week quota, like the agenda layout's. Nothing else: this
is the frame to glance at from across the room.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from studylife_display.layouts.common import (
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
from studylife_display.model import DashboardData

TEXT: dict[str, dict[str, str]] = {
    "de": {
        "today_label": "HEUTE GELERNT",
        "sessions_one": "1 Session",
        "sessions_many": "{count} Sessions",
        "target_left": "noch {hours} h bis zum Tagesziel",
        "target_reached": "Tagesziel erreicht",
    },
    "en": {
        "today_label": "STUDIED TODAY",
        "sessions_one": "1 session",
        "sessions_many": "{count} sessions",
        "target_left": "{hours} h to the daily target",
        "target_reached": "daily target reached",
    },
}

LABEL_BASELINE = 96
HERO_BASELINE = 268
UNIT_GAP = 16
SESSIONS_BASELINE = 322
TARGET_BASELINE = 382
FOOTER_RULE_Y = 428

# Where the huge number stands: the render test checks that it is not white.
HERO_BOX = (200, HERO_BASELINE - 140, WIDTH - 200, HERO_BASELINE)
# Where the target line stands: white when there is no week target.
TARGET_BOX = (MARGIN, TARGET_BASELINE - 32, WIDTH - MARGIN, TARGET_BASELINE + 10)


def daily_target_hours(data: DashboardData) -> float:
    """The week minimum spread evenly over the seven days; 0 without a week target."""
    return max(0.0, data.week_quota.target_min) / 7


def format_sessions(count: int, t: dict[str, str]) -> str:
    key = "sessions_one" if count == 1 else "sessions_many"
    return t[key].format(count=count)


def format_target_line(data: DashboardData, t: dict[str, str], c: dict[str, str]) -> str:
    """ "noch 1,2 h bis zum Tagesziel", "Tagesziel erreicht", or "" without a target."""
    target = daily_target_hours(data)
    if target <= 0:
        return ""
    remaining = max(0.0, target - data.today_hours)
    if remaining < 0.05:
        return t["target_reached"]
    return t["target_left"].format(hours=format_decimal(remaining, c["decimal"]))


def format_week_quota_line(data: DashboardData, c: dict[str, str]) -> str:
    """ "WOCHENZIEL 12,5 h von 15–20 h" - the agenda layout's footer."""
    quota = data.week_quota
    value = c["quota_value"].format(
        hours=format_decimal(quota.hours, c["decimal"]),
        minimum=format_decimal(quota.target_min, c["decimal"]),
        maximum=format_decimal(quota.target_max, c["decimal"]),
    )
    return f"{c['quota_label']} {value}"


def _sessions_line(data: DashboardData, t: dict[str, str], c: dict[str, str]) -> str:
    return c["separator"].join(
        [
            format_sessions(data.today_stats.session_count, t),
            f"{c['streak_label']} {format_streak(data.streak_days, c)}",
        ]
    )


def _draw_hours(
    draw: ImageDraw.ImageDraw,
    data: DashboardData,
    fonts: Fonts,
    c: dict[str, str],
    centre: float,
    baseline: float,
    max_width: float,
    first: str = "huge",
) -> None:
    """The clock figure with the unit beside it, centred on `centre`. Starts at the `first`
    size and steps down while a two-digit hour would not fit into `max_width`."""
    number = format_hours_clock(data.today_hours)
    unit = c["today_unit"]
    unit_width = text_width(unit, fonts.big_unit)
    sizes = [fonts.huge, fonts.big, fonts.big_narrow]
    if first == "big":
        sizes = sizes[1:]
    font = sizes[-1]
    for candidate in sizes:
        if text_width(number, candidate) + UNIT_GAP + unit_width <= max_width:
            font = candidate
            break
    width = text_width(number, font) + UNIT_GAP + unit_width
    end_x = draw_text(draw, (centre - width / 2, baseline), number, font)
    draw_text(draw, (end_x + UNIT_GAP, baseline - 6), unit, fonts.big_unit)


def render(data: DashboardData, language: str) -> Image.Image:
    t = TEXT[language]
    c = COMMON_TEXT[language]
    fonts = load_fonts()
    canvas, draw = new_canvas()
    draw_header(draw, data, fonts, c)

    centre = WIDTH / 2
    max_width = WIDTH - 2 * MARGIN
    draw_text(draw, (centre, LABEL_BASELINE), t["today_label"], fonts.label, anchor="ms")
    _draw_hours(draw, data, fonts, c, centre, HERO_BASELINE, max_width)

    line = ellipsize(_sessions_line(data, t, c), fonts.body, max_width)
    draw_text(draw, (centre, SESSIONS_BASELINE), line, fonts.body, anchor="ms")

    target = format_target_line(data, t, c)
    if target:
        target = ellipsize(target, fonts.value, max_width)
        draw_text(draw, (centre, TARGET_BASELINE), target, fonts.value, anchor="ms")

    footer = ellipsize(format_week_quota_line(data, c), fonts.body, max_width)
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
    """The hours in the big font with the unit, the session/streak line and the target line
    under it, all centred in the box."""
    t = TEXT[language]
    c = COMMON_TEXT[language]
    left, top, right, _ = box
    centre = (left + right) / 2
    max_width = right - left

    _draw_hours(draw, data, fonts, c, centre, top + 120, max_width, first="big")
    line = ellipsize(_sessions_line(data, t, c), fonts.body, max_width)
    draw_text(draw, (centre, top + 176), line, fonts.body, anchor="ms")
    target = format_target_line(data, t, c)
    if target:
        target = ellipsize(target, fonts.body, max_width)
        draw_text(draw, (centre, top + 222), target, fonts.body, anchor="ms")
