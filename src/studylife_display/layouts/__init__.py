"""The layout registry: every way the 800x480 frame can be arranged, keyed by name.

A layout is a pure function `render(data, language) -> Image` (mode "1", 800x480). The
names, display names and one-line descriptions here feed the web interface and the README;
`studylife_display.render.render` dispatches through LAYOUTS.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PIL import Image

from studylife_display.layouts import classic, exam, focus, week
from studylife_display.model import DashboardData

Renderer = Callable[[DashboardData, str], Image.Image]

AUTO = "auto"


@dataclass(frozen=True)
class LayoutSpec:
    key: str
    name: dict[str, str]
    description: dict[str, str]
    render: Renderer


LAYOUTS: dict[str, LayoutSpec] = {
    spec.key: spec
    for spec in (
        LayoutSpec(
            key="classic",
            name={"de": "Klassisch", "en": "Classic"},
            description={
                "de": (
                    "Stunden heute, Serie, Countdown, Wochenziel, Heatmap und Timer "
                    "auf einen Blick."
                ),
                "en": (
                    "Today's hours, streak, countdown, week target, heatmap and timer at a glance."
                ),
            },
            render=classic.render,
        ),
        LayoutSpec(
            key="focus",
            name={"de": "Fokus", "en": "Focus"},
            description={
                "de": "Die Restzeit des laufenden Timers riesig, ohne Timer die Stunden von heute.",
                "en": "The running timer's remaining time, huge; today's hours when none runs.",
            },
            render=focus.render,
        ),
        LayoutSpec(
            key="exam",
            name={"de": "Prüfung", "en": "Exam"},
            description={
                "de": (
                    "Der Countdown zur nächsten Prüfung groß, darunter die Stunden je "
                    "Kurs (28 Tage)."
                ),
                "en": "The countdown to the next exam, large, over the hours per course (28 days).",
            },
            render=exam.render,
        ),
        LayoutSpec(
            key="week",
            name={"de": "Woche", "en": "Week"},
            description={
                "de": (
                    "Das Wochenziel als großer Balken, die vier Wochen als große Heatmap "
                    "mit Summen."
                ),
                "en": (
                    "The week target as a large bar, the four weeks as a large heatmap with sums."
                ),
            },
            render=week.render,
        ),
    )
}

__all__ = ["AUTO", "LAYOUTS", "LayoutSpec", "Renderer"]
