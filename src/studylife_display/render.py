"""Draws a DashboardData onto an 800x480 black/white frame, in the layout asked for.

Pure: same data + language + layout -> same pixels. The drawing itself lives in
`studylife_display.layouts`; this module is the entry point the pipeline and the tests use,
and it re-exports the helpers that tests and callers historically imported from here.
"""

from __future__ import annotations

from PIL import Image

from studylife_display.layouts import LAYOUTS
from studylife_display.layouts.classic import COUNTDOWN_BOX
from studylife_display.layouts.common import (
    HEIGHT,
    TEXT,
    WIDTH,
    format_decimal,
    format_hours_clock,
    heatmap_level,
    load_fonts,
    text_width,
)
from studylife_display.model import DashboardData

__all__ = [
    "COUNTDOWN_BOX",
    "HEIGHT",
    "TEXT",
    "WIDTH",
    "format_decimal",
    "format_hours_clock",
    "heatmap_level",
    "load_fonts",
    "render",
    "text_width",
]


def render(data: DashboardData, language: str, layout: str = "classic") -> Image.Image:
    """Renders the dashboard as an 800x480 mode "1" image, black on white, in `layout`
    (a key of LAYOUTS; "auto" must already be resolved by the caller)."""
    spec = LAYOUTS.get(layout)
    if spec is None:
        raise ValueError(f"unknown layout {layout!r} (known: {', '.join(LAYOUTS)})")
    return spec.render(data, language)
