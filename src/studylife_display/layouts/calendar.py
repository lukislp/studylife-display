"""The week as a calendar: seven columns Monday to Sunday under a header of weekday initials
and day numbers (today's inverted), a time axis on the left (06 to 22 h, a label every four
hours) and every session of the week (from `GET /api/sessions`, planned and completed) as a
block placed by its start and end - planned ones outlined with a light hatch, completed
ones solid. Sessions outside 06-22 are clamped to the edges; overlapping ones are drawn on
top of each other. No text in the blocks.

Days with an exam (an upcoming course goal dated in this week) carry a small inverted marker
under the header ("P" / "E") and the footer names them; a thin line marks "now" in today's
column. The footer counts the sessions and sums their hours; without any it says so, and
without the `Sessions.GetAll` scope it names the scope. The pane form is the same grid
scaled into half the frame, with weekday initials only and no footer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from PIL import Image, ImageDraw

import studylife_display.layouts.common as common
from studylife_display.layouts.common import (
    BLACK,
    MARGIN,
    WHITE,
    WIDTH,
    Fonts,
    draw_footer_line,
    draw_header,
    draw_text,
    ellipsize,
    fill_pattern,
    finish,
    format_decimal,
    load_fonts,
    new_canvas,
    text_width,
)
from studylife_display.model import AgendaItem, DashboardData

# This layout's own strings; everything else comes from the shared table in common.py.
TEXT: dict[str, dict[str, str]] = {
    "de": {
        "calendar_exam_initial": "P",
        "calendar_exams": "Prüfung: {exams}",
        "calendar_exam_entry": "{course} {weekday} {date}",
        "calendar_none": "keine Sessions diese Woche",
        "calendar_scope": "Schlüssel ohne Scope Sessions.GetAll",
        "calendar_sessions_one": "1 Session",
        "calendar_sessions_many": "{count} Sessions",
        "calendar_summary": "{sessions} · {hours} h geplant",
    },
    "en": {
        "calendar_exam_initial": "E",
        "calendar_exams": "Exam: {exams}",
        "calendar_exam_entry": "{course} {weekday} {date}",
        "calendar_none": "no sessions this week",
        "calendar_scope": "key lacks the scope Sessions.GetAll",
        "calendar_sessions_one": "1 session",
        "calendar_sessions_many": "{count} sessions",
        "calendar_summary": "{sessions} · {hours} h planned",
    },
}

# The hours the grid covers; sessions outside are clamped to the edges.
FIRST_HOUR = 6
LAST_HOUR = 22
AXIS_LABEL_STEP = 4
DAYS = 7
MIN_BLOCK_HEIGHT = 4
FOOTER_RULE_Y = 428


@dataclass(frozen=True)
class Geometry:
    """Where the grid stands: the left edge of the first column, the header row, the marker
    row under it and the grid, in canvas pixels."""

    axis_left: int
    grid_left: int
    col_width: int
    header_top: int
    header_height: int
    marker_top: int
    marker_height: int
    grid_top: int
    hour_height: int
    block_inset: int
    day_numbers: bool

    @property
    def grid_bottom(self) -> int:
        return self.grid_top + (LAST_HOUR - FIRST_HOUR) * self.hour_height

    @property
    def grid_right(self) -> int:
        return self.grid_left + DAYS * self.col_width

    def column_left(self, index: int) -> int:
        return self.grid_left + index * self.col_width

    def y_at(self, hour: float) -> int:
        """The pixel row of a time of day (hours since midnight), clamped to the grid."""
        clamped = max(float(FIRST_HOUR), min(float(LAST_HOUR), hour))
        return self.grid_top + int(round((clamped - FIRST_HOUR) * self.hour_height))


FRAME = Geometry(
    axis_left=MARGIN,
    grid_left=MARGIN + 36,
    col_width=102,
    header_top=60,
    header_height=28,
    marker_top=92,
    marker_height=18,
    grid_top=114,
    hour_height=19,
    block_inset=3,
    day_numbers=True,
)


def pane_geometry(box: tuple[int, int, int, int]) -> Geometry:
    """The same grid in a pane box: initials only, narrower columns, shorter hours."""
    left, top, right, _ = box
    grid_left = left + 30
    return Geometry(
        axis_left=left,
        grid_left=grid_left,
        col_width=(right - grid_left) // DAYS,
        header_top=top,
        header_height=26,
        marker_top=top + 30,
        marker_height=18,
        grid_top=top + 52,
        hour_height=17,
        block_inset=2,
        day_numbers=False,
    )


def hour_of_day(moment: datetime, tz: object) -> float:
    local = moment.astimezone(tz)  # type: ignore[arg-type]
    return local.hour + local.minute / 60 + local.second / 3600


def block_box(
    item: AgendaItem, data: DashboardData, geometry: Geometry
) -> tuple[int, int, int, int]:
    """The rectangle of a session: its column by weekday, its rows by start and end, with
    a minimum height so a clamped or very short session stays visible."""
    tz = data.now.tzinfo
    start = item.start.astimezone(tz)
    end = item.end.astimezone(tz)
    day = start.weekday()
    start_hour = hour_of_day(start, tz)
    # A session running past midnight ends at the bottom edge of its own day.
    end_hour = float(LAST_HOUR) if end.date() != start.date() else hour_of_day(end, tz)
    top = geometry.y_at(start_hour)
    bottom = geometry.y_at(end_hour)
    if bottom - top < MIN_BLOCK_HEIGHT:
        if bottom >= geometry.grid_bottom:
            top = geometry.grid_bottom - MIN_BLOCK_HEIGHT
            bottom = geometry.grid_bottom
        else:
            bottom = top + MIN_BLOCK_HEIGHT
    left = geometry.column_left(day) + geometry.block_inset
    right = geometry.column_left(day + 1) - geometry.block_inset
    return left, top, right, bottom


def now_y(data: DashboardData, geometry: Geometry) -> int | None:
    """The row of the now-line, None while the clock is outside the grid's hours."""
    hour = hour_of_day(data.now, data.now.tzinfo)
    if not FIRST_HOUR <= hour <= LAST_HOUR:
        return None
    return geometry.y_at(hour)


