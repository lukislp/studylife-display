"""The web interface, end to end against a real ThreadingHTTPServer on 127.0.0.1:0 with the
file driver and a temporary state directory. The StudyLife API is never reachable here: the
fetch is stubbed to fail, so every refresh goes through the cached snapshot exactly like a
Pi with the network down would."""

from __future__ import annotations

import base64
import hashlib
import http.client
import io
import json
import re
import ssl
import subprocess
import threading
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from PIL import Image, ImageChops

from studylife_display import main as main_module
from studylife_display import package_version
from studylife_display import web as web_module
from studylife_display.config import Settings
from studylife_display.main import main
from studylife_display.model import DashboardData
from studylife_display.settings_store import effective_settings
from studylife_display.snapshot import Snapshot, save_snapshot
from studylife_display.status_store import LastError, Status, save_status
from studylife_display.studylife_client import StudyLifeApiError
from studylife_display.web import SESSION_COOKIE, DisplayServer, health_report, make_server

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
        # The pages resolve "auto" at the real clock; without this the layout the tests
        # expect ("focus", the sample's running timer) would flip to "review" on Sunday
        # evenings. The review rule itself is covered in test_auto.py.
        "display_auto_review": "",
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
    metrics, history, timer, sessions = sample
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

    @pytest.mark.parametrize(
        "key",
        [
            "auto",
            "classic",
            "focus",
            "exam",
            "week",
            "semester",
            "agenda",
            "review",
            "courses",
            "milestone",
        ],
    )
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

    def test_referrer_policy_keeps_same_origin_headers_intact(self, client: Client) -> None:
        """A "no-referrer" policy makes real browsers send Origin: null on the very same-origin
        form POSTs test_cross_site_post_is_a_403 checks above, which failed on real hardware in
        Chrome even though this suite's Client always sets a real Origin. "same-origin" still
        keeps the page's URL from leaking to StudyLife."""
        client.login()
        _, headers, _ = client.request("GET", "/")
        assert headers["referrer-policy"] == "same-origin"

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

    def test_serve_refuses_tls_without_certificate_files(
        self, env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DISPLAY_WEB_TOKEN", TOKEN)
        monkeypatch.setenv("DISPLAY_TLS", "true")
        monkeypatch.setattr(main_module, "TLS_CERT_PATH", str(tmp_path / "missing.pem"))
        monkeypatch.setattr(main_module, "TLS_KEY_PATH", str(tmp_path / "missing.key"))
        assert main(["serve"]) != 0


class TestTls:
    @pytest.fixture
    def cert_and_key(self, tmp_path: Path) -> tuple[Path, Path]:
        cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "ec",
                "-pkeyopt",
                "ec_paramgen_curve:prime256v1",
                "-nodes",
                "-days",
                "1",
                "-subj",
                "/CN=test.local",
                "-keyout",
                str(key),
                "-out",
                str(cert),
            ],
            check=True,
            capture_output=True,
        )
        return cert, key

    def test_make_server_wraps_the_socket_in_tls_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cert_and_key: tuple[Path, Path]
    ) -> None:
        cert, key = cert_and_key
        monkeypatch.setattr(web_module, "TLS_CERT_PATH", str(cert))
        monkeypatch.setattr(web_module, "TLS_KEY_PATH", str(key))
        settings = make_settings(tmp_path, display_tls=True)
        server = web_module.make_server(settings, lambda: 0, bind="127.0.0.1:0")
        try:
            assert isinstance(server.socket, ssl.SSLSocket)
        finally:
            server.server_close()

    def test_make_server_leaves_a_plain_socket_when_disabled(
        self, tmp_path: Path, cert_and_key: tuple[Path, Path]
    ) -> None:
        settings = make_settings(tmp_path, display_tls=False)
        server = web_module.make_server(settings, lambda: 0, bind="127.0.0.1:0")
        try:
            assert not isinstance(server.socket, ssl.SSLSocket)
        finally:
            server.server_close()


