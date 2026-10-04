"""How evenly the hours spread over the courses: one row per selected course with its
all-time hours as a bar, the share of the total as a percentage, a tick on every bar where
the even share (one n-th of the total) would be, and a "short" tag on courses that got less
than half of that. The hero line up top counts the courses and the hours, the footer names
the course that has gone longest without a session. Never picked by "auto"."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from studylife_display.layouts.common import (
    BLACK,
    MARGIN,
    WHITE,
    WIDTH,
    Fonts,
    draw_footer_line,
    draw_header,
    draw_inverted_block,
    draw_text,
    ellipsize,
    finish,
    format_decimal,
    load_fonts,
    new_canvas,
    text_width,
)
from studylife_display.layouts.common import TEXT as COMMON_TEXT
from studylife_display.model import CourseShare, DashboardData

TEXT: dict[str, dict[str, str]] = {
    "de": {
        "label": "KURSBALANCE",
        "hero_one": "1 Kurs · {hours} h",
        "hero_many": "{count} Kurse · {hours} h",
        "percent": "{percent} %",
        "short": "zu kurz",
    },
    "en": {
        "label": "COURSE BALANCE",
        "hero_one": "1 course · {hours} h",
        "hero_many": "{count} courses · {hours} h",
        "percent": "{percent} %",
        "short": "short",
    },
}

HERO_BASELINE = 108
LABEL_BASELINE = 148
ROWS_TOP = 156
MAX_ROWS = 8
# Rows are at least this tall; with fewer courses they stretch up to the maximum so the
# chart fills the frame.
ROW_HEIGHT = 34
ROW_HEIGHT_MAX = 48
BAR_HEIGHT = 20
NAME_WIDTH = 220
BAR_LEFT = MARGIN + NAME_WIDTH + 16
BAR_RIGHT = WIDTH - MARGIN - 150
TAG_GAP = 10
TAG_PADDING = 8
# Courses below this fraction of the even share get the tag.
SHORT_FRACTION = 0.5

PANE_MAX_ROWS = 5
PANE_ROW_HEIGHT = 34
PANE_BAR_HEIGHT = 16
PANE_NAME_WIDTH = 130
PANE_VALUE_WIDTH = 60
PANE_LINE_HEIGHT = 26

FOOTER_RULE_Y = 428


def even_share(shares: tuple[CourseShare, ...]) -> float:
    """One n-th: every course's share if the hours were spread evenly."""
    return 1.0 / len(shares) if shares else 0.0


def share_of(share: CourseShare, shares: tuple[CourseShare, ...]) -> float:
    total = sum(entry.hours for entry in shares)
    return share.hours / total if total > 0 else 0.0


def is_short(share: CourseShare, shares: tuple[CourseShare, ...]) -> bool:
    return share_of(share, shares) < SHORT_FRACTION * even_share(shares)


def _bar_scale(shares: tuple[CourseShare, ...]) -> float:
    """The share that fills a bar completely: the largest one, or the even share when every
    course sits below it, so the longest bar always spans the chart."""
    return max([share_of(share, shares) for share in shares] + [even_share(shares)]) or 1.0


def _draw_share_bar(
    draw: ImageDraw.ImageDraw,
    left: int,
    top: int,
    right: int,
    height: int,
    fraction: float,
    tick_fraction: float,
) -> None:
    """A solid bar `fraction` of the width long, plus the even-share tick with a white halo so
    it stays visible where it crosses the bar."""
    bottom = top + height
    width = int((right - left) * max(0.0, min(1.0, fraction)))
    draw.rectangle((left, top, left + max(width, 2), bottom), fill=BLACK)
    tick_x = left + int((right - left) * max(0.0, min(1.0, tick_fraction)))
    draw.line([(tick_x - 3, top), (tick_x - 3, bottom)], fill=WHITE, width=1)
    draw.line([(tick_x + 3, top), (tick_x + 3, bottom)], fill=WHITE, width=1)
    draw.line([(tick_x, top - 4), (tick_x, bottom + 4)], fill=BLACK, width=3)


