"""Connecting the display to a StudyLife account from the web interface, without copying
a key by hand.

Same wire shape as studylife-cli's login and studylife-telegram's linking (StudyLife's
generic dynamic-client consent flow, AuthController.10.OAuthClients.cs on the server):

1. The web process generates a PKCE pair and a state and shows the connect URL
   `<instance>/connect/client/studylife-display?redirect_uri=...&state=...&code_challenge=...
   &code_challenge_method=S256`.
2. The person approves in StudyLife, on any device. StudyLife redirects the browser to
   `<redirect_uri>?assertion=...&state=...`.
3. The web process redeems the assertion together with the PKCE verifier at the anonymous
   `POST /api/auth/assertion-exchange` (`{"clientId", "assertion", "codeVerifier"}`) and
   receives `{"userId", "apiKey"}`.

Where the redirect lands is the one thing that differs from the CLI: StudyLife accepts as a
redirect URI only https or the RFC 8252 loopback (`http://localhost:<port>/...`), never the
Pi's plain-http LAN address. So by default the redirect URI is
`http://localhost:<web port>/connect/callback`, a URL the approving browser cannot load;
the person copies it from the address bar and pastes it into the connect page ("paste"
mode). With DISPLAY_PUBLIC_BASE_URL (an https name, Tailscale say) the redirect goes to
`<that>/connect/callback` and the web process handles it directly ("redirect" mode).

What protects the flow: the verifier never leaves this process, so the redirect URL alone
redeems nothing; the state is single-use, expires after ten minutes, is compared in constant
time and lives only in the web process's memory. The key itself is never shown in the
browser: it is written to `credentials.pending.json` (0600) in the state directory, from
where the root-only `credentials-apply` moves it into the environment file.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

log = logging.getLogger(__name__)

CLIENT_ID = "studylife-display"
CALLBACK_PATH = "/connect/callback"
PENDING_FILE = "credentials.pending.json"
STATE_LIFETIME = timedelta(minutes=10)
# Generous on purpose: the exchange happens once per connection and a slow instance should
# not turn into "that approval is no longer valid".
EXCHANGE_TIMEOUT_SECONDS = 30.0
# What an API key may look like on one line of the environment file: printable ASCII
# without whitespace, and not absurdly long.
MAX_KEY_LENGTH = 512


class ConnectError(Exception):
    """A failure the page can name: `key` picks the message, `detail` is appended."""

    def __init__(self, key: str, detail: str = "") -> None:
        super().__init__(f"{key}: {detail}" if detail else key)
        self.key = key
        self.detail = detail


# -- the pieces of the connect URL -------------------------------------------------------


def new_pkce_pair() -> tuple[str, str]:
    """(verifier, challenge): 43 unreserved characters and the unpadded base64url SHA-256
    of them, exactly the S256 shape StudyLife's connect endpoint validates."""
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def new_state() -> str:
    return secrets.token_urlsafe(32)


def normalise_instance(url: str) -> str:
    return url.strip().rstrip("/")


