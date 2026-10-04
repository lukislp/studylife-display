import json
import logging
from pathlib import Path

import pytest

from studylife_display.config import CONCRETE_LAYOUTS, LAYOUT_CHOICES, Settings
from studylife_display.settings_store import (
    effective_settings,
    is_valid_choice,
    load_layout_choice,
    override_sources,
    save_layout_choice,
    settings_path,
    update_overrides,
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


def test_valid_choices_are_the_layouts_plus_the_pseudo_choices() -> None:
    # config.LAYOUT_CHOICES is the literal pydantic validates against; it must list exactly
    # the registry plus "auto", or a layout could be rendered but never chosen.
    assert valid_choices() == set(LAYOUT_CHOICES)
    assert {"auto", "duo", "classic", "month", "year", "quiet"} <= valid_choices()
    assert set(CONCRETE_LAYOUTS) == valid_choices() - {"auto", "duo"}


def test_semester_is_an_alias_of_degree(settings: Settings) -> None:
    assert is_valid_choice("semester") and is_valid_choice("degree")
    assert "semester" not in valid_choices()
    assert (
        Settings(
            studylife_base_url="https://studylife.test",  # type: ignore[arg-type]
            display_layout="semester",  # type: ignore[arg-type]
            display_duo="semester,agenda",
        ).display_layout
        == "degree"
    )
    duo = Settings(
        studylife_base_url="https://studylife.test",  # type: ignore[arg-type]
        display_duo="semester,agenda",
    ).display_duo
    assert duo == "degree,agenda"


def test_a_settings_file_with_the_old_name_loads_and_is_rewritten_canonically(
    settings: Settings,
) -> None:
    path = settings_path(settings)
    path.parent.mkdir(parents=True)
    path.write_text('{"layout": "semester", "duo": "agenda,semester"}', "utf-8")
    assert load_layout_choice(settings) == "degree"
    assert effective_settings(settings).display_duo == "agenda,degree"
    save_layout_choice(settings, "semester")
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "layout": "degree",
        "duo": "agenda,semester".replace("semester", "degree"),
    }


def test_a_legacy_cycle_key_is_ignored_not_fatal(settings: Settings) -> None:
    # 1.11.0 wrote a "cycle" key; a file from then must still yield its other choices.
    path = settings_path(settings)
    path.parent.mkdir(parents=True)
    path.write_text('{"layout": "month", "cycle": "classic,week", "language": "en"}', "utf-8")
    assert load_layout_choice(settings) == "month"
    assert effective_settings(settings).display_language == "en"
    # The next write drops it for good.
    save_layout_choice(settings, "week")
    assert json.loads(path.read_text(encoding="utf-8")) == {"layout": "week", "language": "en"}


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


class TestRecapMinutesOverride:
    def test_round_trip_and_precedence(self, settings: Settings) -> None:
        assert effective_settings(settings).display_auto_recap_minutes == 10
        update_overrides(settings, auto_recap_minutes=25)
        assert json.loads(settings_path(settings).read_text(encoding="utf-8")) == {
            "auto_recap_minutes": 25
        }
        assert effective_settings(settings).display_auto_recap_minutes == 25
        assert override_sources(settings)["auto_recap_minutes"] is True
        update_overrides(settings, auto_recap_minutes=0)  # 0 = off is a value, not "unset"
        assert effective_settings(settings).display_auto_recap_minutes == 0
        update_overrides(settings, auto_recap_minutes=None)
        assert effective_settings(settings).display_auto_recap_minutes == 10
        assert override_sources(settings)["auto_recap_minutes"] is False

    @pytest.mark.parametrize("bad", [-1, 241, "10", 2.5, True])
    def test_invalid_values_are_rejected_and_nothing_is_written(
        self, settings: Settings, bad: object
    ) -> None:
        with pytest.raises(ValueError):
            update_overrides(settings, auto_recap_minutes=bad)
        assert not settings_path(settings).exists()

    def test_a_damaged_file_is_ignored(self, settings: Settings) -> None:
        path = settings_path(settings)
        path.parent.mkdir(parents=True)
        path.write_text('{"auto_recap_minutes": "soon"}', encoding="utf-8")
        assert effective_settings(settings).display_auto_recap_minutes == 10