def _draw_row(
    draw: ImageDraw.ImageDraw,
    share: CourseShare,
    shares: tuple[CourseShare, ...],
    fonts: Fonts,
    t: dict[str, str],
    ct: dict[str, str],
    top: int,
    row_height: int,
    name_left: int,
    bar_left: int,
    bar_right: int,
    bar_height: int,
    value_right: int,
    name_font: ImageFont.FreeTypeFont,
    with_tag: bool,
) -> None:
    bar_top = top + (row_height - bar_height) // 2
    baseline = bar_top + bar_height - 2
    name = ellipsize(
        share.course_name or ct["course_unknown"], name_font, bar_left - name_left - 12
    )
    draw_text(draw, (name_left, baseline), name, name_font)
    scale = _bar_scale(shares)
    fraction = share_of(share, shares)
    _draw_share_bar(
        draw, bar_left, bar_top, bar_right, bar_height, fraction / scale, even_share(shares) / scale
    )
    percent = t["percent"].format(percent=int(round(fraction * 100)))
    percent_left = draw_text(draw, (value_right, baseline), percent, name_font, anchor="rs")
    if with_tag and is_short(share, shares):
        tag = t["short"]
        tag_width = int(text_width(tag, fonts.small)) + 2 * TAG_PADDING
        tag_left = bar_right + TAG_GAP
        tag_right = min(tag_left + tag_width, int(percent_left) - TAG_GAP)
        if tag_right - tag_left >= tag_width // 2:
            draw_inverted_block(
                draw,
                (tag_left, bar_top, tag_right, bar_top + bar_height),
                ellipsize(tag, fonts.small, tag_right - tag_left - 2 * TAG_PADDING // 2),
                fonts.small,
                bar_top + bar_height - 5,
                radius=6,
            )


def _draw_hero(
    draw: ImageDraw.ImageDraw,
    data: DashboardData,
    fonts: Fonts,
    t: dict[str, str],
    ct: dict[str, str],
) -> None:
    shares = data.course_shares
    hours = format_decimal(sum(share.hours for share in shares), ct["decimal"])
    key = "hero_one" if len(shares) == 1 else "hero_many"
    hero = t[key].format(count=len(shares), hours=hours)
    draw_text(
        draw, (MARGIN, HERO_BASELINE), ellipsize(hero, fonts.title, WIDTH - 2 * MARGIN), fonts.title
    )
    draw_text(draw, (MARGIN, LABEL_BASELINE), t["label"], fonts.label)


def _draw_rows(
    draw: ImageDraw.ImageDraw,
    data: DashboardData,
    fonts: Fonts,
    t: dict[str, str],
    ct: dict[str, str],
) -> None:
    shares = data.course_shares[:MAX_ROWS]
    if not shares:
        draw_text(draw, (MARGIN, ROWS_TOP + ROW_HEIGHT - 8), ct["courses_none"], fonts.body)
        return
    available = FOOTER_RULE_Y - 12 - ROWS_TOP
    row_height = max(ROW_HEIGHT, min(ROW_HEIGHT_MAX, available // len(shares)))
    for index, share in enumerate(shares):
        _draw_row(
            draw,
            share,
            shares,
            fonts,
            t,
            ct,
            ROWS_TOP + index * row_height,
            row_height,
            MARGIN,
            BAR_LEFT,
            BAR_RIGHT,
            BAR_HEIGHT,
            WIDTH - MARGIN,
            fonts.body,
            with_tag=True,
        )


def neglected_line(data: DashboardData, ct: dict[str, str]) -> str:
    """The semester layout's wording for the course that has gone longest without a session."""
    course = data.neglected_course
    if course is None:
        return ct["neglected_none"]
    name = course.course_name or ct["course_unknown"]
    if course.days_since is None:
        return ct["neglected_never"].format(course=name)
    if course.days_since == 1:
        return ct["neglected_one"].format(course=name)
    return ct["neglected_days"].format(course=name, days=course.days_since)


def render(data: DashboardData, language: str) -> Image.Image:
    t = TEXT[language]
    ct = COMMON_TEXT[language]
    fonts = load_fonts()
    canvas, draw = new_canvas()
    draw_header(draw, data, fonts, ct)
    _draw_hero(draw, data, fonts, t, ct)
    _draw_rows(draw, data, fonts, t, ct)
    footer = f"{ct['neglected_label']} {neglected_line(data, ct)}"
    draw_footer_line(draw, ellipsize(footer, fonts.body, WIDTH - 2 * MARGIN), fonts, FOOTER_RULE_Y)
    return finish(canvas)


def render_pane(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    data: DashboardData,
    fonts: Fonts,
    language: str,
    box: tuple[int, int, int, int],
) -> None:
    """Up to PANE_MAX_ROWS rows of name, bar (with the even-share tick) and percentage, then
    the course count with the hours and the neglected course as small lines."""
    t = TEXT[language]
    ct = COMMON_TEXT[language]
    left, top, right, _ = box
    shares = data.course_shares[:PANE_MAX_ROWS]
    if not shares:
        draw_text(draw, (left, top + PANE_ROW_HEIGHT - 8), ct["courses_none"], fonts.body)
        return
    for index, share in enumerate(shares):
        _draw_row(
            draw,
            share,
            shares,
            fonts,
            t,
            ct,
            top + index * PANE_ROW_HEIGHT,
            PANE_ROW_HEIGHT,
            left,
            left + PANE_NAME_WIDTH + 10,
            right - PANE_VALUE_WIDTH,
            PANE_BAR_HEIGHT,
            right,
            fonts.small,
            with_tag=False,
        )
    baseline = top + len(shares) * PANE_ROW_HEIGHT + 24
    total = format_decimal(sum(share.hours for share in data.course_shares), ct["decimal"])
    key = "hero_one" if len(data.course_shares) == 1 else "hero_many"
    summary = t[key].format(count=len(data.course_shares), hours=total)
    draw_text(draw, (left, baseline), ellipsize(summary, fonts.small, right - left), fonts.small)
    line = neglected_line(data, ct)
    draw_text(
        draw,
        (left, baseline + PANE_LINE_HEIGHT),
        ellipsize(line, fonts.small, right - left),
        fonts.small,
    )
