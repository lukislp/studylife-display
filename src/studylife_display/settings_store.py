"""The layout choice made in the web interface, persisted next to the cached snapshot.

Precedence: `settings.json` in the state directory (written by `POST /layout`) beats the
DISPLAY_LAYOUT environment variable, which is the default for a fresh install. The file is
tiny (`{"layout": "<key|auto>"}`) and written atomically like the cache, so a power cut
mid-write leaves the previous choice, never half a file.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from studylife_display.config import Settings
from studylife_display.layouts import AUTO, LAYOUTS

log = logging.getLogger(__name__)

SETTINGS_FILE = "settings.json"


def valid_choices() -> frozenset[str]:
    return frozenset(LAYOUTS) | {AUTO}


def is_valid_choice(choice: object) -> bool:
    return isinstance(choice, str) and choice in valid_choices()


def settings_path(settings: Settings) -> Path:
    return Path(settings.display_state_path).parent / SETTINGS_FILE


def load_layout_choice(settings: Settings) -> str:
    """The persisted choice, or DISPLAY_LAYOUT when there is no file. A file that cannot be
    read, is not JSON or names an unknown layout falls back the same way, with a warning."""
    path = settings_path(settings)
    default = settings.display_layout
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as exc:
        log.warning("ignoring unreadable %s (%s), using DISPLAY_LAYOUT=%s", path, exc, default)
        return default
    choice = raw.get("layout") if isinstance(raw, dict) else None
    if not is_valid_choice(choice):
        log.warning("ignoring %s with layout %r, using DISPLAY_LAYOUT=%s", path, choice, default)
        return default
    assert isinstance(choice, str)
    return choice


def save_layout_choice(settings: Settings, choice: str) -> Path:
    """Persists `choice` atomically; ValueError for anything but a layout key or "auto"."""
    if not is_valid_choice(choice):
        raise ValueError(f"unknown layout {choice!r} (known: {', '.join(sorted(valid_choices()))})")
    path = settings_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"layout": choice}), encoding="utf-8")
    tmp.replace(path)
    return path
