"""STUDYLIFE_BASE_URL is optional: the server may come from settings.json (chosen in the web
interface), the environment keeps precedence, a key is only used with the server that issued
it, and a display without a server shows the "choose your server" screen."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
import respx
from PIL import Image, ImageChops

from studylife_display import main as main_module
from studylife_display.config import Settings, WebOverrides
from studylife_display.connect import setup_server_url, write_pending_credentials
from studylife_display.credentials import ENV_KEY_INSTANCE, apply_pending_credentials
from studylife_display.current_frame import load_current_frame
from studylife_display.health import health_report
from studylife_display.layouts.setup import render_setup
from studylife_display.main import main
from studylife_display.settings_store import (
    effective_settings,
    export_layout_choice,
    import_layout_choice,
    server_source,
    settings_path,
    update_overrides,
)

ID_A = "0123456789abcdef0123456789abcdef"
STORE_URL = "https://studylife.lan"
ENV_URL = "https://studylife.env.test"


def make(tmp_path: Path, **values: Any) -> Settings:
    base: dict[str, Any] = {
        "display_state_path": str(tmp_path / "state" / "last.json"),
        "display_driver": "file",
        "display_output_path": str(tmp_path / "frame.png"),
        "studylife_timezone": "Europe/Berlin",
    }
    base.update(values)
    return Settings(**base)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("STUDYLIFE_BASE_URL", "STUDYLIFE_API_KEY", "STUDYLIFE_API_KEY_INSTANCE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(Path(__file__).parent)  # no stray .env


# -- the setting itself -------------------------------------------------------------------


def test_the_base_url_is_optional(tmp_path: Path) -> None:
    assert make(tmp_path).studylife_base_url is None
    assert make(tmp_path).server_url == ""


@pytest.mark.parametrize("empty", ["", "   "])
def test_an_empty_base_url_means_no_server(tmp_path: Path, empty: str) -> None:
    assert make(tmp_path, studylife_base_url=empty).server_url == ""


def test_an_empty_env_value_is_read_as_no_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUDYLIFE_BASE_URL", "")
    assert Settings().studylife_base_url is None


def test_a_garbage_base_url_is_still_an_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        make(tmp_path, studylife_base_url="not a url")


def test_server_url_has_no_trailing_slash(tmp_path: Path) -> None:
    assert make(tmp_path, studylife_base_url="https://studylife.test/").server_url == (
        "https://studylife.test"
    )


# -- settings.json holds the chosen server ------------------------------------------------


def test_overrides_accept_a_valid_server_and_normalise_it() -> None:
    parsed = WebOverrides(server_url="HTTPS://StudyLife.LAN/", server_id=ID_A.upper())
    assert parsed.as_json() == {"server_url": STORE_URL, "server_id": ID_A}


@pytest.mark.parametrize(
    "values",
    [
        {"server_url": "ftp://x"},
        {"server_url": "https://u:p@x"},
        {"server_url": "https://x/path"},
        {"server_url": ""},
        {"server_id": "nope"},
        {"server_id": ""},
    ],
)
def test_overrides_reject_invalid_servers(values: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        WebOverrides(**values)


def test_update_overrides_rejects_an_invalid_server_and_writes_nothing(tmp_path: Path) -> None:
    settings = make(tmp_path)
    with pytest.raises(ValueError):
        update_overrides(settings, server_url="https://u:p@x")
    assert not settings_path(settings).exists()


def test_the_store_is_used_when_the_environment_has_no_server(tmp_path: Path) -> None:
    settings = make(tmp_path)
    update_overrides(settings, server_url=STORE_URL, server_id=ID_A)
    effective = effective_settings(settings)
    assert effective.server_url == STORE_URL
    assert effective.studylife_server_id == ID_A
    assert server_source(settings) == "store"


def test_nothing_chosen_means_no_server(tmp_path: Path) -> None:
    settings = make(tmp_path)
    assert effective_settings(settings).server_url == ""
    assert server_source(settings) == "none"


def test_the_environment_wins_over_the_store(tmp_path: Path) -> None:
    settings = make(tmp_path, studylife_base_url=ENV_URL, studylife_api_key="k")
    update_overrides(settings, server_url=STORE_URL, server_id=ID_A)
    effective = effective_settings(settings)
    assert effective.server_url == ENV_URL
    assert effective.studylife_server_id == ""
    assert effective.studylife_api_key == "k"
    assert server_source(settings) == "env"


def test_an_install_with_an_env_url_is_untouched_without_server_settings(tmp_path: Path) -> None:
    """Existing installs: no server keys anywhere, the effective settings ARE the env ones."""
    settings = make(tmp_path, studylife_base_url=ENV_URL, studylife_api_key="k")
    assert effective_settings(settings) is settings
    update_overrides(settings, language="en")
    effective = effective_settings(settings)
    assert effective.studylife_base_url == settings.studylife_base_url
    assert effective.studylife_api_key == "k"
    assert effective.display_language == "en"


def test_a_key_is_dropped_unless_it_was_issued_by_the_stored_server(tmp_path: Path) -> None:
    update = {"server_url": STORE_URL, "server_id": ID_A}
    # No record of where the key came from (an old key in the environment file).
    unknown = make(tmp_path, studylife_api_key="old-key")
    update_overrides(unknown, **update)
    assert effective_settings(unknown).studylife_api_key == ""
    # The key was issued by another server.
    other = make(
        tmp_path,
        studylife_api_key="old-key",
        studylife_api_key_instance="https://other.example.org",
    )
    assert effective_settings(other).studylife_api_key == ""
    # The key was issued by exactly this server (spelling differences do not matter).
    same = make(
        tmp_path,
        studylife_api_key="good-key",
        studylife_api_key_instance="https://StudyLife.lan/",
    )
    assert effective_settings(same).studylife_api_key == "good-key"


def test_a_garbage_key_origin_counts_as_unknown(tmp_path: Path) -> None:
    settings = make(tmp_path, studylife_api_key="k", studylife_api_key_instance="junk")
    update_overrides(settings, server_url=STORE_URL)
    assert effective_settings(settings).studylife_api_key == ""


def test_the_server_choice_is_mirrored_to_the_boot_partition(tmp_path: Path) -> None:
    persist = tmp_path / "boot" / "settings.json"
    settings = make(tmp_path, display_persist_path=str(persist))
    update_overrides(settings, server_url=STORE_URL, server_id=ID_A)
    assert export_layout_choice(settings) == 0
    assert json.loads(persist.read_text(encoding="utf-8"))["server_url"] == STORE_URL
    settings_path(settings).unlink()
    assert import_layout_choice(settings) == 0
    assert effective_settings(settings).server_url == STORE_URL


# -- credentials-apply records which server issued the key --------------------------------


def apply(tmp_path: Path, settings: Settings, instance: str) -> str:
    env_file = tmp_path / "studylife-display.env"
    env_file.write_text("STUDYLIFE_BASE_URL=\nSTUDYLIFE_API_KEY=\nX=1\n", encoding="utf-8")
    os.chmod(env_file, 0o640)
    from datetime import datetime

    write_pending_credentials(
        Path(settings.display_state_path).parent, "new-key", instance, datetime(2026, 1, 1)
    )
    assert apply_pending_credentials(settings, env_file, lambda args: None) == 0
    return env_file.read_text(encoding="utf-8")


def test_apply_records_the_issuing_server_when_the_server_is_not_in_the_environment(
    tmp_path: Path,
) -> None:
    text = apply(tmp_path, make(tmp_path), STORE_URL + "/")
    assert "STUDYLIFE_API_KEY=new-key\n" in text
    assert f"{ENV_KEY_INSTANCE}={STORE_URL}\n" in text
    assert "X=1\n" in text


def test_apply_leaves_an_install_with_an_env_url_as_it_was(tmp_path: Path) -> None:
    text = apply(tmp_path, make(tmp_path, studylife_base_url=ENV_URL), ENV_URL)
    assert text == "STUDYLIFE_BASE_URL=\nSTUDYLIFE_API_KEY=new-key\nX=1\n"


def test_key_and_origin_survive_a_round_trip_through_effective_settings(tmp_path: Path) -> None:
    settings = make(tmp_path)
    update_overrides(settings, server_url=STORE_URL, server_id=ID_A)
    text = apply(tmp_path, settings, STORE_URL)
    values = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    reloaded = make(
        tmp_path,
        studylife_api_key=values["STUDYLIFE_API_KEY"],
        studylife_api_key_instance=values[ENV_KEY_INSTANCE],
    )
    assert effective_settings(reloaded).studylife_api_key == "new-key"


# -- the "choose your server" screen ------------------------------------------------------


def test_the_server_setup_url_is_the_server_page_on_the_same_origin(tmp_path: Path) -> None:
    assert setup_server_url(make(tmp_path), "pi") == "http://pi.local:8795/server"
    assert setup_server_url(make(tmp_path, display_web_bind="0.0.0.0:9000"), "pi") == (
        "http://pi.local:9000/server"
    )
    public = make(tmp_path, display_public_base_url="https://pi.example.ts.net/")
    assert setup_server_url(public, "pi") == "https://pi.example.ts.net/server"
    explicit = make(tmp_path, display_setup_url="http://display.lan:8795/connect")
    assert setup_server_url(explicit, "pi") == "http://display.lan:8795/server"


def test_the_choose_server_frame_is_a_panel_frame_that_differs_from_the_connect_one() -> None:
    url = "http://pi.local:8795/server"
    for language in ("de", "en"):
        connect = render_setup(url, language, "pi")
        choose = render_setup(url, language, "pi", choose_server=True)
        assert choose.size == (800, 480) and choose.mode == "1"
        assert ImageChops.difference(connect, choose).getbbox() is not None
        # Same QR code: the box on the right is identical.
        box = (580, 130, 780, 330)
        assert ImageChops.difference(connect.crop(box), choose.crop(box)).getbbox() is None


@pytest.fixture
def no_server_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    monkeypatch.setenv("STUDYLIFE_TIMEZONE", "Europe/Berlin")
    monkeypatch.setenv("DISPLAY_DRIVER", "file")
    monkeypatch.setenv("DISPLAY_OUTPUT_PATH", str(tmp_path / "frame.png"))
    monkeypatch.setenv("DISPLAY_STATE_PATH", str(tmp_path / "state" / "last.json"))
    monkeypatch.setenv("DISPLAY_SETUP_URL", "http://pi.local:8795/connect")
    return {"frame": tmp_path / "frame.png", "state": tmp_path / "state" / "last.json"}


def test_run_without_a_server_shows_the_choose_server_screen_and_exits_cleanly(
    no_server_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    drawn: list[tuple[str, bool]] = []
    real = main_module.render_setup

    def spy(url: str, language: str, hostname: str, *args: Any, **kwargs: Any) -> Image.Image:
        drawn.append((url, bool(kwargs.get("choose_server"))))
        return real(url, language, hostname, *args, **kwargs)

    monkeypatch.setattr(main_module, "render_setup", spy)
    with respx.mock(assert_all_called=False) as router:
        assert main(["run"]) == 0
        assert not router.calls
    assert drawn == [("http://pi.local:8795/server", True)]
    state_dir = no_server_env["state"].parent
    from zoneinfo import ZoneInfo

    current = load_current_frame(state_dir, ZoneInfo("Europe/Berlin"))
    assert current is not None and current.kind == "setup"
    assert not no_server_env["state"].exists()


def test_refresh_now_without_a_server_also_exits_cleanly(
    no_server_env: dict[str, Path],
) -> None:
    assert main(["refresh-now"]) == 0


def test_a_stored_server_without_a_key_shows_the_connect_screen(
    no_server_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    update_overrides(Settings(), server_url=STORE_URL, server_id=ID_A)
    drawn: list[bool] = []
    real = main_module.render_setup

    def spy(url: str, language: str, hostname: str, *args: Any, **kwargs: Any) -> Image.Image:
        drawn.append(bool(kwargs.get("choose_server")))
        return real(url, language, hostname, *args, **kwargs)

    monkeypatch.setattr(main_module, "render_setup", spy)
    assert main(["run"]) == 0
    assert drawn == [False]


def test_check_without_a_server_fails_clearly(no_server_env: dict[str, Path]) -> None:
    assert main(["check"]) == 2


@respx.mock
def test_a_stored_server_with_its_bound_key_is_fetched(
    no_server_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch, sample: Any
) -> None:
    monkeypatch.setenv("STUDYLIFE_API_KEY", "bound-key")
    monkeypatch.setenv("STUDYLIFE_API_KEY_INSTANCE", STORE_URL)
    update_overrides(Settings(), server_url=STORE_URL, server_id=ID_A)
    route = respx.get(f"{STORE_URL}/api/metrics/summary").mock(
        side_effect=lambda request: __import__("httpx").Response(503)
    )
    main(["run"])
    assert route.called
    assert route.calls.last.request.headers["x-api-key"] == "bound-key"


@respx.mock
def test_the_old_key_is_never_sent_to_a_newly_chosen_server(
    no_server_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUDYLIFE_API_KEY", "key-of-the-old-server")
    monkeypatch.setenv("STUDYLIFE_API_KEY_INSTANCE", "https://old.example.org")
    update_overrides(Settings(), server_url=STORE_URL, server_id=ID_A)
    with respx.mock(assert_all_called=False) as router:
        assert main(["run"]) == 0
        assert not router.calls


# -- /healthz -----------------------------------------------------------------------------


def test_health_reports_setup_without_a_server(tmp_path: Path) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    settings = make(tmp_path)
    report, status = health_report(settings, datetime.now(ZoneInfo("Europe/Berlin")))
    assert status == 200
    assert report["status"] == "setup"
    assert report["setup"] is True
    assert report["server_configured"] is False


def test_health_with_a_server_but_no_key_is_still_setup(tmp_path: Path) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    settings = make(tmp_path, studylife_base_url=ENV_URL)
    report, _ = health_report(settings, datetime.now(ZoneInfo("Europe/Berlin")))
    assert (report["status"], report["server_configured"]) == ("setup", True)


def test_health_counts_a_stored_server_with_a_foreign_key_as_setup(tmp_path: Path) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    settings = make(tmp_path, studylife_api_key="k")
    update_overrides(settings, server_url=STORE_URL)
    report, _ = health_report(settings, datetime.now(ZoneInfo("Europe/Berlin")))
    assert (report["status"], report["server_configured"]) == ("setup", True)
