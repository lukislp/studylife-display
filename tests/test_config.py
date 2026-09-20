from pathlib import Path

import pytest
from pydantic import ValidationError

from studylife_display.config import Settings


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # no .env from the working tree
    monkeypatch.setenv("STUDYLIFE_BASE_URL", "https://studylife.test")
    monkeypatch.setenv("STUDYLIFE_API_KEY", "k")


def test_defaults(env: None) -> None:
    settings = Settings()  # type: ignore[call-arg]
    assert settings.display_rotate == 0
    assert settings.display_stale_error_hours == 24.0
    assert settings.display_quiet_hours == ""
    assert settings.display_clear_at == "04:00"
    assert settings.display_update_check is False
    assert settings.display_auto_review == "sun 18-24"
    assert settings.display_auto_agenda == "06-12"


@pytest.mark.parametrize("value", ["0", "180"])
def test_rotation_accepts_0_and_180(env: None, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("DISPLAY_ROTATE", value)
    assert Settings().display_rotate == int(value)  # type: ignore[call-arg]


@pytest.mark.parametrize("value", ["90", "270", "-180", "upside-down", "1"])
def test_rotation_rejects_anything_else(
    env: None, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("DISPLAY_ROTATE", value)
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


@pytest.mark.parametrize("value", ["23-7", "22:30-06:15", "", "13-15"])
def test_quiet_hours_accepts_both_notations_and_off(
    env: None, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("DISPLAY_QUIET_HOURS", value)
    assert Settings().display_quiet_hours == value  # type: ignore[call-arg]


@pytest.mark.parametrize("value", ["23", "7-7", "25-3", "night"])
def test_quiet_hours_rejects_garbage(
    env: None, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("DISPLAY_QUIET_HOURS", value)
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


@pytest.mark.parametrize("value", ["04:00", "4", "", "23:30"])
def test_clear_at_accepts_times_and_off(
    env: None, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("DISPLAY_CLEAR_AT", value)
    assert Settings().display_clear_at == value  # type: ignore[call-arg]


@pytest.mark.parametrize("value", ["24:00", "4:60", "dawn"])
def test_clear_at_rejects_garbage(env: None, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("DISPLAY_CLEAR_AT", value)
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


@pytest.mark.parametrize("value", ["0", "-1"])
def test_stale_hours_must_be_positive(
    env: None, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("DISPLAY_STALE_ERROR_HOURS", value)
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


@pytest.mark.parametrize("value", ["sun 18-24", "sat,sun 17:30-22", "", "mon-fri 6-9"])
def test_auto_windows_accept_the_rule_notation_and_off(
    env: None, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("DISPLAY_AUTO_REVIEW", value)
    monkeypatch.setenv("DISPLAY_AUTO_AGENDA", value)
    settings = Settings()  # type: ignore[call-arg]
    assert settings.display_auto_review == value
    assert settings.display_auto_agenda == value


@pytest.mark.parametrize("field", ["DISPLAY_AUTO_REVIEW", "DISPLAY_AUTO_AGENDA"])
@pytest.mark.parametrize("value", ["sun", "23-7", "sun 18-18", "someday 18-20", "18-25"])
def test_auto_windows_reject_garbage(
    env: None, monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    monkeypatch.setenv(field, value)
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_update_check_opt_in(env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISPLAY_UPDATE_CHECK", "true")
    assert Settings().display_update_check is True  # type: ignore[call-arg]
