"""Drawing helpers shared by every layout: fonts, tabular digits, the language tables, the
ordered fill patterns, the quota bar, the heatmap grid and the header line.

Text is rasterised anti-aliased on a greyscale canvas and thresholded at the end (see
`finish`); no error-diffusion dithering, which turns glyph edges into noise on a 1-bit panel.
The fill levels use explicit pixel patterns for the same reason.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources
from typing import Literal

from PIL import Image, ImageDraw, ImageFont

from studylife_display.model import HEATMAP_COLUMNS, DashboardData, WeekQuota

WIDTH = 800
HEIGHT = 480

BLACK = 0
WHITE = 255

MARGIN = 24
HEADER_BASELINE = 40
RULE_Y = 54

Language = Literal["de", "en"]

# Only lookups happen on these tables: the code never branches on the language itself.
TEXT: dict[str, dict[str, str]] = {
    "de": {
        "brand": "STUDYLIFE",
        "updated": "aktualisiert {time}",
        "stale": "· vor {minutes} min",
        "today_unit": "h heute",
        "today_line": "heute {hours} h",
        "streak_label": "SERIE",
        "streak_one": "1 Tag",
        "streak_many": "{days} Tage",
        "goal_label": "NÄCHSTE PRÜFUNG",
        "goal_none": "keine anstehend",
        "goal_in_zero": "heute",
        "goal_in_one": "in 1 Tag",
        "goal_in_many": "in {days} Tagen",
        "goal_overdue": "überfällig",
        "goal_date": "am {date}",
        "goal_date_format": "%d.%m.%Y",
        "quota_label": "WOCHENZIEL",
        "quota_value": "{hours} h von {minimum}–{maximum} h",
        "quota_percent": "{percent} %",
        "heatmap_label": "LETZTE 4 WOCHEN",
        "week_sum": "{hours} h",
        "courses_label": "STUNDEN JE KURS · 28 TAGE",
        "courses_none": "noch keine Sitzungen",
        "course_unknown": "(ohne Kurs)",
        "hours_short": "{hours} h",
        "timer_running": "Timer läuft",
        "timer_none": "kein Timer aktiv",
        "timer_focus": "Fokus",
        "timer_break": "Pause",
        "timer_round": "Runde {round}",
        "timer_ends": "endet {time}",
        "ects_label": "ECTS",
        "ects_value": "{earned} von {total}",
        "ects_percent": "{percent} %",
        "grade_label": "NOTENSCHNITT",
        "grade_none": "noch keine Note",
        "forecast_label": "ABSCHLUSS VORAUSSICHTLICH",
        "forecast_none": "nicht verfügbar",
        "forecast_done": "abgeschlossen",
        "neglected_label": "LÄNGER NICHT GELERNT",
        "neglected_none": "alle Kurse aktiv",
        "neglected_days": "{course} · seit {days} Tagen",
        "neglected_one": "{course} · seit 1 Tag",
        "neglected_never": "{course} · noch nie",
        "topics_label": "THEMEN",
        "topics_value": "{completed} von {total}",
        "agenda_label": "HEUTE",
        "agenda_today_label": "GELERNT HEUTE",
        "agenda_none": "keine Sessions geplant",
        "agenda_more": "+{count} weitere",
        "agenda_time": "{start}–{end}",
        "agenda_running": "läuft",
        "review_label": "WOCHENRÜCKBLICK",
        "review_week": "KW {week}",
        "review_unit": "h diese Woche",
        "review_delta": "{sign}{hours} h zur Vorwoche",
        "review_top_label": "MEISTGELERNT",
        "review_top_none": "kein Kurs",
        "review_sessions_label": "SESSIONS",
        "review_sessions_one": "1 Session",
        "review_sessions_many": "{count} Sessions",
        "review_strip_label": "DIESE WOCHE",
        "review_previous": "Vorwoche {week}: {hours} h · {sessions}",
        "review_previous_none": "Vorwoche: noch keine Daten",
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
        "today_line": "today {hours} h",
        "streak_label": "STREAK",
        "streak_one": "1 day",
        "streak_many": "{days} days",
        "goal_label": "NEXT EXAM",
        "goal_none": "none upcoming",
        "goal_in_zero": "today",
        "goal_in_one": "in 1 day",
        "goal_in_many": "in {days} days",
        "goal_overdue": "overdue",
        "goal_date": "on {date}",
        "goal_date_format": "%d %b %Y",
        "quota_label": "WEEK TARGET",
        "quota_value": "{hours} h of {minimum}–{maximum} h",
        "quota_percent": "{percent} %",
        "heatmap_label": "LAST 4 WEEKS",
        "week_sum": "{hours} h",
        "courses_label": "HOURS PER COURSE · 28 DAYS",
        "courses_none": "no sessions yet",
        "course_unknown": "(no course)",
        "hours_short": "{hours} h",
        "timer_running": "Timer running",
        "timer_none": "no timer running",
        "timer_focus": "Focus",
        "timer_break": "Break",
        "timer_round": "round {round}",
        "timer_ends": "ends {time}",
        "ects_label": "ECTS",
        "ects_value": "{earned} of {total}",
        "ects_percent": "{percent} %",
        "grade_label": "AVERAGE GRADE",
        "grade_none": "no grade yet",
        "forecast_label": "EXPECTED GRADUATION",
        "forecast_none": "n/a",
        "forecast_done": "done",
        "neglected_label": "NOT STUDIED FOR A WHILE",
        "neglected_none": "all courses active",
        "neglected_days": "{course} · {days} days ago",
        "neglected_one": "{course} · 1 day ago",
        "neglected_never": "{course} · never",
        "topics_label": "TOPICS",
        "topics_value": "{completed} of {total}",
        "agenda_label": "TODAY",
        "agenda_today_label": "STUDIED TODAY",
        "agenda_none": "nothing planned",
        "agenda_more": "+{count} more",
        "agenda_time": "{start}–{end}",
        "agenda_running": "running",
        "review_label": "WEEKLY REVIEW",
        "review_week": "W{week}",
        "review_unit": "h this week",
        "review_delta": "{sign}{hours} h vs. last week",
        "review_top_label": "TOP COURSE",
        "review_top_none": "no course",
        "review_sessions_label": "SESSIONS",
        "review_sessions_one": "1 session",
        "review_sessions_many": "{count} sessions",
        "review_strip_label": "THIS WEEK",
        "review_previous": "Last week {week}: {hours} h · {sessions}",
        "review_previous_none": "Last week: no data yet",
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
    huge: ImageFont.FreeTypeFont
    big: ImageFont.FreeTypeFont
    big_narrow: ImageFont.FreeTypeFont
    big_unit: ImageFont.FreeTypeFont
    title: ImageFont.FreeTypeFont
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
        huge=_font("IBMPlexSans-Bold.ttf", 200),
        big=_font("IBMPlexSans-Bold.ttf", 124),
        big_narrow=_font("IBMPlexSans-Bold.ttf", 96),
        big_unit=_font("IBMPlexSans-Regular.ttf", 30),
        title=_font("IBMPlexSans-Bold.ttf", 60),
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


def font_fitting(
    text: str, sizes: Sequence[int], max_width: float, name: str = "IBMPlexSans-Bold.ttf"
) -> ImageFont.FreeTypeFont:
    """The largest of `sizes` (tried in the given order) at which `text` fits into
    `max_width`; the last size when none does, for the caller to ellipsize at."""
    font = _font(name, sizes[0])
    for size in sizes:
        font = _font(name, size)
        if text_width(text, font) <= max_width:
            break
    return font


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


def format_minutes_seconds(seconds: float) -> str:
    """1083 -> "18:03"; negative values clamp to "00:00"."""
    total = int(max(0.0, seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


def format_decimal(value: float, separator: str) -> str:
    """12.5 -> "12,5" (de) / "12.5" (en); 15.0 -> "15"."""
    if abs(value - round(value)) < 0.05:
        return str(int(round(value)))
    return f"{value:.1f}".replace(".", separator)


def format_streak(days: int, t: dict[str, str]) -> str:
    key = "streak_one" if days == 1 else "streak_many"
    return t[key].format(days=days)


def format_countdown(days_left: int, t: dict[str, str]) -> str:
    if days_left < 0:
        return t["goal_overdue"]
    if days_left == 0:
        return t["goal_in_zero"]
    if days_left == 1:
        return t["goal_in_one"]
    return t["goal_in_many"].format(days=days_left)


def format_goal_line(data: DashboardData, t: dict[str, str]) -> str:
    """ "NÄCHSTE PRÜFUNG Betriebssysteme in 9 Tagen" or the placeholder."""
    goal = data.next_goal
    if goal is None:
        return f"{t['goal_label']} {t['goal_none']}"
    return f"{t['goal_label']} {goal.course_name} {format_countdown(goal.days_left, t)}"


def format_timer_phase(data: DashboardData, t: dict[str, str]) -> str:
    """ "Fokus · Runde 2 · endet 17:03" for a running timer, "" otherwise."""
    timer = data.timer
    if timer is None or not timer.is_running:
        return ""
    parts = [t["timer_break"] if timer.is_break else t["timer_focus"]]
    if timer.current_round is not None:
        parts.append(t["timer_round"].format(round=timer.current_round))
    if timer.phase_ends_at is not None:
        ends = timer.phase_ends_at.astimezone(data.now.tzinfo).strftime("%H:%M")
        parts.append(t["timer_ends"].format(time=ends))
    return t["separator"].join(parts)


# --------------------------------------------------------------------------------------
# Drawing primitives
# --------------------------------------------------------------------------------------


def new_canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    """A white greyscale canvas; hand it to `finish` once everything is drawn."""
    canvas = Image.new("L", (WIDTH, HEIGHT), WHITE)
    return canvas, ImageDraw.Draw(canvas)


def finish(canvas: Image.Image) -> Image.Image:
    """Thresholds the greyscale canvas into the 1-bit frame the panel takes."""
    return canvas.point(lambda value: WHITE if value > 128 else BLACK).convert(
        "1", dither=Image.Dither.NONE
    )


def fill_pattern(image: Image.Image, box: tuple[int, int, int, int], level: int) -> None:
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


def quota_fill_fraction(hours: float, target_max: float) -> float:
    scale = target_max if target_max > 0 else max(hours, 1.0)
    return max(0.0, min(1.0, hours / scale))


def draw_inverted_block(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    baseline: float,
    radius: int = 8,
) -> None:
    """A black rounded block with `text` centred in white on `baseline`."""
    left, top, right, bottom = box
    draw.rounded_rectangle((left, top, right, bottom), radius=radius, fill=BLACK)
    draw_text(draw, ((left + right) / 2, baseline), text, font, fill=WHITE, anchor="ms")


def draw_quota_bar(
    draw: ImageDraw.ImageDraw,
    quota: WeekQuota,
    left: int,
    top: int,
    right: int,
    height: int,
    tick_overhang: int = 6,
) -> None:
    """The outlined bar, filled to hours/target_max, with the two target ticks and their
    white halo so a tick stays visible where it crosses the filled part."""
    bottom = top + height
    draw.rectangle((left, top, right, bottom), outline=BLACK, width=2)
    fraction = quota_fill_fraction(quota.hours, quota.target_max)
    fill_right = left + int((right - left) * fraction)
    if fill_right > left + 2:
        draw.rectangle((left + 2, top + 2, fill_right - 1, bottom - 2), fill=BLACK)

    if quota.target_max > 0:
        for target in (quota.target_min, quota.target_max):
            tick_fraction = max(0.0, min(1.0, target / quota.target_max))
            tick_x = left + int((right - left) * tick_fraction)
            draw.line(
                [(tick_x, top - tick_overhang), (tick_x, bottom + tick_overhang)],
                fill=BLACK,
                width=3,
            )
            draw.line([(tick_x - 3, top + 2), (tick_x - 3, bottom - 2)], fill=WHITE, width=1)
            draw.line([(tick_x + 3, top + 2), (tick_x + 3, bottom - 2)], fill=WHITE, width=1)


def draw_heatmap_grid(
    image: Image.Image,
    data: DashboardData,
    t: dict[str, str],
    left: int,
    top: int,
    cell: int,
    gap: int,
    initials_font: ImageFont.FreeTypeFont,
    initials_gap: int = 6,
) -> None:
    """Weekday initials above HEATMAP_ROWS x HEATMAP_COLUMNS outlined cells, each filled
    with the pattern of its level."""
    draw = ImageDraw.Draw(image)
    initials = t["weekday_initials"].split(",")
    for column in range(HEATMAP_COLUMNS):
        weekday = (data.heatmap_first_weekday + column) % 7
        cx = left + column * (cell + gap) + cell / 2
        draw_text(draw, (cx, top - initials_gap), initials[weekday], initials_font, anchor="ms")
    for row_index, row in enumerate(data.heatmap):
        for column, hours in enumerate(row):
            x0 = left + column * (cell + gap)
            y0 = top + row_index * (cell + gap)
            draw.rectangle((x0, y0, x0 + cell, y0 + cell), outline=BLACK, width=1)
            fill_pattern(
                image, (x0 + 2, y0 + 2, x0 + cell - 2, y0 + cell - 2), heatmap_level(hours)
            )


def draw_heatmap_legend(
    image: Image.Image,
    t: dict[str, str],
    left: int,
    top: int,
    cell: int,
    gap: int,
    font: ImageFont.FreeTypeFont,
) -> None:
    """The three non-empty levels as swatches with their lower bound."""
    draw = ImageDraw.Draw(image)
    for level, bound in enumerate(HEATMAP_LEVEL_BOUNDS, start=1):
        y0 = top + (level - 1) * (cell + gap)
        box = (left, y0, left + cell, y0 + cell)
        draw.rectangle(box, outline=BLACK, width=1)
        fill_pattern(image, (box[0] + 2, box[1] + 2, box[2] - 2, box[3] - 2), level)
        label = "> 0 h" if level == 1 else f"≥ {format_decimal(bound, t['decimal'])} h"
        draw_text(draw, (left + cell + 10, y0 + cell - 6), label, font)


def draw_header(
    draw: ImageDraw.ImageDraw, data: DashboardData, fonts: Fonts, t: dict[str, str]
) -> None:
    """Brand, weekday and date on the left; "updated HH:MM" (plus the stale marker when the
    last fetch failed) on the right; a rule underneath. Every layout starts with this line
    because the stale marker is the only way to tell an old frame from a fresh one."""
    local_now = data.now
    weekday = t["weekdays"].split(",")[local_now.weekday()]
    left = t["brand"] + t["separator"] + f"{weekday} {local_now.strftime(t['date_format'])}"
    draw_text(draw, (MARGIN, HEADER_BASELINE), left, fonts.header)

    right = t["updated"].format(time=data.fetched_at.strftime("%H:%M"))
    if data.stale_minutes > 0:
        right += " " + t["stale"].format(minutes=data.stale_minutes)
    draw_text(draw, (WIDTH - MARGIN, HEADER_BASELINE), right, fonts.small, anchor="rs")

    draw.line([(MARGIN, RULE_Y), (WIDTH - MARGIN, RULE_Y)], fill=BLACK, width=2)


def draw_footer_line(draw: ImageDraw.ImageDraw, text: str, fonts: Fonts, rule_y: int) -> None:
    """A thin rule with one line of body text under it, at the bottom of the frame."""
    draw.line([(MARGIN, rule_y), (WIDTH - MARGIN, rule_y)], fill=BLACK)
    draw_text(draw, (MARGIN, rule_y + 26), text, fonts.body)
