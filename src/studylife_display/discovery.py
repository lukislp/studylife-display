"""Finding the StudyLife server on the local network (mDNS / DNS-SD).

A StudyLife server can announce `_studylife._tcp.local.` (opt-in on the server side, see its
docs/MDNS.md): instance name, port and the TXT records `version`, `url` (advertised base
URL, scheme + host[:port]), `https` ("true"/"false"), `path` ("/") and `id` (the stable
32-hex instance id; it can be missing for a moment after the server started).

Security model, in two sentences: anyone on the LAN can announce a fake `_studylife._tcp`,
so an announcement is only ever a PROPOSAL shown to a human - nothing in this module connects
an account, sends a credential or changes the configured server. Every proposal is validated
strictly and then verified by an anonymous `GET <url>/api/instance`, which has to answer with
the same id the announcement carried; a candidate that does not verify is dropped.

The pieces are separate on purpose so the tests need no network: `parse_announcement` and
`validate_server_url` are pure, `fetch_instance` is one HTTP call, `browse_zeroconf` is the
only function that touches multicast, and `discover` glues them together with injectable
`browse` and `fetch` callables.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

SERVICE_TYPE = "_studylife._tcp.local."
# How long a search listens for announcements. Servers answer a query within a few hundred
# milliseconds; the window is generous because mDNS over a reflector is slower.
BROWSE_SECONDS = 4.0
# Per-service resolve timeout (TXT records), milliseconds.
RESOLVE_TIMEOUT_MS = 2000
# Announcements looked at per search; a flood of fake ones cannot make a search long.
MAX_ANNOUNCEMENTS = 8
# Verification request: short, no redirects, tiny body.
VERIFY_TIMEOUT_SECONDS = 3.0
MAX_BODY_BYTES = 4096
MAX_URL_LENGTH = 200
MAX_NAME_LENGTH = 63

_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
# Server versions look like 3.22.1 (build metadata stripped); anything else is not shown.
_VERSION_PATTERN = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,39}")
_NAME_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f]")


class InvalidServerUrl(ValueError):
    """A URL that is not a plain StudyLife base URL; the message says why."""


def validate_server_url(value: str) -> str:
    """The canonical base URL (`scheme://host[:port]`, lower-case, no trailing slash) of
    `value`, or InvalidServerUrl. Absolute http/https only, a host, no credentials, no path
    beyond `/`, no query, no fragment, no whitespace or control characters."""
    if not isinstance(value, str):
        raise InvalidServerUrl("not a string")
    text = value.strip()
    if not text or len(text) > MAX_URL_LENGTH:
        raise InvalidServerUrl("empty or too long")
    if any(ord(char) < 33 or ord(char) == 127 for char in text):
        raise InvalidServerUrl("contains whitespace or control characters")
    try:
        parts = urlsplit(text)
        port = parts.port
        host = parts.hostname
    except ValueError as exc:
        raise InvalidServerUrl("not a valid URL") from exc
    if parts.scheme not in ("http", "https"):
        raise InvalidServerUrl("must start with http:// or https://")
    if not host:
        raise InvalidServerUrl("no host")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise InvalidServerUrl("must not contain credentials")
    if parts.path not in ("", "/"):
        raise InvalidServerUrl("must not contain a path")
    if parts.query or parts.fragment or "?" in text or "#" in text:
        raise InvalidServerUrl("must not contain a query or fragment")
    shown_host = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{shown_host}" + (f":{port}" if port is not None else "")


def normalise_instance_id(value: object) -> str | None:
    """A 32-hex instance id lower-cased, or None for anything else."""
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    return candidate if _ID_PATTERN.fullmatch(candidate) else None


def _clean_version(value: object) -> str:
    return value if isinstance(value, str) and _VERSION_PATTERN.fullmatch(value) else ""


def _clean_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return _NAME_FORBIDDEN.sub("", value).strip()[:MAX_NAME_LENGTH]


@dataclass(frozen=True)
class Announcement:
    """One announcement as parsed from the TXT records: syntactically valid, NOT verified."""

    name: str
    url: str
    id: str | None
    version: str
    https: bool | None


@dataclass(frozen=True)
class Server:
    """A verified candidate: `/api/instance` at `url` answered with `id`."""

    name: str
    url: str
    id: str
    version: str
    https: bool

    def as_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "url": self.url,
            "id": self.id,
            "version": self.version,
            "https": self.https,
        }


@dataclass(frozen=True)
class DiscoveryResult:
    """What one search found: the verified servers (one per instance id) and how many
    announcements were ignored (invalid, unreachable, or answering with another id)."""

    servers: tuple[Server, ...]
    ignored: int
    searched_at: float

    def as_json(self) -> dict[str, object]:
        return {
            "servers": [server.as_json() for server in self.servers],
            "ignored": self.ignored,
            "searched_at": self.searched_at,
        }


def _text(value: object) -> str | None:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return value if isinstance(value, str) else None


def parse_announcement(
    name: str, properties: Mapping[bytes | str, bytes | str | None]
) -> Announcement | None:
    """The announcement behind a service name and its TXT records, or None when the `url`
    is missing or fails `validate_server_url`. A malformed `id` is treated like a missing
    one (the verification fetch decides); `https` only counts when it is exactly
    "true"/"false", and an announcement whose flag contradicts its URL scheme is dropped."""
    txt = {(_text(key) or "").lower(): _text(value) for key, value in properties.items() if key}
    raw_url = txt.get("url")
    if raw_url is None:
        return None
    try:
        url = validate_server_url(raw_url)
    except InvalidServerUrl:
        return None
    flag = (txt.get("https") or "").strip().lower()
    https = {"true": True, "false": False}.get(flag)
    if https is not None and https != url.startswith("https://"):
        return None
    return Announcement(
        name=_clean_name(name.removesuffix("." + SERVICE_TYPE)) or "StudyLife",
        url=url,
        id=normalise_instance_id(txt.get("id")),
        version=_clean_version((txt.get("version") or "").strip()),
        https=https,
    )


def fetch_instance(url: str, timeout: float = VERIFY_TIMEOUT_SECONDS) -> tuple[str, str] | None:
    """`(id, version)` from the anonymous `GET <url>/api/instance`, or None when the server
    does not answer 200 with a JSON object holding a valid id. Redirects are NOT followed
    (a server that moved is not one this URL vouches for), the body is read up to
    MAX_BODY_BYTES. No credential is sent."""
    body = b""
    try:
        with (
            httpx.Client(timeout=timeout, follow_redirects=False) as client,
            client.stream("GET", f"{url}/api/instance") as response,
        ):
            if response.status_code != 200:
                return None
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) > MAX_BODY_BYTES:
                    return None
    except httpx.HTTPError:
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    instance_id = normalise_instance_id(payload.get("id"))
    if instance_id is None:
        return None
    return instance_id, _clean_version(payload.get("version"))


Fetch = Callable[[str], tuple[str, str] | None]
RawService = tuple[str, Mapping[bytes | str, bytes | str | None]]
Browse = Callable[[float], list[RawService]]


def verify(announcement: Announcement, fetch: Fetch = fetch_instance) -> Server | None:
    """The announcement as a Server when the URL's own `/api/instance` confirms it: the
    answered id must equal the announced one (when it carried one). None otherwise."""
    answer = fetch(announcement.url)
    if answer is None:
        log.info("ignoring %s: /api/instance did not answer", announcement.url)
        return None
    instance_id, version = answer
    if announcement.id is not None and announcement.id != instance_id:
        log.warning(
            "ignoring %s: it announced id %s but answers with %s",
            announcement.url,
            announcement.id,
            instance_id,
        )
        return None
    return Server(
        name=announcement.name,
        url=announcement.url,
        id=instance_id,
        version=version or announcement.version,
        https=announcement.url.startswith("https://"),
    )


def dedupe_by_id(servers: list[Server]) -> list[Server]:
    """One server per instance id (the same installation announced twice, e.g. by two
    replicas of the announcer or on two interfaces): the https URL wins, otherwise the
    first one seen. Order of first appearance is kept."""
    chosen: dict[str, Server] = {}
    for server in servers:
        current = chosen.get(server.id)
        if current is None or (server.https and not current.https):
            chosen[server.id] = server
    return list(chosen.values())


def browse_zeroconf(timeout: float = BROWSE_SECONDS) -> list[RawService]:
    """Listens for `_studylife._tcp.local.` for `timeout` seconds and resolves what turned
    up. Returns [] when zeroconf is not installed or no multicast socket can be opened (a
    container on a bridge network, no network at all): discovery then simply finds nothing."""
    try:
        from zeroconf import IPVersion, ServiceBrowser, ServiceListener, Zeroconf
    except ImportError:
        log.warning("zeroconf is not installed, cannot search the network")
        return []

    names: list[str] = []

    class Collector(ServiceListener):
        def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            if name not in names:
                names.append(name)

        def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            self.add_service(zc, type_, name)

        def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            pass

    try:
        zc = Zeroconf(ip_version=IPVersion.V4Only)
    except Exception as exc:  # no interface, no permission, socket error
        log.warning("mDNS is not available here (%s)", exc)
        return []
    found: list[RawService] = []
    try:
        browser = ServiceBrowser(zc, SERVICE_TYPE, Collector())
        time.sleep(timeout)
        browser.cancel()
        for name in names[:MAX_ANNOUNCEMENTS]:
            info = zc.get_service_info(SERVICE_TYPE, name, timeout=RESOLVE_TIMEOUT_MS)
            if info is not None:
                properties: dict[bytes | str, bytes | str | None] = dict(info.properties.items())
                found.append((name, properties))
    except Exception as exc:
        log.warning("mDNS search failed (%s)", exc)
    finally:
        zc.close()
    return found


def discover(
    timeout: float = BROWSE_SECONDS,
    browse: Browse = browse_zeroconf,
    fetch: Fetch = fetch_instance,
    now: Callable[[], float] = time.time,
) -> DiscoveryResult:
    """One search: browse, parse, verify (in parallel, each with a short timeout), dedupe
    by instance id. Never raises for network trouble."""
    try:
        raw = browse(timeout)
    except Exception as exc:
        log.warning("search for StudyLife servers failed: %s", exc)
        raw = []
    raw = raw[:MAX_ANNOUNCEMENTS]
    announcements = [
        parsed
        for name, properties in raw
        if (parsed := parse_announcement(name, properties)) is not None
    ]
    ignored = len(raw) - len(announcements)
    # The same URL announced twice is verified once.
    unique = list({a.url: a for a in announcements}.values())
    verified: list[Server | None] = []
    if unique:
        with ThreadPoolExecutor(max_workers=len(unique)) as pool:
            verified = list(pool.map(lambda a: verify(a, fetch), unique))
    servers = [server for server in verified if server is not None]
    ignored += len(unique) - len(servers)
    return DiscoveryResult(tuple(dedupe_by_id(servers)), ignored, now())