def fresh_cache(settings: Settings, sample: Any, tz: ZoneInfo, ok: bool = True) -> datetime:
    """A snapshot fetched just now plus a matching status.json; returns the moment."""
    now = datetime.now(tz)
    metrics, history, timer, sessions = sample
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
        assert report["setup"] is False

    def test_no_key_at_all_is_setup_with_a_200(self, tmp_path: Path, tz: ZoneInfo) -> None:
        no_key = make_settings(tmp_path / "nokey", studylife_api_key="")
        report, status = health_report(no_key, datetime.now(tz))
        assert status == 200
        assert report["status"] == "setup"
        assert report["setup"] is True
        assert report["last_error"] is None
        # A key that is there but rejected is still an error, not "setup".
        rejected = make_settings(tmp_path / "rejected")
        state_dir = Path(rejected.display_state_path).parent
        save_status(
            state_dir,
            Status(
                last_fetch_ok=False,
                last_error=LastError("rejected", 403, "no scope", datetime.now(tz)),
            ),
        )
        report, status = health_report(rejected, datetime.now(tz))
        assert (status, report["status"], report["setup"]) == (503, "error", False)

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
        assert report["layout"] in {"classic", "focus", "exam", "week", "agenda"}
        assert report["sessions_ok"] is True

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
            metrics, history, timer, sessions = sample
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


# --- connecting the account ---------------------------------------------------------------


def connect_url_from(body: bytes) -> str:
    match = re.search(r"href='(https://studylife\.test/connect/client/[^']+)'", body.decode())
    assert match is not None, "no connect link on the page"
    return match.group(1).replace("&amp;", "&")


