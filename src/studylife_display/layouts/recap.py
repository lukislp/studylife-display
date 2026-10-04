"""The session recap: what the auto rule shows for a few minutes right after a study
session ends (`DISPLAY_AUTO_RECAP_MINUTES`), and a layout one can pick by hand too.

The label says "just finished"; the hero is the duration of the newest finished session of
the history (`DashboardData.last_session`) as H:MM in big type, with its course and topic
next to it and how long ago it ended underneath. Three label/value pairs follow - today's
hours, the streak and the week quota with a small bar - and the footer names the next
planned session of today, else the week quota line. Without a finished session the layout
says so. It is never picked automatically except by that rule.
"""

from __future__ import annotations

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
    format_hours_clock,
    format_streak,
    load_fonts,
    new_canvas,
    text_width,
)
from studylife_display.layouts.today import format_week_quota_line
from studylife_display.model import DashboardData, FinishedSession

# This layout's own strings; everything else comes from the shared table in common.py.
TEXT: dict[str, dict[str, str]] = {
    "de": {
        "recap_label": "GERADE FERTIG",
        "recap_none": "noch keine Sitzung",
        "recap_today": "HEUTE",
        "recap_streak": "SERIE",
        "recap_week": "WOCHE",
        "recap_ended_now": "gerade eben beendet",
        "recap_ended_minutes": "vor {minutes} min beendet",
        "recap_ended_hours": "vor {hours} h beendet",
        "recap_ended_day": "vor 1 Tag beendet",
        "recap_ended_days": "vor {days} Tagen beendet",
        "recap_next": "Als Nächstes {time} {course}",
    },
    "en": {
        "recap_label": "JUST FINISHED",
        "recap_none": "no session yet",
        "recap_today": "TODAY",
        "recap_streak": "STREAK",
        "recap_week": "WEEK",
        "recap_ended_now": "ended just now",
        "recap_ended_minutes": "ended {minutes} min ago",
        "recap_ended_hours": "ended {hours} h ago",
        "recap_ended_day": "ended 1 day ago",
        "recap_ended_days": "ended {days} days ago",
        "recap_next": "Up next {time} {course}",
    },
}

LABEL_BASELINE = 86
HERO_BASELINE = 214
UNIT_GAP = 12
COURSE_BASELINE = 152
TOPIC_BASELINE = 186
HERO_GAP = 36
ENDED_BASELINE = 256
STATS_LABEL_BASELINE = 312
STATS_VALUE_BASELINE = 358
BAR_TOP = 374
BAR_HEIGHT = 14
FOOTER_RULE_Y = 428

# Where the hero duration stands: the render test checks that it is not white.
HERO_BOX = (MARGIN, HERO_BASELINE - 100, MARGIN + 260, HERO_BASELINE)

# The pane form.
PANE_HERO_BASELINE = 110
PANE_COURSE_BASELINE = 152
PANE_ENDED_BASELINE = 182
PANE_STATS_BASELINE = 236
PANE_STATS_STEP = 40


