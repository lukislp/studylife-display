import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import respx

from studylife_display.update_check import (
    LATEST_RELEASE_URL,
    fetch_latest_tag,
    is_newer,
    latest_release,
    parse_version,
)


def test_parse_version_handles_tags_and_dev_builds() -> None:
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("1.2.3") == (1, 2, 3)
    assert parse_version("1.2.3.dev4+gabcdef0.d20260917") == (1, 2, 3)
    assert parse_version("main") is None
    assert parse_version("") is None


def test_is_newer_compares_releases_only() -> None:
    assert is_newer("v1.3.0", "1.2.0") is True
    assert is_newer("v1.2.0", "1.2.0") is False
    assert is_newer("v1.2.0", "1.2.1.dev2+gabc") is False
    assert is_newer("v1.10.0", "1.9.9") is True
    assert is_newer("latest", "1.2.0") is False
    assert is_newer("v1.3.0", "0.0.0") is True


class TestLatestRelease:
    def test_asks_once_and_then_uses_the_cache(self, tmp_path: Path, tz: ZoneInfo) -> None:
        calls: list[int] = []

        def fetch() -> str | None:
            calls.append(1)
            return "v9.9.9"

        now = datetime(2026, 9, 17, 12, 0, tzinfo=tz)
        assert latest_release(tmp_path, now, tz, fetch) == "v9.9.9"
        assert latest_release(tmp_path, now + timedelta(hours=5, minutes=59), tz, fetch) == "v9.9.9"
        assert len(calls) == 1
        cached = json.loads((tmp_path / "update_check.json").read_text(encoding="utf-8"))
        assert cached["latest"] == "v9.9.9"
        # Six hours later it asks again.
        assert latest_release(tmp_path, now + timedelta(hours=6), tz, fetch) == "v9.9.9"
        assert len(calls) == 2

    def test_a_failure_is_silent_and_cached_too(self, tmp_path: Path, tz: ZoneInfo) -> None:
        calls: list[int] = []

        def fetch() -> str | None:
            calls.append(1)
            return None

        now = datetime(2026, 9, 17, 12, 0, tzinfo=tz)
        assert latest_release(tmp_path, now, tz, fetch) is None
        assert latest_release(tmp_path, now + timedelta(minutes=30), tz, fetch) is None
        assert len(calls) == 1

    def test_damaged_cache_triggers_a_fresh_check(self, tmp_path: Path, tz: ZoneInfo) -> None:
        (tmp_path / "update_check.json").write_text("{nope", encoding="utf-8")
        now = datetime(2026, 9, 17, 12, 0, tzinfo=tz)
        assert latest_release(tmp_path, now, tz, lambda: "v2.0.0") == "v2.0.0"


class TestFetchLatestTag:
    @respx.mock
    def test_reads_the_tag_name(self) -> None:
        respx.get(LATEST_RELEASE_URL).mock(
            return_value=httpx.Response(200, json={"tag_name": "v1.5.0"})
        )
        assert fetch_latest_tag() == "v1.5.0"

    @respx.mock
    def test_any_failure_is_none(self) -> None:
        respx.get(LATEST_RELEASE_URL).mock(return_value=httpx.Response(403, json={}))
        assert fetch_latest_tag() is None
        respx.get(LATEST_RELEASE_URL).mock(side_effect=httpx.ConnectError("offline"))
        assert fetch_latest_tag() is None
        respx.get(LATEST_RELEASE_URL).mock(return_value=httpx.Response(200, text="not json"))
        assert fetch_latest_tag() is None