def marker_box(index: int, geometry: Geometry) -> tuple[int, int, int, int]:
    """The small block under the header of day `index` that marks an exam."""
    centre = geometry.column_left(index) + geometry.col_width // 2
    return (
        centre - 11,
        geometry.marker_top,
        centre + 11,
        geometry.marker_top + geometry.marker_height,
    )


def week_monday(data: DashboardData) -> date:
    today = data.now.date()
    return today - timedelta(days=today.weekday())


def planned_hours(items: tuple[AgendaItem, ...]) -> float:
    return sum((item.end - item.start) / timedelta(hours=1) for item in items)


def summary_line(data: DashboardData, t: dict[str, str], s: dict[str, str]) -> str:
    """ "12 Sessions · 18,5 h geplant", or the "no sessions" / scope hint."""
    if "sessions" in data.unavailable:
        return s["calendar_scope"]
    count = len(data.week_calendar)
    if count == 0:
        return s["calendar_none"]
    key = "calendar_sessions_one" if count == 1 else "calendar_sessions_many"
    return s["calendar_summary"].format(
        sessions=s[key].format(count=count),
        hours=format_decimal(planned_hours(data.week_calendar), t["decimal"]),
    )


def exams_line(data: DashboardData, t: dict[str, str], s: dict[str, str]) -> str:
    """ "Prüfung: Betriebssysteme Fr 18.09." - every exam of the week; "" without one."""
    if not data.week_goal_days:
        return ""
    monday = week_monday(data)
    weekdays = t["weekdays"].split(",")
    entries = []
    for weekday, course in data.week_goal_days:
        day = monday + timedelta(days=weekday)
        entries.append(
            s["calendar_exam_entry"].format(
                course=course or t["course_unknown"],
                weekday=weekdays[weekday],
                date=day.strftime(t["date_format"]),
            )
        )
    return s["calendar_exams"].format(exams=", ".join(entries))


def _dotted_line(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int]) -> None:
    """A horizontal or vertical line of every fourth pixel."""
    (x0, y0), (x1, y1) = start, end
    if y0 == y1:
        for x in range(x0, x1 + 1, 4):
            draw.point((x, y0), fill=BLACK)
    else:
        for y in range(y0, y1 + 1, 4):
            draw.point((x0, y), fill=BLACK)


def _draw_header_row(
    draw: ImageDraw.ImageDraw,
    data: DashboardData,
    fonts: Fonts,
    t: dict[str, str],
    geometry: Geometry,
) -> None:
    initials = t["weekday_initials"].split(",")
    monday = week_monday(data)
    today = data.now.weekday()
    baseline = geometry.header_top + geometry.header_height - 7
    for index in range(DAYS):
        left = geometry.column_left(index)
        label = initials[index]
        if geometry.day_numbers:
            label = f"{label} {(monday + timedelta(days=index)).day}"
        if index == today:
            draw.rectangle(
                (
                    left + 1,
                    geometry.header_top,
                    left + geometry.col_width - 1,
                    geometry.header_top + geometry.header_height,
                ),
                fill=BLACK,
            )
            fill = WHITE
        else:
            fill = BLACK
        centre = left + geometry.col_width / 2
        draw_text(draw, (centre, baseline), label, fonts.small, fill=fill, anchor="ms")