class TestConnectPage:
    def test_requires_the_cookie(self, client: Client) -> None:
        assert client.request("GET", "/connect")[0] == 403
        assert client.request("POST", "/connect/start", {}, headers=client.same_origin())[0] == 403

    def test_shows_the_identity_behind_the_stored_key(self, client: Client) -> None:
        client.login()
        with respx.mock(assert_all_called=True) as router:
            router.get(f"{BASE_URL}/api/auth/whoami").mock(
                return_value=httpx.Response(
                    200, json={"userId": 7, "credential": "client:studylife-display"}
                )
            )
            _, _, body = client.request("GET", "/connect")
        assert b"Benutzer-ID 7" in body
        assert b"client:studylife-display" in body
        assert b"Verbindung starten" in body
        assert b"http://localhost:8795/connect/callback" in body

    def test_says_when_the_key_is_rejected_or_missing(
        self, client: Client, tmp_path: Path, offline: None
    ) -> None:
        client.login()
        with respx.mock() as router:
            router.get(f"{BASE_URL}/api/auth/whoami").mock(return_value=httpx.Response(403))
            _, _, body = client.request("GET", "/connect")
        assert b"lehnt den hinterlegten Schl" in body
        no_key = make_settings(tmp_path / "nokey", studylife_api_key="")
        page = web_module.WebApp(no_key, lambda: 0).connect_page()
        assert b"Noch kein Schl" in page

    def test_start_builds_the_exact_connect_url(
        self, client: Client, server: DisplayServer
    ) -> None:
        client.login()
        status, headers, _ = client.request(
            "POST", "/connect/start", {}, headers=client.same_origin()
        )
        assert status == 303
        assert headers["location"] == "/connect?m=started"
        with respx.mock() as router:
            router.get(f"{BASE_URL}/api/auth/whoami").mock(
                return_value=httpx.Response(200, json={})
            )
            _, _, body = client.request("GET", "/connect")
        url = connect_url_from(body)
        parts = urlsplit(url)
        assert parts.path == "/connect/client/studylife-display"
        query = parse_qs(parts.query)
        assert set(query) == {"redirect_uri", "state", "code_challenge", "code_challenge_method"}
        assert query["redirect_uri"] == ["http://localhost:8795/connect/callback"]
        assert query["code_challenge_method"] == ["S256"]
        pending = server.app.pending()
        assert pending is not None
        assert query["state"] == [pending.state]
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(pending.verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        assert query["code_challenge"] == [expected]
        assert pending.verifier not in url
        assert b"Link g" in body and b"Adresse aus der Adresszeile" in body

    def test_pasted_url_is_exchanged_and_the_key_never_reaches_the_browser(
        self, client: Client, server: DisplayServer, settings: Settings
    ) -> None:
        client.login()
        client.request("POST", "/connect/start", {}, headers=client.same_origin())
        pending = server.app.pending()
        assert pending is not None
        pasted = f"http://localhost:8795/connect/callback?assertion=A-1&state={pending.state}"
        with respx.mock(assert_all_called=True) as router:
            exchange = router.post(f"{BASE_URL}/api/auth/assertion-exchange").mock(
                return_value=httpx.Response(200, json={"userId": 7, "apiKey": "k-new-123"})
            )
            status, headers, body = client.request(
                "POST", "/connect/paste", {"callback_url": pasted}, headers=client.same_origin()
            )
        assert status == 303
        assert headers["location"] == "/connect?m=applied"
        assert json.loads(exchange.calls.last.request.content) == {
            "clientId": "studylife-display",
            "assertion": "A-1",
            "codeVerifier": pending.verifier,
        }
        pending_file = Path(settings.display_state_path).parent / "credentials.pending.json"
        assert json.loads(pending_file.read_text(encoding="utf-8"))["apiKey"] == "k-new-123"
        with respx.mock() as router:
            router.get(f"{BASE_URL}/api/auth/whoami").mock(
                return_value=httpx.Response(200, json={})
            )
            _, _, body = client.request("GET", "/connect?m=applied")
        assert "Schlüssel übernommen".encode() in body
        assert b"k-new-123" not in body
        assert server.app.pending() is None  # single use

    @pytest.mark.parametrize(
        ("pasted", "flash"),
        [
            ("http://localhost:8795/connect/callback?assertion=A&state=wrong", "state_mismatch"),
            ("http://localhost:8795/connect/callback?state=STATE", "missing_assertion"),
        ],
    )
    def test_bad_pastes(
        self, client: Client, server: DisplayServer, settings: Settings, pasted: str, flash: str
    ) -> None:
        client.login()
        client.request("POST", "/connect/start", {}, headers=client.same_origin())
        pending = server.app.pending()
        assert pending is not None
        pasted = pasted.replace("STATE", pending.state)
        with respx.mock(assert_all_called=False) as router:
            router.post(f"{BASE_URL}/api/auth/assertion-exchange").mock(
                return_value=httpx.Response(200, json={"userId": 7, "apiKey": "k"})
            )
            _, headers, _ = client.request(
                "POST", "/connect/paste", {"callback_url": pasted}, headers=client.same_origin()
            )
            assert not router.calls
        assert headers["location"] == f"/connect?m={flash}"
        assert not (Path(settings.display_state_path).parent / "credentials.pending.json").exists()

    def test_expired_and_absent_attempts(self, client: Client, server: DisplayServer) -> None:
        client.login()
        _, headers, _ = client.request(
            "POST",
            "/connect/paste",
            {"callback_url": "assertion=A&state=S"},
            headers=client.same_origin(),
        )
        assert headers["location"] == "/connect?m=no_pending"
        client.request("POST", "/connect/start", {}, headers=client.same_origin())
        pending = server.app.pending()
        assert pending is not None
        server.app._pending = replace(
            pending, created_at=pending.created_at - timedelta(minutes=11)
        )
        pasted = f"assertion=A&state={pending.state}"
        _, headers, _ = client.request(
            "POST", "/connect/paste", {"callback_url": pasted}, headers=client.same_origin()
        )
        assert headers["location"] == "/connect?m=expired"

    def test_redirect_mode_callback_needs_no_cookie(
        self, tmp_path: Path, offline: None, sample: Any
    ) -> None:
        settings = make_settings(tmp_path, display_public_base_url="https://pi.tail.ts.net")
        server = make_server(settings, lambda: 0, "127.0.0.1:0")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = Client(server.server_address[1])
            client.login()
            client.request("POST", "/connect/start", {}, headers=client.same_origin())
            with respx.mock() as router:
                router.get(f"{BASE_URL}/api/auth/whoami").mock(
                    return_value=httpx.Response(200, json={})
                )
                _, _, body = client.request("GET", "/connect")
            url = connect_url_from(body)
            query = parse_qs(urlsplit(url).query)
            assert query["redirect_uri"] == ["https://pi.tail.ts.net/connect/callback"]
            assert b"Adresse aus der Adresszeile" not in body
            state = query["state"][0]
            with respx.mock(assert_all_called=True) as router:
                router.post(f"{BASE_URL}/api/auth/assertion-exchange").mock(
                    return_value=httpx.Response(200, json={"userId": 7, "apiKey": "k-cb"})
                )
                status, _, body = client.request(
                    "GET", f"/connect/callback?assertion=B&state={state}", with_cookie=False
                )
            assert status == 200
            assert "Schlüssel übernommen".encode() in body
            assert b"k-cb" not in body
            pending_file = Path(settings.display_state_path).parent / "credentials.pending.json"
            assert json.loads(pending_file.read_text(encoding="utf-8"))["apiKey"] == "k-cb"
            status, _, body = client.request(
                "GET", "/connect/callback?assertion=B&state=nope", with_cookie=False
            )
            assert status == 400
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_overlay_warning(self, client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(web_module, "root_is_overlay", lambda: True)
        client.login()
        with respx.mock() as router:
            router.get(f"{BASE_URL}/api/auth/whoami").mock(
                return_value=httpx.Response(200, json={})
            )
            _, _, body = client.request("GET", "/connect")
        assert b"Overlay" in body
        assert b"#sd-card-protection" in body


# --- the settings page --------------------------------------------------------------------


class TestSettingsPage:
    def test_requires_the_cookie(self, client: Client) -> None:
        assert client.request("GET", "/settings")[0] == 403
        assert client.request("POST", "/settings", {}, headers=client.same_origin())[0] == 403

    def test_shows_the_environment_values_and_their_source(self, client: Client) -> None:
        client.login()
        _, _, body = client.request("GET", "/settings")
        assert b"<option value='de' selected>" in body
        assert b"<option value='0' selected>" in body
        assert b"value='04:00'" in body
        assert body.count(b"aus Umgebung/Standard") == 7
        assert b"studylife-display.timer" in body
        assert b"STUDYLIFE_BASE_URL" in body and BASE_URL.encode() in body
        assert b"STUDYLIFE_API_KEY" in body and b"gesetzt" in body
        assert TOKEN.encode() not in body
        assert b">k<" not in body

    def test_round_trip_and_precedence(
        self, client: Client, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The form turns the update check on; the footer then asks (a stub, here).
        monkeypatch.setattr(web_module, "fetch_latest_tag", lambda: None)
        client.login()
        form = {
            "language": "en",
            "rotate": "180",
            "quiet_hours": "23-7",
            "clear_at": "05:30",
            "update_check": "on",
            "auto_review": "sat,sun 19-23",
            "auto_agenda": "",
        }
        status, headers, _ = client.request("POST", "/settings", form, headers=client.same_origin())
        assert status == 303
        assert headers["location"] == "/settings?m=saved"
        settings_file = Path(settings.display_state_path).parent / "settings.json"
        assert json.loads(settings_file.read_text(encoding="utf-8")) == {
            "language": "en",
            "rotate": 180,
            "quiet_hours": "23-7",
            "clear_at": "05:30",
            "update_check": True,
            "auto_review": "sat,sun 19-23",
            "auto_agenda": "",
        }
        effective = effective_settings(settings)
        assert effective.display_language == "en"
        assert effective.display_rotate == 180
        assert effective.display_quiet_hours == "23-7"
        assert effective.display_clear_at == "05:30"
        assert effective.display_update_check is True
        assert effective.display_auto_review == "sat,sun 19-23"
        assert effective.display_auto_agenda == ""
        assert settings.display_language == "de"  # the environment object is untouched
        _, _, body = client.request("GET", "/settings?m=saved")
        assert b"Settings saved." in body
        assert body.count(b"from settings.json") == 7
        assert b"value='sat,sun 19-23'" in body
        assert b"<option value='180' selected>" in body
        _, _, body = client.request("GET", "/")
        assert b"Sign out" in body  # the layouts page follows the new language at once
        assert b"inside the window sat,sun 19-23" in body  # the auto hint shows the windows
        assert b"inside the window off" in body

        # The layout choice shares the file and survives the settings write.
        client.request("POST", "/layout", {"layout": "week"}, headers=client.same_origin())
        assert json.loads(settings_file.read_text(encoding="utf-8"))["layout"] == "week"
        assert json.loads(settings_file.read_text(encoding="utf-8"))["language"] == "en"

        # Reset: the environment values apply again, the layout choice stays.
        _, headers, _ = client.request("POST", "/settings/reset", {}, headers=client.same_origin())
        assert headers["location"] == "/settings?m=reset"
        assert json.loads(settings_file.read_text(encoding="utf-8")) == {"layout": "week"}
        assert effective_settings(settings).display_language == "de"

    def test_validation_errors_are_shown_inline_and_nothing_is_written(
        self, client: Client, settings: Settings
    ) -> None:
        client.login()
        form = {
            "language": "de",
            "rotate": "90",
            "quiet_hours": "night",
            "clear_at": "25:00",
            "auto_review": "sun 23-7",
            "auto_agenda": "06-12",
        }
        status, _, body = client.request("POST", "/settings", form, headers=client.same_origin())
        assert status == 400
        text = body.decode()
        assert "Bitte die markierten Felder korrigieren" in text
        assert text.count("class='error'") == 4
        assert "value='night'" in text and "value='25:00'" in text  # what was typed stays
        assert "value='sun 23-7'" in text
        assert not (Path(settings.display_state_path).parent / "settings.json").exists()
        form["rotate"] = "upside-down"
        status, _, _ = client.request("POST", "/settings", form, headers=client.same_origin())
        assert status == 400

    def test_run_honours_a_rotation_changed_in_the_web_interface(
        self,
        client: Client,
        settings: Settings,
        cached: Path,
        fixed_now: datetime,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("STUDYLIFE_BASE_URL", BASE_URL)
        monkeypatch.setenv("STUDYLIFE_API_KEY", "k")
        monkeypatch.setenv("DISPLAY_DRIVER", "file")
        monkeypatch.setenv("DISPLAY_OUTPUT_PATH", settings.display_output_path)
        monkeypatch.setenv("DISPLAY_STATE_PATH", settings.display_state_path)
        monkeypatch.setenv("DISPLAY_CLEAR_AT", "")
        monkeypatch.setenv("DISPLAY_LAYOUT", "classic")
        monkeypatch.delenv("DISPLAY_ROTATE", raising=False)
        seen: list[DashboardData] = []
        real_render = main_module.render

        def spy(data: DashboardData, language: str, layout: str = "classic") -> Image.Image:
            seen.append(data)
            return real_render(data, language, layout)

        monkeypatch.setattr(main_module, "render", spy)

        # run() compares the cached snapshot's age against real wall-clock time
        # (datetime.now(tz)), not the frozen `fixed_now` the cached fixture stamps the
        # snapshot with - left unpatched, that gap grows every day this suite runs after
        # FIXED_NOW and eventually crosses display_stale_error_hours, diverting to the
        # stale-error screen instead of the normal render path this test exercises (seen
        # then stays empty and seen[-1] below raises IndexError). Freeze it a few minutes
        # after fixed_now, matching the cached fixture's own timestamp.
        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz: Any = None) -> datetime:
                return fixed_now + timedelta(minutes=5)

        monkeypatch.setattr(main_module, "datetime", _FrozenDatetime)

        client.login()
        form = {"language": "de", "rotate": "180", "quiet_hours": "", "clear_at": ""}
        client.request("POST", "/settings", form, headers=client.same_origin())
        assert main(["run"]) == 0  # the fetch fails (offline), the cached snapshot is drawn
        upright = main_module.render(seen[-1], "de", "classic")
        with Image.open(settings.display_output_path) as frame:
            shown = frame.convert("1")
        assert (
            ImageChops.difference(shown, upright.transpose(Image.Transpose.ROTATE_180)).getbbox()
            is None
        )
        assert ImageChops.difference(shown, upright).getbbox() is not None


class TestCurrentFrame:
    """The frame on the panel, at the top of the layouts page and at /current.png."""

    def test_requires_the_cookie(self, client: Client) -> None:
        assert client.request("GET", "/current.png")[0] == 403

    def test_placeholder_and_404_before_the_first_frame(self, client: Client) -> None:
        client.login()
        assert client.request("GET", "/current.png")[0] == 404
        _, _, body = client.request("GET", "/")
        assert b"Aktuell auf dem Panel" in body
        assert b"Noch kein Bild auf dem Panel gezeigt." in body
        assert b"/current.png" not in body

    def test_a_refresh_puts_the_frame_on_the_page(
        self, client: Client, settings: Settings, no_update_check: list[int]
    ) -> None:
        client.login()
        # Offline and without a cache: the "no data" screen goes on the panel.
        status, _, _ = client.request("POST", "/refresh", {}, headers=client.same_origin())
        assert status == 303
        status, headers, body = client.request("GET", "/current.png")
        assert status == 200
        assert headers["content-type"] == "image/png"
        assert headers["cache-control"] == "no-store"
        with Image.open(io.BytesIO(body)) as image:
            assert image.size == (800, 480)
        state_dir = Path(settings.display_state_path).parent
        assert body == (state_dir / "current.png").read_bytes()
        _, _, page = client.request("GET", "/")
        assert b"Aktuell auf dem Panel" in page
        assert b"Gezeigt seit" in page
        assert b"Fehlerbildschirm" in page
        assert re.search(rb"<img src='/current\.png\?t=\d+'", page) is not None
        assert b"Noch kein Bild auf dem Panel gezeigt." not in page

    def test_a_dashboard_frame_names_its_layout(
        self,
        client: Client,
        settings: Settings,
        sample: Any,
        tz: ZoneInfo,
        no_update_check: list[int],
    ) -> None:
        # A snapshot stamped now (not the frozen fixture date, which ages past the stale
        # limit), so the offline refresh draws the cached dashboard, not the stale screen.
        metrics, history, timer, sessions = sample
        save_snapshot(
            Path(settings.display_state_path), Snapshot(metrics, history, timer, datetime.now(tz))
        )
        client.login()
        form = {"layout": "week"}
        assert client.request("POST", "/layout", form, headers=client.same_origin())[0] == 303
        _, _, page = client.request("GET", "/")
        assert b"Gezeigt seit" in page
        assert b"\xc2\xb7 Woche" in page  # "· Woche": the layout's display name
        assert client.request("GET", "/current.png")[0] == 200


class TestSetupHint:
    def test_the_layouts_page_says_where_to_connect_while_no_key_is_stored(
        self, tmp_path: Path, no_update_check: list[int]
    ) -> None:
        no_key = make_settings(
            tmp_path / "nokey",
            studylife_api_key="",
            display_setup_url="http://pi.local:8795/connect",
        )
        page = web_module.WebApp(no_key, lambda: 0).layouts_page()
        assert "Noch kein Schlüssel hinterlegt – Konto verbinden unter: ".encode() in page
        assert b"<a href='http://pi.local:8795/connect'>http://pi.local:8795/connect</a>" in page

    def test_the_hint_is_gone_once_a_key_is_stored(
        self, settings: Settings, no_update_check: list[int]
    ) -> None:
        page = web_module.WebApp(settings, lambda: 0).layouts_page()
        assert b"Konto verbinden unter" not in page

    def test_the_settings_page_lists_the_setup_url(self, client: Client) -> None:
        client.login()
        _, _, body = client.request("GET", "/settings")
        assert b"DISPLAY_SETUP_URL" in body
