"""A bearer-token JSON API under /api/, mirroring everything the cookie/browser web
interface can do - the current layout and frame, the layout previews, a refresh, the
settings, connecting the account - for a machine client on the same LAN that cannot hold a
browser session cookie. The studylife-hacs Home Assistant integration is the motivating
client: a `sensor`/`camera` for what is on the panel, a `select` to change the layout.

Off unless DISPLAY_API_TOKEN is set (see config.py): every /api/ route then answers 404,
the same as an unknown path, so an unconfigured install never grows a second door next to
the cookie one by accident. A bearer token carries no ambient browser authority the way a
cookie does, so unlike the cookie routes these never check Sec-Fetch-Site/Origin - there is
no cross-site request forgery to guard against a header no page in any browser ever holds.

`handle()` is the single entry point web.py's RequestHandler calls for any path starting
with "/api/"; it never returns None, so an unmatched /api/ path 404s from here rather than
falling through to the HTML router.
"""

from __future__ import annotations

import hmac
import json
import logging
import time
from collections.abc import Callable
from datetime import datetime
from email.message import Message
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from studylife_display.config import READONLY_FIELDS, Settings
from studylife_display.connect import CLIENT_ID, ConnectError, root_is_overlay, whoami
from studylife_display.layouts import AUTO, LAYOUTS
from studylife_display.layouts.auto import resolve_layout, rules_from_settings
from studylife_display.settings_store import (
    is_valid_choice,
    load_layout_choice,
    override_sources,
    save_layout_choice,
    update_overrides,
)
from studylife_display.studylife_client import SCOPES

if TYPE_CHECKING:
    from studylife_display.web import WebApp

log = logging.getLogger(__name__)

# Same delay as a wrong cookie-login token, and applied for the same reason: guessing a
# bearer token from the LAN should cost time, not be free. Also applied when the API is
# off entirely, so "off" and "wrong token" cannot be told apart by response latency either.
WRONG_TOKEN_DELAY_SECONDS = 1.0

# settings.json keys /api/settings may change - everything WebOverrides holds except
# "layout", which has its own endpoint (/api/layout) the same way the layouts page and the
# settings page are separate on the web interface.
SETTINGS_KEYS = frozenset(
    {"language", "rotate", "quiet_hours", "clear_at", "update_check", "auto_review", "auto_agenda"}
)

JsonResult = tuple[HTTPStatus, bytes, str]
HealthReport = Callable[[Settings, datetime], tuple[dict[str, Any], HTTPStatus]]


def _json(status: HTTPStatus, payload: Any) -> JsonResult:
    return status, json.dumps(payload).encode("utf-8"), "application/json"


def _error(status: HTTPStatus, message: str) -> JsonResult:
    return _json(status, {"error": message})


def _authorized(app: WebApp, headers: Message) -> bool:
    token = app.settings.display_api_token
    auth = headers.get("Authorization", "")
    if not token or not auth.startswith("Bearer "):
        return False
    candidate = auth[len("Bearer ") :]
    return hmac.compare_digest(candidate.encode("utf-8"), token.encode("utf-8"))


def _parse_json_object(body: bytes) -> dict[str, Any] | None:
    """`{}` for an empty body (every field then keeps its current value/is invalid the
    same way a missing form field would be), None for anything that is not a JSON object."""
    if not body:
        return {}
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _layout_options() -> list[dict[str, Any]]:
    return [
        {"key": spec.key, "name": spec.name, "description": spec.description}
        for spec in LAYOUTS.values()
    ]


def _current_frame_json(app: WebApp) -> dict[str, Any] | None:
    frame = app.current_frame()
    return None if frame is None else frame.as_json()


def _identity(app: WebApp) -> dict[str, Any]:
    settings = app.settings
    instance = str(settings.studylife_base_url).rstrip("/")
    key = settings.studylife_api_key
    if not key:
        return {"connected": False, "instance": instance, "user_id": None, "error": None}
    try:
        who = whoami(instance, key, settings.http_timeout_seconds)
    except ConnectError as exc:
        return {
            "connected": False,
            "instance": instance,
            "user_id": None,
            "error": {"kind": exc.key, "detail": exc.detail},
        }
    return {
        "connected": True,
        "instance": instance,
        "user_id": who.get("userId"),
        "credential": who.get("credential"),
        "error": None,
    }


def _pending_json(app: WebApp) -> dict[str, Any] | None:
    pending = app.pending()
    if pending is None:
        return None
    return {
        "connect_url": pending.connect_url,
        "redirect_uri": pending.redirect_uri,
        "expires_at": pending.expires_at.isoformat(),
    }


def _state(app: WebApp, health_report: HealthReport) -> tuple[dict[str, Any], HTTPStatus]:
    """/healthz's report plus the persisted layout choice and the current frame - the one
    call a HA integration needs to poll for a `sensor`/`camera`. The HTTP status mirrors
    /healthz's (503 for "error"), so a client that just checks the status code behaves the
    same against either route."""
    settings = app.effective()
    report, status = health_report(app.settings, app.now())
    payload = {
        **report,
        "layout_choice": load_layout_choice(settings),
        "current_frame": _current_frame_json(app),
    }
    return payload, status


def _layouts(app: WebApp) -> dict[str, Any]:
    settings = app.effective()
    data, _ = app.current_data()
    return {
        "choice": load_layout_choice(settings),
        "resolved": resolve_layout(AUTO, data, rules_from_settings(settings)),
        "options": _layout_options(),
    }


