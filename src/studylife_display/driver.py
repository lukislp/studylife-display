"""Output targets: the real panel, or a PNG for development and tests."""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any, Protocol

from PIL import Image

from studylife_display.panels import DEFAULT_PANEL, PANELS, PanelProfile

log = logging.getLogger(__name__)


class Display(Protocol):
    def show(self, image: Image.Image) -> None: ...

    def clear(self) -> None: ...

    def sleep(self) -> None: ...


def oriented(image: Image.Image, rotate: int) -> Image.Image:
    """The frame as the panel has to receive it: unchanged for an upright panel, turned by
    180 degrees (flipped both ways) for one mounted upside down. This is the only place the
    rotation is applied; the layouts always draw upright."""
    if rotate == 180:
        return image.transpose(Image.Transpose.ROTATE_180)
    if rotate != 0:
        raise ValueError(f"rotation must be 0 or 180, not {rotate}")
    return image


class FileDisplay:
    """Writes the frame to a PNG. Used by `preview`, by the tests and by anyone developing
    without the panel attached (`DISPLAY_PANEL` picks the native size it emulates). `clear()`
    writes an all-white frame next to the output (`frame-clear.png` for `frame.png`), so a run
    that cleared leaves a trace to look at."""

    def __init__(
        self, path: str | Path, rotate: int = 0, panel: PanelProfile = PANELS[DEFAULT_PANEL]
    ) -> None:
        self.path = Path(path)
        self.rotate = rotate
        # The panel being emulated: the cleared frame has its native size; `show` writes the
        # frame it is given (the pipeline hands it one that already has the panel's size).
        self.panel = panel

    @property
    def clear_path(self) -> Path:
        return self.path.with_name(f"{self.path.stem}-clear{self.path.suffix or '.png'}")

    def show(self, image: Image.Image) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        oriented(image, self.rotate).save(self.path, format="PNG")
        log.info("frame written to %s", self.path)

    def clear(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("1", self.panel.native_size, 1).save(self.clear_path, format="PNG")
        log.info("panel cleared (white frame written to %s)", self.clear_path)

    def sleep(self) -> None:
        return None


class WaveshareDisplay:
    """A Waveshare e-Paper HAT over SPI: one class for every black/white (or colour panel
    driven black/white) model whose vendor module takes a single buffer, selected by the
    panel profile. All of them follow the same vendor protocol: `init()`, `display(buffer)`
    with the buffer from `getbuffer(image)`, `sleep()`, plus `Clear()`.

    The vendor library is imported lazily so that importing this module never requires the
    `pi` extra: it only builds on a Raspberry Pi with the GPIO/SPI bindings, and the CI
    runners are plain Ubuntu machines.

    Every update is a FULL refresh (init -> display -> sleep). The panel vendor warns against
    running partial refreshes continuously because they leave ghosting and, over months, burn
    the panel; with one update every five minutes a full refresh is the only mode that is
    both safe and legible.
    """

    def __init__(self, rotate: int = 0, panel: PanelProfile = PANELS[DEFAULT_PANEL]) -> None:
        module = importlib.import_module(f"waveshare_epd.{panel.module}")
        self._epd: Any = module.EPD()
        self.rotate = rotate
        self.panel = panel

    def _check_size(self, image: Image.Image) -> None:
        if image.size != (self._epd.width, self._epd.height):
            raise ValueError(
                f"frame is {image.size}, panel is {(self._epd.width, self._epd.height)}"
            )

    def show(self, image: Image.Image) -> None:
        self._check_size(image)
        self._epd.init()
        self._send(self._epd.getbuffer(oriented(image, self.rotate)))

    def _send(self, buffer: Any) -> None:
        self._epd.display(buffer)

    def clear(self) -> None:
        # The vendor's full clear drives every pixel to white with the long waveform, which
        # is what removes the ghost of frames drawn hours ago. Once a day is plenty.
        self._epd.init()
        self._epd.Clear()

    def sleep(self) -> None:
        # Deep sleep between refreshes: the panel keeps the image without power and the
        # controller stops driving the (heat-sensitive) panel until the next init().
        self._epd.sleep()


class WaveshareTriColourDisplay(WaveshareDisplay):
    """The black/white/red models (7.5 inch B V2): the vendor's `display()` takes a black
    and a red buffer. The frame goes into the black plane; the red plane stays blank
    (all zero, the vendor's own "no red" value in its `Clear()`), so nothing is drawn red."""

    def _send(self, buffer: Any) -> None:
        # The vendor mutates the black buffer in place while sending, so hand it a bytearray.
        black = bytearray(buffer)
        self._epd.display(black, bytearray(len(black)))


class InkyDisplay:
    """The Pimoroni Inky Impression 7.3 inch (both the 7-colour and the 2025 Spectra 6
    edition, 800x480) through the `inky` library, which detects the model from the board's
    EEPROM. The library only takes colour palettes, so the black/white frame is sent as RGB
    with saturation 0: the quantiser then maps pure black and pure white onto the panel's own
    black and white, without dithering noise. `show()` blocks until the panel has finished
    (about half a minute) and powers the controller down itself, so `sleep()` has nothing to do.
    """

    def __init__(
        self, rotate: int = 0, panel: PanelProfile = PANELS["inky_impression_7in3"]
    ) -> None:
        auto = importlib.import_module(panel.module).auto
        self._inky: Any = auto()
        self.rotate = rotate
        self.panel = panel
        size = (self._inky.width, self._inky.height)
        if size != panel.native_size:
            raise ValueError(
                f"the detected Inky display is {size[0]}x{size[1]}, "
                f"DISPLAY_PANEL={panel.key} expects {panel.width}x{panel.height}"
            )

    def _draw(self, image: Image.Image) -> None:
        self._inky.set_image(image.convert("RGB"), saturation=0.0)
        self._inky.show()

    def show(self, image: Image.Image) -> None:
        if image.size != self.panel.native_size:
            raise ValueError(f"frame is {image.size}, panel is {self.panel.native_size}")
        self._draw(oriented(image, self.rotate))

    def clear(self) -> None:
        # No separate clear waveform in the library: a full white frame is a full refresh.
        self._draw(Image.new("1", self.panel.native_size, 1))

    def sleep(self) -> None:
        return None


def hardware_display(panel: PanelProfile, rotate: int = 0) -> Display:
    """The driver for real hardware of the given panel (vendor imports happen here)."""
    if panel.family == "inky":
        return InkyDisplay(rotate, panel)
    if panel.family == "waveshare_bwr":
        return WaveshareTriColourDisplay(rotate, panel)
    return WaveshareDisplay(rotate, panel)
