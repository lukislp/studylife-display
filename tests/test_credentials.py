"""`credentials-apply`: the root-side half of connecting. Driven through the CLI the way
the systemd unit does, with a temp directory for the state directory and a temp copy of
the environment file; systemctl is recorded, never run."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from studylife_display import credentials as credentials_module
from studylife_display.credentials import rewrite_env_line
from studylife_display.main import main

ENV_TEXT = (
    "# StudyLife instance and key\n"
    "STUDYLIFE_BASE_URL=https://studylife.test\n"
    "STUDYLIFE_API_KEY=replace-me\n"
    "# STUDYLIFE_API_KEY=commented-out\n"
    "STUDYLIFE_TIMEZONE=Europe/Berlin\n"
    "DISPLAY_WEB_TOKEN=correct-horse-battery\n"
)


class TestRewriteEnvLine:
    def test_replaces_the_line_and_nothing_else(self) -> None:
        result = rewrite_env_line(ENV_TEXT, "STUDYLIFE_API_KEY", "k-123")
        assert result == ENV_TEXT.replace("STUDYLIFE_API_KEY=replace-me", "STUDYLIFE_API_KEY=k-123")
        assert "# STUDYLIFE_API_KEY=commented-out" in result

    def test_keeps_crlf_endings(self) -> None:
        text = "A=1\r\nSTUDYLIFE_API_KEY=old\r\nB=2\r\n"
        assert rewrite_env_line(text, "STUDYLIFE_API_KEY", "new") == (
            "A=1\r\nSTUDYLIFE_API_KEY=new\r\nB=2\r\n"
        )

    def test_adds_the_line_when_missing(self) -> None:
        assert rewrite_env_line("A=1\n", "STUDYLIFE_API_KEY", "k") == "A=1\nSTUDYLIFE_API_KEY=k\n"
        assert rewrite_env_line("A=1", "STUDYLIFE_API_KEY", "k") == "A=1\nSTUDYLIFE_API_KEY=k\n"
        assert rewrite_env_line("", "STUDYLIFE_API_KEY", "k") == "STUDYLIFE_API_KEY=k\n"

    def test_replaces_an_empty_value(self) -> None:
        assert rewrite_env_line("STUDYLIFE_API_KEY=\nX=1\n", "STUDYLIFE_API_KEY", "k") == (
            "STUDYLIFE_API_KEY=k\nX=1\n"
        )


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    state = tmp_path / "state"
    state.mkdir()
    env_file = tmp_path / "studylife-display.env"
    env_file.write_text(ENV_TEXT, encoding="utf-8")
    os.chmod(env_file, 0o640)
    monkeypatch.setenv("STUDYLIFE_BASE_URL", "https://studylife.test")
    monkeypatch.setenv("STUDYLIFE_API_KEY", "replace-me")
    monkeypatch.setenv("DISPLAY_STATE_PATH", str(state / "last.json"))
    return {"env": env_file, "pending": state / "credentials.pending.json"}


@pytest.fixture
def systemctl(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []
    monkeypatch.setattr(credentials_module, "run_systemctl", calls.append)
    return calls


def apply(env: dict[str, Path]) -> int:
    return main(["credentials-apply", "--env-file", str(env["env"])])


def test_applies_the_key_removes_the_file_and_restarts(
    env: dict[str, Path], systemctl: list[list[str]]
) -> None:
    env["pending"].write_text(json.dumps({"apiKey": "k-123"}), encoding="utf-8")
    assert apply(env) == 0
    text = env["env"].read_text(encoding="utf-8")
    assert text == ENV_TEXT.replace("STUDYLIFE_API_KEY=replace-me", "STUDYLIFE_API_KEY=k-123")
    assert not env["pending"].exists()
    assert not env["env"].with_name("studylife-display.env.tmp").exists()
    if sys.platform != "win32":
        assert oct(os.stat(env["env"]).st_mode & 0o777) == oct(0o640)
    assert systemctl == [
        ["restart", "--no-block", "studylife-display-web.service"],
        ["start", "--no-block", "studylife-display.service"],
    ]


def test_adds_the_line_to_an_env_file_without_one(
    env: dict[str, Path], systemctl: list[list[str]]
) -> None:
    env["env"].write_text("STUDYLIFE_BASE_URL=https://studylife.test\n", encoding="utf-8")
    env["pending"].write_text(json.dumps({"apiKey": "k-123"}), encoding="utf-8")
    assert apply(env) == 0
    assert env["env"].read_text(encoding="utf-8") == (
        "STUDYLIFE_BASE_URL=https://studylife.test\nSTUDYLIFE_API_KEY=k-123\n"
    )


@pytest.mark.parametrize("content", ["{not json", "[]", '{"apiKey": ""}', '{"apiKey": "a b"}'])
def test_refuses_an_invalid_file_and_removes_it(
    env: dict[str, Path], systemctl: list[list[str]], content: str
) -> None:
    env["pending"].write_text(content, encoding="utf-8")
    assert apply(env) == 1
    assert env["env"].read_text(encoding="utf-8") == ENV_TEXT
    assert not env["pending"].exists()
    assert systemctl == []


def test_nothing_to_apply(env: dict[str, Path], systemctl: list[list[str]]) -> None:
    assert apply(env) == 0
    assert env["env"].read_text(encoding="utf-8") == ENV_TEXT
    assert systemctl == []


def test_missing_env_file_fails_and_drops_the_pending_file(
    env: dict[str, Path], systemctl: list[list[str]]
) -> None:
    env["env"].unlink()
    env["pending"].write_text(json.dumps({"apiKey": "k-123"}), encoding="utf-8")
    assert apply(env) == 1
    assert not env["env"].exists()
    assert not env["pending"].exists()
    assert systemctl == []
