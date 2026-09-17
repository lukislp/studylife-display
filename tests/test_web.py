"""The web interface, end to end against a real ThreadingHTTPServer on 127.0.0.1:0 with the
file driver and a temporary state directory. The StudyLife API is never reachable here: the
fetch is stubbed to fail, so every refresh goes through the cached snapshot exactly like a
Pi with the network down would."""

from __future__ import annotations

import http.client
import io
import json
import threading
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from studylife_display import main as main_module
from studylife_display import package_version
from studylife_display import web as web_module
from studylife_display.config import Settings
from studylife_display.main import main
from studylife_display.snapshot import Snapshot, save_snapshot
from studylife_display.status_store import LastError, Status, save_status
from studylife_display.studylife_client import StudyLifeApiError
from studylife_display.web import SESSION_COOKIE, DisplayServer, make_server

TOKEN = "correct-horse-battery"
BASE_URL = "https://studylife.test"


class Client:
    """A tiny http.client wrapper that keeps the session cookie and never follows
    redirects, so every status code is the server's own."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.cookie: str | None = None

    def request(
        self,
        method: str,
        path: str,
        form: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        with_cookie: bool = True,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        sent = {"Host": f"127.0.0.1:{self.port}"}
        body = None
        if form is not None:
            body = urlencode(form).encode()
            sent["Content-Type"] = "application/x-www-form-urlencoded"
        if with_cookie and self.cookie:
            sent["Cookie"] = self.cookie
        sent.update(headers or {})
        connection.request(method, path, body=body, headers=sent)
        response = connection.getresponse()
        payload = response.read()
        received = {name.lower(): value for name, value in response.getheaders()}
        connection.close()
        return response.status, received, payload

    def login(self, token: str = TOKEN) -> tuple[int, dict[str, str], bytes]:
        status, headers, body = self.request("POST", "/login", {"token": token}, with_cookie=False)
        if "set-cookie" in headers:
            self.cookie = headers["set-cookie"].split(";", 1)[0]
        return status, headers, body

    def same_origin(self) -> dict[str, str]:
        return {"Origin": f"http://127.0.0.1:{self.port}"}


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "studylife_base_url": BASE_URL,
        "studylife_api_key": "k",
        "display_driver": "file",
        "display_output_path": str(tmp_path / "frame.png"),
        "display_state_path": str(tmp_path / "state" / "last.json"),
        "display_web_token": TOKEN,
        "display_layout": "auto",
    }
    values.update(overrides)
    return Settings(**values)


def quiet_hours_around(now: datetime) -> str:
    """A three-hour quiet window that contains `now`, whatever the clock says."""
    return f"{(now.hour - 1) % 24}-{(now.hour + 2) % 24}"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def no_update_check(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Fails the test if the page ever asks GitHub; returns the call log."""
    calls: list[int] = []

    def must_not_call() -> str | None:
        calls.append(1)
        raise AssertionError("the update check reached out although it is off")

    monkeypatch.setattr(web_module, "fetch_latest_tag", must_not_call)
    return calls


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
    metrics, history, timer = sample
    path = Path(settings.display_state_path)
    save_snapshot(path, Snapshot(metrics, history, timer, fixed_now))
    return path


class TestLogin:
    def test_root_without_a_session_shows_the_login_page(self, client: Client) -> None:
        status, headers, body = client.request("GET", "/")
        assert status == 200
        assert "text/html" in headers["content-type"]
        assert b"name='token'" in body
        assert b"type='password'" in body
        assert b"/preview/" not in body

    def test_wrong_token_is_a_403_without_a_cookie(self, client: Client) -> None:
        status, headers, body = client.login("definitely-not-it")
        assert status == 403
        assert "set-cookie" not in headers
        assert client.cookie is None

    def test_right_token_sets_a_hardened_session_cookie(self, client: Client) -> None:
        status, headers, _ = client.login()
        assert status == 303
        assert headers["location"] == "/"
        cookie = headers["set-cookie"]
        assert cookie.startswith(SESSION_COOKIE + "=")
        assert "HttpOnly" in cookie
        assert "SameSite=Strict" in cookie
        assert "Path=/" in cookie
        assert TOKEN not in cookie
        status, _, body = client.request("GET", "/")
        assert status == 200
        assert b"/preview/exam.png" in body
        assert b"name='layout'" in body

    def test_a_forged_cookie_does_not_pass(self, client: Client) -> None:
        client.cookie = f"{SESSION_COOKIE}=0000"
        status, _, body = client.request("GET", "/")
        assert status == 200
        assert b"name='token'" in body


