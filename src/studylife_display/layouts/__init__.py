"""The layout registry: every way the 800x480 frame can be arranged, keyed by name.

A layout is a pure function `render(data, language) -> Image` (mode "1", 800x480) plus a
`render_pane` that draws the same content into one half of the frame (see panes.py and the
"duo" layout). The names, display names and one-line descriptions here feed the web
interface, the JSON API and the README; `studylife_display.render.render` dispatches
through LAYOUTS.

Two choices are not layouts but rules that pick one per refresh (PSEUDO_CHOICES): "auto"
(layouts/auto.py's rules) and "cycle" (the next one of a configured list). They have names
and descriptions like the layouts so the pickers can list them, but nothing to render.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PIL import Image

from studylife_display.layouts import (
    achievements,
    agenda,
    balance,
    classic,
    courses,
    duo,
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
from studylife_display.model import DashboardData

Renderer = Callable[[DashboardData, str], Image.Image]

AUTO = "auto"
CYCLE = "cycle"
DUO = "duo"


@dataclass(frozen=True)
class LayoutSpec:
    key: str
    name: dict[str, str]
    description: dict[str, str]
    render: Renderer


@dataclass(frozen=True)
class PseudoSpec:
    """A choice that resolves to a layout per refresh instead of being one."""

    key: str
    name: dict[str, str]
    description: dict[str, str]


PSEUDO_CHOICES: dict[str, PseudoSpec] = {
    spec.key: spec
    for spec in (
        PseudoSpec(
            key=AUTO,
            name={"de": "Automatisch", "en": "Automatic"},
            description={
                "de": "Wählt bei jeder Aktualisierung das passende Layout.",
                "en": "Picks the fitting layout on every refresh.",
            },
        ),
        PseudoSpec(
            key=CYCLE,
            name={"de": "Wechsel", "en": "Cycle"},
            description={
                "de": (
                    "Zeigt bei jeder Aktualisierung das nächste Layout aus der eingestellten "
                    "Liste; der Inhalt wechselt, die Ausrichtung des Panels nie."
                ),
                "en": (
                    "Shows the next layout of the configured list on every refresh; the "
                    "content changes, the panel's orientation never does."
                ),
            },
        ),
    )
}

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
        LayoutSpec(
            key="semester",
            name={"de": "Semester", "en": "Semester"},
            description={
                "de": (
                    "ECTS mit Fortschrittsbalken, Notenschnitt, Abschlussprognose, "
                    "vernachlässigter Kurs und Themen; nie automatisch gewählt."
                ),
                "en": (
                    "ECTS with a progress bar, average grade, graduation forecast, neglected "
                    "course and topics; never picked automatically."
                ),
            },
            render=semester.render,
        ),
        LayoutSpec(
            key="agenda",
            name={"de": "Tagesplan", "en": "Agenda"},
            description={
                "de": (
                    "Die heute geplanten Sessions als Liste, die nächste hervorgehoben; "
                    "rechts Stunden, Serie und nächste Prüfung."
                ),
                "en": (
                    "Today's planned sessions as a list with the next one highlighted; hours, "
                    "streak and next exam on the right."
                ),
            },
            render=agenda.render,
        ),
        LayoutSpec(
            key="courses",
            name={"de": "Kursverteilung", "en": "Course breakdown"},
            description={
                "de": (
                    "Stunden je Kurs (28 Tage) als große Balken über den ganzen Rahmen, "
                    "mit der Gesamtsumme oben; nie automatisch gewählt."
                ),
                "en": (
                    "Hours per course (28 days) as large bars across the whole frame, with "
                    "the total up top; never picked automatically."
                ),
            },
            render=courses.render,
        ),
        LayoutSpec(
            key="milestone",
            name={"de": "Serien-Meilenstein", "en": "Streak milestone"},
            description={
                "de": (
                    "Die Serie riesig, für einen Feiertag: nur automatisch gewählt, wenn sie "
                    "heute eine runde Zahl erreicht (7, 30, 100, ...)."
                ),
                "en": (
                    "The streak, huge, for a celebration: only picked automatically on the "
                    "day it hits a round number (7, 30, 100, ...)."
                ),
            },
            render=milestone.render,
        ),
        LayoutSpec(
            key="review",
            name={"de": "Wochenrückblick", "en": "Weekly review"},
            description={
                "de": (
                    "Die Stunden dieser Woche groß, die Änderung zur Vorwoche, meistgelernter "
                    "Kurs, Sessions, Serie und die sieben Tage als Balken."
                ),
                "en": (
                    "This week's hours, large, the change against last week, top course, "
                    "sessions, streak and the seven days as bars."
                ),
            },
            render=review.render,
        ),
        LayoutSpec(
            key="month",
            name={"de": "Monat", "en": "Month"},
            description={
                "de": (
                    "Das Monatsziel als großer Balken, die Tage des Monats als Streifen, "
                    "Resttage und was pro Tag noch fehlt; nie automatisch gewählt."
                ),
                "en": (
                    "The month target as a large bar, the days of the month as a strip, days "
                    "left and what is still needed per day; never picked automatically."
                ),
            },
            render=month.render,
        ),
        LayoutSpec(
            key="exams",
            name={"de": "Prüfungsplan", "en": "Exam schedule"},
            description={
                "de": (
                    "Alle anstehenden Prüfungen als Liste mit Countdown und den Stunden je "
                    "Prüfungskurs; nie automatisch gewählt."
                ),
                "en": (
                    "Every upcoming exam as a list with its countdown and the hours per exam "
                    "course; never picked automatically."
                ),
            },
            render=exams.render,
        ),
        LayoutSpec(
            key="year",
            name={"de": "Jahr", "en": "Year"},
            description={
                "de": (
                    "53 Wochen als Heatmap, dazu Gesamtstunden, aktive Tage, Sessions und die "
                    "längste Serie; nie automatisch gewählt."
                ),
                "en": (
                    "53 weeks as a heatmap, with total hours, active days, sessions and the "
                    "longest streak; never picked automatically."
                ),
            },
            render=year.render,
        ),
        LayoutSpec(
            key="balance",
            name={"de": "Kursbalance", "en": "Course balance"},
            description={
                "de": (
                    "Der Stundenanteil je Kurs gegen den gleichmäßigen Anteil, zu kurz "
                    "gekommene Kurse markiert; nie automatisch gewählt."
                ),
                "en": (
                    "Each course's share of the hours against an even share, with the "
                    "under-served ones flagged; never picked automatically."
                ),
            },
            render=balance.render,
        ),
        LayoutSpec(
            key="timer",
            name={"de": "Timer-Bilanz", "en": "Timer summary"},
            description={
                "de": (
                    "Die laufende Phase und die Tagesbilanz: Sessions heute, Stunden, längste "
                    "Session, erste und letzte; nie automatisch gewählt."
                ),
                "en": (
                    "The running phase and today's tally: sessions today, hours, the longest "
                    "one, first and last; never picked automatically."
                ),
            },
            render=timer.render,
        ),
        LayoutSpec(
            key="tomorrow",
            name={"de": "Morgen", "en": "Tomorrow"},
            description={
                "de": (
                    "Der Plan für morgen als Liste, mit der ersten Session groß; automatisch "
                    "abends, solange morgen Sessions geplant sind."
                ),
                "en": (
                    "Tomorrow's plan as a list with the first session large; picked "
                    "automatically in the evening while tomorrow has sessions."
                ),
            },
            render=tomorrow.render,
        ),
        LayoutSpec(
            key="today",
            name={"de": "Heute", "en": "Today"},
            description={
                "de": (
                    "Die Stunden von heute riesig, lesbar quer durch den Raum, mit dem Rest "
                    "zum Tagesziel; nie automatisch gewählt."
                ),
                "en": (
                    "Today's hours, huge, readable across the room, with what is left to the "
                    "daily target; never picked automatically."
                ),
            },
            render=today.render,
        ),
        LayoutSpec(
            key="goals",
            name={"de": "Kursziele", "en": "Course goals"},
            description={
                "de": (
                    "Die Kursziele als Liste: offene mit Datum und Countdown, erledigte "
                    "abgehakt mit Note (Scope CourseGoals.GetAll); nie automatisch gewählt."
                ),
                "en": (
                    "The course goals as a list: open ones with date and countdown, completed "
                    "ones ticked with their grade (scope CourseGoals.GetAll); never picked "
                    "automatically."
                ),
            },
            render=goals.render,
        ),
        LayoutSpec(
            key="achievements",
            name={"de": "Achievements", "en": "Achievements"},
            description={
                "de": (
                    "Freigeschaltete Achievements von allen, das zuletzt erreichte und das "
                    "nächste mit Fortschritt (Scope Metrics.GetAchievements); nie automatisch "
                    "gewählt."
                ),
                "en": (
                    "Achievements unlocked of all, the latest one and the next with its "
                    "progress (scope Metrics.GetAchievements); never picked automatically."
                ),
            },
            render=achievements.render,
        ),
        LayoutSpec(
            key="note",
            name={"de": "Notiz", "en": "Note"},
            description={
                "de": (
                    "Die neueste Notiz als Lernzettel: Titel und Auszug, darunter zwei weitere "
                    "(Scope Notes.GetAll); nie automatisch gewählt."
                ),
                "en": (
                    "The newest note as a study sheet: title and excerpt, two more underneath "
                    "(scope Notes.GetAll); never picked automatically."
                ),
            },
            render=note.render,
        ),
        LayoutSpec(
            key="quiet",
            name={"de": "Nacht", "en": "Night"},
            description={
                "de": (
                    "Das Minimalbild für die Nacht: Datum, Serie und die erste Session von "
                    "morgen; automatisch im Nacht-Fenster, sonst nie."
                ),
                "en": (
                    "The minimal frame for the night: date, streak and tomorrow's first "
                    "session; picked automatically inside the night window, never otherwise."
                ),
            },
            render=quiet.render,
        ),
        LayoutSpec(
            key=DUO,
            name={"de": "Duo", "en": "Duo"},
            description={
                "de": (
                    "Zwei Layouts nebeneinander, jedes in seiner kompakten Form; welche zwei, "
                    "steht in der Duo-Einstellung; nie automatisch gewählt."
                ),
                "en": (
                    "Two layouts side by side, each in its compact form; which two is the duo "
                    "setting; never picked automatically."
                ),
            },
            render=duo.render,
        ),
    )
}

__all__ = [
    "AUTO",
    "CYCLE",
    "DUO",
    "LAYOUTS",
    "PSEUDO_CHOICES",
    "LayoutSpec",
    "PseudoSpec",
    "Renderer",
]
