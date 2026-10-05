"""The "StudyLife server" section of the web interface: searching the network, choosing a
server, the manual address, CSRF, the environment lock and the stored-key rule."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from test_web import TOKEN, Client, make_settings

from studylife_display import main as main_module
from studylife_display.config import Settings
from studylife_display.discovery import DiscoveryResult, Server
from studylife_display.settings_store import effective_settings, settings_path, update_overrides
from studylife_display.web import DisplayServer, make_server

ID_A = "0123456789abcdef0123456789abcdef"
ID_B = "fedcba9876543210fedcba9876543210"
LAN_URL = "https://studylife.lan"
OLD_URL = "https://old.example.org"


def found(url: str = LAN_URL, id_: str = ID_A, name: str = "StudyLife") -> Server:
    return Server(name, url, id_, "3.22.1", url.startswith("https://"))


class Fakes:
    """What the server's network helpers answer in these tests."""

    def __init__(self) -> None:
        self.servers: tuple[Server, ...] = (found(),)
        self.ignored = 0
        self.answers: dict[str, tuple[str, str] | None] = {LAN_URL: (ID_A, "3.22.1")}
        self.searches = 0
        self.fetched: list[str] = []

    def discover(self) -> DiscoveryResult:
        self.searches += 1
        return DiscoveryResult(self.servers, self.ignored, 1.0)

    def fetch_instance(self, url: str) -> tuple[str, str] | None:
        self.fetched.append(url)
        return self.answers.get(url)


@pytest.fixture
def fakes() -> Fakes:
    return Fakes()


def start(settings: Settings, fakes: Fakes) -> Iterator[Client]:
    def refresh() -> int:
        return main_module.refresh_panel(effective_settings(settings), force=True)

    instance: DisplayServer = make_server(settings, refresh, "127.0.0.1:0")
    instance.app.discover = fakes.discover
    instance.app.fetch_instance = fakes.fetch_instance
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    client = Client(instance.server_address[1])
    client.login(TOKEN)
    try:
        yield client
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """No server in the environment, no key either."""
    return make_settings(tmp_path, studylife_base_url=None, studylife_api_key="")


@pytest.fixture
def client(settings: Settings, fakes: Fakes) -> Iterator[Client]:
    yield from start(settings, fakes)


def post(client: Client, path: str, form: dict[str, str]) -> tuple[int, str]:
    status, headers, _ = client.request("POST", path, form, headers=client.same_origin())
    return status, headers.get("location", "")


def stored(settings: Settings) -> dict[str, Any]:
    path = settings_path(settings)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


# -- the page -----------------------------------------------------------------------------


def test_the_page_needs_the_session(client: Client) -> None:
    status, _, _ = client.request("GET", "/server", with_cookie=False)
    assert status == 403


def test_the_page_says_that_no_server_is_chosen_and_offers_both_ways(client: Client) -> None:
    status, _, body = client.request("GET", "/server")
    page = body.decode()
    assert status == 200
    assert "No server chosen yet" in page or "Noch kein Server gewählt" in page
    assert "action='/server/search'" in page
    assert "action='/server/use'" in page
    assert "name='url'" in page
    assert "href='/server'" in page  # the navigation links to it


def test_the_layouts_page_points_at_the_server_page_while_there_is_no_server(
    client: Client,
) -> None:
    _, _, body = client.request("GET", "/")
    page = body.decode()
    assert "/server" in page
    assert "choose one at" in page or "Server wählen unter" in page


def test_the_connect_page_asks_for_a_server_first(client: Client) -> None:
    _, _, body = client.request("GET", "/connect")
    page = body.decode()
    assert "action='/connect/start'" not in page
    assert "href='/server'" in page


def test_starting_a_connect_attempt_without_a_server_goes_to_the_server_page(
    client: Client,
) -> None:
    assert post(client, "/connect/start", {}) == (303, "/server")


# -- searching ----------------------------------------------------------------------------


def test_a_search_lists_what_was_found_and_changes_nothing(
    client: Client, settings: Settings, fakes: Fakes
) -> None:
    assert post(client, "/server/search", {}) == (303, "/server?m=searched")
    assert fakes.searches == 1
    _, _, body = client.request("GET", "/server?m=searched")
    page = body.decode()
    assert LAN_URL in page and "3.22.1" in page
    assert f"name='id' value='{ID_A}'" in page
    assert not settings_path(settings).exists()  # only proposed
    assert fakes.fetched == []


def test_an_empty_search_explains_what_to_check(client: Client, fakes: Fakes) -> None:
    fakes.servers = ()
    fakes.ignored = 2
    post(client, "/server/search", {})
    _, _, body = client.request("GET", "/server")
    page = body.decode()
    assert "VLAN" in page
    assert "2 " in page


def test_announced_text_is_escaped_in_the_results(client: Client, fakes: Fakes) -> None:
    fakes.servers = (found(name="<script>alert(1)</script>"),)
    post(client, "/server/search", {})
    _, _, body = client.request("GET", "/server")
    page = body.decode()
    assert "<script>alert(1)" not in page
    assert "&lt;script&gt;" in page


