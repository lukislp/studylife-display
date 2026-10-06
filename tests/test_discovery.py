"""mDNS discovery: TXT parsing and validation, verification, dedupe, and the zeroconf layer
with a fake browser. No real network is touched anywhere in this file."""

from __future__ import annotations

import sys
import types
from typing import Any

import httpx
import pytest
import respx

from studylife_display import discovery
from studylife_display.discovery import (
    Announcement,
    InvalidServerUrl,
    Server,
    dedupe_by_id,
    discover,
    fetch_instance,
    normalise_instance_id,
    parse_announcement,
    validate_server_url,
    verify,
)

ID_A = "0123456789abcdef0123456789abcdef"
ID_B = "fedcba9876543210fedcba9876543210"


def txt(**values: str) -> dict[bytes | str, bytes | str | None]:
    return {key.encode(): value.encode() for key, value in values.items()}


# -- URL validation -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("https://studylife.example.org", "https://studylife.example.org"),
        ("https://StudyLife.Example.org/", "https://studylife.example.org"),
        ("http://192.168.1.5:8080", "http://192.168.1.5:8080"),
        ("  http://studylife.lan  ", "http://studylife.lan"),
        ("http://[fd00::1]:8080", "http://[fd00::1]:8080"),
    ],
)
def test_valid_urls_are_canonicalised(raw: str, canonical: str) -> None:
    assert validate_server_url(raw) == canonical


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "studylife.lan",
        "ftp://studylife.lan",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "https://",
        "https://user:pw@studylife.lan",
        "https://user@studylife.lan",
        "https://studylife.lan/app",
        "https://studylife.lan/?x=1",
        "https://studylife.lan/#frag",
        "https://studylife.lan:notaport",
        "https://studylife.lan:99999",
        "https://stu dylife.lan",
        "https://studylife.lan\r\nHost: evil",
        "https://" + "a" * 300,
    ],
)
def test_hostile_or_malformed_urls_are_rejected(raw: str) -> None:
    with pytest.raises(InvalidServerUrl):
        validate_server_url(raw)


def test_instance_id_must_be_32_lowercase_hex() -> None:
    assert normalise_instance_id(ID_A.upper()) == ID_A
    for bad in ("", "abc", ID_A + "0", "g" * 32, None, 12, ID_A[:-1] + "/"):
        assert normalise_instance_id(bad) is None


# -- parsing the announcement -------------------------------------------------------------


def test_a_complete_announcement_is_parsed() -> None:
    parsed = parse_announcement(
        "My StudyLife._studylife._tcp.local.",
        txt(url="https://studylife.example.org", https="true", version="3.22.1", id=ID_A, path="/"),
    )
    assert parsed == Announcement(
        "My StudyLife", "https://studylife.example.org", ID_A, "3.22.1", True
    )


def test_a_missing_id_is_allowed_and_a_malformed_one_counts_as_missing() -> None:
    no_id = parse_announcement("S._studylife._tcp.local.", txt(url="http://s.lan"))
    assert no_id is not None and no_id.id is None and no_id.https is None
    bad_id = parse_announcement("S._studylife._tcp.local.", txt(url="http://s.lan", id="nope"))
    assert bad_id is not None and bad_id.id is None


@pytest.mark.parametrize(
    "properties",
    [
        {},
        txt(version="3.22.1"),
        txt(url="https://user:pw@s.lan"),
        txt(url="https://s.lan/path"),
        txt(url="gopher://s.lan"),
        # The https flag contradicts the URL scheme.
        txt(url="http://s.lan", https="true"),
        txt(url="https://s.lan", https="false"),
        {b"url": b"\xff\xfe"},
        {b"url": None},
    ],
)
def test_announcements_without_a_usable_url_are_dropped(properties: dict[Any, Any]) -> None:
    assert parse_announcement("S._studylife._tcp.local.", properties) is None


def test_hostile_text_values_are_neutralised() -> None:
    parsed = parse_announcement(
        "Evil\x00\x1b[31m<script>._studylife._tcp.local.",
        txt(url="http://s.lan", version="<script>alert(1)</script>", https="maybe"),
    )
    assert parsed is not None
    assert "\x00" not in parsed.name and "\x1b" not in parsed.name
    assert parsed.version == ""
    assert parsed.https is None


def test_keys_are_case_insensitive_and_str_values_work() -> None:
    parsed = parse_announcement("S._studylife._tcp.local.", {"URL": "http://s.lan", "Id": ID_A})
    assert parsed is not None and parsed.id == ID_A


