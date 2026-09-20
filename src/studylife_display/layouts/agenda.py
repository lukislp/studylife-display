"""Today's plan: the sessions planned for today (from `GET /api/sessions`) as a list on the
left - time, course and topic per row, completed ones ticked off, the running or next one
inverted like the exam countdown - and today's hours, the streak and the next exam in a
column on the right. Empty when nothing is planned or the session list could not be
fetched (a key without the `Sessions.GetAll` scope)."""

from __future__ import annotations

from PIL import Image, ImageDraw

from studylife_display.layouts.common import (
    BLACK,
    MARGIN,
    TEXT,
    WHITE,
    WIDTH,
    Fonts,
    draw_footer_line,
    draw_header,
    draw_text,
    ellipsize,
    finish,
    format_countdown,
    format_decimal,
    format_hours_clock,
    format_streak,
    format_timer_phase,
    load_fonts,
    new_canvas,
)
from studylife_display.model import AgendaItem, DashboardData

LABEL_BASELINE = 86
LEFT_RIGHT = 500
ROW_TOP = 100
ROW_HEIGHT = 48
ROW_GAP = 6
MAX_ROWS = 6
MARK_X = MARGIN + 8
MARK_SIZE = 18
TIME_X = MARGIN + 44
TITLE_X = TIME_X + 158
MORE_BASELINE = ROW_TOP + MAX_ROWS * ROW_HEIGHT + 18

COLUMN_RULE_X = 520
RIGHT_X = 544
TODAY_LABEL_BASELINE = 86
TODAY_VALUE_BASELINE = 130
STREAK_LABEL_BASELINE = 182
STREAK_VALUE_BASELINE = 226
GOAL_LABEL_BASELINE = 278
GOAL_VALUE_BASELINE = 312
GOAL_DATE_BASELINE = 342

FOOTER_RULE_Y = 428


def row_box(index: int) -> tuple[int, int, int, int]:
    """The box of agenda row `index` (0-based); the render test looks for majority-black
    pixels in the inverted row."""
    top = ROW_TOP + index * ROW_HEIGHT
    return (MARGIN, top, LEFT_RIGHT, top + ROW_HEIGHT - ROW_GAP)


def format_agenda_time(item: AgendaItem, data: DashboardData, t: dict[str, str]) -> str:
    zone = data.now.tzinfo
    return t["agenda_time"].format(
        start=item.start.astimezone(zone).strftime("%H:%M"),
        end=item.end.astimezone(zone).strftime("%H:%M"),
    )


def _draw_mark(draw: ImageDraw.ImageDraw, top: int, item: AgendaItem, inverted: bool) -> None:
    """A small square in front of the row: ticked when the session is completed, empty
    otherwise; drawn in white on an inverted row."""
    colour = WHITE if inverted else BLACK
    box_top = top + (ROW_HEIGHT - ROW_GAP - MARK_SIZE) // 2
    box = (MARK_X, box_top, MARK_X + MARK_SIZE, box_top + MARK_SIZE)
    draw.rectangle(box, outline=colour, width=2)
    if item.is_completed:
        left, box_top, right, bottom = box
        draw.line(
            [(left + 4, box_top + 9), (left + 8, bottom - 5), (right - 4, box_top + 4)],
            fill=colour,
            width=3,
        )


