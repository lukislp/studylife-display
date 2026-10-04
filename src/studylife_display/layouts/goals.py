"""The course goals (from `GET /api/coursegoals`) as a list: the open ones first with an
empty mark, the target date and the countdown to it (soonest first, undated ones last),
the completed ones under a thin rule with a ticked mark, the grade and the completion date.
The label line counts both groups, the footer line names the next exam. Needs a key with the
`CourseGoals.GetAll` scope; without it the layout says so."""

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
    format_countdown,
    format_decimal,
    format_goal_line,
    load_fonts,
    new_canvas,
    text_width,
)
from studylife_display.model import CourseGoal, DashboardData

# This layout's own strings; everything else comes from the shared table in common.py.
TEXT: dict[str, dict[str, str]] = {
    "de": {
        "goals_label": "KURSZIELE",
        "goals_open": "{count} offen",
        "goals_done": "{count} erledigt",
        "goals_none": "keine Kursziele",
        "goals_no_date": "ohne Datum",
        "goals_grade": "Note {grade}",
        "goals_scope": "Schlüssel ohne Scope CourseGoals.GetAll",
    },
    "en": {
        "goals_label": "COURSE GOALS",
        "goals_open": "{count} open",
        "goals_done": "{count} done",
        "goals_none": "no course goals",
        "goals_no_date": "no date",
        "goals_grade": "grade {grade}",
        "goals_scope": "key lacks the scope CourseGoals.GetAll",
    },
}

LABEL_BASELINE = 86
ROW_TOP = 100
ROW_HEIGHT = 44
MAX_ROWS = 7
# Extra space between the open and the completed group, with the rule in its middle.
GROUP_GAP = 10
MARK_X = MARGIN + 8
MARK_SIZE = 18
NAME_X = MARGIN + 44
# The date column (left-aligned, small type) and the right-aligned countdown / grade column.
DATE_X = 470
NAME_RIGHT = DATE_X - 16

FOOTER_RULE_Y = 428

# The pane form.
PANE_COUNTS_BASELINE = 16
PANE_ROW_TOP = 26
PANE_ROW_HEIGHT = 40
PANE_MAX_ROWS = 5
PANE_GROUP_GAP = 8
PANE_MARK_SIZE = 16
PANE_NAME_X = 30


def days_left(goal: CourseGoal, data: DashboardData) -> int | None:
    """Calendar days from today (in the server zone) to the goal's target date."""
    if goal.target_date is None:
        return None
    return (goal.target_date - data.now.date()).days


def counts_line(data: DashboardData, t: dict[str, str], s: dict[str, str]) -> str:
    """ "3 offen · 2 erledigt"."""
    done = sum(1 for goal in data.goals if goal.is_completed)
    return t["separator"].join(
        [
            s["goals_open"].format(count=len(data.goals) - done),
            s["goals_done"].format(count=done),
        ]
    )


def _right_value(
    goal: CourseGoal, data: DashboardData, t: dict[str, str], s: dict[str, str]
) -> str:
    """The right column: the countdown (or "no date") for an open goal, the grade for a
    completed one ("" without a grade)."""
    if goal.is_completed:
        if goal.grade is None:
            return ""
        return s["goals_grade"].format(grade=format_decimal(goal.grade, t["decimal"]))
    left = days_left(goal, data)
    return s["goals_no_date"] if left is None else format_countdown(left, t)


def _date_value(goal: CourseGoal, data: DashboardData, t: dict[str, str]) -> str:
    """The date column: the completion date for a completed goal, else the target date."""
    if goal.is_completed and goal.completed_at is not None:
        return goal.completed_at.astimezone(data.now.tzinfo).strftime(t["goal_date_format"])
    if goal.target_date is not None:
        return goal.target_date.strftime(t["goal_date_format"])
    return ""


