"""A year of studying as a heatmap: 53 Monday-to-Sunday columns of small outlined cells
(oldest week left, the current one right), weekday initials down the left, month names
along the top, and the year's figures underneath - total hours, active days, sessions and
the longest streak - with the legend on the right when the figures leave room for it.
Never picked by "auto": it is a view to switch to on purpose."""

from __future__ import annotations

from datetime import timedelta

from PIL import Image, ImageDraw, ImageFont

from studylife_display.layouts.common import (
    BLACK,
    MARGIN,
    WIDTH,
    Fonts,
    draw_footer_line,
    draw_header,
    draw_heatmap_legend,
    draw_text,
    ellipsize,
    fill_pattern,
    finish,
    format_decimal,
    format_hours_clock,
    format_streak,
    heatmap_level,
    load_fonts,
    new_canvas,
    text_width,
)
from studylife_display.layouts.common import TEXT as COMMON_TEXT
from studylife_display.model import YEAR_WEEKS, DashboardData, YearData

TEXT: dict[str, dict[str, str]] = {
    "de": {
        "label": "LETZTE 53 WOCHEN",
        "total": "{hours} h gesamt",
        "active_one": "1 aktiver Tag",
        "active_many": "{days} aktive Tage",
        "longest": "längste Serie {streak}",
        "months": "Jan,Feb,Mär,Apr,Mai,Jun,Jul,Aug,Sep,Okt,Nov,Dez",
    },
    "en": {
        "label": "LAST 53 WEEKS",
        "total": "{hours} h total",
        "active_one": "1 active day",
        "active_many": "{days} active days",
        "longest": "longest streak {streak}",
        "months": "Jan,Feb,Mar,Apr,May,Jun,Jul,Aug,Sep,Oct,Nov,Dec",
    },
}

DAYS_PER_WEEK = 7
# Weekday rows that get an initial: every second one keeps the labels off each other at the
# row pitch of the full grid; the pane's tighter pitch labels Monday, Thursday and Sunday.
LABELLED_ROWS = (0, 2, 4, 6)
PANE_LABELLED_ROWS = (0, 3, 6)

LABEL_BASELINE = 86
MONTHS_BASELINE = 144
CELL = 11
GAP = 2
GRID_LEFT = MARGIN + 22
GRID_TOP = 152
GRID_WIDTH = YEAR_WEEKS * (CELL + GAP) - GAP
GRID_HEIGHT = DAYS_PER_WEEK * (CELL + GAP) - GAP
# Where the cells sit: the render test checks the region against an empty history.
GRID_BOX = (GRID_LEFT, GRID_TOP, GRID_LEFT + GRID_WIDTH, GRID_TOP + GRID_HEIGHT)

STATS_BASELINE = 294
STATS_LINE_HEIGHT = 34
LEGEND_LEFT = WIDTH - MARGIN - 120
LEGEND_TOP = 270
LEGEND_CELL = 22
LEGEND_GAP = 4

PANE_CELL = 5
PANE_GAP = 1
PANE_GRID_LEFT_INSET = 16
PANE_MONTHS_BASELINE = 14
PANE_GRID_TOP = 28
PANE_STATS_BASELINE = 104
PANE_STATS_LINE_HEIGHT = 26
PANE_LEGEND_CELL = 16
PANE_LEGEND_GAP = 4

FOOTER_RULE_Y = 428


def padded_weeks(year: YearData) -> tuple[tuple[float, ...], ...]:
    """Exactly YEAR_WEEKS columns of DAYS_PER_WEEK values: a short or empty history (the
    default YearData has no columns at all) is padded with empty weeks on the left."""
    weeks = tuple(
        tuple(week[:DAYS_PER_WEEK]) + (0.0,) * max(0, DAYS_PER_WEEK - len(week))
        for week in year.weeks[-YEAR_WEEKS:]
    )
    missing = YEAR_WEEKS - len(weeks)
    return ((0.0,) * DAYS_PER_WEEK,) * missing + weeks


def stats_items(data: DashboardData, t: dict[str, str], ct: dict[str, str]) -> list[str]:
    """The four figures under the grid, as separate items for `pack_lines`."""
    year = data.year
    active_key = "active_one" if year.active_days == 1 else "active_many"
    sessions_key = "review_sessions_one" if year.session_count == 1 else "review_sessions_many"
    return [
        t["total"].format(hours=format_decimal(year.total_hours, ct["decimal"])),
        t[active_key].format(days=year.active_days),
        ct[sessions_key].format(count=year.session_count),
        t["longest"].format(streak=format_streak(data.longest_streak_days, ct)),
    ]


def pack_lines(
    items: list[str], separator: str, font: ImageFont.FreeTypeFont, max_width: float
) -> list[str]:
    """Joins `items` with `separator` into as few lines as fit into `max_width`, never
    splitting an item; an item wider than the line stands alone (the caller ellipsizes)."""
    lines: list[str] = []
    current = ""
    for item in items:
        candidate = item if not current else current + separator + item
        if current and text_width(candidate, font) > max_width:
            lines.append(current)
            current = item
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _draw_grid(
    image: Image.Image, year: YearData, left: int, top: int, cell: int, gap: int
) -> None:
    """The outlined cells, each filled with the pattern of its level with a 1 px inset."""
    draw = ImageDraw.Draw(image)
    for column, week in enumerate(padded_weeks(year)):
        x0 = left + column * (cell + gap)
        for row, hours in enumerate(week):
            y0 = top + row * (cell + gap)
            draw.rectangle((x0, y0, x0 + cell - 1, y0 + cell - 1), outline=BLACK, width=1)
            fill_pattern(
                image, (x0 + 1, y0 + 1, x0 + cell - 1, y0 + cell - 1), heatmap_level(hours)
            )


