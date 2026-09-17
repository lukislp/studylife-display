"""The layout choice made in the web interface, persisted next to the cached snapshot.

Precedence: `settings.json` in the state directory (written by `POST /layout`) beats the
DISPLAY_LAYOUT environment variable, which is the default for a fresh install. The file is
tiny (`{"layout": "<key|auto>"}`) and written atomically like the cache, so a power cut
mid-write leaves the previous choice, never half a file.

Surviving a reboot with the overlay filesystem on: the state directory then lives in RAM,
so the choice is mirrored to a second copy on the boot partition (DISPLAY_PERSIST_PATH;
`/boot/firmware` stays writable by root even with the overlay). The unprivileged web
service keeps writing `settings.json` where it always did; two root-run oneshot units call
`persist-export` (state directory -> boot partition, triggered by a path unit on every
change) and `persist-import` (boot partition -> state directory, once at boot, before the
timer and the web service start). Both are plain file copies with the same validation as
the loader, so a damaged copy on either side is never propagated.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from studylife_display.config import Settings
from studylife_display.layouts import AUTO, LAYOUTS

log = logging.getLogger(__name__)

SETTINGS_FILE = "settings.json"


class InvalidSettingsFile(ValueError):
    """A settings file exists but is not JSON, not an object or names an unknown layout."""


def valid_choices() -> frozenset[str]:
    return frozenset(LAYOUTS) | {AUTO}


def is_valid_choice(choice: object) -> bool:
    return isinstance(choice, str) and choice in valid_choices()


def settings_path(settings: Settings) -> Path:
    return Path(settings.display_state_path).parent / SETTINGS_FILE


def persist_path(settings: Settings) -> Path | None:
    """Where the boot-partition copy lives, or None when DISPLAY_PERSIST_PATH is empty."""
    return Path(settings.display_persist_path) if settings.display_persist_path else None


def read_layout_file(path: Path) -> str | None:
    """The choice in a settings file; None when the file does not exist. Raises
    InvalidSettingsFile for content that is not `{"layout": <key|auto>}` and OSError when
    the file exists but cannot be read."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except ValueError as exc:
        raise InvalidSettingsFile(f"{path} is not JSON ({exc})") from exc
    choice = raw.get("layout") if isinstance(raw, dict) else None
    if not is_valid_choice(choice):
        raise InvalidSettingsFile(f"{path} names layout {choice!r}")
    assert isinstance(choice, str)
    return choice


def _write_layout_file(path: Path, choice: str) -> None:
    """Temp file, fsync, rename: a power cut leaves either the old or the new file. The
    fsync matters on the boot partition (FAT, no journal), where a rename that lands before
    the data does can leave an empty file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"layout": choice}))
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)


def _current_choice(path: Path) -> str | None:
    """Like read_layout_file, but a missing, unreadable or invalid file is just None."""
    try:
        return read_layout_file(path)
    except (OSError, ValueError):
        return None


def load_layout_choice(settings: Settings) -> str:
    """The persisted choice, or DISPLAY_LAYOUT when there is no file. A file that cannot be
    read, is not JSON or names an unknown layout falls back the same way, with a warning."""
    path = settings_path(settings)
    default = settings.display_layout
    try:
        choice = read_layout_file(path)
    except (OSError, ValueError) as exc:
        log.warning("ignoring %s (%s), using DISPLAY_LAYOUT=%s", path, exc, default)
        return default
    return default if choice is None else choice


def save_layout_choice(settings: Settings, choice: str) -> Path:
    """Persists `choice` atomically; ValueError for anything but a layout key or "auto"."""
    if not is_valid_choice(choice):
        raise ValueError(f"unknown layout {choice!r} (known: {', '.join(sorted(valid_choices()))})")
    path = settings_path(settings)
    _write_layout_file(path, choice)
    return path


def _adopt_directory_owner(path: Path) -> None:
    """After root has written into the service user's state directory, hand the file to
    that user so `ls -l` stays unsurprising. Best effort; a no-op when not root (the tests)
    and on platforms without chown."""
    chown = getattr(os, "chown", None)
    geteuid = getattr(os, "geteuid", None)
    if chown is None or geteuid is None or geteuid() != 0:
        return
    owner = path.parent.stat()
    try:
        chown(path, owner.st_uid, owner.st_gid)
    except OSError as exc:
        log.warning("could not chown %s to its directory's owner: %s", path, exc)


def export_layout_choice(settings: Settings) -> int:
    """`persist-export`: copy settings.json to DISPLAY_PERSIST_PATH. Exit status: 0 when
    the copy is up to date or there is nothing to copy (no settings file yet, persistence
    disabled), 1 when the settings file is damaged or the copy cannot be written. Nothing is
    written when the copy already holds the same choice, so the path unit firing on the
    boot-time import costs no write on the boot partition."""
    target = persist_path(settings)
    if target is None:
        log.info("DISPLAY_PERSIST_PATH is empty, not exporting the layout choice")
        return 0
    source = settings_path(settings)
    try:
        choice = read_layout_file(source)
    except (OSError, ValueError) as exc:
        log.error("not exporting the layout choice: %s", exc)
        return 1
    if choice is None:
        log.info("no %s yet, nothing to export", source)
        return 0
    if _current_choice(target) == choice:
        log.debug("%s already holds layout %s", target, choice)
        return 0
    try:
        _write_layout_file(target, choice)
    except OSError as exc:
        log.error("could not write %s: %s", target, exc)
        return 1
    log.info("exported layout %s to %s", choice, target)
    return 0


def import_layout_choice(settings: Settings) -> int:
    """`persist-import`: copy DISPLAY_PERSIST_PATH back to settings.json. A valid local file
    is only replaced when it differs and is not newer than the copy; an invalid copy never
    replaces anything and exits 1 so that it shows up as a failed unit. Exit 0 when there is
    nothing to import (no copy, persistence disabled, local file already current)."""
    source = persist_path(settings)
    if source is None:
        log.info("DISPLAY_PERSIST_PATH is empty, not importing a layout choice")
        return 0
    target = settings_path(settings)
    try:
        choice = read_layout_file(source)
    except (OSError, ValueError) as exc:
        log.error("not importing the layout choice, keeping %s as it is: %s", target, exc)
        return 1
    if choice is None:
        log.info("no %s, nothing to import", source)
        return 0
    local = _current_choice(target)
    if local == choice:
        log.info("%s already holds layout %s", target, choice)
        return 0
    if local is not None and target.stat().st_mtime > source.stat().st_mtime:
        log.info("keeping %s (layout %s): newer than %s (layout %s)", target, local, source, choice)
        return 0
    try:
        _write_layout_file(target, choice)
        _adopt_directory_owner(target)
    except OSError as exc:
        log.error("could not write %s: %s", target, exc)
        return 1
    log.info("restored layout %s from %s", choice, source)
    return 0
