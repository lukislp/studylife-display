"""The streak milestone: a celebration screen for a round number of consecutive study days.

`auto.py` only ever picks this layout on the one day `data.streak_days` actually hits one of
`MILESTONE_DAYS` - this module just draws whatever streak it is handed, the same way `exam`
draws whatever countdown it is handed. Picking it by hand from the web interface always shows
today's real streak, milestone or not.
"""

from __future__ import annotations

from PIL import Image

from studylife_display.layouts.common import (
    MARGIN,
    TEXT,
    WIDTH,
    draw_footer_line,
    draw_header,
    draw_text,
    ellipsize,
    finish,
    format_goal_line,
    format_hours_clock,
    load_fonts,
    new_canvas,
    text_width,
)
from studylife_display.model import DashboardData

# Ticked off once per streak, not re-shown the next day: a first month by week, then round
# hundreds, then every half year. Long enough to stay meaningful, close enough early on to
# actually get celebrated.
MILESTONE_DAYS = frozenset({7, 14, 21, 30, 50, 100, 150, 200, 250, 300, 365, 500, 750, 1000})

LABEL_BASELINE = 112
HERO_BASELINE = 300
FOOTER_RULE_Y = 428


def is_milestone(streak_days: int) -> bool:
    return streak_days in MILESTONE_DAYS


def render(data: DashboardData, language: str) -> Image.Image:
    t = TEXT[language]
    fonts = load_fonts()
    canvas, draw = new_canvas()
    draw_header(draw, data, fonts, t)

    centre = WIDTH / 2
    draw_text(draw, (centre, LABEL_BASELINE), t["milestone_label"], fonts.value, anchor="ms")

    number = str(data.streak_days)
    unit = t["milestone_unit"]
    width = text_width(number, fonts.huge) + 16 + text_width(unit, fonts.big_unit)
    start = centre - width / 2
    end_x = draw_text(draw, (start, HERO_BASELINE), number, fonts.huge)
    draw_text(draw, (end_x + 16, HERO_BASELINE - 6), unit, fonts.big_unit)

    footer = t["separator"].join(
        [
            t["today_line"].format(hours=format_hours_clock(data.today_hours)),
            format_goal_line(data, t),
        ]
    )
    draw_footer_line(draw, ellipsize(footer, fonts.body, WIDTH - 2 * MARGIN), fonts, FOOTER_RULE_Y)
    return finish(canvas)
