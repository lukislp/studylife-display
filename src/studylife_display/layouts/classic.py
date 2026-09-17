"""The original dashboard: today's hours large on the left, streak and countdown on the
right, the week-quota bar, the 4-week heatmap with legend, and the timer line."""

from __future__ import annotations

from PIL import Image, ImageDraw

from studylife_display.layouts.common import (
    BLACK,
    MARGIN,
    TEXT,
    WIDTH,
    Fonts,
    draw_header,
    draw_heatmap_grid,
    draw_heatmap_legend,
    draw_inverted_block,
    draw_quota_bar,
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
from studylife_display.model import HEATMAP_COLUMNS, DashboardData

BIG_BASELINE = 194
RIGHT_COLUMN_X = 430
STREAK_LABEL_BASELINE = 84
STREAK_VALUE_BASELINE = 120
GOAL_LABEL_BASELINE = 152

QUOTA_LABEL_BASELINE = 236
QUOTA_BAR_TOP = 246
QUOTA_BAR_HEIGHT = 18

HEATMAP_LABEL_BASELINE = 296
HEATMAP_TOP = 324
CELL = 22
GAP = 4

# The inverted countdown block: the render test looks for majority-black pixels here.
COUNTDOWN_BOX = (RIGHT_COLUMN_X, 160, RIGHT_COLUMN_X + 150, 196)
# Where the heatmap cells sit: the focus-layout test checks that this stays white there.
HEATMAP_BOX = (
    MARGIN,
    HEATMAP_TOP,
    MARGIN + HEATMAP_COLUMNS * (CELL + GAP),
    HEATMAP_TOP + 4 * (CELL + GAP),
)

TIMER_RULE_Y = 440
TIMER_BASELINE = 466


def _draw_today(
    draw: ImageDraw.ImageDraw, data: DashboardData, fonts: Fonts, t: dict[str, str]
) -> None:
    number = format_hours_clock(data.today_hours)
    unit = t["today_unit"]
    # "10:30 h heute" is one digit wider than the usual "3:45"; drop to the narrower size
    # rather than run into the right column.
    font = fonts.big
    if (
        MARGIN + text_width(number, font) + 12 + text_width(unit, fonts.big_unit)
        > RIGHT_COLUMN_X - 12
    ):
        font = fonts.big_narrow
    end_x = draw_text(draw, (MARGIN, BIG_BASELINE), number, font)
    draw_text(draw, (end_x + 12, BIG_BASELINE - 4), unit, fonts.big_unit)


def _draw_streak_and_goal(
    draw: ImageDraw.ImageDraw, data: DashboardData, fonts: Fonts, t: dict[str, str]
) -> None:
    x = RIGHT_COLUMN_X
    draw_text(draw, (x, STREAK_LABEL_BASELINE), t["streak_label"], fonts.label)
    draw_text(draw, (x, STREAK_VALUE_BASELINE), format_streak(data.streak_days, t), fonts.value)

    draw_text(draw, (x, GOAL_LABEL_BASELINE), t["goal_label"], fonts.label)
    right, bottom = COUNTDOWN_BOX[2], COUNTDOWN_BOX[3]
    text_baseline = bottom - 11
    goal = data.next_goal
    if goal is None:
        draw_text(draw, (x, text_baseline), t["goal_none"], fonts.body)
        return

    countdown = format_countdown(goal.days_left, t)
    draw_inverted_block(draw, COUNTDOWN_BOX, countdown, fonts.body, text_baseline)
    name = ellipsize(goal.course_name, fonts.body, WIDTH - MARGIN - right - 12)
    draw_text(draw, (right + 12, text_baseline), name, fonts.body)


def _draw_quota(
    draw: ImageDraw.ImageDraw, data: DashboardData, fonts: Fonts, t: dict[str, str]
) -> None:
    quota = data.week_quota
    draw_text(draw, (MARGIN, QUOTA_LABEL_BASELINE), t["quota_label"], fonts.label)
    value = t["quota_value"].format(
        hours=format_decimal(quota.hours, t["decimal"]),
        minimum=format_decimal(quota.target_min, t["decimal"]),
        maximum=format_decimal(quota.target_max, t["decimal"]),
    )
    draw_text(draw, (MARGIN + 150, QUOTA_LABEL_BASELINE), value, fonts.small)
    percent = t["quota_percent"].format(percent=int(round(quota.percent)))
    draw_text(draw, (WIDTH - MARGIN, QUOTA_LABEL_BASELINE), percent, fonts.small, anchor="rs")
    draw_quota_bar(draw, quota, MARGIN, QUOTA_BAR_TOP, WIDTH - MARGIN, QUOTA_BAR_HEIGHT)


def _draw_heatmap(image: Image.Image, data: DashboardData, fonts: Fonts, t: dict[str, str]) -> None:
    draw = ImageDraw.Draw(image)
    draw_text(draw, (MARGIN, HEATMAP_LABEL_BASELINE), t["heatmap_label"], fonts.label)
    draw_heatmap_grid(image, data, t, MARGIN, HEATMAP_TOP, CELL, GAP, fonts.small)

    # Legend to the right of the grid, plus the programme and the week total.
    legend_x = MARGIN + HEATMAP_COLUMNS * (CELL + GAP) + 30
    draw_heatmap_legend(image, t, legend_x, HEATMAP_TOP, CELL, GAP, fonts.small)

    info_x = RIGHT_COLUMN_X
    if data.program_name:
        name = ellipsize(data.program_name, fonts.body, WIDTH - MARGIN - info_x)
        draw_text(draw, (info_x, HEATMAP_TOP + CELL - 4), name, fonts.body)
    week = format_decimal(data.week_hours, t["decimal"])
    draw_text(
        draw, (info_x, HEATMAP_TOP + 2 * (CELL + GAP) + CELL - 4), f"{week} h / 7 d", fonts.body
    )


def _draw_timer(
    draw: ImageDraw.ImageDraw, data: DashboardData, fonts: Fonts, t: dict[str, str]
) -> None:
    timer = data.timer
    if timer is None or not timer.is_running:
        return
    phase = t["timer_break"] if timer.is_break else t["timer_focus"]
    parts = [t["timer_running"], phase]
    if timer.phase_ends_at is not None:
        ends = timer.phase_ends_at.astimezone(data.now.tzinfo).strftime("%H:%M")
        parts.append(t["timer_ends"].format(time=ends))
    line = t["separator"].join(parts)
    draw.line([(MARGIN, TIMER_RULE_Y), (WIDTH - MARGIN, TIMER_RULE_Y)], fill=BLACK)
    draw_text(draw, (MARGIN, TIMER_BASELINE), line, fonts.body)


def render(data: DashboardData, language: str) -> Image.Image:
    t = TEXT[language]
    fonts = load_fonts()
    canvas, draw = new_canvas()

    draw_header(draw, data, fonts, t)
    _draw_today(draw, data, fonts, t)
    _draw_streak_and_goal(draw, data, fonts, t)
    _draw_quota(draw, data, fonts, t)
    _draw_heatmap(canvas, data, fonts, t)
    _draw_timer(draw, data, fonts, t)

    return finish(canvas)
