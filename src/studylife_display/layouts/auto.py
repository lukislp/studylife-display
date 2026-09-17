"""The "auto" choice: pick the layout that matters most right now."""

from __future__ import annotations

from studylife_display.layouts import AUTO, LAYOUTS
from studylife_display.model import DashboardData

# An exam this close is what the panel should be about; one day further and the timer or
# the classic overview win again.
EXAM_SOON_DAYS = 7


def resolve_layout(choice: str, data: DashboardData) -> str:
    """ "auto" -> exam when the next goal is due within EXAM_SOON_DAYS, else focus while the
    timer runs, else classic. A concrete layout key is returned unchanged; anything else
    raises ValueError."""
    if choice != AUTO:
        if choice not in LAYOUTS:
            raise ValueError(f"unknown layout {choice!r}")
        return choice
    goal = data.next_goal
    if goal is not None and goal.days_left <= EXAM_SOON_DAYS:
        return "exam"
    if data.timer is not None and data.timer.is_running:
        return "focus"
    return "classic"
