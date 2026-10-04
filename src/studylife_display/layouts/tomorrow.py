"""Tomorrow's plan: the sessions planned for tomorrow (from `GET /api/sessions`), the first
one as the hero - its time range large, the course and topic next to it - and the rest as
compact rows underneath. The label line names tomorrow's weekday and date and sums the
plan up ("3 Sessions · 4,5 h geplant"); today's hours and the streak sit in the footer.
Needs a key with the `Sessions.GetAll` scope; without it the layout says so."""

from __future__ import annotations

from datetime import timedelta

from PIL import Image, ImageDraw

import studylife_display.layouts.common as common
from studylife_display.layouts.common import (
    BLACK,
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
)
from studylife_display.model import AgendaItem, DashboardData

# This layout's own strings; everything else comes from the shared table in common.py.
TEXT: dict[str, dict[str, str]] = {
    "de": {
        "tomorrow_label": "MORGEN",
        "tomorrow_none": "morgen nichts geplant",
        "tomorrow_scope": "Schlüssel ohne Scope Sessions.GetAll",
        "tomorrow_sessions_one": "1 Session",
        "tomorrow_sessions_many": "{count} Sessions",
        "tomorrow_summary": "{sessions} · {hours} h geplant",
    },
    "en": {
        "tomorrow_label": "TOMORROW",
        "tomorrow_none": "nothing planned for tomorrow",
        "tomorrow_scope": "key lacks the scope Sessions.GetAll",
        "tomorrow_sessions_one": "1 session",
        "tomorrow_sessions_many": "{count} sessions",
        "tomorrow_summary": "{sessions} · {hours} h planned",
    },
}

LABEL_BASELINE = 86
# The hero: the time range in title type, the course and topic stacked to its right.
HERO_BASELINE = 162
HERO_COURSE_BASELINE = 140
HERO_TOPIC_BASELINE = 172
HERO_GAP = 28
# The remaining sessions as one-line rows.
ROW_TOP = 200
ROW_HEIGHT = 40
MAX_ROWS = 5
MARK_X = MARGIN + 8
MARK_SIZE = 18
TIME_X = MARGIN + 44
TITLE_X = TIME_X + 158

FOOTER_RULE_Y = 428

# The pane form.
PANE_TIME_BASELINE = 40
PANE_COURSE_BASELINE = 72
PANE_TOPIC_BASELINE = 96
PANE_ROW_TOP = 116
PANE_ROW_HEIGHT = 38
PANE_MAX_ROWS = 3
PANE_TITLE_X = 150


def format_time_range(item: AgendaItem, data: DashboardData, t: dict[str, str]) -> str:
    zone = data.now.tzinfo
    return t["agenda_time"].format(
        start=item.start.astimezone(zone).strftime("%H:%M"),
        end=item.end.astimezone(zone).strftime("%H:%M"),
    )


def planned_hours(items: tuple[AgendaItem, ...]) -> float:
    return sum((item.end - item.start) / timedelta(hours=1) for item in items)


def label_line(data: DashboardData, t: dict[str, str], s: dict[str, str]) -> str:
    """ "MORGEN · Fr 18.09."."""
    tomorrow = data.now + timedelta(days=1)
    weekday = t["weekdays"].split(",")[tomorrow.weekday()]
    return s["tomorrow_label"] + t["separator"] + f"{weekday} {tomorrow.strftime(t['date_format'])}"


def summary_line(data: DashboardData, t: dict[str, str], s: dict[str, str]) -> str:
    """ "3 Sessions · 4,5 h geplant"."""
    count = len(data.tomorrow)
    key = "tomorrow_sessions_one" if count == 1 else "tomorrow_sessions_many"
    return s["tomorrow_summary"].format(
        sessions=s[key].format(count=count),
        hours=format_decimal(planned_hours(data.tomorrow), t["decimal"]),
    )


def _draw_mark(draw: ImageDraw.ImageDraw, top: int, item: AgendaItem) -> None:
    """A small square in front of the row, ticked when the session is already completed."""
    box_top = top + (ROW_HEIGHT - MARK_SIZE) // 2
    box = (MARK_X, box_top, MARK_X + MARK_SIZE, box_top + MARK_SIZE)
    draw.rectangle(box, outline=BLACK, width=2)
    if item.is_completed:
        left, box_top, right, bottom = box
        draw.line(
            [(left + 4, box_top + 9), (left + 8, bottom - 5), (right - 4, box_top + 4)],
            fill=BLACK,
            width=3,
        )


def _draw_hero(
    draw: ImageDraw.ImageDraw,
    item: AgendaItem,
    data: DashboardData,
    fonts: Fonts,
    t: dict[str, str],
) -> None:
    end_x = draw_text(draw, (MARGIN, HERO_BASELINE), format_time_range(item, data, t), fonts.title)
    x = end_x + HERO_GAP
    max_width = WIDTH - MARGIN - x
    course = ellipsize(item.course_name or t["course_unknown"], fonts.value, max_width)
    draw_text(draw, (x, HERO_COURSE_BASELINE), course, fonts.value)
    if item.topic:
        draw_text(
            draw, (x, HERO_TOPIC_BASELINE), ellipsize(item.topic, fonts.body, max_width), fonts.body
        )


