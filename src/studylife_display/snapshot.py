"""The four raw payloads plus when they were obtained - exactly what gets cached, and what
both the refresh pipeline and the web interface's previews are built from.

Metrics, history and timer are the dashboard; a failure there is a failed fetch. The session
list (for the agenda layout) is the optional fourth: a key issued before the
`Sessions.GetAll` scope existed answers 403 on it, and that must not blank the other
layouts, so a failed sessions call yields an empty list, a warning and a note in the
snapshot (`sessions_error`) for status.json. The ETag the endpoint returned is cached next
to the payload so the next refresh can ask with `If-None-Match` and keep its copy on 304.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from studylife_display.studylife_client import StudyLifeApiError, StudyLifeClient

log = logging.getLogger(__name__)

HISTORY_DAYS = 28


@dataclass(frozen=True)
class Snapshot:
    metrics: dict[str, Any]
    history: list[dict[str, Any]]
    timer: dict[str, Any]
    fetched_at: datetime
    sessions: list[dict[str, Any]] = field(default_factory=list)
    # ETag of `sessions` as the server stamped it; sent back as If-None-Match next time.
    sessions_etag: str | None = None
    # Why the sessions call failed this time (None when it worked or was a 304); not cached.
    sessions_error: str | None = field(default=None, compare=False)


def fetch_snapshot(
    client: StudyLifeClient, now: datetime, previous: Snapshot | None = None
) -> Snapshot:
    """Fetches all four payloads. The first three raise on failure like before; the session
    list falls back to the previous snapshot's copy on 304 (via its ETag) and to an empty
    list with `sessions_error` set on any error."""
    metrics = client.get_metrics_summary()
    history = client.get_session_history(days=HISTORY_DAYS)
    timer = client.get_timer_state()

    sessions: list[dict[str, Any]] = []
    etag = previous.sessions_etag if previous is not None else None
    error: str | None = None
    try:
        page = client.list_sessions(etag=etag)
    except (StudyLifeApiError, httpx.HTTPError) as exc:
        error = str(exc)
        etag = None
        log.warning(
            "could not fetch the session list (%s) - the agenda layout will be empty; "
            "a key issued before the Sessions.GetAll scope needs to be re-issued",
            exc,
        )
    else:
        if page.not_modified and previous is not None:
            sessions = previous.sessions
            etag = page.etag
        else:
            sessions = page.items
            etag = None if page.not_modified else page.etag
    return Snapshot(
        metrics=metrics,
        history=history,
        timer=timer,
        fetched_at=now,
        sessions=sessions,
        sessions_etag=etag,
        sessions_error=error,
    )


def save_snapshot(path: Path, snapshot: Snapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": snapshot.fetched_at.isoformat(),
        "metrics": snapshot.metrics,
        "history": snapshot.history,
        "timer": snapshot.timer,
        "sessions": snapshot.sessions,
        "sessions_etag": snapshot.sessions_etag,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


def load_snapshot(path: Path, tz: ZoneInfo) -> Snapshot | None:
    """The cached snapshot, or None when there is none or it cannot be read. A cache written
    before the session list existed loads with an empty list and no ETag."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        fetched_at = datetime.fromisoformat(raw["fetched_at"])
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=tz)
        sessions = raw.get("sessions")
        etag = raw.get("sessions_etag")
        return Snapshot(
            metrics=dict(raw["metrics"]),
            history=list(raw["history"]),
            timer=dict(raw["timer"]),
            fetched_at=fetched_at.astimezone(tz),
            sessions=list(sessions) if isinstance(sessions, list) else [],
            sessions_etag=etag if isinstance(etag, str) and etag else None,
        )
    except (KeyError, TypeError, ValueError):
        return None
