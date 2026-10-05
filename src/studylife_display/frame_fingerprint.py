"""A fingerprint of what a frame looks like, to tell "the same picture again" from "a
different one" without comparing against the panel itself (an e-paper cannot be read back).

Every successful fetch changes the header's "updated HH:MM" text although nothing a person
cares about moved, so a plain image hash would never match twice. The fingerprint therefore
blanks that one text box (`HEADER_UPDATED_BOX`) on dashboard frames, but still reacts to
everything else: the body, the date on the left, the layout, the rotation, and the stale
marker - which is the one change inside the blanked box that matters, so it enters the hash
as a number (and its value, so "35 min ago" -> "40 min ago" still redraws while stale).
"""

from __future__ import annotations

import hashlib

from PIL import Image, ImageDraw

from studylife_display.layouts.common import HEADER_UPDATED_BOX

DASHBOARD_KIND = "dashboard"


def frame_fingerprint(
    image: Image.Image,
    *,
    kind: str,
    layout: str | None,
    stale_minutes: int,
    rotate: int,
    duo: tuple[str, str] | None = None,
) -> str:
    """sha256 hex of the upright 1-bit frame (header update text blanked for dashboards)
    plus everything else that decides what the panel shows. Pure: `image` is not modified."""
    frame = image.convert("1")
    if kind == DASHBOARD_KIND:
        left, top, right, bottom = HEADER_UPDATED_BOX
        frame = frame.copy()
        ImageDraw.Draw(frame).rectangle((left, top, right - 1, bottom - 1), fill=1)
    stale = max(stale_minutes, 0)
    meta = "|".join(
        [
            kind,
            layout or "",
            str(rotate),
            f"stale={stale}" if stale > 0 else "fresh",
            ",".join(duo) if duo is not None and layout == "duo" else "",
            f"{frame.width}x{frame.height}",
        ]
    )
    digest = hashlib.sha256()
    digest.update(meta.encode("utf-8"))
    digest.update(b"\0")
    digest.update(frame.tobytes())
    return digest.hexdigest()
