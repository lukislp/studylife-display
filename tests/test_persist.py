"""`persist-export` / `persist-import`: the layout choice's round trip over the boot partition.

The tests drive the CLI the way the systemd units do (environment plus subcommand) with a
temp directory standing in for /var/lib/studylife-display and another for /boot/firmware.
"""

import json
import os
from pathlib import Path

import pytest

from studylife_display.config import Settings
from studylife_display.main import main
from studylife_display.settings_store import (
    load_layout_choice,
    persist_path,
    save_layout_choice,
)

INVALID = ["{not json", '{"layout": "holographic"}', '["auto"]', ""]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    state = tmp_path / "state"
    boot = tmp_path / "boot" / "studylife-display"
    monkeypatch.setenv("STUDYLIFE_BASE_URL", "https://studylife.test")
    monkeypatch.setenv("STUDYLIFE_API_KEY", "test-key")
    monkeypatch.setenv("DISPLAY_STATE_PATH", str(state / "last.json"))
    monkeypatch.setenv("DISPLAY_PERSIST_PATH", str(boot / "settings.json"))
    return {"local": state / "settings.json", "persisted": boot / "settings.json"}


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def read(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def age(path: Path, seconds: int) -> None:
    """Back-dates a file's mtime so that "newer" comparisons do not hinge on timer resolution."""
    stamp = path.stat().st_mtime - seconds
    os.utime(path, (stamp, stamp))


def test_default_points_at_the_bookworm_boot_partition(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DISPLAY_PERSIST_PATH", raising=False)
    settings = Settings(studylife_base_url="https://studylife.test", studylife_api_key="k")  # type: ignore[arg-type]
    assert persist_path(settings) == Path("/boot/firmware/studylife-display/settings.json")


# --- export -----------------------------------------------------------------------------


def test_export_copies_a_valid_choice_and_creates_the_directory(env: dict[str, Path]) -> None:
    write(env["local"], '{"layout": "exam"}')
    assert not env["persisted"].parent.exists()
    assert main(["persist-export"]) == 0
    assert read(env["persisted"]) == {"layout": "exam"}
    assert not env["persisted"].with_suffix(".json.tmp").exists()


def test_export_without_a_settings_file_is_a_silent_no_op(env: dict[str, Path]) -> None:
    assert main(["persist-export"]) == 0
    assert not env["persisted"].exists()
    assert not env["persisted"].parent.exists()


@pytest.mark.parametrize("content", INVALID)
def test_export_refuses_invalid_content_and_keeps_the_old_copy(
    env: dict[str, Path], content: str
) -> None:
    write(env["persisted"], '{"layout": "week"}')
    write(env["local"], content)
    assert main(["persist-export"]) == 1
    assert read(env["persisted"]) == {"layout": "week"}
    assert not env["persisted"].with_suffix(".json.tmp").exists()


def test_export_does_not_rewrite_an_identical_copy(env: dict[str, Path]) -> None:
    write(env["local"], '{"layout": "focus"}')
    assert main(["persist-export"]) == 0
    age(env["persisted"], 3600)
    before = env["persisted"].stat().st_mtime
    assert main(["persist-export"]) == 0
    assert env["persisted"].stat().st_mtime == before


# --- import -----------------------------------------------------------------------------


def test_import_restores_the_choice_into_the_state_directory(env: dict[str, Path]) -> None:
    write(env["persisted"], '{"layout": "focus"}')
    assert not env["local"].parent.exists()
    assert main(["persist-import"]) == 0
    assert read(env["local"]) == {"layout": "focus"}
    assert not env["local"].with_suffix(".json.tmp").exists()


def test_import_without_a_copy_is_a_no_op(env: dict[str, Path]) -> None:
    assert main(["persist-import"]) == 0
    assert not env["local"].exists()


@pytest.mark.parametrize("content", INVALID)
def test_import_never_replaces_a_valid_local_file_with_an_invalid_copy(
    env: dict[str, Path], content: str
) -> None:
    write(env["local"], '{"layout": "week"}')
    write(env["persisted"], content)
    assert main(["persist-import"]) == 1
    assert read(env["local"]) == {"layout": "week"}


@pytest.mark.parametrize("content", INVALID)
def test_import_of_an_invalid_copy_creates_nothing(env: dict[str, Path], content: str) -> None:
    write(env["persisted"], content)
    assert main(["persist-import"]) == 1
    assert not env["local"].exists()


def test_import_keeps_a_newer_local_choice(env: dict[str, Path]) -> None:
    write(env["persisted"], '{"layout": "focus"}')
    age(env["persisted"], 3600)
    write(env["local"], '{"layout": "exam"}')
    assert main(["persist-import"]) == 0
    assert read(env["local"]) == {"layout": "exam"}


def test_import_replaces_an_older_local_choice(env: dict[str, Path]) -> None:
    write(env["local"], '{"layout": "exam"}')
    age(env["local"], 3600)
    write(env["persisted"], '{"layout": "focus"}')
    assert main(["persist-import"]) == 0
    assert read(env["local"]) == {"layout": "focus"}


# --- both -------------------------------------------------------------------------------


def test_empty_persist_path_disables_both_directions(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DISPLAY_PERSIST_PATH", "")
    write(env["local"], '{"layout": "exam"}')
    assert main(["persist-export"]) == 0
    assert not env["persisted"].parent.exists()
    write(env["persisted"], '{"layout": "focus"}')
    assert main(["persist-import"]) == 0
    assert read(env["local"]) == {"layout": "exam"}


def test_reboot_round_trip_through_the_web_writer(env: dict[str, Path]) -> None:
    """What the overlay does: the web service saves, the path unit exports, the reboot wipes
    the state directory, the restore unit imports, the loader sees the old choice."""
    settings = Settings()  # type: ignore[call-arg]
    save_layout_choice(settings, "week")
    assert main(["persist-export"]) == 0
    env["local"].unlink()
    assert load_layout_choice(settings) == "auto"
    assert main(["persist-import"]) == 0
    assert load_layout_choice(settings) == "week"
