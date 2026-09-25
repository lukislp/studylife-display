"""The bearer-token JSON API under /api/, end to end against a real ThreadingHTTPServer -
same harness shape as test_web.py (file driver, temporary state directory, the StudyLife
API stubbed to fail so a refresh always goes through the cache fallback), trimmed to what a
machine client actually sends: an `Authorization: Bearer ...` header and, for POST, a JSON
body instead of a cookie and a form."""

from __future__ import annotations

import http.client
import io
import json
import threading
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from PIL import Image

from studylife_display import main as main_module
from studylife_display.config import Settings
from studylife_display.settings_store import settings_path
from studylife_display.snapshot import Snapshot, save_snapshot
from studylife_display.status_store import Status, save_status
from studylife_display.studylife_client import StudyLifeApiError
from studylife_display.web import DisplayServer, make_server

TOKEN = "correct-horse-battery-api"
BASE_URL = "https://studylife.test"


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "studylife_base_url": BASE_URL,
        "studylife_api_key": "k",
        "display_driver": "file",
        "display_output_path": str(tmp_path / "frame.png"),
        "display_state_path": str(tmp_path / "state" / "last.json"),
        "display_web_token": "unused-cookie-token-here",
        "display_api_token": TOKEN,
        "display_layout": "auto",
        # See test_web.py's make_settings: keeps "auto" from flipping to "review" on a
        # Sunday evening regardless of when this actually runs.
        "display_auto_review": "",
    }
    values.update(overrides)
    return Settings(**values)


