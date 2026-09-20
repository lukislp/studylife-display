"""The frame that is on the panel right now, kept as `current.png` next to the cached
snapshot together with `current.json` (when it was shown, which layout, what kind of
screen), for the web interface's "currently on the panel" picture.

Written by the one place that puts frames on the panel (`main._put_on_panel`), for every
driver, and only for frames that really reach the panel: `preview` writes its PNG elsewhere
and leaves these files alone. The copy is the UPRIGHT frame as the layouts drew it; the
rotation for an upside-down panel is the driver's business, and on a phone the picture is
wanted the right way up. Both files are written atomically (temp file, then rename) like
the snapshot cache, so a reader never sees a half-written PNG.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from PIL import Image

CURRENT_PNG = "current.png"
CURRENT_JSON = "current.json"

# What kind of screen the frame is; "layout" names the dashboard layout for "dashboard"
# frames and is None otherwise.
DASHBOARD = "dashboard"
ERROR = "error"
SETUP = "setup"
KINDS = frozenset({DASHBOARD, ERROR, SETUP})


@dataclass(frozen=True)
class CurrentFrame:
    shown_at: datetime
    layout: str | None
    kind: str

    def as_json(self) -> dict[str, Any]:
        return {"shown_at": self.shown_at.isoformat(), "layout": self.layout, "kind": self.kind}


def current_png_path(state_dir: Path) -> Path:
    return state_dir / CURRENT_PNG


def current_json_path(state_dir: Path) -> Path:
    return state_dir / CURRENT_JSON


def _atomic_write(path: Path, write: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    write(tmp)
    tmp.replace(path)


def save_current_frame(
    state_dir: Path, image: Image.Image, shown_at: datetime, layout: str | None, kind: str
) -> None:
    """Stores `image` (upright) and its metadata; the PNG first, so the metadata never
    describes a picture that is not there yet. Raises OSError like the other stores."""
    if kind not in KINDS:
        raise ValueError(f"unknown frame kind {kind!r} (known: {', '.join(sorted(KINDS))})")
    state_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(current_png_path(state_dir), lambda tmp: image.save(tmp, format="PNG"))
    payload = json.dumps(CurrentFrame(shown_at, layout, kind).as_json())
    _atomic_write(
        current_json_path(state_dir), lambda tmp: tmp.write_text(payload, encoding="utf-8")
    )


def load_current_frame(state_dir: Path, tz: ZoneInfo) -> CurrentFrame | None:
    """The metadata of the frame on the panel, or None when nothing was shown yet, the
    picture is missing, or the file cannot be read."""
    if not current_png_path(state_dir).is_file():
        return None
    try:
        raw = json.loads(current_json_path(state_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    shown_at_raw = raw.get("shown_at")
    if not isinstance(shown_at_raw, str):
        return None
    try:
        shown_at = datetime.fromisoformat(shown_at_raw)
    except ValueError:
        return None
    if shown_at.tzinfo is None:
        shown_at = shown_at.replace(tzinfo=tz)
    layout = raw.get("layout")
    kind = raw.get("kind")
    return CurrentFrame(
        shown_at=shown_at.astimezone(tz),
        layout=layout if isinstance(layout, str) else None,
        kind=kind if isinstance(kind, str) and kind in KINDS else DASHBOARD,
    )


def read_current_png(state_dir: Path) -> bytes | None:
    """The PNG bytes, or None when there is no frame yet."""
    try:
        return current_png_path(state_dir).read_bytes()
    except OSError:
        return None
