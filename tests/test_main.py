import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from PIL import Image

from studylife_display import main as main_module
from studylife_display.main import Snapshot, load_snapshot, main, save_snapshot
from studylife_display.model import DashboardData

BASE_URL = "https://studylife.test"


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    frame = tmp_path / "frame.png"
    state = tmp_path / "state" / "last.json"
    monkeypatch.setenv("STUDYLIFE_BASE_URL", BASE_URL)
    monkeypatch.setenv("STUDYLIFE_API_KEY", "test-key")
    monkeypatch.setenv("STUDYLIFE_TIMEZONE", "Europe/Berlin")
    monkeypatch.setenv("DISPLAY_DRIVER", "file")
    monkeypatch.setenv("DISPLAY_OUTPUT_PATH", str(frame))
    monkeypatch.setenv("DISPLAY_STATE_PATH", str(state))
    return {"frame": frame, "state": state}


@pytest.fixture
def rendered(monkeypatch: pytest.MonkeyPatch) -> list[DashboardData]:
    """Captures the DashboardData handed to the renderer, without stubbing the renderer."""
    seen: list[DashboardData] = []
    real_render = main_module.render

    def spy(data: DashboardData, language: str, layout: str = "classic") -> Image.Image:
        seen.append(data)
        return real_render(data, language, layout)

    monkeypatch.setattr(main_module, "render", spy)
    return seen


def mock_api(sample: tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]) -> None:
    metrics, history, timer = sample
    respx.get(f"{BASE_URL}/api/metrics/summary").mock(
        return_value=httpx.Response(200, json=metrics)
    )
    respx.get(f"{BASE_URL}/api/sessions/history").mock(
        return_value=httpx.Response(200, json=history)
    )
    respx.get(f"{BASE_URL}/api/timerstate").mock(return_value=httpx.Response(200, json=timer))


@respx.mock
def test_run_fetches_renders_and_caches(
    env: dict[str, Path], sample: Any, rendered: list[DashboardData]
) -> None:
    mock_api(sample)
    assert main(["run"]) == 0
    assert env["frame"].exists()
    cached = json.loads(env["state"].read_text(encoding="utf-8"))
    assert set(cached) == {"fetched_at", "metrics", "history", "timer"}
    assert cached["metrics"] == sample[0]
    assert rendered[0].stale_minutes == 0
    assert rendered[0].streak_days == 12
    history_call = respx.get(f"{BASE_URL}/api/sessions/history").calls.last
    assert history_call.request.headers["X-Api-Key"] == "test-key"
    assert history_call.request.url.params["days"] == "28"
    assert history_call.request.url.params["onlyCompleted"] == "true"


@respx.mock
def test_run_falls_back_to_the_cache_with_a_stale_marker(
    env: dict[str, Path], sample: Any, rendered: list[DashboardData], tz: ZoneInfo
) -> None:
    metrics, history, timer = sample
    fetched = datetime.now(tz) - timedelta(minutes=31)
    save_snapshot(env["state"], Snapshot(metrics, history, timer, fetched))
    respx.get(f"{BASE_URL}/api/metrics/summary").mock(side_effect=httpx.ConnectError("down"))

    assert main(["run"]) == 0
    assert env["frame"].exists()
    assert rendered[0].stale_minutes >= 31
    assert rendered[0].streak_days == 12


@respx.mock
def test_run_treats_a_403_like_an_outage(
    env: dict[str, Path], sample: Any, rendered: list[DashboardData], tz: ZoneInfo
) -> None:
    metrics, history, timer = sample
    save_snapshot(env["state"], Snapshot(metrics, history, timer, datetime.now(tz)))
    respx.get(f"{BASE_URL}/api/metrics/summary").mock(
        return_value=httpx.Response(403, text="no scope")
    )
    assert main(["run"]) == 0
    assert len(rendered) == 1


@respx.mock
def test_run_without_any_cache_fails(env: dict[str, Path], rendered: list[DashboardData]) -> None:
    respx.get(f"{BASE_URL}/api/metrics/summary").mock(side_effect=httpx.ConnectError("down"))
    assert main(["run"]) == 1
    assert not env["frame"].exists()
    assert rendered == []


@respx.mock
def test_check_prints_what_it_got(
    env: dict[str, Path], sample: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    mock_api(sample)
    assert main(["check"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["streak_days"] == 12
    assert report["next_goal"]["course_name"] == "Betriebssysteme"
    assert len(report["heatmap"]) == 4
    assert not env["frame"].exists()


def test_preview_with_sample_data_needs_no_instance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("STUDYLIFE_BASE_URL", raising=False)
    monkeypatch.delenv("STUDYLIFE_API_KEY", raising=False)
    out = tmp_path / "preview.png"
    with respx.mock(assert_all_called=False) as router:
        assert main(["preview", "--sample", "--out", str(out)]) == 0
        assert not router.calls
    with Image.open(out) as image:
        assert image.size == (800, 480)


@respx.mock
def test_preview_forces_the_file_driver(
    env: dict[str, Path], sample: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DISPLAY_DRIVER", "waveshare")
    mock_api(sample)
    out = tmp_path / "live.png"
    assert main(["preview", "--out", str(out)]) == 0
    assert out.exists()


def test_snapshot_round_trip(
    tmp_path: Path, sample: Any, tz: ZoneInfo, fixed_now: datetime
) -> None:
    metrics, history, timer = sample
    path = tmp_path / "a" / "b" / "last.json"
    save_snapshot(path, Snapshot(metrics, history, timer, fixed_now))
    loaded = load_snapshot(path, tz)
    assert loaded == Snapshot(metrics, history, timer, fixed_now)


def test_load_snapshot_tolerates_garbage(tmp_path: Path, tz: ZoneInfo) -> None:
    path = tmp_path / "last.json"
    assert load_snapshot(path, tz) is None
    path.write_text("{not json", encoding="utf-8")
    assert load_snapshot(path, tz) is None
    path.write_text('{"metrics": {}}', encoding="utf-8")
    assert load_snapshot(path, tz) is None