def build_connect_url(
    instance_url: str, client_id: str, redirect_uri: str, state: str, challenge: str
) -> str:
    query = urlencode(
        {
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{normalise_instance(instance_url)}/connect/client/{client_id}?{query}"


def loopback_redirect_uri(port: int) -> str:
    """Paste mode: the RFC 8252 loopback URI StudyLife accepts for a client without an
    https callback. The port is the web interface's, so that a browser that does reach the
    Pi as localhost (an SSH port forward) lands on the callback route directly."""
    return f"http://localhost:{port}{CALLBACK_PATH}"


def public_redirect_uri(public_base_url: str) -> str:
    """Redirect mode: the exact string to register on the client - the server matches it
    character for character."""
    return f"{normalise_instance(public_base_url)}{CALLBACK_PATH}"


# -- one attempt -------------------------------------------------------------------------


@dataclass(frozen=True)
class PendingConnect:
    """A started attempt: what the page shows and what the callback is checked against."""

    state: str
    verifier: str
    redirect_uri: str
    connect_url: str
    created_at: datetime

    @property
    def expires_at(self) -> datetime:
        return self.created_at + STATE_LIFETIME

    def expired(self, now: datetime) -> bool:
        return now >= self.expires_at


def start_connect(
    instance_url: str, redirect_uri: str, now: datetime, client_id: str = CLIENT_ID
) -> PendingConnect:
    verifier, challenge = new_pkce_pair()
    state = new_state()
    return PendingConnect(
        state=state,
        verifier=verifier,
        redirect_uri=redirect_uri,
        connect_url=build_connect_url(instance_url, client_id, redirect_uri, state, challenge),
        created_at=now,
    )


@dataclass(frozen=True)
class CallbackResult:
    state: str
    assertion: str


def parse_callback(text: str) -> CallbackResult:
    """What StudyLife put into the redirect: from the whole pasted URL, or from just its
    query string. ConnectError("missing_assertion") when there is no assertion in it."""
    text = text.strip()
    if not text:
        raise ConnectError("missing_assertion")
    query = urlsplit(text).query if "?" in text or "://" in text else text
    values = parse_qs(query, keep_blank_values=False)
    assertion = values.get("assertion", [""])[0]
    if not assertion:
        raise ConnectError("missing_assertion")
    return CallbackResult(state=values.get("state", [""])[0], assertion=assertion)


def check_callback(pending: PendingConnect | None, result: CallbackResult, now: datetime) -> None:
    """The callback belongs to the started attempt, which is still valid: ConnectError
    otherwise (no_pending, expired, state_mismatch). Compared in constant time as bytes -
    the state is not secret, but `result.state` is whatever was pasted or requested."""
    if pending is None:
        raise ConnectError("no_pending")
    if pending.expired(now):
        raise ConnectError("expired")
    if not hmac.compare_digest(result.state.encode("utf-8"), pending.state.encode("utf-8")):
        raise ConnectError("state_mismatch")


def exchange_assertion(
    instance_url: str,
    client_id: str,
    assertion: str,
    code_verifier: str,
    timeout: float = EXCHANGE_TIMEOUT_SECONDS,
) -> tuple[str, int | None]:
    """Redeems the single-use assertion for the key: (api_key, user_id). No X-Api-Key is
    sent, the endpoint is anonymous by design; the assertion plus the verifier are the
    credential. A 401 almost always means the assertion was used already or has expired."""
    try:
        response = httpx.post(
            f"{normalise_instance(instance_url)}/api/auth/assertion-exchange",
            json={"clientId": client_id, "assertion": assertion, "codeVerifier": code_verifier},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise ConnectError("unreachable", str(exc)) from exc
    if response.status_code == 401:
        raise ConnectError("exchange_rejected")
    if response.status_code >= 400:
        raise ConnectError("exchange_failed", f"HTTP {response.status_code}")
    try:
        payload = response.json()
        api_key = str(payload["apiKey"])
    except (ValueError, KeyError, TypeError) as exc:
        raise ConnectError("exchange_failed", "unexpected response") from exc
    if not valid_api_key(api_key):
        raise ConnectError("exchange_failed", "unexpected key format")
    user_id = payload.get("userId")
    return api_key, user_id if isinstance(user_id, int) and not isinstance(user_id, bool) else None


# -- the key, on its way to the environment file ------------------------------------------


def valid_api_key(value: object) -> bool:
    """One token of printable ASCII without whitespace, i.e. something that is safe on a
    `STUDYLIFE_API_KEY=` line of the environment file."""
    return (
        isinstance(value, str)
        and 0 < len(value) <= MAX_KEY_LENGTH
        and all(33 <= ord(char) <= 126 for char in value)
    )


def pending_path(state_dir: Path) -> Path:
    return state_dir / PENDING_FILE


def write_pending_credentials(
    state_dir: Path, api_key: str, instance_url: str, obtained_at: datetime
) -> Path:
    """Writes `credentials.pending.json`, readable by the owner only, atomically. The
    root-only path unit picks it up from here; the browser never sees the key."""
    state_dir.mkdir(parents=True, exist_ok=True)
    path = pending_path(state_dir)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "apiKey": api_key,
        "instance": normalise_instance(instance_url),
        "obtainedAt": obtained_at.isoformat(),
    }
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload))
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return path


def read_pending_credentials(path: Path) -> str:
    """The key in a pending file. FileNotFoundError when there is none; ValueError when the
    file is not what write_pending_credentials writes."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} is not a JSON object")
    api_key = raw.get("apiKey")
    if not valid_api_key(api_key):
        raise ValueError(f"{path} holds no usable apiKey")
    assert isinstance(api_key, str)
    return api_key


# -- who the key belongs to ---------------------------------------------------------------


def whoami(instance_url: str, api_key: str, timeout: float = 10.0) -> dict[str, Any]:
    """`GET /api/auth/whoami` with the key: `{"userId": int, "credential": str}` (the only
    two fields the server sends - there is no name or e-mail on this endpoint).
    ConnectError("rejected") on 401/403, ("unreachable") on a network failure."""
    try:
        response = httpx.get(
            f"{normalise_instance(instance_url)}/api/auth/whoami",
            headers={"X-Api-Key": api_key},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise ConnectError("unreachable", str(exc)) from exc
    if response.status_code in (401, 403):
        raise ConnectError("rejected", f"HTTP {response.status_code}")
    if response.status_code >= 400:
        raise ConnectError("unreachable", f"HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ConnectError("unreachable", "unexpected response") from exc
    if not isinstance(payload, dict):
        raise ConnectError("unreachable", "unexpected response")
    return {"userId": payload.get("userId"), "credential": payload.get("credential")}


# -- the overlay caveat -------------------------------------------------------------------


def root_is_overlay(mounts: Path = Path("/proc/mounts")) -> bool:
    """Whether the root filesystem is an overlay (Raspberry Pi OS with the overlay
    filesystem on): `/etc` is then RAM-backed and a key written there is gone at the next
    reboot. False when the mounts table cannot be read (not Linux)."""
    try:
        lines = mounts.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        fields = line.split()
        if len(fields) >= 3 and fields[1] == "/" and fields[2] == "overlay":
            return True
    return False
