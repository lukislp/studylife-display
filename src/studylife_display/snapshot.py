"""The raw payloads plus when they were obtained - exactly what gets cached, and what both
the refresh pipeline and the web interface's previews are built from.

Metrics, history and timer are the dashboard; a failure there is a failed fetch. The session
list (for the agenda layouts), the course goals, the achievements and the notes are the
optional extras: each needs its own scope, a key issued before that scope existed answers
403 on it, and that must not blank the other layouts - so a failed optional call yields an
empty payload, a warning and a note in the snapshot (`errors`) for status.json and the
layouts. The ETag the sessions endpoint returned is cached next to the payload so the next
refresh can ask with `If-None-Match` and keep its copy on 304.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from studylife_display.model import NOTE_EXCERPT_CHARS, NOTES_KEPT
from studylife_display.studylife_client import StudyLifeApiError, StudyLifeClient

log = logging.getLogger(__name__)

# 53 weeks: the year layout's 53 Monday-to-Sunday columns need at most 52 weeks plus the
# current one, whatever weekday today is (see model.YEAR_WEEKS).
HISTORY_DAYS = 371

# What the note layout can ever show: the newest notes, title plus a short excerpt. Notes
# can be a megabyte each, so the list is trimmed to that before it is cached.
_NOTE_FIELDS = ("id", "title", "summary", "updatedAt")
_NOTE_CONTENT_KEPT = NOTE_EXCERPT_CHARS * 2


@dataclass(frozen=True)
class Snapshot:
    metrics: dict[str, Any]
    history: list[dict[str, Any]]
    timer: dict[str, Any]
    fetched_at: datetime
    sessions: list[dict[str, Any]] = field(default_factory=list)
    # ETag of `sessions` as the server stamped it; sent back as If-None-Match next time.
    sessions_etag: str | None = None
    goals: list[dict[str, Any]] = field(default_factory=list)
    achievements: dict[str, Any] = field(default_factory=dict)
    notes: list[dict[str, Any]] = field(default_factory=list)
    # Why an optional call failed this time, by payload name ("sessions", "goals",
    # "achievements", "notes"); absent when it worked (or was a 304). Not cached.
    errors: dict[str, str] = field(default_factory=dict, compare=False)

    @property
    def sessions_error(self) -> str | None:
        return self.errors.get("sessions")

    @property
    def unavailable(self) -> frozenset[str]:
        return frozenset(self.errors)


def trim_notes(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The newest NOTES_KEPT notes with their content cut to what the excerpt can use."""
    usable = [note for note in notes if isinstance(note, dict)]
    usable.sort(key=lambda note: str(note.get("updatedAt") or ""), reverse=True)
    trimmed = []
    for note in usable[:NOTES_KEPT]:
        kept = {key: note.get(key) for key in _NOTE_FIELDS if key in note}
        content = note.get("content")
        kept["content"] = str(content)[:_NOTE_CONTENT_KEPT] if content else ""
        trimmed.append(kept)
    return trimmed


def _optional(name: str, call: Callable[[], Any], errors: dict[str, str], empty: Any) -> Any:
    try:
        return call()
    except (StudyLifeApiError, httpx.HTTPError) as exc:
        errors[name] = str(exc)
        log.warning(
            "could not fetch %s (%s) - the layouts using it stay empty; a key issued without "
            "that scope needs to be re-issued",
            name,
            exc,
        )
        return empty


def fetch_snapshot(
    client: StudyLifeClient, now: datetime, previous: Snapshot | None = None
) -> Snapshot:
    """Fetches every payload. The first three raise on failure like before; the session list
    falls back to the previous snapshot's copy on 304 (via its ETag) and to an empty list with
    an error note on any failure, and the three extras to empty payloads the same way."""
    metrics = client.get_metrics_summary()
    history = client.get_session_history(days=HISTORY_DAYS)
    timer = client.get_timer_state()

    errors: dict[str, str] = {}
    sessions: list[dict[str, Any]] = []
    etag = previous.sessions_etag if previous is not None else None
    try:
        page = client.list_sessions(etag=etag)
    except (StudyLifeApiError, httpx.HTTPError) as exc:
        errors["sessions"] = str(exc)
        etag = None
        log.warning(
            "could not fetch the session list (%s) - the agenda layouts will be empty; "
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

    goals = _optional("goals", client.get_course_goals, errors, [])
    achievements = _optional("achievements", client.get_achievements, errors, {})
    notes = trim_notes(_optional("notes", client.get_notes, errors, []))
    return Snapshot(
        metrics=metrics,
        history=history,
        timer=timer,
        fetched_at=now,
        sessions=sessions,
        sessions_etag=etag,
        goals=goals,
        achievements=achievements,
        notes=notes,
        errors=errors,
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
        "goals": snapshot.goals,
        "achievements": snapshot.achievements,
        "notes": snapshot.notes,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


def load_snapshot(path: Path, tz: ZoneInfo) -> Snapshot | None:
    """The cached snapshot, or None when there is none or it cannot be read. A cache written
    before an optional list existed loads with that list empty (and no ETag)."""
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
        goals = raw.get("goals")
        achievements = raw.get("achievements")
        notes = raw.get("notes")
        return Snapshot(
            metrics=dict(raw["metrics"]),
            history=list(raw["history"]),
            timer=dict(raw["timer"]),
            fetched_at=fetched_at.astimezone(tz),
            sessions=list(sessions) if isinstance(sessions, list) else [],
            sessions_etag=etag if isinstance(etag, str) and etag else None,
            goals=list(goals) if isinstance(goals, list) else [],
            achievements=dict(achievements) if isinstance(achievements, dict) else {},
            notes=list(notes) if isinstance(notes, list) else [],
        )
    except (KeyError, TypeError, ValueError):
        return None