class TestPreviews:
    def test_preview_requires_the_cookie(self, client: Client) -> None:
        status, _, _ = client.request("GET", "/preview/exam.png")
        assert status == 403

    @pytest.mark.parametrize("key", ["auto", "classic", "focus", "exam", "week"])
    def test_preview_is_an_800x480_png(self, client: Client, key: str) -> None:
        client.login()
        status, headers, body = client.request("GET", f"/preview/{key}.png")
        assert status == 200
        assert headers["content-type"] == "image/png"
        with Image.open(io.BytesIO(body)) as image:
            assert image.size == (800, 480)
            assert image.format == "PNG"

    def test_unknown_preview_is_a_404(self, client: Client) -> None:
        client.login()
        assert client.request("GET", "/preview/holographic.png")[0] == 404

    def test_page_says_sample_data_without_a_cache(self, client: Client) -> None:
        client.login()
        _, _, body = client.request("GET", "/")
        assert b"Beispieldaten" in body

    def test_page_shows_the_cache_time_with_a_cache(self, client: Client, cached: Path) -> None:
        client.login()
        _, _, body = client.request("GET", "/")
        assert b"Beispieldaten" not in body
        assert b"17.09. 16:45" in body
        assert b"derzeit: Fokus" in body


class TestActions:
    def test_layout_post_writes_settings_and_refreshes_the_panel(
        self, client: Client, cached: Path, settings: Settings
    ) -> None:
        client.login()
        status, headers, _ = client.request(
            "POST", "/layout", {"layout": "week"}, headers=client.same_origin()
        )
        assert status == 303
        assert headers["location"] == "/?m=saved"
        settings_file = cached.parent / "settings.json"
        assert json.loads(settings_file.read_text(encoding="utf-8")) == {"layout": "week"}
        with Image.open(settings.display_output_path) as image:
            assert image.size == (800, 480)

    def test_refresh_post_only_refreshes(
        self, client: Client, cached: Path, settings: Settings
    ) -> None:
        client.login()
        status, headers, _ = client.request("POST", "/refresh", {}, headers=client.same_origin())
        assert status == 303
        assert headers["location"] == "/?m=refreshed"
        assert not (cached.parent / "settings.json").exists()
        assert Path(settings.display_output_path).exists()

    def test_refresh_without_any_data_reports_failure(
        self, client: Client, settings: Settings
    ) -> None:
        client.login()
        status, headers, _ = client.request("POST", "/refresh", {}, headers=client.same_origin())
        assert status == 303
        assert headers["location"] == "/?m=failed"
        # The "no data" screen went on the panel; the flash still reports the failure.
        assert Path(settings.display_output_path).exists()

    def test_invalid_layout_is_a_400(self, client: Client, cached: Path) -> None:
        client.login()
        status, _, _ = client.request(
            "POST", "/layout", {"layout": "holographic"}, headers=client.same_origin()
        )
        assert status == 400
        assert not (cached.parent / "settings.json").exists()

    def test_post_without_a_cookie_is_a_403(self, client: Client, cached: Path) -> None:
        status, _, _ = client.request(
            "POST", "/layout", {"layout": "week"}, headers=client.same_origin()
        )
        assert status == 403
        assert not (cached.parent / "settings.json").exists()

    def test_cross_site_post_is_a_403(self, client: Client, cached: Path) -> None:
        client.login()
        for headers in (
            {"Origin": "http://evil.example"},
            {"Sec-Fetch-Site": "cross-site"},
            {},
        ):
            status, _, _ = client.request("POST", "/layout", {"layout": "week"}, headers=headers)
            assert status == 403, headers
        status, _, _ = client.request(
            "POST", "/layout", {"layout": "week"}, headers={"Sec-Fetch-Site": "same-origin"}
        )
        assert status == 303

    def test_unknown_paths_are_404(self, client: Client) -> None:
        assert client.request("GET", "/admin")[0] == 404
        client.login()
        assert client.request("GET", "/admin")[0] == 404
        assert client.request("POST", "/admin", {})[0] == 404


