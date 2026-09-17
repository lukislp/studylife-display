"""The three raw payloads plus when they were obtained - exactly what gets cached, and what
both the refresh pipeline and the web interface's previews are built from."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from studylife_display.studylife_client import StudyLifeClient

HISTORY_DAYS = 28


@dataclass(frozen=True)
class Snapshot:
    metrics: dict[str, Any]
    history: list[dict[str, Any]]
    timer: dict[str, Any]
    fetched_at: datetime


def fetch_snapshot(client: StudyLifeClient, now: datetime) -> Snapshot:
    return Snapshot(
        metrics=client.get_metrics_summary(),
        history=client.get_session_history(days=HISTORY_DAYS),
        timer=client.get_timer_state(),
        fetched_at=now,
    )


def save_snapshot(path: Path, snapshot: Snapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": snapshot.fetched_at.isoformat(),
        "metrics": snapshot.metrics,
        "history": snapshot.history,
        "timer": snapshot.timer,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


def load_snapshot(path: Path, tz: ZoneInfo) -> Snapshot | None:
    """The cached snapshot, or None when there is none or it cannot be read."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        fetched_at = datetime.fromisoformat(raw["fetched_at"])
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=tz)
        return Snapshot(
            metrics=dict(raw["metrics"]),
            history=list(raw["history"]),
            timer=dict(raw["timer"]),
            fetched_at=fetched_at.astimezone(tz),
        )
    except (KeyError, TypeError, ValueError):
        return None