# -- verification -------------------------------------------------------------------------


def announcement(url: str = "https://s.lan", id_: str | None = ID_A) -> Announcement:
    return Announcement("StudyLife", url, id_, "3.22.0", None)


def test_verification_succeeds_when_the_id_matches() -> None:
    server = verify(announcement(), lambda url: (ID_A, "3.22.1"))
    assert server == Server("StudyLife", "https://s.lan", ID_A, "3.22.1", True)


def test_verification_fails_on_an_id_mismatch() -> None:
    assert verify(announcement(), lambda url: (ID_B, "3.22.1")) is None


def test_verification_fails_when_the_server_does_not_answer() -> None:
    assert verify(announcement(), lambda url: None) is None


def test_a_missing_announced_id_is_filled_from_the_instance_endpoint() -> None:
    server = verify(announcement(id_=None), lambda url: (ID_B, ""))
    assert server is not None and server.id == ID_B and server.version == "3.22.0"


@respx.mock
def test_fetch_instance_reads_id_and_version_anonymously() -> None:
    route = respx.get("https://s.lan/api/instance").mock(
        return_value=httpx.Response(200, json={"id": ID_A.upper(), "version": "3.22.1"})
    )
    assert fetch_instance("https://s.lan") == (ID_A, "3.22.1")
    assert "x-api-key" not in route.calls.last.request.headers
    assert "authorization" not in route.calls.last.request.headers


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404),
        httpx.Response(500),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=["list"]),
        httpx.Response(200, json={"id": "short"}),
        httpx.Response(200, json={"version": "3.22.1"}),
        httpx.Response(200, content=b"x" * 10_000),
        httpx.Response(302, headers={"Location": "https://evil.example/api/instance"}),
    ],
)
def test_fetch_instance_rejects_everything_but_a_clean_answer(response: httpx.Response) -> None:
    respx.get("https://s.lan/api/instance").mock(return_value=response)
    assert fetch_instance("https://s.lan") is None


@respx.mock
def test_fetch_instance_never_follows_a_redirect() -> None:
    respx.get("https://s.lan/api/instance").mock(
        return_value=httpx.Response(302, headers={"Location": "https://evil.example/x"})
    )
    evil = respx.get("https://evil.example/x").mock(
        return_value=httpx.Response(200, json={"id": ID_A})
    )
    assert fetch_instance("https://s.lan") is None
    assert not evil.called


@respx.mock
def test_fetch_instance_survives_network_errors() -> None:
    respx.get("https://s.lan/api/instance").mock(side_effect=httpx.ConnectTimeout("slow"))
    assert fetch_instance("https://s.lan") is None


# -- dedupe and the whole search ----------------------------------------------------------


def server(url: str, id_: str = ID_A) -> Server:
    return Server("StudyLife", url, id_, "3.22.1", url.startswith("https://"))


def test_dedupe_keeps_one_server_per_id_and_prefers_https() -> None:
    result = dedupe_by_id(
        [server("http://192.168.1.5"), server("https://studylife.org"), server("http://x", ID_B)]
    )
    assert [s.url for s in result] == ["https://studylife.org", "http://x"]


def test_dedupe_keeps_the_first_when_both_have_the_same_scheme() -> None:
    result = dedupe_by_id([server("https://a.org"), server("https://b.org")])
    assert [s.url for s in result] == ["https://a.org"]


def fake_fetch(answers: dict[str, tuple[str, str] | None]) -> Any:
    return lambda url: answers.get(url)


def test_discover_verifies_dedupes_and_counts_ignored() -> None:
    raw = [
        ("A._studylife._tcp.local.", txt(url="https://a.lan", id=ID_A)),
        # Same installation announced a second time under another URL.
        ("A2._studylife._tcp.local.", txt(url="http://10.0.0.5", id=ID_A)),
        # Announces ID_B but answers with ID_A: impersonation attempt, dropped.
        ("Fake._studylife._tcp.local.", txt(url="http://10.0.0.66", id=ID_B)),
        # Unusable URL.
        ("Bad._studylife._tcp.local.", txt(url="https://u:p@evil.lan", id=ID_B)),
        # Does not answer.
        ("Down._studylife._tcp.local.", txt(url="http://10.0.0.77", id=ID_B)),
    ]
    fetch = fake_fetch(
        {
            "https://a.lan": (ID_A, "3.22.1"),
            "http://10.0.0.5": (ID_A, "3.22.1"),
            "http://10.0.0.66": (ID_A, "3.22.1"),
        }
    )
    result = discover(browse=lambda timeout: raw, fetch=fetch, now=lambda: 42.0)
    assert [s.url for s in result.servers] == ["https://a.lan"]
    assert result.ignored == 3
    assert result.searched_at == 42.0
    assert result.as_json()["servers"] == [result.servers[0].as_json()]


