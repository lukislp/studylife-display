"""The week as the hero: the quota bar large at the top with hours, target and percent,
the 4-week heatmap large with weekday initials and the per-week sums on the right, and a
small today/streak line at the bottom."""

from __future__ import annotations

from PIL import Image, ImageDraw

from studylife_display.layouts.common import (
    MARGIN,
    TEXT,
    WIDTH,
    Fonts,
    draw_footer_line,
    draw_header,
    draw_heatmap_grid,
    draw_heatmap_legend,
    draw_text,
    ellipsize,
    finish,
    format_decimal,
    format_hours_clock,
    format_streak,
    load_fonts,
    new_canvas,
)
from studylife_display.model import HEATMAP_COLUMNS, DashboardData

QUOTA_LABEL_BASELINE = 86
QUOTA_BAR_TOP = 98
QUOTA_BAR_HEIGHT = 36
QUOTA_TEXT_BASELINE = 178
# The filled part of the bar: the render test checks that it is solid black.
QUOTA_BAR_BOX = (MARGIN, QUOTA_BAR_TOP, WIDTH - MARGIN, QUOTA_BAR_TOP + QUOTA_BAR_HEIGHT)

HEATMAP_LABEL_BASELINE = 220
HEATMAP_TOP = 250
CELL = 38
GAP = 6
SUMS_X = MARGIN + HEATMAP_COLUMNS * (CELL + GAP) + 18
INFO_X = 520

FOOTER_RULE_Y = 440


def _draw_quota(
    draw: ImageDraw.ImageDraw, data: DashboardData, fonts: Fonts, t: dict[str, str]
) -> None:
    from studylife_display.layouts.common import draw_quota_bar

    quota = data.week_quota
    draw_text(draw, (MARGIN, QUOTA_LABEL_BASELINE), t["quota_label"], fonts.label)
    draw_quota_bar(
        draw, quota, MARGIN, QUOTA_BAR_TOP, WIDTH - MARGIN, QUOTA_BAR_HEIGHT, tick_overhang=8
    )
    value = t["quota_value"].format(
        hours=format_decimal(quota.hours, t["decimal"]),
        minimum=format_decimal(quota.target_min, t["decimal"]),
        maximum=format_decimal(quota.target_max, t["decimal"]),
    )
    draw_text(draw, (MARGIN, QUOTA_TEXT_BASELINE), value, fonts.body)
    percent = t["quota_percent"].format(percent=int(round(quota.percent)))
    draw_text(draw, (WIDTH - MARGIN, QUOTA_TEXT_BASELINE), percent, fonts.value, anchor="rs")


def _draw_heatmap(image: Image.Image, data: DashboardData, fonts: Fonts, t: dict[str, str]) -> None:
    draw = ImageDraw.Draw(image)
    draw_text(draw, (MARGIN, HEATMAP_LABEL_BASELINE), t["heatmap_label"], fonts.label)
    draw_heatmap_grid(image, data, t, MARGIN, HEATMAP_TOP, CELL, GAP, fonts.small, 8)
    for row_index, row in enumerate(data.heatmap):
        baseline = HEATMAP_TOP + row_index * (CELL + GAP) + CELL - 9
        total = t["week_sum"].format(hours=format_decimal(sum(row), t["decimal"]))
        draw_text(draw, (SUMS_X, baseline), total, fonts.body)

    if data.program_name:
        name = ellipsize(data.program_name, fonts.body, WIDTH - MARGIN - INFO_X)
        draw_text(draw, (INFO_X, HEATMAP_TOP + CELL - 9), name, fonts.body)
    week = format_decimal(data.week_hours, t["decimal"])
    draw_text(draw, (INFO_X, HEATMAP_TOP + (CELL + GAP) + CELL - 9), f"{week} h / 7 d", fonts.body)
    draw_heatmap_legend(image, t, INFO_X, HEATMAP_TOP + 2 * (CELL + GAP) + 6, 22, 4, fonts.small)


def render(data: DashboardData, language: str) -> Image.Image:
    t = TEXT[language]
    fonts = load_fonts()
    canvas, draw = new_canvas()
    draw_header(draw, data, fonts, t)
    _draw_quota(draw, data, fonts, t)
    _draw_heatmap(canvas, data, fonts, t)
    footer = t["separator"].join(
        [
            t["today_line"].format(hours=format_hours_clock(data.today_hours)),
            f"{t['streak_label']} {format_streak(data.streak_days, t)}",
        ]
    )
    draw_footer_line(draw, footer, fonts, FOOTER_RULE_Y)
    return finish(canvas)
