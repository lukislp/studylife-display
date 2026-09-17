"""The web interface: one page to pick a layout and refresh the panel, from a phone on the
same network. Standard library only (`http.server`), inline CSS, no external resources.

Security model, in one paragraph: the person installing chooses DISPLAY_WEB_TOKEN; `serve`
refuses to start without one of at least MIN_TOKEN_LENGTH characters. The login form posts
the token, which is compared in constant time; a wrong one costs a second and a 403. A right
one sets an HttpOnly, SameSite=Strict session cookie whose value is an HMAC of the token
under a key drawn at process start, so cookies die with the process and the token itself is
never stored in the browser. State-changing POSTs additionally require a same-origin
`Sec-Fetch-Site` or `Origin`. Nothing on this path ever calls the StudyLife API: previews
are drawn from the cached payloads (or sample data), and a refresh runs the same pipeline
the five-minute timer uses, under the panel lock.
"""

from __future__ import annotations

import hmac
import html
import io
import logging
import secrets
import time
from collections.abc import Callable
from datetime import datetime
from hashlib import sha256
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from studylife_display.config import Settings
from studylife_display.layouts import AUTO, LAYOUTS
from studylife_display.layouts.auto import EXAM_SOON_DAYS, resolve_layout
from studylife_display.layouts.common import TEXT
from studylife_display.model import DashboardData, build_dashboard
from studylife_display.render import render
from studylife_display.sample import sample_payloads
from studylife_display.settings_store import (
    is_valid_choice,
    load_layout_choice,
    save_layout_choice,
    valid_choices,
)
from studylife_display.snapshot import load_snapshot
from studylife_display.times import zone

log = logging.getLogger(__name__)

MIN_TOKEN_LENGTH = 12
SESSION_COOKIE = "studylife_display_session"
WRONG_TOKEN_DELAY_SECONDS = 1.0
MAX_BODY_BYTES = 64 * 1024

WEB_TEXT: dict[str, dict[str, str]] = {
    "de": {
        "title": "StudyLife Display",
        "login_heading": "Anmelden",
        "token_label": "Zugriffstoken",
        "login_button": "Anmelden",
        "wrong_token": "Falsches Zugriffstoken.",
        "forbidden": "Nicht erlaubt.",
        "not_found": "Nicht gefunden.",
        "bad_request": "Ungültige Anfrage.",
        "layouts_heading": "Layout",
        "apply": "Übernehmen",
        "refresh": "Jetzt aktualisieren",
        "auto_name": "Automatisch",
        "auto_description": "Wählt bei jeder Aktualisierung das passende Layout.",
        "auto_rules": (
            "Automatisch heißt: Prüfung, wenn die nächste Prüfung in höchstens {days} Tagen "
            "ansteht; sonst Fokus, solange ein Timer läuft; sonst Klassisch."
        ),
        "auto_now": "derzeit: {layout}",
        "last_updated": "Panel zuletzt aktualisiert: {time}",
        "never_updated": "Panel noch nie aktualisiert (kein Zwischenspeicher).",
        "sample_note": (
            "Die Vorschauen zeigen Beispieldaten, weil noch kein Zwischenspeicher vorhanden ist."
        ),
        "full_refresh_note": "Ein Layoutwechsel ist eine vollständige Aktualisierung des Panels.",
        "flash_saved": "Layout gespeichert und Panel aktualisiert.",
        "flash_refreshed": "Panel aktualisiert.",
        "flash_failed": "Aktualisierung fehlgeschlagen, siehe Protokoll.",
        "flash_busy": "Das Panel ist gerade beschäftigt, bitte gleich noch einmal versuchen.",
        "logout": "Abmelden",
    },
    "en": {
        "title": "StudyLife Display",
        "login_heading": "Sign in",
        "token_label": "Access token",
        "login_button": "Sign in",
        "wrong_token": "Wrong access token.",
        "forbidden": "Not allowed.",
        "not_found": "Not found.",
        "bad_request": "Bad request.",
        "layouts_heading": "Layout",
        "apply": "Apply",
        "refresh": "Refresh now",
        "auto_name": "Automatic",
        "auto_description": "Picks the fitting layout on every refresh.",
        "auto_rules": (
            "Automatic means: exam when the next exam is at most {days} days away; otherwise "
            "focus while a timer runs; otherwise classic."
        ),
        "auto_now": "currently: {layout}",
        "last_updated": "Panel last updated: {time}",
        "never_updated": "Panel never updated yet (no cache).",
        "sample_note": "The previews show sample data because there is no cache yet.",
        "full_refresh_note": "Switching layouts is a full refresh of the panel.",
        "flash_saved": "Layout saved and panel refreshed.",
        "flash_refreshed": "Panel refreshed.",
        "flash_failed": "Refresh failed, see the log.",
        "flash_busy": "The panel is busy right now, please try again in a moment.",
        "logout": "Sign out",
    },
}

