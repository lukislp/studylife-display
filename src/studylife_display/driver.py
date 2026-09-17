"""Output targets: the real panel, or a PNG for development and tests."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

from PIL import Image

log = logging.getLogger(__name__)


class Display(Protocol):
    def show(self, image: Image.Image) -> None: ...

    def sleep(self) -> None: ...


class FileDisplay:
    """Writes the frame to a PNG. Used by `preview`, by the tests and by anyone developing
    without the HAT attached."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def show(self, image: Image.Image) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        image.save(self.path, format="PNG")
        log.info("frame written to %s", self.path)

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

    def __init__(self) -> None:
        from waveshare_epd import epd7in5_V2  # type: ignore[import-not-found]

        self._epd: Any = epd7in5_V2.EPD()

    def show(self, image: Image.Image) -> None:
        if image.size != (self._epd.width, self._epd.height):
            raise ValueError(
                f"frame is {image.size}, panel is {(self._epd.width, self._epd.height)}"
            )
        self._epd.init()
        self._epd.display(self._epd.getbuffer(image))

    def sleep(self) -> None:
        # Deep sleep between refreshes: the panel keeps the image without power and the
        # controller stops driving the (heat-sensitive) panel until the next init().
        self._epd.sleep()
