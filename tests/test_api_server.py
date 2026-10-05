"""The bearer-token API for discovery and the server choice: GET /api/discovery,
POST /api/discovery/search, POST /api/server, and the `server` entry of /api/state."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from test_api import TOKEN, Client, make_settings

from studylife_display import main as main_module
from studylife_display.config import Settings
from studylife_display.discovery import DiscoveryResult, Server
from studylife_display.settings_store import effective_settings, settings_path, update_overrides
from studylife_display.web import DisplayServer, make_server

ID_A = "0123456789abcdef0123456789abcdef"
ID_B = "fedcba9876543210fedcba9876543210"
LAN_URL = "https://studylife.lan"


class Fakes:
    def __init__(self) -> None:
        self.servers = (Server("StudyLife", LAN_URL, ID_A, "3.22.1", True),)
        self.answers: dict[str, tuple[str, str] | None] = {LAN_URL: (ID_A, "3.22.1")}
        self.searches = 0

    def discover(self) -> DiscoveryResult:
        self.searches += 1
        return DiscoveryResult(self.servers, 1, 5.0)

    def fetch_instance(self, url: str) -> tuple[str, str] | None:
        return self.answers.get(url)


def run(settings: Settings, fakes: Fakes) -> Iterator[tuple[Client, DisplayServer]]:
    instance = make_server(
        settings,
        lambda: main_module.refresh_panel(effective_settings(settings), force=True),
        "127.0.0.1:0",
    )
    instance.app.discover = fakes.discover
    instance.app.fetch_instance = fakes.fetch_instance
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield Client(instance.server_address[1]), instance
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)


@pytest.fixture
def fakes() -> Fakes:
    return Fakes()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path, studylife_base_url=None, studylife_api_key="")


@pytest.fixture
def client(settings: Settings, fakes: Fakes) -> Iterator[Client]:
    for api_client, _ in run(settings, fakes):
        yield api_client


def stored(settings: Settings) -> dict[str, Any]:
    path = settings_path(settings)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


# -- auth ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/discovery", None),
        ("POST", "/api/discovery/search", {}),
        ("POST", "/api/server", {"url": LAN_URL}),
    ],
)
def test_every_route_needs_the_bearer_token(
    settings: Settings, fakes: Fakes, method: str, path: str, body: dict[str, Any] | None
) -> None:
    for _, server in run(settings, fakes):
        port = server.server_address[1]
        assert Client(port, None).request(method, path, body)[0] == 401
        assert Client(port, "wrong-token-value").request(method, path, body)[0] == 401
    assert fakes.searches == 0
    assert not settings_path(settings).exists()


def test_the_routes_are_404_while_the_api_is_off(tmp_path: Path, fakes: Fakes) -> None:
    settings = make_settings(tmp_path, display_api_token="", studylife_base_url=None)
    for _, server in run(settings, fakes):
        off = Client(server.server_address[1], TOKEN)
        assert off.request("GET", "/api/discovery")[0] == 404
        assert off.request("POST", "/api/server", {"url": LAN_URL})[0] == 404


# -- reading ------------------------------------------------------------------------------


def test_the_listing_is_empty_before_any_search_and_a_get_never_searches(
    client: Client, fakes: Fakes
) -> None:
    for _ in range(2):
        status, body = client.json("GET", "/api/discovery")
        assert status == 200
        assert body == {
            "server": {"url": "", "source": "none", "id": None, "version": None},
            "locked": False,
            "last_search": None,
        }
    assert fakes.searches == 0


def test_a_search_returns_and_remembers_the_verified_servers(client: Client, fakes: Fakes) -> None:
    status, body = client.json("POST", "/api/discovery/search")
    assert status == 200
    assert body["last_search"] == {
        "servers": [
            {"name": "StudyLife", "url": LAN_URL, "id": ID_A, "version": "3.22.1", "https": True}
        ],
        "ignored": 1,
        "searched_at": 5.0,
    }
    _, again = client.json("GET", "/api/discovery")
    assert again["last_search"] == body["last_search"]
    assert fakes.searches == 1


def test_a_search_changes_nothing(client: Client, settings: Settings) -> None:
    client.json("POST", "/api/discovery/search")
    assert not settings_path(settings).exists()
    assert effective_settings(settings).server_url == ""


# -- choosing -----------------------------------------------------------------------------


def test_choosing_a_server(client: Client, settings: Settings) -> None:
    status, body = client.json("POST", "/api/server", {"url": LAN_URL, "id": ID_A})
    assert status == 200
    assert body["outcome"] == "saved"
    assert body["refresh"] in ("refreshed", "failed")
    assert body["server"] == {"url": LAN_URL, "source": "store", "id": ID_A, "version": None}
    assert stored(settings) == {"server_url": LAN_URL, "server_id": ID_A}


def test_the_version_shows_once_a_search_has_seen_the_server(client: Client) -> None:
    client.json("POST", "/api/discovery/search")
    _, body = client.json("POST", "/api/server", {"url": LAN_URL})
    assert body["server"]["version"] == "3.22.1"


def test_a_manual_url_without_an_id_works(client: Client, settings: Settings) -> None:
    status, body = client.json("POST", "/api/server", {"url": "https://studylife.lan/"})
    assert (status, body["outcome"]) == (200, "saved")
    assert stored(settings)["server_url"] == LAN_URL


def test_an_unverifiable_manual_url_is_saved_and_says_so(
    client: Client, settings: Settings
) -> None:
    status, body = client.json("POST", "/api/server", {"url": "http://192.168.1.9:8080"})
    assert (status, body["outcome"]) == (200, "saved_unverified")
    assert body["server"]["id"] is None
    assert stored(settings) == {"server_url": "http://192.168.1.9:8080"}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"url": 5},
        {"url": LAN_URL, "id": 5},
        {"url": "ftp://x"},
        {"url": "https://u:p@x"},
        {"url": "https://x/path"},
        {"url": LAN_URL, "id": "not-an-id"},
    ],
)
def test_invalid_input_is_a_400_and_writes_nothing(
    client: Client, settings: Settings, body: dict[str, Any]
) -> None:
    status, payload = client.json("POST", "/api/server", body)
    assert status == 400
    assert payload["error"] == "invalid_url"
    assert not settings_path(settings).exists()


def test_a_missing_body_is_a_400(client: Client) -> None:
    status, _, _ = client.request("POST", "/api/server", None)
    assert status == 400  # an empty body is {} -> no url


def test_a_mismatching_id_is_a_409(client: Client, settings: Settings, fakes: Fakes) -> None:
    fakes.answers[LAN_URL] = (ID_B, "3.22.1")
    status, payload = client.json("POST", "/api/server", {"url": LAN_URL, "id": ID_A})
    assert (status, payload["error"]) == (409, "instance_id_mismatch")
    assert not settings_path(settings).exists()


def test_an_announced_server_that_vanished_is_a_502(
    client: Client, settings: Settings, fakes: Fakes
) -> None:
    fakes.answers[LAN_URL] = None
    status, payload = client.json("POST", "/api/server", {"url": LAN_URL, "id": ID_A})
    assert (status, payload["error"]) == (502, "unreachable")
    assert not settings_path(settings).exists()


def test_choosing_another_server_stops_using_the_key_of_the_previous_one(
    tmp_path: Path, fakes: Fakes
) -> None:
    settings = make_settings(
        tmp_path,
        studylife_base_url=None,
        studylife_api_key="old-key",
        studylife_api_key_instance="https://old.example.org",
    )
    update_overrides(settings, server_url="https://old.example.org", server_id=ID_B)
    assert effective_settings(settings).studylife_api_key == "old-key"
    for client, _ in run(settings, fakes):
        status, _ = client.json("POST", "/api/server", {"url": LAN_URL, "id": ID_A})
        assert status == 200
        assert effective_settings(settings).studylife_api_key == ""
        _, state = client.json("GET", "/api/state")
        assert state["setup"] is True
        assert state["server"]["url"] == LAN_URL


def test_connect_start_without_a_server_is_a_409(client: Client) -> None:
    status, payload = client.json("POST", "/api/connect/start")
    assert (status, payload["error"]) == (409, "no_server")


def test_state_reports_the_server(client: Client) -> None:
    _, state = client.json("GET", "/api/state")
    assert state["server"] == {"url": "", "source": "none", "id": None, "version": None}
    assert state["server_configured"] is False
    assert state["status"] == "setup"


# -- STUDYLIFE_BASE_URL wins ---------------------------------------------------------------


def test_an_environment_url_cannot_be_changed_through_the_api(tmp_path: Path, fakes: Fakes) -> None:
    settings = make_settings(tmp_path)  # BASE_URL in the environment
    for client, _ in run(settings, fakes):
        _, listing = client.json("GET", "/api/discovery")
        assert listing["locked"] is True
        assert listing["server"]["source"] == "env"
        assert client.json("POST", "/api/server", {"url": LAN_URL})[0] == 409
        status, payload = client.json("POST", "/api/discovery/search")
        assert (status, payload["error"]) == (409, "server_fixed_by_environment")
    assert fakes.searches == 0
    assert not settings_path(settings).exists()