FLASH_KEYS = {"saved", "refreshed", "failed", "busy"}

STYLE = """
:root { color-scheme: light dark; }
body { font-family: system-ui, sans-serif; margin: 0; padding: 16px; max-width: 1100px;
  margin-inline: auto; line-height: 1.4; }
h1 { font-size: 1.4rem; margin: 0 0 12px; }
p.note { opacity: .8; font-size: .95rem; }
p.flash { border: 1px solid currentColor; border-radius: 8px; padding: 8px 12px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px; margin: 16px 0; }
.card { border: 1px solid #8884; border-radius: 12px; padding: 12px; display: block; }
.card img { width: 100%; height: auto; border: 1px solid #8886; background: #fff;
  border-radius: 4px; }
.card b { font-size: 1.05rem; }
.card small { display: block; opacity: .8; margin: 4px 0 8px; }
.actions { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
button { font: inherit; padding: 10px 18px; border-radius: 8px; border: 1px solid #8888;
  cursor: pointer; }
button.primary { font-weight: 600; }
form.login { max-width: 360px; }
input[type=password] { font: inherit; width: 100%; box-sizing: border-box; padding: 10px;
  border-radius: 8px; border: 1px solid #8888; margin: 6px 0 12px; }
"""


def _page(title: str, body: str) -> bytes:
    document = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{STYLE}</style></head>"
        f"<body>{body}</body></html>"
    )
    return document.encode("utf-8")