def _draw_weekday_initials(
    draw: ImageDraw.ImageDraw,
    ct: dict[str, str],
    x: int,
    top: int,
    cell: int,
    gap: int,
    rows: tuple[int, ...],
    font: ImageFont.FreeTypeFont,
) -> None:
    initials = ct["weekday_initials"].split(",")
    for row in rows:
        baseline = top + row * (cell + gap) + cell - 1
        draw_text(draw, (x, baseline), initials[row], font)


def _draw_month_labels(
    draw: ImageDraw.ImageDraw,
    year: YearData,
    t: dict[str, str],
    left: int,
    baseline: int,
    cell: int,
    gap: int,
    font: ImageFont.FreeTypeFont,
    right_limit: int,
) -> None:
    """A month's name over the first column whose Monday falls in it; labels that would run
    into the previous one or past `right_limit` are skipped."""
    months = t["months"].split(",")
    last_end = -1.0
    previous_month = None
    for column in range(YEAR_WEEKS):
        monday = year.first_monday + timedelta(days=DAYS_PER_WEEK * column)
        if monday.month == previous_month:
            continue
        previous_month = monday.month
        label = months[monday.month - 1]
        x = left + column * (cell + gap)
        width = text_width(label, font)
        if x < last_end + 8 or x + width > right_limit:
            continue
        draw_text(draw, (x, baseline), label, font)
        last_end = x + width


def _draw_stats(
    draw: ImageDraw.ImageDraw,
    data: DashboardData,
    fonts: Fonts,
    t: dict[str, str],
    ct: dict[str, str],
) -> list[str]:
    max_width = WIDTH - 2 * MARGIN
    lines = pack_lines(stats_items(data, t, ct), ct["separator"], fonts.body, max_width)
    for index, line in enumerate(lines):
        baseline = STATS_BASELINE + index * STATS_LINE_HEIGHT
        draw_text(draw, (MARGIN, baseline), ellipsize(line, fonts.body, max_width), fonts.body)
    return lines


def render(data: DashboardData, language: str) -> Image.Image:
    t = TEXT[language]
    ct = COMMON_TEXT[language]
    fonts = load_fonts()
    canvas, draw = new_canvas()
    draw_header(draw, data, fonts, ct)
    draw_text(draw, (MARGIN, LABEL_BASELINE), t["label"], fonts.label)
    _draw_month_labels(
        draw, data.year, t, GRID_LEFT, MONTHS_BASELINE, CELL, GAP, fonts.small, WIDTH - MARGIN
    )
    _draw_weekday_initials(draw, ct, MARGIN, GRID_TOP, CELL, GAP, LABELLED_ROWS, fonts.small)
    _draw_grid(canvas, data.year, GRID_LEFT, GRID_TOP, CELL, GAP)
    lines = _draw_stats(draw, data, fonts, t, ct)
    # The legend only when no stats line reaches into its column.
    if all(text_width(line, fonts.body) < LEGEND_LEFT - MARGIN - 24 for line in lines):
        draw_heatmap_legend(
            canvas, ct, LEGEND_LEFT, LEGEND_TOP, LEGEND_CELL, LEGEND_GAP, fonts.small
        )
    footer = ct["separator"].join(
        [
            ct["today_line"].format(hours=format_hours_clock(data.today_hours)),
            f"{ct['streak_label']} {format_streak(data.streak_days, ct)}",
        ]
    )
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
    """The same grid at a fifth of the size with the month names, the figures as small
    lines underneath and the legend below them."""
    t = TEXT[language]
    ct = COMMON_TEXT[language]
    left, top, right, _ = box
    grid_left = left + PANE_GRID_LEFT_INSET
    grid_top = top + PANE_GRID_TOP
    _draw_month_labels(
        draw,
        data.year,
        t,
        grid_left,
        top + PANE_MONTHS_BASELINE,
        PANE_CELL,
        PANE_GAP,
        fonts.small,
        right,
    )
    _draw_weekday_initials(
        draw, ct, left, grid_top, PANE_CELL, PANE_GAP, PANE_LABELLED_ROWS, fonts.small
    )
    _draw_grid(image, data.year, grid_left, grid_top, PANE_CELL, PANE_GAP)
    max_width = right - left
    lines = pack_lines(stats_items(data, t, ct), ct["separator"], fonts.small, max_width)
    baseline = top + PANE_STATS_BASELINE
    for line in lines:
        draw_text(draw, (left, baseline), ellipsize(line, fonts.small, max_width), fonts.small)
        baseline += PANE_STATS_LINE_HEIGHT
    draw_heatmap_legend(
        image, ct, left, baseline + 2, PANE_LEGEND_CELL, PANE_LEGEND_GAP, fonts.small
    )