def _draw_mark(draw: ImageDraw.ImageDraw, x: int, centre_y: int, size: int, ticked: bool) -> None:
    """A small square in front of the row, ticked for a completed goal."""
    top = centre_y - size // 2
    box = (x, top, x + size, top + size)
    draw.rectangle(box, outline=BLACK, width=2)
    if ticked:
        left, box_top, right, bottom = box
        inset = max(3, size // 5)
        draw.line(
            [
                (left + inset, box_top + size // 2),
                (left + size // 2 - 1, bottom - inset - 1),
                (right - inset, box_top + inset),
            ],
            fill=BLACK,
            width=3,
        )


def _shown_rows(goals: tuple[CourseGoal, ...], max_rows: int) -> tuple[CourseGoal, ...]:
    """The rows that fit: all of them, or one fewer than `max_rows` so the "+N more" line
    takes the last slot."""
    if len(goals) <= max_rows:
        return goals
    return goals[: max_rows - 1]


def _draw_rows(
    draw: ImageDraw.ImageDraw,
    data: DashboardData,
    fonts: Fonts,
    t: dict[str, str],
    s: dict[str, str],
) -> None:
    draw_text(draw, (MARGIN, LABEL_BASELINE), s["goals_label"], fonts.label)
    if "goals" in data.unavailable:
        draw_text(draw, (MARGIN, ROW_TOP + 31), s["goals_scope"], fonts.body)
        return
    if not data.goals:
        draw_text(draw, (MARGIN, ROW_TOP + 31), s["goals_none"], fonts.body)
        return
    counts = counts_line(data, t, s)
    draw_text(draw, (WIDTH - MARGIN, LABEL_BASELINE), counts, fonts.small, anchor="rs")

    shown = _shown_rows(data.goals, MAX_ROWS)
    top = ROW_TOP
    previous: CourseGoal | None = None
    for goal in shown:
        if previous is not None and goal.is_completed and not previous.is_completed:
            rule_y = top + GROUP_GAP // 2
            draw.line([(MARGIN, rule_y), (WIDTH - MARGIN, rule_y)], fill=BLACK)
            top += GROUP_GAP
        _draw_mark(draw, MARK_X, top + ROW_HEIGHT // 2, MARK_SIZE, goal.is_completed)
        baseline = top + 30
        name_right = NAME_RIGHT
        tag = goal.tag if goal.tag and not goal.is_completed else None
        if tag is not None:
            name_right -= int(text_width(tag, fonts.small)) + 12
        name = ellipsize(goal.course_name or t["course_unknown"], fonts.body, name_right - NAME_X)
        end_x = draw_text(draw, (NAME_X, baseline), name, fonts.body)
        if tag is not None:
            draw_text(draw, (end_x + 12, baseline), tag, fonts.small)
        date = _date_value(goal, data, t)
        if date:
            draw_text(draw, (DATE_X, baseline), date, fonts.small)
        value = _right_value(goal, data, t, s)
        if value:
            draw_text(draw, (WIDTH - MARGIN, baseline), value, fonts.body, anchor="rs")
        previous = goal
        top += ROW_HEIGHT
    hidden = len(data.goals) - len(shown)
    if hidden > 0:
        more = t["agenda_more"].format(count=hidden)
        draw_text(draw, (NAME_X, top + 28), more, fonts.small)


def render(data: DashboardData, language: str) -> Image.Image:
    t = common.TEXT[language]
    fonts = load_fonts()
    canvas, draw = new_canvas()
    draw_header(draw, data, fonts, t)
    _draw_rows(draw, data, fonts, t, TEXT[language])
    footer = ellipsize(format_goal_line(data, t), fonts.body, WIDTH - 2 * MARGIN)
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
    """The counts line, then up to PANE_MAX_ROWS goals: mark, course and the countdown or
    grade; the rule between the groups as in the full frame."""
    t = common.TEXT[language]
    s = TEXT[language]
    left, top, right, _ = box
    if "goals" in data.unavailable:
        text = ellipsize(s["goals_scope"], fonts.small, right - left)
        draw_text(draw, (left, top + 31), text, fonts.small)
        return
    if not data.goals:
        draw_text(draw, (left, top + 31), s["goals_none"], fonts.body)
        return
    draw_text(draw, (left, top + PANE_COUNTS_BASELINE), counts_line(data, t, s), fonts.small)

    shown = _shown_rows(data.goals, PANE_MAX_ROWS)
    row_top = top + PANE_ROW_TOP
    name_x = left + PANE_NAME_X
    previous: CourseGoal | None = None
    for goal in shown:
        if previous is not None and goal.is_completed and not previous.is_completed:
            rule_y = row_top + PANE_GROUP_GAP // 2
            draw.line([(left, rule_y), (right, rule_y)], fill=BLACK)
            row_top += PANE_GROUP_GAP
        _draw_mark(
            draw, left + 2, row_top + PANE_ROW_HEIGHT // 2, PANE_MARK_SIZE, goal.is_completed
        )
        baseline = row_top + 28
        value = _right_value(goal, data, t, s)
        name_right = right
        if value:
            draw_text(draw, (right, baseline), value, fonts.small, anchor="rs")
            name_right -= int(text_width(value, fonts.small)) + 12
        name = ellipsize(goal.course_name or t["course_unknown"], fonts.body, name_right - name_x)
        draw_text(draw, (name_x, baseline), name, fonts.body)
        previous = goal
        row_top += PANE_ROW_HEIGHT
    hidden = len(data.goals) - len(shown)
    if hidden > 0:
        more = t["agenda_more"].format(count=hidden)
        draw_text(draw, (name_x, row_top + 26), more, fonts.small)