class WebApp:
    """Everything the request handler needs that is not the request itself."""

    def __init__(self, settings: Settings, refresh: Callable[[], int]) -> None:
        self.settings = settings
        self.refresh = refresh
        self.language = settings.display_language
        self.text = WEB_TEXT[self.language]
        # A fresh key per process: sessions are worthless after a restart, and the token
        # itself never leaves the environment file.
        self._session_value = hmac.new(
            secrets.token_bytes(32), settings.display_web_token.encode("utf-8"), sha256
        ).hexdigest()

    # -- authentication -----------------------------------------------------------------

    def token_matches(self, candidate: str) -> bool:
        return hmac.compare_digest(
            candidate.encode("utf-8"), self.settings.display_web_token.encode("utf-8")
        )

    def session_cookie_value(self) -> str:
        return self._session_value

    def is_authenticated(self, cookie_header: str | None) -> bool:
        if not cookie_header:
            return False
        jar: SimpleCookie = SimpleCookie()
        try:
            jar.load(cookie_header)
        except Exception:
            return False
        morsel = jar.get(SESSION_COOKIE)
        return morsel is not None and hmac.compare_digest(morsel.value, self._session_value)

    # -- data ---------------------------------------------------------------------------

    def current_data(self) -> tuple[DashboardData, bool]:
        """The dashboard as the panel would draw it right now, from the cache; sample data
        (flagged True) when there is no cache. Never touches the API."""
        tz = zone(self.settings.studylife_timezone)
        now = datetime.now(tz)
        snapshot = load_snapshot(Path(self.settings.display_state_path), tz)
        if snapshot is None:
            metrics, history, timer = sample_payloads(now, tz)
            return build_dashboard(metrics, history, timer, now, tz), True
        data = build_dashboard(
            snapshot.metrics, snapshot.history, snapshot.timer, now, tz, snapshot.fetched_at
        )
        return data, False

    def preview_png(self, key: str) -> bytes:
        data, _ = self.current_data()
        image = render(data, self.language, resolve_layout(key, data))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    # -- pages --------------------------------------------------------------------------

    def login_page(self, error: str | None = None) -> bytes:
        t = self.text
        message = f"<p class='flash'>{html.escape(error)}</p>" if error else ""
        body = (
            f"<h1>{html.escape(t['title'])}</h1>{message}"
            f"<form class='login' method='post' action='/login'>"
            f"<label for='token'>{html.escape(t['token_label'])}</label>"
            "<input id='token' name='token' type='password' autocomplete='current-password' "
            "autofocus required>"
            f"<button class='primary' type='submit'>{html.escape(t['login_button'])}</button>"
            "</form>"
        )
        return _page(t["title"], body)

    def layouts_page(self, flash: str | None = None) -> bytes:
        t = self.text
        data, is_sample = self.current_data()
        choice = load_layout_choice(self.settings)
        resolved = resolve_layout(AUTO, data)
        parts = [f"<h1>{html.escape(t['title'])} · {html.escape(t['layouts_heading'])}</h1>"]
        if flash in FLASH_KEYS:
            parts.append(f"<p class='flash'>{html.escape(t['flash_' + flash])}</p>")
        if is_sample:
            parts.append(f"<p class='note'>{html.escape(t['sample_note'])}</p>")
            parts.append(f"<p class='note'>{html.escape(t['never_updated'])}</p>")
        else:
            stamp = data.fetched_at.strftime(TEXT[self.language]["date_format"] + " %H:%M")
            parts.append(f"<p class='note'>{html.escape(t['last_updated'].format(time=stamp))}</p>")

        parts.append("<form method='post' action='/layout'><div class='grid'>")
        cards: list[tuple[str, str, str]] = [
            (
                AUTO,
                t["auto_name"]
                + " · "
                + t["auto_now"].format(layout=LAYOUTS[resolved].name[self.language]),
                t["auto_description"],
            )
        ]
        cards += [
            (spec.key, spec.name[self.language], spec.description[self.language])
            for spec in LAYOUTS.values()
        ]
        for key, name, description in cards:
            checked = " checked" if key == choice else ""
            parts.append(
                "<label class='card'>"
                f"<input type='radio' name='layout' value='{key}'{checked}> "
                f"<b>{html.escape(name)}</b><small>{html.escape(description)}</small>"
                f"<img src='/preview/{key}.png' width='800' height='480' loading='lazy' "
                f"alt='{html.escape(name)}'>"
                "</label>"
            )
        parts.append("</div><div class='actions'>")
        parts.append(f"<button class='primary' type='submit'>{html.escape(t['apply'])}</button>")
        parts.append("</div></form>")
        parts.append(
            "<form method='post' action='/refresh' class='actions' style='margin-top:12px'>"
            f"<button type='submit'>{html.escape(t['refresh'])}</button></form>"
        )
        parts.append(
            f"<p class='note'>{html.escape(t['auto_rules'].format(days=EXAM_SOON_DAYS))}</p>"
        )
        parts.append(f"<p class='note'>{html.escape(t['full_refresh_note'])}</p>")
        return _page(t["title"], "".join(parts))

    def simple_page(self, message: str) -> bytes:
        return _page(self.text["title"], f"<p class='flash'>{html.escape(message)}</p>")


class DisplayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], app: WebApp) -> None:
        super().__init__(address, RequestHandler)
        self.app = app