def test_discover_asks_each_url_once_and_caps_the_announcements() -> None:
    asked: list[str] = []

    def fetch(url: str) -> tuple[str, str] | None:
        asked.append(url)
        return None

    raw = [(f"S{i}._studylife._tcp.local.", txt(url="http://s.lan")) for i in range(20)]
    discover(browse=lambda timeout: raw, fetch=fetch)
    assert asked == ["http://s.lan"]
    distinct = [(f"S{i}._studylife._tcp.local.", txt(url=f"http://s{i}.lan")) for i in range(20)]
    asked.clear()
    discover(browse=lambda timeout: distinct, fetch=fetch)
    assert len(asked) == discovery.MAX_ANNOUNCEMENTS


def test_discover_without_announcements_or_with_a_failing_browser_finds_nothing() -> None:
    assert discover(browse=lambda timeout: []).servers == ()

    def boom(timeout: float) -> list[Any]:
        raise OSError("no multicast")

    assert discover(browse=boom).servers == ()


# -- the zeroconf layer, with a fake library ----------------------------------------------


class FakeInfo:
    def __init__(self, properties: dict[bytes, bytes | None]) -> None:
        self.properties = properties


def install_fake_zeroconf(
    monkeypatch: pytest.MonkeyPatch,
    services: dict[str, FakeInfo | None],
    fail_open: bool = False,
) -> list[str]:
    """A stand-in `zeroconf` module: the browser reports `services` to the listener, and
    `events` records what the code did with the library."""
    events: list[str] = []

    class ServiceListener:
        pass

    class IPVersion:
        V4Only = "v4"

    class Zeroconf:
        def __init__(self, ip_version: object = None) -> None:
            if fail_open:
                raise OSError("no interface")
            events.append("open")

        def get_service_info(self, type_: str, name: str, timeout: int = 0) -> FakeInfo | None:
            events.append(f"resolve {name}")
            return services[name]

        def close(self) -> None:
            events.append("close")

    class ServiceBrowser:
        def __init__(self, zc: Zeroconf, type_: str, listener: Any) -> None:
            assert type_ == "_studylife._tcp.local."
            for name in services:
                listener.add_service(zc, type_, name)
                listener.add_service(zc, type_, name)  # repeated announcement

        def cancel(self) -> None:
            events.append("cancel")

    module = types.SimpleNamespace(
        IPVersion=IPVersion,
        ServiceBrowser=ServiceBrowser,
        ServiceListener=ServiceListener,
        Zeroconf=Zeroconf,
    )
    monkeypatch.setitem(sys.modules, "zeroconf", module)
    monkeypatch.setattr(discovery.time, "sleep", lambda seconds: events.append("listen"))
    return events


def test_browse_zeroconf_collects_resolved_services(monkeypatch: pytest.MonkeyPatch) -> None:
    name = "StudyLife._studylife._tcp.local."
    events = install_fake_zeroconf(
        monkeypatch,
        {name: FakeInfo({b"url": b"https://s.lan", b"id": ID_A.encode()}), "Gone._x": None},
    )
    found = discovery.browse_zeroconf(1.0)
    assert found == [(name, {b"url": b"https://s.lan", b"id": ID_A.encode()})]
    assert events == ["open", "listen", "cancel", f"resolve {name}", "resolve Gone._x", "close"]


def test_browse_zeroconf_end_to_end_with_discover(monkeypatch: pytest.MonkeyPatch) -> None:
    name = "StudyLife._studylife._tcp.local."
    install_fake_zeroconf(
        monkeypatch, {name: FakeInfo({b"url": b"https://s.lan", b"id": ID_A.encode()})}
    )
    result = discover(1.0, fetch=fake_fetch({"https://s.lan": (ID_A, "3.22.1")}))
    assert [s.url for s in result.servers] == ["https://s.lan"]


def test_browse_zeroconf_without_a_multicast_socket_finds_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_zeroconf(monkeypatch, {}, fail_open=True)
    assert discovery.browse_zeroconf(1.0) == []


def test_browse_zeroconf_without_the_library_finds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "zeroconf", None)  # makes `import zeroconf` raise
    assert discovery.browse_zeroconf(1.0) == []
