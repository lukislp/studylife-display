"""The JSON `/healthz` report: one function shared by the unauthenticated `GET /healthz`
route (for an uptime monitor) and the bearer-authenticated `GET /api/state` route (for a
machine client like the studylife-hacs Home Assistant integration), so the two never drift
apart on what "ok"/"degraded"/"error" means. Split out of web.py so that api.py (which
web.py imports to dispatch /api/ requests) never has to import web.py back.
"""

from __future__ import annotations

from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from typing import Any

from studylife_display import package_version
from studylife_display.config import Settings
from studylife_display.layouts.auto import resolve_layout, rules_from_settings
from studylife_display.model import build_dashboard
from studylife_display.quiet_hours import in_quiet_hours
from studylife_display.settings_store import effective_settings, load_layout_choice
from studylife_display.snapshot import load_snapshot
from studylife_display.status_store import REJECTED, load_status
from studylife_display.times import zone

# /healthz says "degraded" when the cached snapshot is older than this although the last
# fetch succeeded: three missed five-minute refreshes mean the timer is not running.
STALE_DEGRADED_MINUTES = 15


def health_report(settings: Settings, now: datetime) -> tuple[dict[str, Any], HTTPStatus]:
    """The JSON for /healthz (and /api/state) and its HTTP status: "setup" (200) while no
    API key is configured at all (the panel shows the setup screen; `setup` is true), "ok"
    (200) when the last fetch succeeded and the snapshot is fresh, "degraded" (200) when the
    last fetch failed but a cached dashboard is shown or the snapshot is older than
    STALE_DEGRADED_MINUTES outside quiet hours, "error" (503) when the key was rejected or
    there is no data at all."""
    settings = effective_settings(settings)
    setup = not settings.studylife_api_key
    tz = zone(settings.studylife_timezone)
    state_path = Path(settings.display_state_path)
    status = load_status(state_path.parent, tz)
    snapshot = load_snapshot(state_path, tz)
    quiet = in_quiet_hours(now, settings.display_quiet_hours)

    layout: str | None = None
    stale_minutes: int | None = None
    last_fetch_at = status.last_fetch_at
    if snapshot is not None:
        data = build_dashboard(
            snapshot.metrics,
            snapshot.history,
            snapshot.timer,
            now,
            tz,
            snapshot.fetched_at,
            sessions=snapshot.sessions,
        )
        layout = resolve_layout(load_layout_choice(settings), data, rules_from_settings(settings))
        stale_minutes = data.stale_minutes
        if last_fetch_at is None:
            last_fetch_at = snapshot.fetched_at

    error = status.last_error
    rejected = error is not None and error.kind == REJECTED and not status.last_fetch_ok
    timer_silent = (
        stale_minutes is not None and stale_minutes > STALE_DEGRADED_MINUTES and not quiet
    )
    if setup:
        state = "setup"
    elif rejected or snapshot is None:
        state = "error"
    elif not status.last_fetch_ok or timer_silent:
        state = "degraded"
    else:
        state = "ok"

    report: dict[str, Any] = {
        "status": state,
        "setup": setup,
        "version": package_version(),
        "last_fetch_at": None if last_fetch_at is None else last_fetch_at.isoformat(),
        "last_fetch_ok": status.last_fetch_ok,
        "stale_minutes": stale_minutes,
        "last_error": None if error is None else error.as_json(),
        "last_panel_update_at": (
            None if status.last_panel_update_at is None else status.last_panel_update_at.isoformat()
        ),
        "layout": layout,
        "quiet_hours_active": quiet,
        "sessions_ok": status.sessions_ok,
    }
    return report, HTTPStatus.OK if state != "error" else HTTPStatus.SERVICE_UNAVAILABLE