class RequestHandler(BaseHTTPRequestHandler):
    server: DisplayServer
    protocol_version = "HTTP/1.1"

    @property
    def app(self) -> WebApp:
        return self.server.app

    # One line per request on stderr through the logger; the request line carries method
    # and path only, never the body (where the token travels) or the cookie header.
    def log_message(self, format: str, *args: object) -> None:
        log.info("%s %s", self.address_string(), format % args)

    # -- helpers ------------------------------------------------------------------------

    def _send(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str = "text/html; charset=utf-8",
        extra_headers: list[tuple[str, str]] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for name, value in extra_headers or []:
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _redirect(self, location: str, extra_headers: list[tuple[str, str]] | None = None) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        for name, value in extra_headers or []:
            self.send_header(name, value)
        self.end_headers()

    def _authenticated(self) -> bool:
        return self.app.is_authenticated(self.headers.get("Cookie"))

    def _same_origin(self) -> bool:
        """Browsers send Sec-Fetch-Site on every request and Origin on form POSTs; either
        one has to say "same origin". A request with neither is not a browser form
        submission and is refused."""
        fetch_site = self.headers.get("Sec-Fetch-Site")
        if fetch_site is not None:
            return fetch_site in ("same-origin", "none")
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if origin is None or host is None:
            return False
        return origin.rstrip("/") == f"http://{host}"

    def _form(self) -> dict[str, str]:
        """Reads and parses the whole body first, whatever the route decides afterwards: an
        unread body on a keep-alive connection would be parsed as the next request line."""
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0 or length > MAX_BODY_BYTES:
            self.close_connection = True
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        return {key: values[0] for key, values in parse_qs(raw).items() if values}

    def _run_refresh(self) -> str:
        """Runs the pipeline and maps its outcome onto a flash key."""
        try:
            outcome = self.app.refresh()
        except Exception:
            log.exception("refresh from the web interface failed")
            return "failed"
        return "refreshed" if outcome == 0 else "failed"

    # -- routes -------------------------------------------------------------------------

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        app = self.app
        if parts.path == "/":
            if not self._authenticated():
                self._send(HTTPStatus.OK, app.login_page())
                return
            flash = parse_qs(parts.query).get("m", [None])[0]
            self._send(HTTPStatus.OK, app.layouts_page(flash))
            return
        if parts.path.startswith("/preview/") and parts.path.endswith(".png"):
            if not self._authenticated():
                self._send(HTTPStatus.FORBIDDEN, app.simple_page(app.text["forbidden"]))
                return
            key = parts.path[len("/preview/") : -len(".png")]
            if not is_valid_choice(key):
                self._send(HTTPStatus.NOT_FOUND, app.simple_page(app.text["not_found"]))
                return
            self._send(HTTPStatus.OK, app.preview_png(key), content_type="image/png")
            return
        self._send(HTTPStatus.NOT_FOUND, app.simple_page(app.text["not_found"]))

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        parts = urlsplit(self.path)
        app = self.app
        form = self._form()
        if parts.path == "/login":
            token = form.get("token", "")
            if not app.token_matches(token):
                time.sleep(WRONG_TOKEN_DELAY_SECONDS)
                self._send(HTTPStatus.FORBIDDEN, app.login_page(app.text["wrong_token"]))
                return
            cookie = (
                f"{SESSION_COOKIE}={app.session_cookie_value()}; HttpOnly; SameSite=Strict; Path=/"
            )
            self._redirect("/", [("Set-Cookie", cookie)])
            return
        if parts.path == "/logout":
            expired = f"{SESSION_COOKIE}=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/"
            self._redirect("/", [("Set-Cookie", expired)])
            return
        if parts.path in ("/layout", "/refresh"):
            if not self._authenticated() or not self._same_origin():
                self._send(HTTPStatus.FORBIDDEN, app.simple_page(app.text["forbidden"]))
                return
            if parts.path == "/layout":
                choice = form.get("layout", "")
                if not is_valid_choice(choice):
                    self._send(HTTPStatus.BAD_REQUEST, app.simple_page(app.text["bad_request"]))
                    return
                save_layout_choice(app.settings, choice)
                log.info("layout choice set to %s", choice)
                outcome = self._run_refresh()
                self._redirect("/?m=" + ("saved" if outcome == "refreshed" else outcome))
                return
            self._redirect("/?m=" + self._run_refresh())
            return
        self._send(HTTPStatus.NOT_FOUND, app.simple_page(app.text["not_found"]))


def parse_bind(bind: str) -> tuple[str, int]:
    """ "0.0.0.0:8795" -> ("0.0.0.0", 8795); a bare port binds every interface."""
    host, sep, port = bind.rpartition(":")
    if not sep:
        return "0.0.0.0", int(bind)
    return host.strip("[]") or "0.0.0.0", int(port)


def make_server(
    settings: Settings, refresh: Callable[[], int], bind: str | None = None
) -> DisplayServer:
    """Binds (but does not serve); tests pass "127.0.0.1:0" and read `server_address`."""
    return DisplayServer(parse_bind(bind or settings.display_web_bind), WebApp(settings, refresh))


def serve_web(settings: Settings, refresh: Callable[[], int]) -> int:
    server = make_server(settings, refresh)
    host, port = server.server_address[0], server.server_address[1]
    log.info(
        "web interface listening on http://%s:%d/ (layouts: %s)",
        host,
        port,
        ", ".join(sorted(valid_choices())),
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