class TestServeCommand:
    @pytest.fixture
    def env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("STUDYLIFE_BASE_URL", BASE_URL)
        monkeypatch.setenv("STUDYLIFE_API_KEY", "k")
        monkeypatch.setenv("DISPLAY_DRIVER", "file")
        monkeypatch.setenv("DISPLAY_STATE_PATH", str(tmp_path / "last.json"))

        def must_not_bind(*args: Any, **kwargs: Any) -> int:
            raise AssertionError("serve bound a socket without a valid token")

        monkeypatch.setattr(main_module, "serve_web", must_not_bind)

    @pytest.mark.parametrize("token", ["", "short"])
    def test_serve_refuses_a_missing_or_short_token(
        self, env: None, monkeypatch: pytest.MonkeyPatch, token: str
    ) -> None:
        monkeypatch.setenv("DISPLAY_WEB_TOKEN", token)
        assert main(["serve"]) != 0

    def test_serve_starts_with_a_proper_token(
        self, env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DISPLAY_WEB_TOKEN", TOKEN)
        monkeypatch.setattr(main_module, "serve_web", lambda settings, refresh: 0)
        assert main(["serve"]) == 0


def fresh_cache(settings: Settings, sample: Any, tz: ZoneInfo, ok: bool = True) -> datetime:
    """A snapshot fetched just now plus a matching status.json; returns the moment."""
    now = datetime.now(tz)
    metrics, history, timer = sample
    path = Path(settings.display_state_path)
    save_snapshot(path, Snapshot(metrics, history, timer, now))
    save_status(path.parent, Status(last_fetch_ok=ok, last_fetch_at=now, last_panel_update_at=now))
    return now


class TestHealth:
    def test_no_data_ever_is_a_503_without_a_cookie(self, client: Client) -> None:
        status, headers, body = client.request("GET", "/healthz")
        assert status == 503
        assert headers["content-type"] == "application/json"
        report = json.loads(body)
        assert report["status"] == "error"
        assert report["version"] == package_version()
        assert report["last_fetch_at"] is None
        assert report["last_fetch_ok"] is False
        assert report["stale_minutes"] is None
        assert report["last_error"] is None
        assert report["last_panel_update_at"] is None
        assert report["layout"] is None
        assert report["quiet_hours_active"] is False

    def test_never_leaks_the_token_or_the_cookie(self, client: Client) -> None:
        client.login()
        _, _, body = client.request("GET", "/healthz")
        assert TOKEN.encode() not in body
        assert client.cookie is not None
        assert client.cookie.split("=", 1)[1].encode() not in body

    def test_fresh_cache_is_ok(
        self, client: Client, settings: Settings, sample: Any, tz: ZoneInfo
    ) -> None:
        now = fresh_cache(settings, sample, tz)
        status, _, body = client.request("GET", "/healthz")
        assert status == 200
        report = json.loads(body)
        assert report["status"] == "ok"
        assert report["last_fetch_ok"] is True
        assert report["last_fetch_at"] == now.isoformat()
        assert report["last_panel_update_at"] == now.isoformat()
        assert report["stale_minutes"] == 0
        assert report["layout"] in {"classic", "focus", "exam", "week"}

    def test_failed_fetch_with_a_cache_is_degraded(
        self, client: Client, settings: Settings, sample: Any, tz: ZoneInfo
    ) -> None:
        now = fresh_cache(settings, sample, tz, ok=False)
        state_dir = Path(settings.display_state_path).parent
        save_status(
            state_dir,
            Status(
                last_fetch_ok=False,
                last_fetch_at=now,
                last_error=LastError("transient", 503, "StudyLife API returned 503", now),
            ),
        )
        status, _, body = client.request("GET", "/healthz")
        assert status == 200
        report = json.loads(body)
        assert report["status"] == "degraded"
        assert report["last_error"] == {
            "kind": "transient",
            "status": 503,
            "message": "StudyLife API returned 503",
            "at": now.isoformat(),
        }

    def test_rejected_key_is_an_error_even_with_a_cache(
        self, client: Client, settings: Settings, sample: Any, tz: ZoneInfo
    ) -> None:
        now = fresh_cache(settings, sample, tz, ok=False)
        save_status(
            Path(settings.display_state_path).parent,
            Status(
                last_fetch_ok=False,
                last_error=LastError("rejected", 403, "StudyLife API returned 403", now),
            ),
        )
        status, _, body = client.request("GET", "/healthz")
        assert status == 503
        report = json.loads(body)
        assert report["status"] == "error"
        assert report["last_error"]["kind"] == "rejected"
        assert report["last_error"]["status"] == 403

    def test_a_silent_timer_is_degraded_unless_quiet_hours_explain_it(
        self, tmp_path: Path, offline: None, sample: Any, tz: ZoneInfo
    ) -> None:
        for quiet, expected in ((False, "degraded"), (True, "ok")):
            now = datetime.now(tz)
            overrides = {"display_quiet_hours": quiet_hours_around(now)} if quiet else {}
            settings = make_settings(tmp_path / expected, **overrides)
            metrics, history, timer = sample
            path = Path(settings.display_state_path)
            old = now - timedelta(minutes=40)
            save_snapshot(path, Snapshot(metrics, history, timer, old))
            save_status(path.parent, Status(last_fetch_ok=True, last_fetch_at=old))
            report, status = web_module.health_report(settings, now)
            assert report["status"] == expected, quiet
            assert status == 200
            assert report["stale_minutes"] >= 40
            assert report["quiet_hours_active"] is quiet

    def test_head_and_post(self, client: Client) -> None:
        status, headers, body = client.request("HEAD", "/healthz")
        assert status == 503
        assert body == b""
        assert headers["content-type"] == "application/json"
        assert client.request("POST", "/healthz", {})[0] == 404


class TestFooterAndNotes:
    def test_footer_shows_the_version_without_asking_github(
        self, client: Client, no_update_check: list[int]
    ) -> None:
        client.login()
        _, _, body = client.request("GET", "/")
        assert f"Version {package_version()}".encode() in body
        assert b"Neue Version" not in body
        assert no_update_check == []

    def test_last_error_is_shown_on_the_page(
        self, client: Client, settings: Settings, tz: ZoneInfo, fixed_now: datetime
    ) -> None:
        save_status(
            Path(settings.display_state_path).parent,
            Status(last_error=LastError("rejected", 403, "StudyLife API returned 403", fixed_now)),
        )
        client.login()
        _, _, body = client.request("GET", "/")
        assert b"Letzter Fehler (17.09. 16:45): StudyLife API returned 403" in body

    def test_no_quiet_note_outside_quiet_hours(self, client: Client) -> None:
        client.login()
        _, _, body = client.request("GET", "/")
        assert b"Ruhezeit" not in body


class TestUpdateCheck:
    @pytest.fixture
    def settings(self, tmp_path: Path) -> Settings:
        return make_settings(tmp_path, display_update_check=True)

    def test_newer_release_is_announced_and_cached(
        self, client: Client, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[int] = []

        def fetch() -> str | None:
            calls.append(1)
            return "v99.0.0"

        monkeypatch.setattr(web_module, "fetch_latest_tag", fetch)
        client.login()
        _, _, body = client.request("GET", "/")
        assert "Neue Version verfügbar: v99.0.0".encode() in body
        _, _, body = client.request("GET", "/")
        assert "Neue Version verfügbar: v99.0.0".encode() in body
        assert len(calls) == 1  # the second page view came from the cache
        cache = Path(settings.display_state_path).parent / "update_check.json"
        assert json.loads(cache.read_text(encoding="utf-8"))["latest"] == "v99.0.0"

    def test_current_or_failed_check_shows_the_version_alone(
        self, client: Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(web_module, "fetch_latest_tag", lambda: None)
        client.login()
        _, _, body = client.request("GET", "/")
        assert f"Version {package_version()}".encode() in body
        assert b"Neue Version" not in body


class TestQuietHoursPage:
    @pytest.fixture
    def settings(self, tmp_path: Path, tz: ZoneInfo) -> Settings:
        return make_settings(tmp_path, display_quiet_hours=quiet_hours_around(datetime.now(tz)))

    def test_page_says_quiet_until(self, client: Client, settings: Settings, tz: ZoneInfo) -> None:
        client.login()
        _, _, body = client.request("GET", "/")
        end = (datetime.now(tz).replace(minute=0) + timedelta(hours=2)).strftime("%H:%M")
        assert f"Ruhezeit bis {end}".encode() in body

    def test_refresh_still_works_inside_quiet_hours(
        self, client: Client, cached: Path, settings: Settings
    ) -> None:
        client.login()
        status, headers, _ = client.request("POST", "/refresh", {}, headers=client.same_origin())
        assert status == 303
        assert headers["location"] == "/?m=refreshed"
        assert Path(settings.display_output_path).exists()

    def test_healthz_reports_quiet_hours(self, client: Client) -> None:
        _, _, body = client.request("GET", "/healthz")
        assert json.loads(body)["quiet_hours_active"] is True