def _draw_markers(
    draw: ImageDraw.ImageDraw,
    data: DashboardData,
    fonts: Fonts,
    s: dict[str, str],
    geometry: Geometry,
) -> None:
    for index in sorted({weekday for weekday, _ in data.week_goal_days}):
        left, top, right, bottom = marker_box(index, geometry)
        draw.rounded_rectangle((left, top, right, bottom), radius=4, fill=BLACK)
        draw_text(
            draw,
            ((left + right) / 2, bottom - 3),
            s["calendar_exam_initial"],
            fonts.small,
            fill=WHITE,
            anchor="ms",
        )


def _draw_grid(draw: ImageDraw.ImageDraw, fonts: Fonts, geometry: Geometry) -> None:
    """The time axis labels, a dotted line at every labelled hour and a dotted separator
    between the columns."""
    for hour in range(FIRST_HOUR, LAST_HOUR + 1, AXIS_LABEL_STEP):
        y = geometry.y_at(hour)
        _dotted_line(draw, (geometry.grid_left, y), (geometry.grid_right, y))
        draw_text(draw, (geometry.grid_left - 6, y + 6), f"{hour:02d}", fonts.small, anchor="rs")
    for index in range(DAYS + 1):
        x = geometry.column_left(index)
        _dotted_line(draw, (x, geometry.grid_top), (x, geometry.grid_bottom))


def _draw_blocks(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    data: DashboardData,
    geometry: Geometry,
) -> None:
    for item in data.week_calendar:
        box = block_box(item, data, geometry)
        left, top, right, bottom = box
        if item.is_completed:
            draw.rectangle(box, fill=BLACK)
            continue
        draw.rectangle(box, fill=WHITE, outline=BLACK, width=1)
        if bottom - top > 2 and right - left > 2:
            fill_pattern(image, (left + 1, top + 1, right, bottom), 1)


def _draw_now(draw: ImageDraw.ImageDraw, data: DashboardData, geometry: Geometry) -> None:
    y = now_y(data, geometry)
    if y is None:
        return
    left = geometry.column_left(data.now.weekday())
    right = left + geometry.col_width
    # A white halo either side keeps the line visible where it crosses a solid block.
    draw.line([(left, y - 2), (right, y - 2)], fill=WHITE, width=1)
    draw.line([(left, y + 2), (right, y + 2)], fill=WHITE, width=1)
    draw.line([(left, y - 1), (right, y - 1)], fill=BLACK, width=1)
    draw.line([(left, y), (right, y)], fill=BLACK, width=1)
    draw.line([(left, y + 1), (right, y + 1)], fill=BLACK, width=1)


def draw_calendar(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    data: DashboardData,
    fonts: Fonts,
    language: str,
    geometry: Geometry,
) -> None:
    t = common.TEXT[language]
    s = TEXT[language]
    _draw_header_row(draw, data, fonts, t, geometry)
    _draw_markers(draw, data, fonts, s, geometry)
    _draw_grid(draw, fonts, geometry)
    _draw_blocks(image, draw, data, geometry)
    _draw_now(draw, data, geometry)


def render(data: DashboardData, language: str) -> Image.Image:
    t = common.TEXT[language]
    s = TEXT[language]
    fonts = load_fonts()
    canvas, draw = new_canvas()
    draw_header(draw, data, fonts, t)
    draw_calendar(canvas, draw, data, fonts, language, FRAME)
    summary = summary_line(data, t, s)
    draw_footer_line(draw, summary, fonts, FOOTER_RULE_Y)
    exams = exams_line(data, t, s)
    if exams:
        left = MARGIN + text_width(summary, fonts.body) + 24
        width = WIDTH - MARGIN - left
        if width > 80:
            draw_text(
                draw,
                (WIDTH - MARGIN, FOOTER_RULE_Y + 26),
                ellipsize(exams, fonts.small, width),
                fonts.small,
                anchor="rs",
            )
    return finish(canvas)


def render_pane(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    data: DashboardData,
    fonts: Fonts,
    language: str,
    box: tuple[int, int, int, int],
) -> None:
    """The same grid scaled into the pane: initials only, no footer."""
    draw_calendar(image, draw, data, fonts, language, pane_geometry(box))
