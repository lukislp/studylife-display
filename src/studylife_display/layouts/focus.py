"""The timer as the hero.

With a running timer: the remaining time of the current phase, huge, with the phase and the
round above it. The number is a snapshot computed against `data.now`; the panel refreshes
every five minutes, so it is "what was left when the frame was drawn", never a live tick.
Without a timer: today's hours, huge, and "no timer running". One thin line at the bottom
with the streak and the next goal, nothing else.
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
    format_minutes_seconds,
    format_streak,
    format_timer_phase,
    load_fonts,
    new_canvas,
    text_width,
)
from studylife_display.model import DashboardData

LABEL_BASELINE = 112
HERO_BASELINE = 300
FOOTER_RULE_Y = 428


def remaining_seconds(data: DashboardData) -> float | None:
    """Seconds left in the current timer phase at `data.now`, or None without a timer or an
    end time."""
    timer = data.timer
    if timer is None or not timer.is_running or timer.phase_ends_at is None:
        return None
    return timer.phase_ends_at.timestamp() - data.now.timestamp()


def render(data: DashboardData, language: str) -> Image.Image:
    t = TEXT[language]
    fonts = load_fonts()
    canvas, draw = new_canvas()
    draw_header(draw, data, fonts, t)

    centre = WIDTH / 2
    timer = data.timer
    if timer is not None and timer.is_running:
        label = format_timer_phase(data, t)
        seconds = remaining_seconds(data)
        hero = "--:--" if seconds is None else format_minutes_seconds(seconds)
        draw_text(draw, (centre, LABEL_BASELINE), label, fonts.value, anchor="ms")
        draw_text(draw, (centre, HERO_BASELINE), hero, fonts.huge, anchor="ms")
    else:
        draw_text(draw, (centre, LABEL_BASELINE), t["timer_none"], fonts.value, anchor="ms")
        number = format_hours_clock(data.today_hours)
        unit = t["today_unit"]
        width = text_width(number, fonts.huge) + 16 + text_width(unit, fonts.big_unit)
        start = centre - width / 2
        end_x = draw_text(draw, (start, HERO_BASELINE), number, fonts.huge)
        draw_text(draw, (end_x + 16, HERO_BASELINE - 6), unit, fonts.big_unit)

    footer = t["separator"].join(
        [
            f"{t['streak_label']} {format_streak(data.streak_days, t)}",
            format_goal_line(data, t),
        ]
    )
    draw_footer_line(draw, ellipsize(footer, fonts.body, WIDTH - 2 * MARGIN), fonts, FOOTER_RULE_Y)
    return finish(canvas)
