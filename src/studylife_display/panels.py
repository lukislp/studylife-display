"""The e-paper panels this software can drive, and how a frame gets onto each of them.

The layouts are pixel-designed for one logical canvas of 800x480 (`WIDTH` x `HEIGHT`). A
panel with another native resolution is fed by resampling that canvas in ONE place,
`frame_for_panel`; nothing else knows about panel sizes. All panels are black/white as far as
this software is concerned: a colour panel is driven with the black and white subset of its
palette (see the README for what that means).

The panel is hardware. It is chosen once by `DISPLAY_PANEL` (environment / installer) and is
deliberately not part of the settings the web interface or Home Assistant can change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from PIL import Image

# The canvas the layouts draw on (layouts.common.WIDTH x HEIGHT; a test keeps the two in step).
# Not imported from there: config.py needs this module and the layouts import config.
WIDTH = 800
HEIGHT = 480

DEFAULT_PANEL = "waveshare_7in5_v2"

Colour = Literal["bw", "bwr", "7colour"]
# Which driver class talks to the panel (see driver.py): a Waveshare vendor module that
# takes one buffer, one that takes a black and a red buffer, or the Pimoroni `inky` library.
Family = Literal["waveshare", "waveshare_bwr", "inky"]

# Greyscale value (0-255) from which a resampled pixel counts as white again. Slightly above
# the 50% mark (128) so that the anti-aliased edge of a thin black stroke stays black when a
# frame is scaled down, instead of the stroke thinning out.
SCALE_WHITE_THRESHOLD = 160


@dataclass(frozen=True)
class PanelProfile:
    key: str
    label: str
    width: int
    height: int
    colour: Colour
    family: Family
    # The vendor module that has to be importable on the Pi: a module of the `waveshare_epd`
    # package, or the `inky` package. Imported lazily, never at import time of this module.
    module: str
    # The optional dependency group that installs it (`pip install '.[<extra>]'`).
    extra: str

    @property
    def native_size(self) -> tuple[int, int]:
        return (self.width, self.height)

    @property
    def scaled(self) -> bool:
        """Whether frames have to be resampled for this panel."""
        return self.native_size != (WIDTH, HEIGHT)


_PROFILES = (
    PanelProfile(
        "waveshare_7in5_v2",
        'Waveshare 7.5" HAT V2',
        800,
        480,
        "bw",
        "waveshare",
        "epd7in5_V2",
        "pi",
    ),
    PanelProfile(
        "waveshare_7in5b_v2",
        'Waveshare 7.5" HAT (B) V2 (black/white/red)',
        800,
        480,
        "bwr",
        "waveshare_bwr",
        "epd7in5b_V2",
        "pi",
    ),
    PanelProfile(
        "waveshare_7in5_v1", 'Waveshare 7.5" HAT V1', 640, 384, "bw", "waveshare", "epd7in5", "pi"
    ),
    PanelProfile(
        "waveshare_7in5_hd",
        'Waveshare 7.5" HAT (HD)',
        880,
        528,
        "bw",
        "waveshare",
        "epd7in5_HD",
        "pi",
    ),
    PanelProfile(
        "waveshare_7in3f",
        'Waveshare 7.3" HAT (F) (7 colours)',
        800,
        480,
        "7colour",
        "waveshare",
        "epd7in3f",
        "pi",
    ),
    PanelProfile(
        "inky_impression_7in3",
        'Pimoroni Inky Impression 7.3" (7 colours)',
        800,
        480,
        "7colour",
        "inky",
        "inky.auto",
        "inky",
    ),
)

PANELS: dict[str, PanelProfile] = {profile.key: profile for profile in _PROFILES}


def unknown_panel_message(key: str) -> str:
    return f"unknown panel {key!r} (DISPLAY_PANEL must be one of: {', '.join(PANELS)})"


def get_panel(key: str) -> PanelProfile:
    """The profile for `key`; ValueError listing the known keys otherwise."""
    profile = PANELS.get(key)
    if profile is None:
        raise ValueError(unknown_panel_message(key))
    return profile


def frame_for_panel(image: Image.Image, panel: PanelProfile) -> Image.Image:
    """The upright logical frame (800x480, mode "1") as a frame of the panel's native size.

    A panel of the logical size gets the very same image back (no copy, no change, so the
    existing panel's output is bit-identical to before). Any other size is resampled with
    LANCZOS on the greyscale image and cut back to 1 bit at SCALE_WHITE_THRESHOLD; the
    layouts' 5:3 canvas maps onto every supported panel without distortion. Pure and
    deterministic. Rotation is not handled here: it stays in `driver.oriented`, applied by the
    driver to the already scaled frame (a 180 degree turn commutes with the resampling up to
    the border pixel, and the driver is the only place that knows the mounting).
    """
    if image.size == panel.native_size:
        return image
    if image.size != (WIDTH, HEIGHT):
        raise ValueError(
            f"frame is {image.size}, expected {(WIDTH, HEIGHT)} or {panel.native_size}"
        )
    grey = image.convert("L").resize(panel.native_size, Image.Resampling.LANCZOS)
    return grey.point(lambda value: 255 if value >= SCALE_WHITE_THRESHOLD else 0).convert("1")