def _draw_rows(
    draw: ImageDraw.ImageDraw, data: DashboardData, fonts: Fonts, t: dict[str, str]
) -> None:
    rest = data.tomorrow[1:]
    shown = rest if len(rest) <= MAX_ROWS else rest[: MAX_ROWS - 1]
    for index, item in enumerate(shown):
        top = ROW_TOP + index * ROW_HEIGHT
        baseline = top + 28
        _draw_mark(draw, top, item)
        draw_text(draw, (TIME_X, baseline), format_time_range(item, data, t), fonts.body)
        max_width = WIDTH - MARGIN - TITLE_X
        course = ellipsize(item.course_name or t["course_unknown"], fonts.body, max_width)
        end_x = draw_text(draw, (TITLE_X, baseline), course, fonts.body)
        if item.topic:
            topic_x = end_x + 12
            topic_width = WIDTH - MARGIN - topic_x
            if topic_width > 40:
                draw_text(
                    draw,
                    (topic_x, baseline),
                    ellipsize(item.topic, fonts.small, topic_width),
                    fonts.small,
                )
    hidden = len(rest) - len(shown)
    if hidden > 0:
        more = t["agenda_more"].format(count=hidden)
        draw_text(draw, (TIME_X, ROW_TOP + len(shown) * ROW_HEIGHT + 28), more, fonts.small)


def render(data: DashboardData, language: str) -> Image.Image:
    t = common.TEXT[language]
    s = TEXT[language]
    fonts = load_fonts()
    canvas, draw = new_canvas()
    draw_header(draw, data, fonts, t)
    draw_text(draw, (MARGIN, LABEL_BASELINE), label_line(data, t, s), fonts.label)
    if "sessions" in data.unavailable:
        draw_text(draw, (MARGIN, HERO_COURSE_BASELINE), s["tomorrow_scope"], fonts.body)
    elif not data.tomorrow:
        draw_text(draw, (MARGIN, HERO_COURSE_BASELINE), s["tomorrow_none"], fonts.body)
    else:
        summary = summary_line(data, t, s)
        draw_text(draw, (WIDTH - MARGIN, LABEL_BASELINE), summary, fonts.small, anchor="rs")
        _draw_hero(draw, data.tomorrow[0], data, fonts, t)
        _draw_rows(draw, data, fonts, t)
    footer = t["separator"].join(
        [
            t["today_line"].format(hours=format_hours_clock(data.today_hours)),
            f"{t['streak_label']} {format_streak(data.streak_days, t)}",
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
    """The first session's time and course large, then up to PANE_MAX_ROWS more rows."""
    t = common.TEXT[language]
    s = TEXT[language]
    left, top, right, _ = box
    width = right - left
    if "sessions" in data.unavailable:
        draw_text(
            draw, (left, top + 31), ellipsize(s["tomorrow_scope"], fonts.small, width), fonts.small
        )
        return
    if not data.tomorrow:
        draw_text(
            draw, (left, top + 31), ellipsize(s["tomorrow_none"], fonts.body, width), fonts.body
        )
        return
    first = data.tomorrow[0]
    draw_text(
        draw, (left, top + PANE_TIME_BASELINE), format_time_range(first, data, t), fonts.value
    )
    course = ellipsize(first.course_name or t["course_unknown"], fonts.body, width)
    draw_text(draw, (left, top + PANE_COURSE_BASELINE), course, fonts.body)
    if first.topic:
        topic = ellipsize(first.topic, fonts.small, width)
        draw_text(draw, (left, top + PANE_TOPIC_BASELINE), topic, fonts.small)

    rest = data.tomorrow[1:]
    shown = rest if len(rest) <= PANE_MAX_ROWS else rest[: PANE_MAX_ROWS - 1]
    title_x = left + PANE_TITLE_X
    for index, item in enumerate(shown):
        baseline = top + PANE_ROW_TOP + index * PANE_ROW_HEIGHT + 26
        draw_text(draw, (left, baseline), format_time_range(item, data, t), fonts.body)
        course = ellipsize(item.course_name or t["course_unknown"], fonts.body, right - title_x)
        draw_text(draw, (title_x, baseline), course, fonts.body)
    hidden = len(rest) - len(shown)
    if hidden > 0:
        baseline = top + PANE_ROW_TOP + len(shown) * PANE_ROW_HEIGHT + 24
        draw_text(draw, (left, baseline), t["agenda_more"].format(count=hidden), fonts.small)
    summary = ellipsize(summary_line(data, t, s), fonts.small, width)
    draw_text(
        draw,
        (left, top + PANE_ROW_TOP + PANE_MAX_ROWS * PANE_ROW_HEIGHT + 40),
        summary,
        fonts.small,
    )
