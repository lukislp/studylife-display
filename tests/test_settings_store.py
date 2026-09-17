import json
import logging
from pathlib import Path

import pytest

from studylife_display.config import Settings
from studylife_display.settings_store import (
    load_layout_choice,
    save_layout_choice,
    settings_path,
    valid_choices,
)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        studylife_base_url="https://studylife.test",  # type: ignore[arg-type]
        studylife_api_key="k",
        display_state_path=str(tmp_path / "state" / "last.json"),
        display_layout="week",
    )


def test_settings_file_lives_next_to_the_cache(settings: Settings, tmp_path: Path) -> None:
    assert settings_path(settings) == tmp_path / "state" / "settings.json"


def test_env_default_without_a_file(settings: Settings) -> None:
    assert load_layout_choice(settings) == "week"


def test_round_trip(settings: Settings) -> None:
    path = save_layout_choice(settings, "exam")
    assert json.loads(path.read_text(encoding="utf-8")) == {"layout": "exam"}
    assert load_layout_choice(settings) == "exam"
    save_layout_choice(settings, "auto")
    assert load_layout_choice(settings) == "auto"
    assert not path.with_suffix(".json.tmp").exists()


def test_valid_choices_are_the_layouts_plus_auto() -> None:
    assert valid_choices() == {"auto", "classic", "focus", "exam", "week", "semester"}


def test_invalid_key_is_rejected_and_nothing_is_written(settings: Settings) -> None:
    with pytest.raises(ValueError):
        save_layout_choice(settings, "holographic")
    assert not settings_path(settings).exists()


@pytest.mark.parametrize("content", ["{not json", '{"layout": "holographic"}', '["auto"]', ""])
def test_corrupt_file_falls_back_to_the_env_default_with_a_warning(
    settings: Settings, content: str, caplog: pytest.LogCaptureFixture
) -> None:
    path = settings_path(settings)
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="studylife_display.settings_store"):
        assert load_layout_choice(settings) == "week"
    assert any("DISPLAY_LAYOUT=week" in record.getMessage() for record in caplog.records)
