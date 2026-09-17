"""The error screens: a headline and a two-line explanation, independent of the layout.

Three kinds, keyed like the error kinds in `status_store`:

- `rejected`: StudyLife answered 401/403. A rejected key does not heal itself, so the panel
  says so instead of showing an ever-older cached dashboard.
- `stale`: the cached snapshot is older than DISPLAY_STALE_ERROR_HOURS. Up to then the
  dashboard is shown with the stale marker; past it, the numbers are not worth trusting.
- `no_data`: no cache and the API unreachable, i.e. nothing to draw.

Plain text, large, black on white, so it reads from across the room like the dashboard.
"""

from __future__ import annotations

from datetime import datetime

from PIL import Image

from studylife_display.layouts.common import (
    HEADER_BASELINE,
    MARGIN,
    RULE_Y,
    TEXT,
    WIDTH,
    draw_text,
    ellipsize,
    finish,
    load_fonts,
    new_canvas,
)

HEADLINE_BASELINE = 200
LINE_BASELINES = (262, 302)
DETAIL_BASELINE = 430

ERROR_TEXT: dict[str, dict[str, dict[str, str]]] = {
    "de": {
        "rejected": {
            "headline": "Schlüssel abgelehnt",
            "line1": "StudyLife hat den API-Schlüssel abgelehnt ({detail}).",
            "line2": "STUDYLIFE_API_KEY und die Scopes des Schlüssels prüfen.",
        },
        "stale": {
            "headline": "Daten veraltet",
            "line1": "Der letzte erfolgreiche Abruf liegt {detail} zurück.",
            "line2": "Verbindung zur StudyLife-Instanz und das Protokoll prüfen.",
        },
        "no_data": {
            "headline": "Keine Daten",
            "line1": "Noch kein Zwischenspeicher und StudyLife ist nicht erreichbar.",
            "line2": "STUDYLIFE_BASE_URL, Netzwerk und Schlüssel prüfen.",
        },
    },
    "en": {
        "rejected": {
            "headline": "API key rejected",
            "line1": "StudyLife rejected the API key ({detail}).",
            "line2": "Check STUDYLIFE_API_KEY and the key's scopes.",
        },
        "stale": {
            "headline": "Data is stale",
            "line1": "The last successful fetch was {detail} ago.",
            "line2": "Check the connection to the StudyLife instance and the log.",
        },
        "no_data": {
            "headline": "No data",
            "line1": "No cache yet and StudyLife is unreachable.",
            "line2": "Check STUDYLIFE_BASE_URL, the network and the key.",
        },
    },
}

# The small line at the bottom with the message of the failure that led here.
ERROR_FOOTER: dict[str, str] = {
    "de": "Letzter Fehler: {message}",
    "en": "Last error: {message}",
}

ERROR_KINDS = frozenset({"rejected", "stale", "no_data"})


def format_age(minutes: int, language: str) -> str:
    """ "26 h" / "3 Tage" - the age of a stale snapshot for the stale screen."""
    hours = minutes // 60
    if hours < 48:
        return f"{hours} h"
    days = hours // 24
    if language == "de":
        return f"{days} Tage"
    return f"{days} days"


def render_error(
    kind: str,
    detail: str,
    language: str,
    now: datetime | None = None,
    last_error: str | None = None,
) -> Image.Image:
    """An 800x480 mode "1" frame for `kind` (rejected, stale, no_data). `detail` fills the
    first explanation line (the HTTP status, the age); `last_error` is shown as a small line
    at the bottom, `now` as the time in the header."""
    if kind not in ERROR_KINDS:
        raise ValueError(f"unknown error screen {kind!r} (known: {', '.join(sorted(ERROR_KINDS))})")
    t = TEXT[language]
    words = ERROR_TEXT[language][kind]
    fonts = load_fonts()
    canvas, draw = new_canvas()

    header = t["brand"]
    if now is not None:
        header += t["separator"] + now.strftime("%H:%M")
    draw_text(draw, (MARGIN, HEADER_BASELINE), header, fonts.header)
    draw.line([(MARGIN, RULE_Y), (WIDTH - MARGIN, RULE_Y)], fill=0, width=2)

    max_width = WIDTH - 2 * MARGIN
    centre = WIDTH / 2
    headline = ellipsize(words["headline"], fonts.title, max_width)
    draw_text(draw, (centre, HEADLINE_BASELINE), headline, fonts.title, anchor="ms")
    for baseline, key in zip(LINE_BASELINES, ("line1", "line2"), strict=True):
        line = ellipsize(words[key].format(detail=detail), fonts.body, max_width)
        draw_text(draw, (centre, baseline), line, fonts.body, anchor="ms")
    if last_error:
        footer = ERROR_FOOTER[language].format(message=last_error)
        footer = ellipsize(footer, fonts.small, max_width)
        draw_text(draw, (centre, DETAIL_BASELINE), footer, fonts.small, anchor="ms")
    return finish(canvas)