class Client:
    """A tiny http.client wrapper for the JSON API: no cookie jar, no same-origin headers
    (a bearer token needs neither) - just the Authorization header and a JSON body."""

    def __init__(self, port: int, token: str | None = TOKEN) -> None:
        self.port = port
        self.token = token

    def request(
        self, method: str, path: str, json_body: dict[str, Any] | None = None
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {"Host": f"127.0.0.1:{self.port}"}
        if self.token is not None:
            headers["Authorization"] = f"Bearer {self.token}"
        body: bytes | None = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read()
        received = {name.lower(): value for name, value in response.getheaders()}
        connection.close()
        return response.status, received, payload

    def json(
        self, method: str, path: str, json_body: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        status, _, body = self.request(method, path, json_body)
        return status, json.loads(body)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> Snapshot:
        raise StudyLifeApiError(503, "offline in tests")

    monkeypatch.setattr(main_module, "fetch_snapshot", fail)


@pytest.fixture
def server(settings: Settings, offline: None) -> Iterator[DisplayServer]:
    instance = make_server(settings, lambda: main_module.refresh_panel(settings), "127.0.0.1:0")
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)


@pytest.fixture
def client(server: DisplayServer) -> Client:
    return Client(server.server_address[1])


@pytest.fixture
def cached(settings: Settings, sample: Any, tz: ZoneInfo, fixed_now: datetime) -> Path:
    metrics, history, timer, sessions = sample
    path = Path(settings.display_state_path)
    save_snapshot(path, Snapshot(metrics, history, timer, fixed_now))
    return path


def fresh_cache(settings: Settings, sample: Any, tz: ZoneInfo) -> datetime:
    """A snapshot fetched just now plus a matching status.json (see test_web.py's helper
    of the same name): unlike `cached` (anchored at the fixed golden-test clock, which is
    "stale" by the time this suite actually runs), this is what /api/state's "ok" needs."""
    now = datetime.now(tz)
    metrics, history, timer, sessions = sample
    path = Path(settings.display_state_path)
    save_snapshot(path, Snapshot(metrics, history, timer, now))
    save_status(
        path.parent, Status(last_fetch_ok=True, last_fetch_at=now, last_panel_update_at=now)
    )
    return now


class TestAuth:
    def test_disabled_without_a_configured_token(self, tmp_path: Path) -> None:
        instance = make_server(
            make_settings(tmp_path, display_api_token=""),
            lambda: 0,
            "127.0.0.1:0",
        )
        thread = threading.Thread(target=instance.serve_forever, daemon=True)
        thread.start()
        try:
            status, _, _ = Client(instance.server_address[1]).request("GET", "/api/state")
            assert status == 404
        finally:
            instance.shutdown()
            instance.server_close()
            thread.join(timeout=5)

    def test_missing_authorization_header_is_401(self, server: DisplayServer) -> None:
        status, _, _ = Client(server.server_address[1], token=None).request("GET", "/api/state")
        assert status == 401

    def test_wrong_token_is_401(self, server: DisplayServer) -> None:
        status, _, _ = Client(server.server_address[1], token="not-the-token").request(
            "GET", "/api/state"
        )
        assert status == 401

    def test_unknown_api_path_is_404(self, client: Client) -> None:
        assert client.request("GET", "/api/nonsense")[0] == 404

    def test_the_cookie_login_routes_are_unaffected(self, client: Client) -> None:
        # /api/ is a separate namespace; the bearer token never doubles as the cookie token.
        assert client.request("GET", "/")[0] == 200  # login page, no cookie yet


class TestState:
    def test_state_without_a_cache_is_an_error(self, client: Client) -> None:
        status, body = client.json("GET", "/api/state")
        assert status == 503
        assert body["status"] == "error"
        assert body["setup"] is False
        assert body["layout_choice"] == "auto"
        assert body["current_frame"] is None

    def test_state_with_a_cache_is_ok(
        self, client: Client, settings: Settings, sample: Any, tz: ZoneInfo
    ) -> None:
        fresh_cache(settings, sample, tz)
        status, body = client.json("GET", "/api/state")
        assert status == 200
        assert body["status"] == "ok"
        assert body["layout"] == "focus"  # the sample's running timer, like test_web.py
        assert body["layout_choice"] == "auto"

    def test_state_reflects_the_current_frame_after_a_refresh(
        self, client: Client, settings: Settings, sample: Any, tz: ZoneInfo
    ) -> None:
        fresh_cache(settings, sample, tz)
        client.json("POST", "/api/refresh")
        _, body = client.json("GET", "/api/state")
        assert body["current_frame"]["kind"] == "dashboard"
        assert body["current_frame"]["layout"] == "focus"


class TestLayouts:
    def test_lists_every_layout_with_both_languages(self, client: Client) -> None:
        status, body = client.json("GET", "/api/layouts")
        assert status == 200
        assert body["choice"] == "auto"
        keys = {option["key"] for option in body["options"]}
        assert keys == {
            "classic",
            "focus",
            "exam",
            "week",
            "semester",
            "agenda",
            "courses",
            "milestone",
            "review",
        }
        classic = next(o for o in body["options"] if o["key"] == "classic")
        assert set(classic["name"]) == {"de", "en"}
        assert set(classic["description"]) == {"de", "en"}

    def test_resolved_follows_the_cached_data(self, client: Client, cached: Path) -> None:
        _, body = client.json("GET", "/api/layouts")
        assert body["resolved"] == "focus"


class TestLayoutChange:
    def test_valid_layout_is_saved_and_refreshes(
        self, client: Client, cached: Path, settings: Settings
    ) -> None:
        status, body = client.json("POST", "/api/layout", {"layout": "week"})
        assert status == 200
        assert body["outcome"] == "refreshed"
        saved = json.loads(settings_path(settings).read_text(encoding="utf-8"))
        assert saved == {"layout": "week"}
        with Image.open(settings.display_output_path) as image:
            assert image.size == (800, 480)

    def test_invalid_layout_is_a_400_and_nothing_is_written(
        self, client: Client, settings: Settings
    ) -> None:
        status, body = client.json("POST", "/api/layout", {"layout": "holographic"})
        assert status == 400
        assert body["error"] == "invalid_layout"
        assert not settings_path(settings).exists()

    def test_missing_layout_field_is_a_400(self, client: Client) -> None:
        status, body = client.json("POST", "/api/layout", {})
        assert status == 400
        assert body["error"] == "invalid_layout"

    def test_refresh_without_a_change(
        self, client: Client, cached: Path, settings: Settings
    ) -> None:
        status, body = client.json("POST", "/api/refresh")
        assert status == 200
        assert body["outcome"] == "refreshed"
        assert not settings_path(settings).exists()

    def test_refresh_without_any_data_reports_failure(self, client: Client) -> None:
        status, body = client.json("POST", "/api/refresh")
        assert status == 200
        assert body["outcome"] == "failed"


class TestImages:
    def test_current_png_is_a_404_before_the_first_refresh(self, client: Client) -> None:
        assert client.request("GET", "/api/current.png")[0] == 404

    def test_current_png_after_a_refresh(self, client: Client, cached: Path) -> None:
        client.json("POST", "/api/refresh")
        status, headers, body = client.request("GET", "/api/current.png")
        assert status == 200
        assert headers["content-type"] == "image/png"
        with Image.open(io.BytesIO(body)) as image:
            assert image.size == (800, 480)

    @pytest.mark.parametrize("key", ["auto", "classic", "focus", "exam"])
    def test_preview_is_an_800x480_png(self, client: Client, key: str) -> None:
        status, headers, body = client.request("GET", f"/api/preview/{key}.png")
        assert status == 200
        assert headers["content-type"] == "image/png"
        with Image.open(io.BytesIO(body)) as image:
            assert image.size == (800, 480)

    def test_unknown_preview_is_a_404(self, client: Client) -> None:
        assert client.request("GET", "/api/preview/holographic.png")[0] == 404


class TestSettings:
    def test_get_reports_values_sources_and_secrets_as_set_not_shown(self, client: Client) -> None:
        status, body = client.json("GET", "/api/settings")
        assert status == 200
        assert body["values"]["rotate"] == 0
        assert body["sources"] == {
            "layout": False,
            "language": False,
            "rotate": False,
            "quiet_hours": False,
            "clear_at": False,
            "update_check": False,
            "auto_review": False,
            "auto_agenda": False,
        }
        assert body["readonly"]["STUDYLIFE_API_KEY"] == {"set": True, "value": None}
        assert body["readonly"]["DISPLAY_API_TOKEN"] == {"set": True, "value": None}
        # pydantic's AnyHttpUrl normalises to a trailing slash.
        assert body["readonly"]["STUDYLIFE_BASE_URL"] == {"set": True, "value": f"{BASE_URL}/"}

    def test_post_saves_a_partial_change(self, client: Client, settings: Settings) -> None:
        status, body = client.json("POST", "/api/settings", {"rotate": 180})
        assert status == 200
        assert body["values"]["rotate"] == 180
        assert body["sources"]["rotate"] is True
        assert body["sources"]["language"] is False
        saved = json.loads(settings_path(settings).read_text(encoding="utf-8"))
        assert saved == {"rotate": 180}

    def test_post_rejects_an_invalid_value(self, client: Client, settings: Settings) -> None:
        status, body = client.json("POST", "/api/settings", {"rotate": 45})
        assert status == 400
        assert "error" in body
        assert not settings_path(settings).exists()

    def test_post_rejects_the_wrong_json_type(self, client: Client) -> None:
        # WebOverrides is strict: "0" is not accepted where an int belongs.
        status, _ = client.json("POST", "/api/settings", {"rotate": "0"})
        assert status == 400

    def test_post_rejects_an_unknown_field(self, client: Client) -> None:
        status, body = client.json("POST", "/api/settings", {"layout": "week"})
        assert status == 400
        assert "layout" in body["error"]

    def test_reset_removes_a_saved_override(self, client: Client, settings: Settings) -> None:
        client.json("POST", "/api/settings", {"rotate": 180})
        status, body = client.json("POST", "/api/settings/reset")
        assert status == 200
        assert body["values"]["rotate"] == 0
        assert body["sources"]["rotate"] is False


class TestConnect:
    def test_state_without_respx_mock_reports_unreachable(self, client: Client) -> None:
        # No respx router active: httpx hits the real network and fails fast for a
        # .test domain, exactly like test_web.py's identity_line tests expect a failure
        # path without one - here just asserting the shape rather than the wording.
        status, body = client.json("GET", "/api/connect")
        assert status == 200
        assert body["identity"]["connected"] is False
        assert body["identity"]["error"] is not None
        assert body["mode"] == "paste"
        assert body["redirect_uri"] == "http://localhost:8795/connect/callback"
        assert body["client_id"] == "studylife-display"
        assert "Sessions.GetAll" in body["scopes"]
        assert body["pending"] is None

    def test_identity_when_the_key_is_accepted(self, client: Client) -> None:
        with respx.mock(assert_all_called=True) as router:
            router.get(f"{BASE_URL}/api/auth/whoami").mock(
                return_value=httpx.Response(
                    200, json={"userId": 7, "credential": "client:studylife-display"}
                )
            )
            status, body = client.json("GET", "/api/connect")
        assert status == 200
        assert body["identity"] == {
            "connected": True,
            "instance": BASE_URL,
            "user_id": 7,
            "credential": "client:studylife-display",
            "error": None,
        }

    def test_start_returns_a_connect_url_and_shows_up_as_pending(
        self, client: Client, server: DisplayServer
    ) -> None:
        status, body = client.json("POST", "/api/connect/start")
        assert status == 200
        assert body["connect_url"].startswith(f"{BASE_URL}/connect/client/studylife-display?")
        assert body["redirect_uri"] == "http://localhost:8795/connect/callback"
        pending = server.app.pending()
        assert pending is not None
        assert pending.connect_url == body["connect_url"]

        _, connect_body = client.json("GET", "/api/connect")
        assert connect_body["pending"]["connect_url"] == body["connect_url"]

    def test_paste_without_a_pending_attempt_reports_the_outcome(self, client: Client) -> None:
        status, body = client.json(
            "POST", "/api/connect/paste", {"callback_url": "not-a-valid-callback"}
        )
        assert status == 200
        assert body["outcome"] == "missing_assertion"

    def test_paste_redeems_the_pending_attempt(
        self, client: Client, server: DisplayServer, settings: Settings
    ) -> None:
        client.json("POST", "/api/connect/start")
        pending = server.app.pending()
        assert pending is not None
        pasted = f"http://localhost:8795/connect/callback?assertion=A-1&state={pending.state}"
        with respx.mock(assert_all_called=True) as router:
            exchange = router.post(f"{BASE_URL}/api/auth/assertion-exchange").mock(
                return_value=httpx.Response(200, json={"userId": 7, "apiKey": "k-new-123"})
            )
            status, body = client.json("POST", "/api/connect/paste", {"callback_url": pasted})
        assert status == 200
        assert body["outcome"] == "applied"
        assert json.loads(exchange.calls.last.request.content) == {
            "clientId": "studylife-display",
            "assertion": "A-1",
            "codeVerifier": pending.verifier,
        }
        pending_file = Path(settings.display_state_path).parent / "credentials.pending.json"
        assert json.loads(pending_file.read_text(encoding="utf-8"))["apiKey"] == "k-new-123"
