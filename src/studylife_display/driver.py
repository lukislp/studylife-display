"""Output targets: the real panel, or a PNG for development and tests."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

from PIL import Image

from studylife_display.layouts.common import HEIGHT, WIDTH

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
    without the HAT attached. `clear()` writes an all-white frame next to the output
    (`frame-clear.png` for `frame.png`), so a run that cleared leaves a trace to look at."""

    def __init__(self, path: str | Path, rotate: int = 0) -> None:
        self.path = Path(path)
        self.rotate = rotate

    @property
    def clear_path(self) -> Path:
        return self.path.with_name(f"{self.path.stem}-clear{self.path.suffix or '.png'}")

    def show(self, image: Image.Image) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        oriented(image, self.rotate).save(self.path, format="PNG")
        log.info("frame written to %s", self.path)

    def clear(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("1", (WIDTH, HEIGHT), 1).save(self.clear_path, format="PNG")
        log.info("panel cleared (white frame written to %s)", self.clear_path)

    def sleep(self) -> None:
        return None


class WaveshareDisplay:
    """The Waveshare 7.5 inch e-Paper HAT V2 (800x480, black/white) over SPI.

    The vendor library is imported lazily so that importing this module never requires the
    `pi` extra: it only builds on a Raspberry Pi with the GPIO/SPI bindings, and the CI
    runners are plain Ubuntu machines.

    Every update is a FULL refresh (init -> display -> sleep). The panel vendor warns against
    running partial refreshes continuously because they leave ghosting and, over months, burn
    the panel; with one update every five minutes a full refresh is the only mode that is
    both safe and legible.
    """

    def __init__(self, rotate: int = 0) -> None:
        from waveshare_epd import epd7in5_V2  # type: ignore[import-not-found]

        self._epd: Any = epd7in5_V2.EPD()
        self.rotate = rotate

    def show(self, image: Image.Image) -> None:
        if image.size != (self._epd.width, self._epd.height):
            raise ValueError(
                f"frame is {image.size}, panel is {(self._epd.width, self._epd.height)}"
            )
        self._epd.init()
        self._epd.display(self._epd.getbuffer(oriented(image, self.rotate)))

    def clear(self) -> None:
        # The vendor's full clear drives every pixel to white with the long waveform, which
        # is what removes the ghost of frames drawn hours ago. Once a day is plenty.
        self._epd.init()
        self._epd.Clear()

    def sleep(self) -> None:
        # Deep sleep between refreshes: the panel keeps the image without power and the
        # controller stops driving the (heat-sensitive) panel until the next init().
        self._epd.sleep()
