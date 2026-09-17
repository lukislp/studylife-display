"""What happened last: the outcome of the latest fetch, the last error and when the panel
was last drawn, persisted as `status.json` next to the cached snapshot.

The snapshot cache (`last.json`) only ever holds a successful fetch; this file is what the
web interface and `/healthz` read to say *why* the panel shows what it shows - including
when there is no snapshot at all. Written atomically like the cache; a missing or damaged
file reads as "nothing known yet".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

STATUS_FILE = "status.json"

# Error kinds, also the keys of the error screens in `layouts.error`.
REJECTED = "rejected"
STALE = "stale"
NO_DATA = "no_data"
# A failure that is neither a rejected key nor "no data": network down, 5xx, timeout.
TRANSIENT = "transient"


@dataclass(frozen=True)
class LastError:
    kind: str
    status: int | None
    message: str
    at: datetime

    def as_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "status": self.status,
            "message": self.message,
            "at": self.at.isoformat(),
        }


@dataclass(frozen=True)
class Status:
    # Whether the most recent fetch attempt succeeded, and when it was made.
    last_fetch_ok: bool = False
    last_fetch_at: datetime | None = None
    # The most recent failure; kept across later successes only until the next success
    # (a success clears it), so "last_error" always explains the current picture.
    last_error: LastError | None = None
    # When a frame (dashboard or error screen) was last put on the panel.
    last_panel_update_at: datetime | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "last_fetch_ok": self.last_fetch_ok,
            "last_fetch_at": _iso(self.last_fetch_at),
            "last_error": None if self.last_error is None else self.last_error.as_json(),
            "last_panel_update_at": _iso(self.last_panel_update_at),
        }


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()


def _moment(value: object, tz: ZoneInfo) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def status_path(state_dir: Path) -> Path:
    return state_dir / STATUS_FILE


def load_status(state_dir: Path, tz: ZoneInfo) -> Status:
    """The persisted status, or an empty one when there is no file or it is unreadable."""
    try:
        raw = json.loads(status_path(state_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Status()
    if not isinstance(raw, dict):
        return Status()
    error_raw = raw.get("last_error")
    error: LastError | None = None
    if isinstance(error_raw, dict):
        at = _moment(error_raw.get("at"), tz)
        status = error_raw.get("status")
        if at is not None:
            error = LastError(
                kind=str(error_raw.get("kind") or TRANSIENT),
                status=status if isinstance(status, int) and not isinstance(status, bool) else None,
                message=str(error_raw.get("message") or ""),
                at=at,
            )
    return Status(
        last_fetch_ok=bool(raw.get("last_fetch_ok")),
        last_fetch_at=_moment(raw.get("last_fetch_at"), tz),
        last_error=error,
        last_panel_update_at=_moment(raw.get("last_panel_update_at"), tz),
    )


def save_status(state_dir: Path, status: Status) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = status_path(state_dir)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(status.as_json()), encoding="utf-8")
    tmp.replace(path)


def update_status(state_dir: Path, tz: ZoneInfo, **changes: Any) -> Status:
    """Read-modify-write of the status file; a write failure is the caller's to log."""
    status = replace(load_status(state_dir, tz), **changes)
    save_status(state_dir, status)
    return status
