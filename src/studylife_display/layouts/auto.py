"""The "auto" choice: pick the layout that matters most right now.

The rules, in the order they are tried (the setup and error screens are decided before any
of this, in `main.refresh_panel`):

1. `review` inside the review window (`DISPLAY_AUTO_REVIEW`, default Sunday 18:00-24:00);
2. `exam` when the next course goal is due within EXAM_SOON_DAYS;
3. `focus` while a timer is running;
4. `agenda` while a session planned for today still lies ahead and the wall clock is inside
   the agenda window (`DISPLAY_AUTO_AGENDA`, default 06:00-12:00);
5. `classic` otherwise. `semester` is never picked automatically.

An empty window switches that rule off. The windows travel in `AutoRules`, built from the
settings by `rules_from_settings`, so this module stays free of I/O and of the clock: the
moment tested is `data.now`, which `build_dashboard` has already put in the server's zone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from studylife_display.auto_rules import in_rule_window
from studylife_display.layouts import AUTO, LAYOUTS
from studylife_display.model import DashboardData

if TYPE_CHECKING:
    from studylife_display.config import Settings

# An exam this close is what the panel should be about; one day further and the timer or
# the classic overview win again.
EXAM_SOON_DAYS = 7

DEFAULT_REVIEW_WINDOW = "sun 18-24"
DEFAULT_AGENDA_WINDOW = "06-12"


@dataclass(frozen=True)
class AutoRules:
    """The configurable windows of the auto rules, in the `auto_rules` notation."""

    review_window: str = DEFAULT_REVIEW_WINDOW
    agenda_window: str = DEFAULT_AGENDA_WINDOW


DEFAULT_RULES = AutoRules()


def rules_from_settings(settings: Settings) -> AutoRules:
    return AutoRules(
        review_window=settings.display_auto_review,
        agenda_window=settings.display_auto_agenda,
    )


def resolve_layout(choice: str, data: DashboardData, rules: AutoRules = DEFAULT_RULES) -> str:
    """ "auto" -> the first rule above that applies. A concrete layout key is returned
    unchanged; anything else raises ValueError."""
    if choice != AUTO:
        if choice not in LAYOUTS:
            raise ValueError(f"unknown layout {choice!r}")
        return choice
    if in_rule_window(data.now, rules.review_window):
        return "review"
    goal = data.next_goal
    if goal is not None and goal.days_left <= EXAM_SOON_DAYS:
        return "exam"
    if data.timer is not None and data.timer.is_running:
        return "focus"
    if data.next_agenda_item is not None and in_rule_window(data.now, rules.agenda_window):
        return "agenda"
    return "classic"