def _settings_get(app: WebApp) -> dict[str, Any]:
    settings = app.effective()
    values = {
        "language": settings.display_language,
        "rotate": settings.display_rotate,
        "quiet_hours": settings.display_quiet_hours,
        "clear_at": settings.display_clear_at,
        "update_check": settings.display_update_check,
        "auto_review": settings.display_auto_review,
        "auto_agenda": settings.display_auto_agenda,
    }
    readonly = {
        name: {
            "set": bool(getattr(app.settings, field)),
            "value": _readonly_value(app, field, show),
        }
        for name, field, show in READONLY_FIELDS
    }
    return {"values": values, "sources": override_sources(app.settings), "readonly": readonly}


def _readonly_value(app: WebApp, field: str, show_value: bool) -> str | None:
    raw = getattr(app.settings, field)
    return str(raw) if show_value and raw else None


def _connect_state(app: WebApp) -> dict[str, Any]:
    mode, redirect_uri = app.connect_mode()
    return {
        "identity": _identity(app),
        "pending": _pending_json(app),
        "overlay_warning": root_is_overlay(),
        "mode": mode,
        "redirect_uri": redirect_uri,
        "client_id": CLIENT_ID,
        "scopes": list(SCOPES),
    }


def handle(
    app: WebApp,
    method: str,
    path: str,
    headers: Message,
    body: bytes,
    health_report: HealthReport,
) -> JsonResult:
    if not _authorized(app, headers):
        time.sleep(WRONG_TOKEN_DELAY_SECONDS)
        if not app.settings.display_api_token:
            return _error(HTTPStatus.NOT_FOUND, "not_found")
        return _error(HTTPStatus.UNAUTHORIZED, "unauthorized")

    if method == "GET" and path == "/api/state":
        state, state_status = _state(app, health_report)
        return _json(state_status, state)

    if method == "GET" and path == "/api/layouts":
        return _json(HTTPStatus.OK, _layouts(app))

    if method == "POST" and path == "/api/layout":
        payload = _parse_json_object(body)
        if payload is None:
            return _error(HTTPStatus.BAD_REQUEST, "invalid_json")
        choice = payload.get("layout")
        if not is_valid_choice(choice):
            return _error(HTTPStatus.BAD_REQUEST, "invalid_layout")
        assert isinstance(choice, str)  # narrows for mypy; is_valid_choice just checked it
        save_layout_choice(app.settings, choice)
        log.info("layout choice set to %s via the API", choice)
        return _json(HTTPStatus.OK, {"outcome": app.run_refresh()})

    if method == "POST" and path == "/api/refresh":
        return _json(HTTPStatus.OK, {"outcome": app.run_refresh()})

    if method == "GET" and path == "/api/current.png":
        png = app.current_png()
        if png is None:
            return _error(HTTPStatus.NOT_FOUND, "not_found")
        return HTTPStatus.OK, png, "image/png"

    if method == "GET" and path.startswith("/api/preview/") and path.endswith(".png"):
        key = path[len("/api/preview/") : -len(".png")]
        if not is_valid_choice(key):
            return _error(HTTPStatus.NOT_FOUND, "not_found")
        return HTTPStatus.OK, app.preview_png(key), "image/png"

    if method == "GET" and path == "/api/settings":
        return _json(HTTPStatus.OK, _settings_get(app))

    if method == "POST" and path == "/api/settings/reset":
        update_overrides(
            app.settings,
            language=None,
            rotate=None,
            quiet_hours=None,
            clear_at=None,
            update_check=None,
            auto_review=None,
            auto_agenda=None,
        )
        log.info("settings reset to the environment values via the API")
        return _json(HTTPStatus.OK, _settings_get(app))

    if method == "POST" and path == "/api/settings":
        payload = _parse_json_object(body)
        if payload is None:
            return _error(HTTPStatus.BAD_REQUEST, "invalid_json")
        unknown = set(payload) - SETTINGS_KEYS
        if unknown:
            return _error(HTTPStatus.BAD_REQUEST, f"unknown field(s): {', '.join(sorted(unknown))}")
        try:
            update_overrides(app.settings, **payload)
        except ValueError as exc:
            return _error(HTTPStatus.BAD_REQUEST, str(exc))
        log.info("settings saved via the API: %s", payload)
        return _json(HTTPStatus.OK, _settings_get(app))

    if method == "GET" and path == "/api/connect":
        return _json(HTTPStatus.OK, _connect_state(app))

    if method == "POST" and path == "/api/connect/start":
        pending = app.begin_connect()
        return _json(
            HTTPStatus.OK,
            {
                "connect_url": pending.connect_url,
                "redirect_uri": pending.redirect_uri,
                "expires_at": pending.expires_at.isoformat(),
            },
        )

    if method == "POST" and path == "/api/connect/paste":
        payload = _parse_json_object(body)
        if payload is None:
            return _error(HTTPStatus.BAD_REQUEST, "invalid_json")
        outcome = app.finish_connect(payload.get("callback_url", ""))
        result: dict[str, Any] = {"outcome": outcome}
        if outcome != "applied":
            result["detail"] = app.last_connect_detail
        return _json(HTTPStatus.OK, result)

    return _error(HTTPStatus.NOT_FOUND, "not_found")
