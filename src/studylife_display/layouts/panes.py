"""The pane form of every layout: the same content squeezed into one half of the frame, for
the "duo" layout that shows two layouts side by side.

Every layout module exports `render_pane(image, draw, data, fonts, language, box)`: it draws into
`box` (left, top, right, bottom; 364 x 340 pixels) on the shared canvas and leaves the rest
alone - no header, no footer, no fill outside the box. `duo.py` draws the frame around two of
them. The registry here maps layout keys onto those functions, in the layouts' own order.
"""

from __future__ import annotations

from collections.abc import Callable

from PIL import Image, ImageDraw

from studylife_display.layouts import (
    achievements,
    agenda,
    balance,
    classic,
    courses,
    exam,
    exams,
    focus,
    goals,
    milestone,
    month,
    note,
    quiet,
    review,
    semester,
    timer,
    today,
    tomorrow,
    week,
    year,
)
from studylife_display.layouts.common import HEIGHT, MARGIN, WIDTH, Fonts
from studylife_display.model import DashboardData

Box = tuple[int, int, int, int]
Pane = Callable[[Image.Image, ImageDraw.ImageDraw, DashboardData, Fonts, str, Box], None]

# Geometry shared with duo.py: two panes under the header line, a gutter between them with
# the divider in its middle, pane titles on PANE_LABEL_BASELINE.
PANE_GAP = 24
PANE_LABEL_BASELINE = 86
PANE_TOP = 100
PANE_BOTTOM = HEIGHT - 40
PANE_WIDTH = (WIDTH - 2 * MARGIN - PANE_GAP) // 2
DIVIDER_X = WIDTH // 2

PANES: dict[str, Pane] = {
    "classic": classic.render_pane,
    "focus": focus.render_pane,
    "exam": exam.render_pane,
    "week": week.render_pane,
    "semester": semester.render_pane,
    "agenda": agenda.render_pane,
    "courses": courses.render_pane,
    "milestone": milestone.render_pane,
    "review": review.render_pane,
    "month": month.render_pane,
    "exams": exams.render_pane,
    "year": year.render_pane,
    "balance": balance.render_pane,
    "timer": timer.render_pane,
    "tomorrow": tomorrow.render_pane,
    "today": today.render_pane,
    "goals": goals.render_pane,
    "achievements": achievements.render_pane,
    "note": note.render_pane,
    "quiet": quiet.render_pane,
}


def pane_boxes() -> tuple[Box, Box]:
    """(left pane, right pane) boxes."""
    left = (MARGIN, PANE_TOP, MARGIN + PANE_WIDTH, PANE_BOTTOM)
    right = (WIDTH - MARGIN - PANE_WIDTH, PANE_TOP, WIDTH - MARGIN, PANE_BOTTOM)
    return left, right