def ended_line(session: FinishedSession, s: dict[str, str]) -> str:
    """ "vor 4 min beendet"; hours and days once it is longer ago (a hand-picked recap may
    show a session from a while back)."""
    minutes = session.minutes_ago
    if minutes < 1:
        return s["recap_ended_now"]
    if minutes < 60:
        return s["recap_ended_minutes"].format(minutes=minutes)
    if minutes < 24 * 60:
        return s["recap_ended_hours"].format(hours=minutes // 60)
    days = minutes // (24 * 60)
    return s["recap_ended_day"] if days == 1 else s["recap_ended_days"].format(days=days)


def duration_text(session: FinishedSession) -> str:
    return format_hours_clock(session.hours)


def stat_values(data: DashboardData, t: dict[str, str]) -> tuple[str, str, str]:
    """(today's hours, streak, week quota percent) as the strings the layout shows."""
    return (
        t["hours_short"].format(hours=format_hours_clock(data.today_hours)),
        format_streak(data.streak_days, t),
        t["quota_percent"].format(percent=int(round(data.week_quota.percent))),
    )


def footer_text(data: DashboardData, t: dict[str, str], s: dict[str, str]) -> str:
    """The next planned session of today ("Als Nächstes 14:00 Kurs"), else the quota line."""
    item = data.next_agenda_item
    if item is None:
        return format_week_quota_line(data, t)
    return s["recap_next"].format(
        time=item.start.astimezone(data.now.tzinfo).strftime("%H:%M"),
        course=item.course_name or t["course_unknown"],
    )


def _draw_hero(
    draw: ImageDraw.ImageDraw,
    session: FinishedSession,
    fonts: Fonts,
    t: dict[str, str],
    s: dict[str, str],
) -> None:
    end_x = draw_text(draw, (MARGIN, HERO_BASELINE), duration_text(session), fonts.big)
    draw_text(draw, (end_x + UNIT_GAP, HERO_BASELINE), "h", fonts.big_unit)
    x = end_x + UNIT_GAP + text_width("h", fonts.big_unit) + HERO_GAP
    max_width = WIDTH - MARGIN - x
    course = ellipsize(session.course_name or t["course_unknown"], fonts.value, max_width)
    draw_text(draw, (x, COURSE_BASELINE), course, fonts.value)
    if session.topic:
        draw_text(
            draw, (x, TOPIC_BASELINE), ellipsize(session.topic, fonts.body, max_width), fonts.body
        )
    draw_text(draw, (MARGIN, ENDED_BASELINE), ended_line(session, s), fonts.body)


def _draw_week_bar(draw: ImageDraw.ImageDraw, data: DashboardData, left: int, right: int) -> None:
    """A small outlined bar filled to the week quota's percent (capped at 100)."""
    draw.rectangle((left, BAR_TOP, right, BAR_TOP + BAR_HEIGHT), outline=BLACK, width=2)
    fraction = max(0.0, min(1.0, data.week_quota.percent / 100))
    fill_right = left + int((right - left) * fraction)
    if fill_right > left + 4:
        draw.rectangle(
            (left + 2, BAR_TOP + 2, fill_right - 2, BAR_TOP + BAR_HEIGHT - 2), fill=BLACK
        )


def _draw_stats(
    draw: ImageDraw.ImageDraw,
    data: DashboardData,
    fonts: Fonts,
    t: dict[str, str],
    s: dict[str, str],
) -> None:
    column = (WIDTH - 2 * MARGIN) // 3
    labels = (s["recap_today"], s["recap_streak"], s["recap_week"])
    for index, (label, value) in enumerate(zip(labels, stat_values(data, t), strict=True)):
        x = MARGIN + index * column
        draw_text(draw, (x, STATS_LABEL_BASELINE), label, fonts.label)
        draw_text(
            draw, (x, STATS_VALUE_BASELINE), ellipsize(value, fonts.value, column - 16), fonts.value
        )
    week_x = MARGIN + 2 * column
    _draw_week_bar(draw, data, week_x, week_x + column - 24)


def render(data: DashboardData, language: str) -> Image.Image:
    t = common.TEXT[language]
    s = TEXT[language]
    fonts = load_fonts()
    canvas, draw = new_canvas()
    draw_header(draw, data, fonts, t)
    draw_text(draw, (MARGIN, LABEL_BASELINE), s["recap_label"], fonts.label)
    if data.last_session is None:
        draw_text(draw, (MARGIN, COURSE_BASELINE + 40), s["recap_none"], fonts.value)
    else:
        _draw_hero(draw, data.last_session, fonts, t, s)
    _draw_stats(draw, data, fonts, t, s)
    footer = ellipsize(footer_text(data, t, s), fonts.body, WIDTH - 2 * MARGIN)
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
    """The duration large with the unit, the course and how long ago it ended, then two
    stats lines (today and streak, week quota)."""
    t = common.TEXT[language]
    s = TEXT[language]
    left, top, right, _ = box
    width = right - left
    session = data.last_session
    if session is None:
        draw_text(draw, (left, top + 31), ellipsize(s["recap_none"], fonts.body, width), fonts.body)
        return
    end_x = draw_text(draw, (left, top + PANE_HERO_BASELINE), duration_text(session), fonts.big)
    draw_text(draw, (end_x + UNIT_GAP, top + PANE_HERO_BASELINE), "h", fonts.big_unit)
    course = ellipsize(session.course_name or t["course_unknown"], fonts.body, width)
    draw_text(draw, (left, top + PANE_COURSE_BASELINE), course, fonts.body)
    ended = ellipsize(ended_line(session, s), fonts.small, width)
    draw_text(draw, (left, top + PANE_ENDED_BASELINE), ended, fonts.small)
    today, streak, week = stat_values(data, t)
    lines = (
        f"{s['recap_today']} {today}",
        t["separator"].join([f"{s['recap_streak']} {streak}", f"{s['recap_week']} {week}"]),
    )
    for index, line in enumerate(lines):
        baseline = top + PANE_STATS_BASELINE + index * PANE_STATS_STEP
        draw_text(draw, (left, baseline), ellipsize(line, fonts.body, width), fonts.body)