def _draw_rows(
    draw: ImageDraw.ImageDraw, data: DashboardData, fonts: Fonts, t: dict[str, str]
) -> None:
    draw_text(draw, (MARGIN, LABEL_BASELINE), t["agenda_label"], fonts.label)
    if not data.agenda:
        draw_text(draw, (MARGIN, ROW_TOP + 31), t["agenda_none"], fonts.body)
        return
    next_item = data.next_agenda_item
    for index, item in enumerate(data.agenda[:MAX_ROWS]):
        left, top, right, bottom = row_box(index)
        inverted = item is next_item
        colour = BLACK
        if inverted:
            draw.rounded_rectangle((left, top, right, bottom), radius=6, fill=BLACK)
            colour = WHITE
        _draw_mark(draw, top, item, inverted)
        draw_text(draw, (TIME_X, top + 31), format_agenda_time(item, data, t), fonts.body, colour)
        # Course on one line, topic (and "running") in small type underneath; a row without
        # either second-line part keeps the course on the row's centre line.
        width = right - 12 - TITLE_X
        course = ellipsize(item.course_name or t["course_unknown"], fonts.body, width)
        details = [
            part
            for part in (item.topic, t["agenda_running"] if item.is_running_now else None)
            if part
        ]
        if details:
            draw_text(draw, (TITLE_X, top + 20), course, fonts.body, colour)
            detail = ellipsize(t["separator"].join(details), fonts.small, width)
            draw_text(draw, (TITLE_X, top + 39), detail, fonts.small, colour)
        else:
            draw_text(draw, (TITLE_X, top + 31), course, fonts.body, colour)
    hidden = len(data.agenda) - MAX_ROWS
    if hidden > 0:
        more = t["agenda_more"].format(count=hidden)
        draw_text(draw, (MARGIN, MORE_BASELINE), more, fonts.small)


def _draw_side(
    draw: ImageDraw.ImageDraw, data: DashboardData, fonts: Fonts, t: dict[str, str]
) -> None:
    draw.line([(COLUMN_RULE_X, 70), (COLUMN_RULE_X, FOOTER_RULE_Y - 16)], fill=BLACK)
    x = RIGHT_X
    max_width = WIDTH - MARGIN - x
    draw_text(draw, (x, TODAY_LABEL_BASELINE), t["agenda_today_label"], fonts.label)
    hours = f"{format_hours_clock(data.today_hours)} h"
    draw_text(draw, (x, TODAY_VALUE_BASELINE), hours, fonts.value)

    draw_text(draw, (x, STREAK_LABEL_BASELINE), t["streak_label"], fonts.label)
    draw_text(draw, (x, STREAK_VALUE_BASELINE), format_streak(data.streak_days, t), fonts.value)

    draw_text(draw, (x, GOAL_LABEL_BASELINE), t["goal_label"], fonts.label)
    goal = data.next_goal
    if goal is None:
        draw_text(draw, (x, GOAL_VALUE_BASELINE), t["goal_none"], fonts.body)
        return
    name = ellipsize(goal.course_name or t["course_unknown"], fonts.body, max_width)
    draw_text(draw, (x, GOAL_VALUE_BASELINE), name, fonts.body)
    when = format_countdown(goal.days_left, t)
    if goal.target_date is not None:
        date = t["goal_date"].format(date=goal.target_date.strftime(t["goal_date_format"]))
        when = f"{when}{t['separator']}{date}"
    draw_text(draw, (x, GOAL_DATE_BASELINE), ellipsize(when, fonts.small, max_width), fonts.small)


def render(data: DashboardData, language: str) -> Image.Image:
    t = TEXT[language]
    fonts = load_fonts()
    canvas, draw = new_canvas()
    draw_header(draw, data, fonts, t)
    _draw_rows(draw, data, fonts, t)
    _draw_side(draw, data, fonts, t)
    if data.timer is not None and data.timer.is_running:
        footer = f"{t['timer_running']}{t['separator']}{format_timer_phase(data, t)}"
    else:
        quota = data.week_quota
        value = t["quota_value"].format(
            hours=format_decimal(quota.hours, t["decimal"]),
            minimum=format_decimal(quota.target_min, t["decimal"]),
            maximum=format_decimal(quota.target_max, t["decimal"]),
        )
        footer = f"{t['quota_label']} {value}"
    draw_footer_line(draw, ellipsize(footer, fonts.body, WIDTH - 2 * MARGIN), fonts, FOOTER_RULE_Y)
    return finish(canvas)
