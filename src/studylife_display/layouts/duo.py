"""Two layouts side by side: the header line, a divider down the middle, and the pane form
(see panes.py) of one layout on each side with its name as a small label above it. Which two
is DISPLAY_DUO / the `duo` setting (`left,right`); `render` draws the default pair, the
pipeline passes the configured one through `render.render(..., duo=...)`."""

from __future__ import annotations

from PIL import Image, ImageDraw

from studylife_display.config import DEFAULT_DUO, parse_layout_list
from studylife_display.layouts.common import (
    BLACK,
    TEXT,
    draw_header,
    draw_text,
    ellipsize,
    finish,
    load_fonts,
    new_canvas,
)
from studylife_display.layouts.panes import (
    DIVIDER_X,
    PANE_BOTTOM,
    PANE_LABEL_BASELINE,
    PANES,
    pane_boxes,
)
from studylife_display.model import DashboardData

_default = parse_layout_list(DEFAULT_DUO, "DISPLAY_DUO")
DEFAULT_PAIR: tuple[str, str] = (_default[0], _default[1])
DIVIDER_TOP = 70


def pane_title(key: str, language: str) -> str:
    # Imported here: layouts/__init__ imports this module, so the registry is not available
    # at import time.
    from studylife_display.layouts import LAYOUTS

    spec = LAYOUTS.get(key)
    return spec.name[language] if spec is not None else key


def render_pair(data: DashboardData, language: str, pair: tuple[str, str]) -> Image.Image:
    for key in pair:
        if key not in PANES:
            raise ValueError(f"no pane for layout {key!r} (known: {', '.join(PANES)})")
    t = TEXT[language]
    fonts = load_fonts()
    canvas, draw = new_canvas()
    draw_header(draw, data, fonts, t)
    draw.line([(DIVIDER_X, DIVIDER_TOP), (DIVIDER_X, PANE_BOTTOM)], fill=BLACK)
    for key, box in zip(pair, pane_boxes(), strict=True):
        left, _, right, _ = box
        title = ellipsize(pane_title(key, language).upper(), fonts.label, right - left)
        draw_text(draw, (left, PANE_LABEL_BASELINE), title, fonts.label)
        PANES[key](canvas, ImageDraw.Draw(canvas), data, fonts, language, box)
    return finish(canvas)


def render(data: DashboardData, language: str) -> Image.Image:
    return render_pair(data, language, DEFAULT_PAIR)
