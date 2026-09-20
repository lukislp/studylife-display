"""The first-run setup screen: shown while no API key is configured at all.

A Pi fresh from `install.sh` has an empty STUDYLIFE_API_KEY. Asking StudyLife with it would
earn a 401 and the "key rejected" screen, which points at a key that was never there. This
screen says what to do instead: the connect URL of the web interface, in large text and as
a QR code, so a phone on the same network gets there without typing.

The QR code comes from `segno`, drawn module by module onto the greyscale canvas (never via
segno's own image writers), so the result stays a plain 800x480 mode "1" frame like every
other screen. Black modules on white with the four-module quiet zone the standard asks for.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import segno
from PIL import Image, ImageDraw

from studylife_display.layouts.common import (
    BLACK,
    HEADER_BASELINE,
    MARGIN,
    RULE_Y,
    TEXT,
    WIDTH,
    draw_text,
    ellipsize,
    finish,
    font_fitting,
    load_fonts,
    new_canvas,
)

SETUP_TEXT: dict[str, dict[str, str]] = {
    "de": {
        "headline": "Einrichtung",
        "line": "Konto verbinden unter:",
        "footer": "Hostname: {hostname}",
    },
    "en": {
        "headline": "Setup",
        "line": "Connect your account at:",
        "footer": "Hostname: {hostname}",
    },
}

# The box on the right the QR code is centred in, quiet zone included.
QR_BOX_SIZE = 200
QR_BOX_LEFT = WIDTH - MARGIN - QR_BOX_SIZE
QR_BOX_TOP = 130
# Modules of white around the symbol; the standard's minimum is four.
QR_QUIET_ZONE = 4
# Medium error correction: the URL is short, the panel is high-contrast, and a phone camera
# reads it from arm's length even with a little ghosting from the previous frame.
QR_ERROR_LEVEL = "m"

HEADLINE_BASELINE = 190
LINE_BASELINE = 240
URL_BASELINE = 300
URL_FONT_SIZES = (40, 34, 28, 24, 20)
FOOTER_BASELINE = 452


@dataclass(frozen=True)
class QrPlacement:
    """A QR code and where its modules land on the frame: module (column, row) of the
    symbol (quiet zone excluded) covers the pixels from
    `(left + (column + quiet_zone) * scale, top + (row + quiet_zone) * scale)`, `scale` wide."""

    qr: segno.QRCode
    left: int
    top: int
    scale: int
    quiet_zone: int

    @property
    def modules(self) -> int:
        """Modules along one side including the quiet zone."""
        return len(self.qr.matrix) + 2 * self.quiet_zone

    @property
    def size(self) -> int:
        """Pixels along one side including the quiet zone."""
        return self.modules * self.scale

    def module_box(self, column: int, row: int) -> tuple[int, int, int, int]:
        """The pixel box (left, top, right, bottom; right/bottom exclusive) of one module of
        the symbol proper, `column`/`row` counted without the quiet zone."""
        x = self.left + (column + self.quiet_zone) * self.scale
        y = self.top + (row + self.quiet_zone) * self.scale
        return x, y, x + self.scale, y + self.scale


def qr_placement(url: str) -> QrPlacement:
    """The QR code for `url` at the largest whole-pixel module size that keeps it inside the
    QR_BOX_SIZE square, centred in that box."""
    qr = segno.make(url, error=QR_ERROR_LEVEL, micro=False)
    modules = len(qr.matrix) + 2 * QR_QUIET_ZONE
    scale = max(1, QR_BOX_SIZE // modules)
    size = modules * scale
    offset = (QR_BOX_SIZE - size) // 2
    return QrPlacement(qr, QR_BOX_LEFT + offset, QR_BOX_TOP + offset, scale, QR_QUIET_ZONE)


def draw_qr(draw: ImageDraw.ImageDraw, placement: QrPlacement) -> None:
    """Paints the dark modules as solid squares; the light ones and the quiet zone stay the
    canvas's white."""
    for row_index, row in enumerate(placement.qr.matrix):
        for column, dark in enumerate(row):
            if dark:
                left, top, right, bottom = placement.module_box(column, row_index)
                draw.rectangle((left, top, right - 1, bottom - 1), fill=BLACK)


def render_setup(
    url: str, language: str, hostname: str, now: datetime | None = None
) -> Image.Image:
    """An 800x480 mode "1" frame: headline, "connect at:", `url` large on the left, its QR
    code on the right, the hostname in a small line at the bottom, `now` in the header."""
    t = TEXT[language]
    words = SETUP_TEXT[language]
    fonts = load_fonts()
    canvas, draw = new_canvas()

    header = t["brand"]
    if now is not None:
        header += t["separator"] + now.strftime("%H:%M")
    draw_text(draw, (MARGIN, HEADER_BASELINE), header, fonts.header)
    draw.line([(MARGIN, RULE_Y), (WIDTH - MARGIN, RULE_Y)], fill=BLACK, width=2)

    text_right = QR_BOX_LEFT - MARGIN
    max_width = text_right - MARGIN
    headline = ellipsize(words["headline"], fonts.title, max_width)
    draw_text(draw, (MARGIN, HEADLINE_BASELINE), headline, fonts.title)
    draw_text(draw, (MARGIN, LINE_BASELINE), words["line"], fonts.body)
    url_font = font_fitting(url, URL_FONT_SIZES, max_width)
    draw_text(draw, (MARGIN, URL_BASELINE), ellipsize(url, url_font, max_width), url_font)

    draw_qr(draw, qr_placement(url))

    footer = ellipsize(words["footer"].format(hostname=hostname), fonts.small, max_width)
    draw_text(draw, (MARGIN, FOOTER_BASELINE), footer, fonts.small)
    return finish(canvas)