def test_a_search_needs_a_session_and_the_same_origin(client: Client, fakes: Fakes) -> None:
    anonymous = client.request("POST", "/server/search", {}, with_cookie=False)
    assert anonymous[0] == 403
    cross = client.request("POST", "/server/search", {}, headers={"Sec-Fetch-Site": "cross-site"})
    assert cross[0] == 403
    no_origin = client.request("POST", "/server/search", {})
    assert no_origin[0] == 403
    assert fakes.searches == 0


def test_a_second_search_while_one_runs_is_not_started(
    client: Client, settings: Settings, fakes: Fakes
) -> None:
    started = threading.Event()
    release = threading.Event()
    real = fakes.discover

    def slow() -> DiscoveryResult:
        started.set()
        release.wait(5)
        return real()

    # Drive the app object directly: two overlapping searches run `discover` once.
    from studylife_display.web import WebApp

    app = WebApp(settings, lambda: 0)
    app.discover = slow
    first: list[DiscoveryResult | None] = []
    thread = threading.Thread(target=lambda: first.append(app.search_servers()))
    thread.start()
    assert started.wait(5)
    assert app.search_servers() is None
    release.set()
    thread.join(5)
    assert first and first[0] is not None
    assert app.last_discovery() is first[0]


# -- choosing -----------------------------------------------------------------------------


def test_choosing_a_found_server_stores_url_and_id(
    client: Client, settings: Settings, fakes: Fakes
) -> None:
    result = post(client, "/server/use", {"url": LAN_URL, "id": ID_A})
    assert result == (303, "/server?m=saved")
    assert stored(settings) == {"server_url": LAN_URL, "server_id": ID_A}
    assert fakes.fetched == [LAN_URL]  # verified again at choice time
    _, _, body = client.request("GET", "/server?m=saved")
    page = body.decode()
    assert f"Server in use: {LAN_URL}" in page or f"Verwendeter Server: {LAN_URL}" in page
    assert ID_A in page
    assert "href='/connect'" in page  # the next step
    _, _, connect = client.request("GET", "/connect")
    assert "action='/connect/start'" in connect.decode()


def test_the_panel_is_refreshed_after_choosing(client: Client, settings: Settings) -> None:
    post(client, "/server/use", {"url": LAN_URL, "id": ID_A})
    _, _, health = client.request("GET", "/healthz")
    report = json.loads(health)
    assert report["server_configured"] is True
    assert report["status"] == "setup"  # a server, but no key yet
    assert (Path(settings.display_state_path).parent / "current.json").exists()


def test_a_mismatching_instance_id_is_refused(
    client: Client, settings: Settings, fakes: Fakes
) -> None:
    fakes.answers[LAN_URL] = (ID_B, "3.22.1")
    assert post(client, "/server/use", {"url": LAN_URL, "id": ID_A}) == (303, "/server?m=mismatch")
    assert not settings_path(settings).exists()


def test_an_announced_server_that_does_not_answer_is_refused(
    client: Client, settings: Settings, fakes: Fakes
) -> None:
    fakes.answers[LAN_URL] = None
    assert post(client, "/server/use", {"url": LAN_URL, "id": ID_A}) == (
        303,
        "/server?m=unreachable",
    )
    assert not settings_path(settings).exists()


@pytest.mark.parametrize(
    "url",
    [
        "",
        "studylife.lan",
        "javascript:alert(1)",
        "https://user:pw@studylife.lan",
        "https://studylife.lan/some/path",
        "ftp://studylife.lan",
    ],
)
def test_an_invalid_address_is_refused_before_anything_is_fetched(
    client: Client, settings: Settings, fakes: Fakes, url: str
) -> None:
    assert post(client, "/server/use", {"url": url}) == (303, "/server?m=invalid")
    assert not settings_path(settings).exists()
    assert fakes.fetched == []


def test_a_malformed_announced_id_is_refused(client: Client, settings: Settings) -> None:
    assert post(client, "/server/use", {"url": LAN_URL, "id": "zz"}) == (303, "/server?m=invalid")
    assert not settings_path(settings).exists()


def test_a_manual_address_is_verified_when_the_server_answers(
    client: Client, settings: Settings
) -> None:
    assert post(client, "/server/use", {"url": LAN_URL + "/"}) == (303, "/server?m=saved")
    assert stored(settings) == {"server_url": LAN_URL, "server_id": ID_A}


def test_a_manual_address_of_a_server_without_the_endpoint_is_saved_unverified(
    client: Client, settings: Settings, fakes: Fakes
) -> None:
    update_overrides(settings, server_url=OLD_URL, server_id=ID_B)
    assert post(client, "/server/use", {"url": "http://192.168.1.9:8080"}) == (
        303,
        "/server?m=saved_unverified",
    )
    # The id of the previous server must not stick to the new address.
    assert stored(settings) == {"server_url": "http://192.168.1.9:8080"}


