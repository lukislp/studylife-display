"""Draws a DashboardData onto an 800x480 black/white frame.

Pure: same data + language -> same pixels. Text is rasterised anti-aliased on a greyscale
canvas and then thresholded (no error-diffusion dithering, which turns glyph edges into
noise on a 1-bit panel); the heatmap fill levels use explicit pixel patterns instead.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from typing import Literal

from PIL import Image, ImageDraw, ImageFont

from studylife_display.model import HEATMAP_COLUMNS, DashboardData

WIDTH = 800
HEIGHT = 480

BLACK = 0
WHITE = 255

Language = Literal["de", "en"]

# Only lookups happen on these tables: the code never branches on the language itself.
TEXT: dict[str, dict[str, str]] = {
    "de": {
        "brand": "STUDYLIFE",
        "updated": "aktualisiert {time}",
        "stale": "· vor {minutes} min",
        "today_unit": "h heute",
        "streak_label": "SERIE",
        "streak_one": "1 Tag",
        "streak_many": "{days} Tage",
        "goal_label": "NÄCHSTE PRÜFUNG",
        "goal_none": "keine anstehend",
        "goal_in_zero": "heute",
        "goal_in_one": "in 1 Tag",
        "goal_in_many": "in {days} Tagen",
        "goal_overdue": "überfällig",
        "quota_label": "WOCHENZIEL",
        "quota_value": "{hours} h von {minimum}–{maximum} h",
        "quota_percent": "{percent} %",
        "heatmap_label": "LETZTE 4 WOCHEN",
        "timer_running": "Timer läuft",
        "timer_focus": "Fokus",
        "timer_break": "Pause",
        "timer_ends": "endet {time}",
        "separator": " · ",
        "decimal": ",",
        "date_format": "%d.%m.",
        "weekdays": "Mo,Di,Mi,Do,Fr,Sa,So",
        "weekday_initials": "M,D,M,D,F,S,S",
    },
    "en": {
        "brand": "STUDYLIFE",
        "updated": "updated {time}",
        "stale": "· {minutes} min ago",
        "today_unit": "h today",
        "streak_label": "STREAK",
        "streak_one": "1 day",
        "streak_many": "{days} days",
        "goal_label": "NEXT EXAM",
        "goal_none": "none upcoming",
        "goal_in_zero": "today",
        "goal_in_one": "in 1 day",
        "goal_in_many": "in {days} days",
        "goal_overdue": "overdue",
        "quota_label": "WEEK TARGET",
        "quota_value": "{hours} h of {minimum}–{maximum} h",
        "quota_percent": "{percent} %",
        "heatmap_label": "LAST 4 WEEKS",
        "timer_running": "Timer running",
        "timer_focus": "Focus",
        "timer_break": "Break",
        "timer_ends": "ends {time}",
        "separator": " · ",
        "decimal": ".",
        "date_format": "%d %b",
        "weekdays": "Mon,Tue,Wed,Thu,Fri,Sat,Sun",
        "weekday_initials": "M,T,W,T,F,S,S",
    },
}

# Heatmap fill levels, in hours per day: below the first bound = empty, then light hatch,
# dense hatch, solid.
HEATMAP_LEVEL_BOUNDS = (0.01, 1.0, 2.5)


@dataclass(frozen=True)
class Fonts:
    header: ImageFont.FreeTypeFont
    label: ImageFont.FreeTypeFont
    big: ImageFont.FreeTypeFont
    big_narrow: ImageFont.FreeTypeFont
    big_unit: ImageFont.FreeTypeFont
    value: ImageFont.FreeTypeFont
    body: ImageFont.FreeTypeFont
    small: ImageFont.FreeTypeFont


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    ref = resources.files("studylife_display").joinpath("fonts", name)
    with resources.as_file(ref) as path:
        return ImageFont.truetype(str(path), size)


def load_fonts() -> Fonts:
    return Fonts(
        header=_font("IBMPlexSans-Bold.ttf", 20),
        label=_font("IBMPlexSans-Bold.ttf", 18),
        big=_font("IBMPlexSans-Bold.ttf", 124),
        big_narrow=_font("IBMPlexSans-Bold.ttf", 96),
        big_unit=_font("IBMPlexSans-Regular.ttf", 30),
        value=_font("IBMPlexSans-Bold.ttf", 40),
        body=_font("IBMPlexSans-Regular.ttf", 24),
        small=_font("IBMPlexSans-Regular.ttf", 18),
    )


# --------------------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------------------


def _digit_advance(font: ImageFont.FreeTypeFont) -> float:
    return max(font.getlength(digit) for digit in "0123456789")


def text_width(text: str, font: ImageFont.FreeTypeFont) -> float:
    """Width of `text` as draw_text renders it (digits at a fixed advance)."""
    advance = _digit_advance(font)
    return sum(advance if char.isdigit() else font.getlength(char) for char in text)


def draw_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: int = BLACK,
    anchor: str = "ls",
) -> float:
    """Draws `text` with tabular digits: every digit gets the advance of the widest one, so
    "1:11" and "0:00" are the same width and the big number never jumps sideways between
    refreshes. Done per glyph rather than through the font's `tnum` feature because that
    needs libraqm, which the Pi's Pillow wheel does not ship. Returns the end x."""
    x, y = xy
    if anchor[0] == "r":
        x -= text_width(text, font)
        anchor = "l" + anchor[1]
    elif anchor[0] == "m":
        x -= text_width(text, font) / 2
        anchor = "l" + anchor[1]
    advance = _digit_advance(font)
    for char in text:
        if char.isdigit():
            width = font.getlength(char)
            draw.text((x + (advance - width) / 2, y), char, font=font, fill=fill, anchor=anchor)
            x += advance
        else:
            draw.text((x, y), char, font=font, fill=fill, anchor=anchor)
            x += font.getlength(char)
    return x


