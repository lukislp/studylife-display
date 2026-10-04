"""The newest note (from `GET /api/notes`) as a study sheet: its title large, the plain-text
excerpt word-wrapped over up to seven lines across the whole width, when it was last
updated, and under a thin rule the titles and dates of the next two notes. Today's hours and
the streak sit in the footer. Needs a key with the `Notes.GetAll` scope; without it the layout
says so."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

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
from studylife_display.model import DashboardData, Note

# This layout's own strings; everything else comes from the shared table in common.py.
TEXT: dict[str, dict[str, str]] = {
    "de": {
        "note_label": "NEUESTE NOTIZ",
        "note_none": "keine Notizen",
        "note_scope": "Schlüssel ohne Scope Notes.GetAll",
        "note_updated": "aktualisiert {date}",
        "note_untitled": "(ohne Titel)",
    },
    "en": {
        "note_label": "LATEST NOTE",
        "note_none": "no notes",
        "note_scope": "key lacks the scope Notes.GetAll",
        "note_updated": "updated {date}",
        "note_untitled": "(untitled)",
    },
}

LABEL_BASELINE = 86
TITLE_BASELINE = 126
EXCERPT_TOP_BASELINE = 160
EXCERPT_LINE_HEIGHT = 29
EXCERPT_MAX_LINES = 7
OTHERS_RULE_Y = 352
OTHERS_BASELINES = (380, 410)

FOOTER_RULE_Y = 428

# The pane form.
PANE_TITLE_BASELINE = 36
PANE_EXCERPT_TOP_BASELINE = 70
PANE_EXCERPT_MAX_LINES = 5
PANE_UPDATED_BASELINE = 214
PANE_RULE_Y = 230
PANE_OTHERS_BASELINES = (254, 278)


def wrap_lines(
    text: str, font: ImageFont.FreeTypeFont, max_width: float, max_lines: int
) -> list[str]:
    """Greedy word wrap of `text` into at most `max_lines` lines no wider than `max_width`
    (measured like draw_text draws them). When the text does not fit, the last line ends
    in an ellipsis; a single word wider than a line is ellipsized on its own."""
    words = text.split()
    lines: list[str] = []
    if not words or max_lines <= 0:
        return lines
    index = 0
    while index < len(words) and len(lines) < max_lines:
        line = words[index]
        index += 1
        while index < len(words):
            candidate = f"{line} {words[index]}"
            if text_width(candidate, font) > max_width:
                break
            line = candidate
            index += 1
        if len(lines) == max_lines - 1 and index < len(words):
            # The last allowed line while words remain: cut it with the ellipsis.
            line = ellipsize(" ".join([line, *words[index:]]), font, max_width)
            index = len(words)
        lines.append(ellipsize(line, font, max_width))
    return lines


def updated_line(note: Note, data: DashboardData, t: dict[str, str], s: dict[str, str]) -> str:
    """ "aktualisiert 17.09." or "" when the note has no timestamp."""
    if note.updated_at is None:
        return ""
    date = note.updated_at.astimezone(data.now.tzinfo).strftime(t["date_format"])
    return s["note_updated"].format(date=date)


def _title(note: Note, s: dict[str, str]) -> str:
    return note.title or s["note_untitled"]


def _draw_sheet(
    draw: ImageDraw.ImageDraw,
    data: DashboardData,
    fonts: Fonts,
    t: dict[str, str],
    s: dict[str, str],
) -> None:
    width = WIDTH - 2 * MARGIN
    note = data.notes[0]
    updated = updated_line(note, data, t, s)
    if updated:
        draw_text(draw, (WIDTH - MARGIN, LABEL_BASELINE), updated, fonts.small, anchor="rs")
    draw_text(
        draw, (MARGIN, TITLE_BASELINE), ellipsize(_title(note, s), fonts.value, width), fonts.value
    )
    for index, line in enumerate(wrap_lines(note.excerpt, fonts.body, width, EXCERPT_MAX_LINES)):
        baseline = EXCERPT_TOP_BASELINE + index * EXCERPT_LINE_HEIGHT
        draw_text(draw, (MARGIN, baseline), line, fonts.body)

    others = data.notes[1 : 1 + len(OTHERS_BASELINES)]
    if not others:
        return
    draw.line([(MARGIN, OTHERS_RULE_Y), (WIDTH - MARGIN, OTHERS_RULE_Y)], fill=BLACK)
    for other, baseline in zip(others, OTHERS_BASELINES, strict=False):
        date = ""
        if other.updated_at is not None:
            date = other.updated_at.astimezone(data.now.tzinfo).strftime(t["date_format"])
            draw_text(draw, (WIDTH - MARGIN, baseline), date, fonts.small, anchor="rs")
        title_width = width - (int(text_width(date, fonts.small)) + 16 if date else 0)
        draw_text(
            draw,
            (MARGIN, baseline),
            ellipsize(_title(other, s), fonts.body, title_width),
            fonts.body,
        )


def render(data: DashboardData, language: str) -> Image.Image:
    t = common.TEXT[language]
    s = TEXT[language]
    fonts = load_fonts()
    canvas, draw = new_canvas()
    draw_header(draw, data, fonts, t)
    draw_text(draw, (MARGIN, LABEL_BASELINE), s["note_label"], fonts.label)
    if "notes" in data.unavailable:
        draw_text(draw, (MARGIN, TITLE_BASELINE), s["note_scope"], fonts.body)
    elif not data.notes:
        draw_text(draw, (MARGIN, TITLE_BASELINE), s["note_none"], fonts.body)
    else:
        _draw_sheet(draw, data, fonts, t, s)
    footer = t["separator"].join(
        [
            t["today_line"].format(hours=format_hours_clock(data.today_hours)),
            f"{t['streak_label']} {format_streak(data.streak_days, t)}",
        ]
    )
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
    """The newest note's title and up to PANE_EXCERPT_MAX_LINES lines of its excerpt, the
    update date, and the next notes' titles under a rule."""
    t = common.TEXT[language]
    s = TEXT[language]
    left, top, right, _ = box
    width = right - left
    if "notes" in data.unavailable:
        draw_text(
            draw, (left, top + 31), ellipsize(s["note_scope"], fonts.small, width), fonts.small
        )
        return
    if not data.notes:
        draw_text(draw, (left, top + 31), s["note_none"], fonts.body)
        return
    note = data.notes[0]
    title = ellipsize(_title(note, s), fonts.value, width)
    draw_text(draw, (left, top + PANE_TITLE_BASELINE), title, fonts.value)
    for index, line in enumerate(
        wrap_lines(note.excerpt, fonts.body, width, PANE_EXCERPT_MAX_LINES)
    ):
        baseline = top + PANE_EXCERPT_TOP_BASELINE + index * EXCERPT_LINE_HEIGHT
        draw_text(draw, (left, baseline), line, fonts.body)
    updated = updated_line(note, data, t, s)
    if updated:
        draw_text(draw, (left, top + PANE_UPDATED_BASELINE), updated, fonts.small)

    others = data.notes[1 : 1 + len(PANE_OTHERS_BASELINES)]
    if not others:
        return
    draw.line([(left, top + PANE_RULE_Y), (right, top + PANE_RULE_Y)], fill=BLACK)
    for other, offset in zip(others, PANE_OTHERS_BASELINES, strict=False):
        parts = [_title(other, s)]
        if other.updated_at is not None:
            parts.append(other.updated_at.astimezone(data.now.tzinfo).strftime(t["date_format"]))
        line = ellipsize(t["separator"].join(parts), fonts.small, width)
        draw_text(draw, (left, top + offset), line, fonts.small)