def test_plain_http_gets_a_warning(client: Client) -> None:
    post(client, "/server/use", {"url": "http://192.168.1.9:8080"})
    _, _, body = client.request("GET", "/server")
    assert "class='warn'" in body.decode()
    post(client, "/server/use", {"url": LAN_URL})
    _, _, body = client.request("GET", "/server")
    assert "http: the API key" not in body.decode()


def test_choosing_needs_a_session_and_the_same_origin(client: Client, settings: Settings) -> None:
    form = {"url": LAN_URL, "id": ID_A}
    assert client.request("POST", "/server/use", form, with_cookie=False)[0] == 403
    assert client.request("POST", "/server/use", form)[0] == 403
    cross = client.request("POST", "/server/use", form, headers={"Sec-Fetch-Site": "cross-site"})
    assert cross[0] == 403
    assert not settings_path(settings).exists()


# -- the key belongs to the server that issued it -----------------------------------------


def test_choosing_another_server_stops_using_the_stored_key(tmp_path: Path, fakes: Fakes) -> None:
    settings = make_settings(
        tmp_path,
        studylife_base_url=None,
        studylife_api_key="old-key",
        studylife_api_key_instance=OLD_URL,
    )
    update_overrides(settings, server_url=OLD_URL, server_id=ID_B)
    assert effective_settings(settings).studylife_api_key == "old-key"
    for client in start(settings, fakes):
        assert post(client, "/server/use", {"url": LAN_URL, "id": ID_A})[1] == "/server?m=saved"
        effective = effective_settings(settings)
        assert effective.server_url == LAN_URL
        assert effective.studylife_api_key == ""
        _, _, connect = client.request("GET", "/connect")
        assert "No key stored yet" in connect.decode() or "Noch kein Schlüssel" in connect.decode()
        report = json.loads(client.request("GET", "/healthz")[2])
        assert report["setup"] is True
        # Going back to the server that issued the key brings the key back into use.
        fakes.answers[OLD_URL] = (ID_B, "3.22.1")
        post(client, "/server/use", {"url": OLD_URL, "id": ID_B})
        assert effective_settings(settings).studylife_api_key == "old-key"


def test_choosing_another_server_drops_its_cache_and_a_pending_attempt(
    tmp_path: Path, fakes: Fakes
) -> None:
    settings = make_settings(tmp_path, studylife_base_url=None, studylife_api_key="")
    update_overrides(settings, server_url=OLD_URL, server_id=ID_B)
    cache = Path(settings.display_state_path)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("{}", encoding="utf-8")
    for client in start(settings, fakes):
        post(client, "/connect/start", {})
        _, _, page = client.request("GET", "/connect")
        assert "connect/client/" in page.decode()  # an attempt is pending
        post(client, "/server/use", {"url": LAN_URL, "id": ID_A})
        assert not cache.exists()
        _, _, page = client.request("GET", "/connect")
        assert "connect/client/" not in page.decode()


def test_choosing_the_same_server_again_keeps_the_cache(tmp_path: Path, fakes: Fakes) -> None:
    settings = make_settings(tmp_path, studylife_base_url=None, studylife_api_key="")
    update_overrides(settings, server_url=LAN_URL, server_id=ID_A)
    cache = Path(settings.display_state_path)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("{}", encoding="utf-8")
    for client in start(settings, fakes):
        post(client, "/server/use", {"url": LAN_URL, "id": ID_A})
        assert cache.exists()


# -- STUDYLIFE_BASE_URL wins ---------------------------------------------------------------


@pytest.fixture
def env_client(tmp_path: Path, fakes: Fakes) -> Iterator[tuple[Client, Settings]]:
    settings = make_settings(tmp_path, studylife_base_url="https://studylife.env.test")
    for client in start(settings, fakes):
        yield client, settings


def test_with_an_environment_url_the_server_cannot_be_changed(
    env_client: tuple[Client, Settings], fakes: Fakes
) -> None:
    client, settings = env_client
    assert post(client, "/server/use", {"url": LAN_URL, "id": ID_A}) == (303, "/server?m=locked")
    assert post(client, "/server/search", {}) == (303, "/server?m=locked")
    assert not settings_path(settings).exists()
    assert fakes.searches == 0 and fakes.fetched == []
    _, _, body = client.request("GET", "/server")
    page = body.decode()
    assert "https://studylife.env.test" in page
    assert "STUDYLIFE_BASE_URL" in page
    assert "action='/server/use'" not in page
    assert "action='/server/search'" not in page


def test_an_environment_url_install_behaves_as_before(
    env_client: tuple[Client, Settings],
) -> None:
    client, settings = env_client
    update_overrides(settings, server_url=LAN_URL, server_id=ID_A)  # a leftover choice
    assert effective_settings(settings).server_url == "https://studylife.env.test"
    _, _, connect = client.request("GET", "/connect")
    assert "action='/connect/start'" in connect.decode()
    report = json.loads(client.request("GET", "/healthz")[2])
    assert report["server_configured"] is True
    assert report["setup"] is False  # the key "k" is used as it always was