def ellipsize(text: str, font: ImageFont.FreeTypeFont, max_width: float) -> str:
    if text_width(text, font) <= max_width:
        return text
    ellipsis = "…"
    while text and text_width(text + ellipsis, font) > max_width:
        text = text[:-1]
    return text.rstrip() + ellipsis


def format_hours_clock(hours: float) -> str:
    """3.75 -> "3:45"."""
    total_minutes = int(round(max(0.0, hours) * 60))
    return f"{total_minutes // 60}:{total_minutes % 60:02d}"


def format_decimal(value: float, separator: str) -> str:
    """12.5 -> "12,5" (de) / "12.5" (en); 15.0 -> "15"."""
    if abs(value - round(value)) < 0.05:
        return str(int(round(value)))
    return f"{value:.1f}".replace(".", separator)


# --------------------------------------------------------------------------------------
# Drawing primitives
# --------------------------------------------------------------------------------------


def _fill_pattern(image: Image.Image, box: tuple[int, int, int, int], level: int) -> None:
    """Fills `box` with one of four ordered patterns: 0 empty, 1 light (one dot in 4x4),
    2 dense (2x2 checkerboard), 3 solid. Ordered patterns stay crisp on the panel, unlike
    error-diffusion dithering."""
    left, top, right, bottom = box
    if level <= 0:
        return
    if level >= 3:
        ImageDraw.Draw(image).rectangle(box, fill=BLACK)
        return
    pixels = image.load()
    assert pixels is not None
    for y in range(top, bottom):
        for x in range(left, right):
            light = x % 4 == 0 and y % 4 == 0
            dense = (x + y) % 2 == 0
            if (level == 1 and light) or (level == 2 and dense):
                pixels[x, y] = BLACK


def heatmap_level(hours: float) -> int:
    level = 0
    for bound in HEATMAP_LEVEL_BOUNDS:
        if hours >= bound:
            level += 1
    return level


def _quota_fill_fraction(hours: float, target_max: float) -> float:
    scale = target_max if target_max > 0 else max(hours, 1.0)
    return max(0.0, min(1.0, hours / scale))


# --------------------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------------------

MARGIN = 24
HEADER_BASELINE = 40
RULE_Y = 54

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

TIMER_RULE_Y = 440
TIMER_BASELINE = 466


