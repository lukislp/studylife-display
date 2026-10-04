"""The two pseudo choices: "auto" picks the layout that matters most right now, "cycle"
steps through a configured list, one layout per refresh.

The auto rules, in the order they are tried (the setup and error screens are decided before
any of this, in `main.refresh_panel`):

1. `review` inside the review window (`DISPLAY_AUTO_REVIEW`, default Sunday 18:00-24:00);
2. `milestone` on the one day the streak hits a round number (`milestone.MILESTONE_DAYS`) -
   rare enough, and worth seeing right away, that it outranks even an exam countdown;
3. `exam` when the next course goal is due within EXAM_SOON_DAYS;
4. `focus` while a timer is running;
5. `quiet` inside the quiet window (`DISPLAY_AUTO_QUIET`, off by default) - meant for the
   hour before the quiet hours, so the frame that stays on all night is the calm one;
6. `agenda` while a session planned for today still lies ahead and the wall clock is inside
   the agenda window (`DISPLAY_AUTO_AGENDA`, default 06:00-12:00);
7. `tomorrow` while tomorrow has sessions planned and the wall clock is inside the tomorrow
   window (`DISPLAY_AUTO_TOMORROW`, default 18:00-23:00);
8. `classic` otherwise. Every other layout is never picked automatically.

An empty window switches that rule off. The windows and the cycle list travel in
`AutoRules`, built from the settings by `rules_from_settings`, so this module stays free of
I/O and of the clock: the moment tested is `data.now`, which `build_dashboard` has already
put in the server's zone. "cycle" needs one more input, the layout shown last (`previous`,
from current.json): the next one in the list follows it, the first one when the last frame
was not part of the list (or there was none). Neither choice ever turns the panel: the
physical orientation is DISPLAY_ROTATE alone, applied in driver.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from studylife_display.auto_rules import in_rule_window
from studylife_display.config import DEFAULT_CYCLE, parse_layout_list
from studylife_display.layouts import AUTO, CYCLE, LAYOUTS
from studylife_display.layouts.milestone import is_milestone
from studylife_display.model import DashboardData

if TYPE_CHECKING:
    from studylife_display.config import Settings

# An exam this close is what the panel should be about; one day further and the timer or
# the classic overview win again.
EXAM_SOON_DAYS = 7

DEFAULT_REVIEW_WINDOW = "sun 18-24"
DEFAULT_AGENDA_WINDOW = "06-12"
DEFAULT_TOMORROW_WINDOW = "18-23"
DEFAULT_QUIET_WINDOW = ""


@dataclass(frozen=True)
class AutoRules:
    """The configurable windows of the auto rules (in the `auto_rules` notation) and the
    list the cycle choice steps through."""

    review_window: str = DEFAULT_REVIEW_WINDOW
    agenda_window: str = DEFAULT_AGENDA_WINDOW
    tomorrow_window: str = DEFAULT_TOMORROW_WINDOW
    quiet_window: str = DEFAULT_QUIET_WINDOW
    cycle: tuple[str, ...] = parse_layout_list(DEFAULT_CYCLE, "DISPLAY_CYCLE")


DEFAULT_RULES = AutoRules()


def rules_from_settings(settings: Settings) -> AutoRules:
    return AutoRules(
        review_window=settings.display_auto_review,
        agenda_window=settings.display_auto_agenda,
        tomorrow_window=settings.display_auto_tomorrow,
        quiet_window=settings.display_auto_quiet,
        cycle=parse_layout_list(settings.display_cycle, "DISPLAY_CYCLE"),
    )


def next_in_cycle(cycle: tuple[str, ...], previous: str | None) -> str:
    """The layout after `previous` in `cycle`, wrapping around; the first one when
    `previous` is None or not in the list."""
    if not cycle:
        return "classic"
    if previous in cycle:
        return cycle[(cycle.index(previous) + 1) % len(cycle)]
    return cycle[0]


def resolve_layout(
    choice: str,
    data: DashboardData,
    rules: AutoRules = DEFAULT_RULES,
    previous: str | None = None,
) -> str:
    """ "auto" -> the first rule above that applies; "cycle" -> the layout after `previous`
    in the cycle list. A concrete layout key is returned unchanged; anything else raises
    ValueError."""
    if choice == CYCLE:
        return next_in_cycle(rules.cycle, previous)
    if choice != AUTO:
        if choice not in LAYOUTS:
            raise ValueError(f"unknown layout {choice!r}")
        return choice
    if in_rule_window(data.now, rules.review_window):
        return "review"
    if is_milestone(data.streak_days):
        return "milestone"
    goal = data.next_goal
    if goal is not None and goal.days_left <= EXAM_SOON_DAYS:
        return "exam"
    if data.timer is not None and data.timer.is_running:
        return "focus"
    if in_rule_window(data.now, rules.quiet_window):
        return "quiet"
    if data.next_agenda_item is not None and in_rule_window(data.now, rules.agenda_window):
        return "agenda"
    if data.tomorrow and in_rule_window(data.now, rules.tomorrow_window):
        return "tomorrow"
    return "classic"