def _draw_header(
    draw: ImageDraw.ImageDraw, data: DashboardData, fonts: Fonts, t: dict[str, str]
) -> None:
    local_now = data.now
    weekday = t["weekdays"].split(",")[local_now.weekday()]
    left = t["brand"] + t["separator"] + f"{weekday} {local_now.strftime(t['date_format'])}"
    draw_text(draw, (MARGIN, HEADER_BASELINE), left, fonts.header)

    right = t["updated"].format(time=data.fetched_at.strftime("%H:%M"))
    if data.stale_minutes > 0:
        right += " " + t["stale"].format(minutes=data.stale_minutes)
    draw_text(draw, (WIDTH - MARGIN, HEADER_BASELINE), right, fonts.small, anchor="rs")

    draw.line([(MARGIN, RULE_Y), (WIDTH - MARGIN, RULE_Y)], fill=BLACK, width=2)


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
    image: Image.Image, data: DashboardData, fonts: Fonts, t: dict[str, str]
) -> None:
    draw = ImageDraw.Draw(image)
    x = RIGHT_COLUMN_X
    draw_text(draw, (x, STREAK_LABEL_BASELINE), t["streak_label"], fonts.label)
    key = "streak_one" if data.streak_days == 1 else "streak_many"
    draw_text(draw, (x, STREAK_VALUE_BASELINE), t[key].format(days=data.streak_days), fonts.value)

    draw_text(draw, (x, GOAL_LABEL_BASELINE), t["goal_label"], fonts.label)
    left, top, right, bottom = COUNTDOWN_BOX
    text_baseline = bottom - 11
    goal = data.next_goal
    if goal is None:
        draw_text(draw, (x, text_baseline), t["goal_none"], fonts.body)
        return

    if goal.days_left < 0:
        countdown = t["goal_overdue"]
    elif goal.days_left == 0:
        countdown = t["goal_in_zero"]
    elif goal.days_left == 1:
        countdown = t["goal_in_one"]
    else:
        countdown = t["goal_in_many"].format(days=goal.days_left)

    draw.rounded_rectangle((left, top, right, bottom), radius=8, fill=BLACK)
    draw_text(
        draw,
        ((left + right) / 2, text_baseline),
        countdown,
        fonts.body,
        fill=WHITE,
        anchor="ms",
    )
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

    left, right = MARGIN, WIDTH - MARGIN
    top, bottom = QUOTA_BAR_TOP, QUOTA_BAR_TOP + QUOTA_BAR_HEIGHT
    draw.rectangle((left, top, right, bottom), outline=BLACK, width=2)
    fraction = _quota_fill_fraction(quota.hours, quota.target_max)
    fill_right = left + int((right - left) * fraction)
    if fill_right > left + 2:
        draw.rectangle((left + 2, top + 2, fill_right - 1, bottom - 2), fill=BLACK)

    if quota.target_max > 0:
        for target in (quota.target_min, quota.target_max):
            tick_fraction = max(0.0, min(1.0, target / quota.target_max))
            tick_x = left + int((right - left) * tick_fraction)
            draw.line([(tick_x, top - 6), (tick_x, bottom + 6)], fill=BLACK, width=3)
            # A white halo keeps the tick visible where it crosses the filled part.
            draw.line([(tick_x - 3, top + 2), (tick_x - 3, bottom - 2)], fill=WHITE, width=1)
            draw.line([(tick_x + 3, top + 2), (tick_x + 3, bottom - 2)], fill=WHITE, width=1)


def _draw_heatmap(image: Image.Image, data: DashboardData, fonts: Fonts, t: dict[str, str]) -> None:
    draw = ImageDraw.Draw(image)
    draw_text(draw, (MARGIN, HEATMAP_LABEL_BASELINE), t["heatmap_label"], fonts.label)
    initials = t["weekday_initials"].split(",")
    left = MARGIN
    top = HEATMAP_TOP
    for column in range(HEATMAP_COLUMNS):
        weekday = (data.heatmap_first_weekday + column) % 7
        cx = left + column * (CELL + GAP) + CELL / 2
        draw_text(draw, (cx, top - 6), initials[weekday], fonts.small, anchor="ms")
    for row_index, row in enumerate(data.heatmap):
        for column, hours in enumerate(row):
            x0 = left + column * (CELL + GAP)
            y0 = top + row_index * (CELL + GAP)
            box = (x0, y0, x0 + CELL, y0 + CELL)
            draw.rectangle(box, outline=BLACK, width=1)
            inner = (x0 + 2, y0 + 2, x0 + CELL - 2, y0 + CELL - 2)
            _fill_pattern(image, inner, heatmap_level(hours))

    # Legend to the right of the grid, plus the programme and the week total.
    legend_x = left + HEATMAP_COLUMNS * (CELL + GAP) + 30
    for level, bound in enumerate(HEATMAP_LEVEL_BOUNDS, start=1):
        y0 = top + (level - 1) * (CELL + GAP)
        box = (legend_x, y0, legend_x + CELL, y0 + CELL)
        draw.rectangle(box, outline=BLACK, width=1)
        _fill_pattern(image, (box[0] + 2, box[1] + 2, box[2] - 2, box[3] - 2), level)
        label = "> 0 h" if level == 1 else f"≥ {format_decimal(bound, t['decimal'])} h"
        draw_text(draw, (legend_x + CELL + 10, y0 + CELL - 6), label, fonts.small)

    info_x = RIGHT_COLUMN_X
    if data.program_name:
        name = ellipsize(data.program_name, fonts.body, WIDTH - MARGIN - info_x)
        draw_text(draw, (info_x, top + CELL - 4), name, fonts.body)
    week = format_decimal(data.week_hours, t["decimal"])
    draw_text(draw, (info_x, top + 2 * (CELL + GAP) + CELL - 4), f"{week} h / 7 d", fonts.body)


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


def render(
    data: DashboardData,
    language: str,
    fonts_loader: Callable[[], Fonts] = load_fonts,
) -> Image.Image:
    """Renders the dashboard as an 800x480 mode "1" image, black on white."""
    t = TEXT[language]
    fonts = fonts_loader()
    canvas = Image.new("L", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(canvas)

    _draw_header(draw, data, fonts, t)
    _draw_today(draw, data, fonts, t)
    _draw_streak_and_goal(canvas, data, fonts, t)
    _draw_quota(draw, data, fonts, t)
    _draw_heatmap(canvas, data, fonts, t)
    _draw_timer(draw, data, fonts, t)

    return canvas.point(lambda value: WHITE if value > 128 else BLACK).convert(
        "1", dither=Image.Dither.NONE
    )
