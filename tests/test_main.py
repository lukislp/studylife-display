import json
import logging
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from PIL import Image, ImageChops

from studylife_display import main as main_module
from studylife_display.current_frame import load_current_frame
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


@pytest.fixture
def error_screens(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Captures (kind, detail) of every error screen drawn, without stubbing the drawing."""
    seen: list[tuple[str, str]] = []
    real_render_error = main_module.render_error

    def spy(kind: str, detail: str, language: str, *args: Any, **kwargs: Any) -> Image.Image:
        seen.append((kind, detail))
        return real_render_error(kind, detail, language, *args, **kwargs)

    monkeypatch.setattr(main_module, "render_error", spy)
    return seen


SESSIONS_ETAG = '"abc123"'


def mock_api(
    sample: tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]],
) -> None:
    metrics, history, timer, sessions = sample
    respx.get(f"{BASE_URL}/api/metrics/summary").mock(
        return_value=httpx.Response(200, json=metrics)
    )
    respx.get(f"{BASE_URL}/api/sessions/history").mock(
        return_value=httpx.Response(200, json=history)
    )
    respx.get(f"{BASE_URL}/api/timerstate").mock(return_value=httpx.Response(200, json=timer))
    respx.get(f"{BASE_URL}/api/sessions").mock(
        return_value=httpx.Response(200, json=sessions, headers={"ETag": SESSIONS_ETAG})
    )


@respx.mock
def test_run_fetches_renders_and_caches(
    env: dict[str, Path], sample: Any, rendered: list[DashboardData]
) -> None:
    mock_api(sample)
    assert main(["run"]) == 0
    assert env["frame"].exists()
    cached = json.loads(env["state"].read_text(encoding="utf-8"))
    assert set(cached) == {
        "fetched_at",
        "metrics",
        "history",
        "timer",
        "sessions",
        "sessions_etag",
    }
    assert cached["metrics"] == sample[0]
    assert cached["sessions"] == sample[3]
    assert cached["sessions_etag"] == SESSIONS_ETAG
    assert rendered[0].stale_minutes == 0
    assert rendered[0].streak_days == 12
    history_call = respx.get(f"{BASE_URL}/api/sessions/history").calls.last
    assert history_call.request.headers["X-Api-Key"] == "test-key"
    assert history_call.request.url.params["days"] == "28"
    assert history_call.request.url.params["onlyCompleted"] == "true"
    sessions_call = respx.get(f"{BASE_URL}/api/sessions").calls.last
    assert "if-none-match" not in sessions_call.request.headers  # nothing cached yet
    assert not sessions_call.request.url.params
    status = json.loads((env["state"].parent / "status.json").read_text(encoding="utf-8"))
    assert status["sessions_ok"] is True and status["sessions_error"] is None


@respx.mock
def test_run_sends_the_cached_etag_and_keeps_the_sessions_on_304(
    env: dict[str, Path], sample: Any, rendered: list[DashboardData]
) -> None:
    mock_api(sample)
    assert main(["run"]) == 0
    route = respx.get(f"{BASE_URL}/api/sessions").mock(
        return_value=httpx.Response(304, headers={"ETag": SESSIONS_ETAG})
    )
    assert main(["run"]) == 0
    assert route.calls.last.request.headers["If-None-Match"] == SESSIONS_ETAG
    cached = json.loads(env["state"].read_text(encoding="utf-8"))
    assert cached["sessions"] == sample[3]  # kept from the first fetch
    assert cached["sessions_etag"] == SESSIONS_ETAG
    # A changed list arrives with a new tag, which is then what gets sent.
    route.mock(return_value=httpx.Response(200, json=[], headers={"ETag": '"def456"'}))
    assert main(["run"]) == 0
    assert route.calls.last.request.headers["If-None-Match"] == SESSIONS_ETAG
    assert json.loads(env["state"].read_text(encoding="utf-8"))["sessions_etag"] == '"def456"'
    assert rendered[-1].agenda == ()
    assert json.loads(env["state"].read_text(encoding="utf-8"))["sessions"] == []


@respx.mock
@pytest.mark.parametrize("failure", ["403", "500", "network"])
def test_a_failed_sessions_call_does_not_break_the_dashboard(
    env: dict[str, Path], sample: Any, rendered: list[DashboardData], failure: str
) -> None:
    mock_api(sample)
    route = respx.get(f"{BASE_URL}/api/sessions")
    if failure == "network":
        route.mock(side_effect=httpx.ConnectError("down"))
    else:
        route.mock(return_value=httpx.Response(int(failure), text="no scope"))
    assert main(["run"]) == 0
    assert env["frame"].exists()
    assert rendered[0].agenda == ()
    assert rendered[0].streak_days == 12
    cached = json.loads(env["state"].read_text(encoding="utf-8"))
    assert cached["sessions"] == [] and cached["sessions_etag"] is None
    status = json.loads((env["state"].parent / "status.json").read_text(encoding="utf-8"))
    assert status["last_fetch_ok"] is True and status["last_error"] is None
    assert status["sessions_ok"] is False
    assert status["sessions_error"]


@respx.mock
def test_run_falls_back_to_the_cache_with_a_stale_marker(
    env: dict[str, Path], sample: Any, rendered: list[DashboardData], tz: ZoneInfo
) -> None:
    metrics, history, timer, sessions = sample
    fetched = datetime.now(tz) - timedelta(minutes=31)
    save_snapshot(env["state"], Snapshot(metrics, history, timer, fetched))
    respx.get(f"{BASE_URL}/api/metrics/summary").mock(side_effect=httpx.ConnectError("down"))

    assert main(["run"]) == 0
    assert env["frame"].exists()
    assert rendered[0].stale_minutes >= 31
    assert rendered[0].streak_days == 12


@respx.mock
@pytest.mark.parametrize("status", [401, 403])
def test_run_shows_the_rejected_screen_on_401_and_403_even_with_a_cache(
    env: dict[str, Path],
    sample: Any,
    rendered: list[DashboardData],
    error_screens: list[tuple[str, str]],
    tz: ZoneInfo,
    status: int,
) -> None:
    metrics, history, timer, sessions = sample
    save_snapshot(env["state"], Snapshot(metrics, history, timer, datetime.now(tz)))
    respx.get(f"{BASE_URL}/api/metrics/summary").mock(
        return_value=httpx.Response(status, text="no scope")
    )
    assert main(["run"]) == 1
    assert rendered == []
    assert error_screens == [("rejected", f"HTTP {status}")]
    assert env["frame"].exists()
    recorded = json.loads((env["state"].parent / "status.json").read_text(encoding="utf-8"))
    assert recorded["last_fetch_ok"] is False
    assert recorded["last_error"]["kind"] == "rejected"
    assert recorded["last_error"]["status"] == status
    assert "no scope" in recorded["last_error"]["message"]
    assert recorded["last_panel_update_at"] is not None


@respx.mock
def test_run_without_any_cache_shows_no_data_and_fails(
    env: dict[str, Path], rendered: list[DashboardData], error_screens: list[tuple[str, str]]
) -> None:
    respx.get(f"{BASE_URL}/api/metrics/summary").mock(side_effect=httpx.ConnectError("down"))
    assert main(["run"]) == 1
    assert env["frame"].exists()
    assert rendered == []
    assert [kind for kind, _ in error_screens] == ["no_data"]
    recorded = json.loads((env["state"].parent / "status.json").read_text(encoding="utf-8"))
    assert recorded["last_error"]["kind"] == "no_data"
    assert recorded["last_error"]["status"] is None


@respx.mock
def test_run_shows_the_stale_screen_once_the_cache_is_too_old(
    env: dict[str, Path],
    sample: Any,
    rendered: list[DashboardData],
    error_screens: list[tuple[str, str]],
    tz: ZoneInfo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics, history, timer, sessions = sample
    monkeypatch.setenv("DISPLAY_STALE_ERROR_HOURS", "2")
    respx.get(f"{BASE_URL}/api/metrics/summary").mock(side_effect=httpx.ConnectError("down"))

    # Just under the limit: the cached dashboard with the stale marker, as before.
    save_snapshot(
        env["state"], Snapshot(metrics, history, timer, datetime.now(tz) - timedelta(minutes=110))
    )
    assert main(["run"]) == 0
    assert len(rendered) == 1
    assert rendered[0].stale_minutes >= 110
    assert error_screens == []
    recorded = json.loads((env["state"].parent / "status.json").read_text(encoding="utf-8"))
    assert recorded["last_error"]["kind"] == "transient"

    # Past the limit: the stale screen, still exit 0 (the outage may end).
    save_snapshot(
        env["state"], Snapshot(metrics, history, timer, datetime.now(tz) - timedelta(hours=2))
    )
    assert main(["run"]) == 0
    assert len(rendered) == 1
    assert error_screens == [("stale", "2 h")]
    recorded = json.loads((env["state"].parent / "status.json").read_text(encoding="utf-8"))
    assert recorded["last_error"]["kind"] == "stale"


@respx.mock
def test_a_successful_fetch_clears_the_last_error(
    env: dict[str, Path], sample: Any, rendered: list[DashboardData]
) -> None:
    respx.get(f"{BASE_URL}/api/metrics/summary").mock(side_effect=httpx.ConnectError("down"))
    assert main(["run"]) == 1
    mock_api(sample)
    assert main(["run"]) == 0
    recorded = json.loads((env["state"].parent / "status.json").read_text(encoding="utf-8"))
    assert recorded["last_fetch_ok"] is True
    assert recorded["last_error"] is None
    assert recorded["last_fetch_at"] is not None


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
    # `check` runs at the real clock, the sample sessions sit on FIXED_NOW's day: the raw
    # count is stable, the agenda is not.
    assert report["sessions"] == 4 and report["sessions_ok"] is True
    assert isinstance(report["agenda"], list)
    assert report["weekly_report"]["session_count"] == 7
    assert report["this_week"]["week_id"]
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
    metrics, history, timer, sessions = sample
    path = tmp_path / "a" / "b" / "last.json"
    save_snapshot(path, Snapshot(metrics, history, timer, fixed_now, sessions, '"tag"'))
    loaded = load_snapshot(path, tz)
    assert loaded == Snapshot(metrics, history, timer, fixed_now, sessions, '"tag"')
    # A cache written before the session list existed still loads.
    old = {"fetched_at": fixed_now.isoformat(), "metrics": metrics, "history": [], "timer": {}}
    path.write_text(json.dumps(old), encoding="utf-8")
    loaded = load_snapshot(path, tz)
    assert loaded is not None
    assert loaded.sessions == [] and loaded.sessions_etag is None


def test_load_snapshot_tolerates_garbage(tmp_path: Path, tz: ZoneInfo) -> None:
    path = tmp_path / "last.json"
    assert load_snapshot(path, tz) is None
    path.write_text("{not json", encoding="utf-8")
    assert load_snapshot(path, tz) is None
    path.write_text('{"metrics": {}}', encoding="utf-8")
    assert load_snapshot(path, tz) is None


def quiet_hours_around(now: datetime) -> str:
    return f"{(now.hour - 1) % 24}-{(now.hour + 2) % 24}"


def clear_file(env: dict[str, Path]) -> Path:
    return env["frame"].with_name("frame-clear.png")


class TestQuietHours:
    def test_run_does_nothing_inside_quiet_hours(
        self,
        env: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
        tz: ZoneInfo,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("DISPLAY_QUIET_HOURS", quiet_hours_around(datetime.now(tz)))
        monkeypatch.setenv("DISPLAY_CLEAR_AT", "")
        caplog.set_level(logging.INFO, logger="studylife_display")
        with respx.mock(assert_all_called=False) as router:
            assert main(["run"]) == 0
            assert not router.calls
        assert not env["frame"].exists()
        assert "quiet hours" in caplog.text

    @respx.mock
    def test_run_refreshes_outside_quiet_hours(
        self, env: dict[str, Path], sample: Any, monkeypatch: pytest.MonkeyPatch, tz: ZoneInfo
    ) -> None:
        now = datetime.now(tz)
        # A window that ended an hour ago (or starts in two hours), never containing now.
        monkeypatch.setenv("DISPLAY_QUIET_HOURS", f"{(now.hour + 2) % 24}-{(now.hour - 1) % 24}")
        mock_api(sample)
        assert main(["run"]) == 0
        assert env["frame"].exists()


class TestRefreshNow:
    """What the web interface's subprocess calls (see TestServeRefresh below) - unlike `run`,
    it must draw even inside quiet hours, because a browser button press is not the scheduled
    timer."""

    @respx.mock
    def test_refresh_now_draws_inside_quiet_hours(
        self, env: dict[str, Path], sample: Any, monkeypatch: pytest.MonkeyPatch, tz: ZoneInfo
    ) -> None:
        monkeypatch.setenv("DISPLAY_QUIET_HOURS", quiet_hours_around(datetime.now(tz)))
        mock_api(sample)
        assert main(["refresh-now"]) == 0
        assert env["frame"].exists()


class TestServeRefresh:
    """`command_serve` wires up a different refresh strategy per driver; see
    `refresh_via_subprocess` for why real hardware cannot refresh in the long-running web
    process itself."""

    def test_file_driver_refreshes_in_process(
        self, env: dict[str, Path], sample: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail_if_called(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("the file driver must never shell out to a subprocess")

        monkeypatch.setattr(main_module.subprocess, "run", fail_if_called)
        settings = main_module._settings(False)
        assert settings.display_driver == "file"
        with respx.mock:
            mock_api(sample)
            assert main_module.make_serve_refresh(settings)() == 0
        assert env["frame"].exists()

    def test_hardware_driver_refreshes_via_subprocess(
        self, env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: Any) -> Any:
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(main_module.subprocess, "run", fake_run)
        monkeypatch.setenv("DISPLAY_DRIVER", "waveshare")
        settings = main_module._settings(False)
        assert main_module.make_serve_refresh(settings)() == 0
        assert calls == [[sys.executable, "-m", "studylife_display.main", "refresh-now"]]

    def test_subprocess_failure_is_logged_and_propagated(
        self,
        env: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        def fake_run(cmd: list[str], **kwargs: Any) -> Any:
            return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="boom")

        monkeypatch.setattr(main_module.subprocess, "run", fake_run)
        monkeypatch.setenv("DISPLAY_DRIVER", "waveshare")
        caplog.set_level(logging.ERROR, logger="studylife_display")
        settings = main_module._settings(False)
        assert main_module.refresh_via_subprocess(settings) == 1
        assert "boom" in caplog.text


class TestDailyClear:
    @respx.mock
    def test_clears_once_per_day_before_the_frame(
        self, env: dict[str, Path], sample: Any, monkeypatch: pytest.MonkeyPatch, tz: ZoneInfo
    ) -> None:
        monkeypatch.setenv("DISPLAY_CLEAR_AT", "00:00")
        mock_api(sample)
        assert main(["run"]) == 0
        assert clear_file(env).exists()
        assert env["frame"].exists()
        last_clear = env["state"].parent / "last_clear"
        recorded = datetime.fromisoformat(last_clear.read_text(encoding="utf-8"))
        assert abs((datetime.now(tz) - recorded).total_seconds()) < 60

        clear_file(env).unlink()
        assert main(["run"]) == 0
        assert not clear_file(env).exists()  # today's clear is done

        # Yesterday's clear makes today's due again.
        last_clear.write_text((datetime.now(tz) - timedelta(days=1)).isoformat(), encoding="utf-8")
        assert main(["run"]) == 0
        assert clear_file(env).exists()

    @respx.mock
    def test_clear_off(
        self, env: dict[str, Path], sample: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DISPLAY_CLEAR_AT", "")
        mock_api(sample)
        assert main(["run"]) == 0
        assert not clear_file(env).exists()
        assert not (env["state"].parent / "last_clear").exists()

    @respx.mock
    def test_clear_runs_inside_quiet_hours(
        self, env: dict[str, Path], sample: Any, monkeypatch: pytest.MonkeyPatch, tz: ZoneInfo
    ) -> None:
        monkeypatch.setenv("DISPLAY_QUIET_HOURS", quiet_hours_around(datetime.now(tz)))
        monkeypatch.setenv("DISPLAY_CLEAR_AT", "00:00")
        mock_api(sample)
        assert main(["run"]) == 0
        assert clear_file(env).exists()
        assert env["frame"].exists()
        # The next run inside quiet hours is skipped again: the clear is done for today.
        env["frame"].unlink()
        assert main(["run"]) == 0
        assert not env["frame"].exists()

    @respx.mock
    def test_the_web_refresh_never_clears(
        self, env: dict[str, Path], sample: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DISPLAY_CLEAR_AT", "00:00")
        mock_api(sample)
        assert main_module.refresh_panel(main_module.Settings()) == 0  # type: ignore[call-arg]
        assert env["frame"].exists()
        assert not clear_file(env).exists()


@respx.mock
def test_run_applies_the_configured_rotation(
    env: dict[str, Path],
    sample: Any,
    rendered: list[DashboardData],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DISPLAY_ROTATE", "180")
    monkeypatch.setenv("DISPLAY_CLEAR_AT", "")
    monkeypatch.setenv("DISPLAY_LAYOUT", "classic")
    mock_api(sample)
    assert main(["run"]) == 0
    # The layouts drew upright (the spy saw the data they were handed); the file holds the
    # same frame turned by 180 degrees, i.e. flipped both ways.
    upright = main_module.render(rendered[0], "de", "classic")
    expected = upright.transpose(Image.Transpose.FLIP_LEFT_RIGHT).transpose(
        Image.Transpose.FLIP_TOP_BOTTOM
    )
    with Image.open(env["frame"]) as frame:
        assert ImageChops.difference(frame.convert("1"), expected).getbbox() is None
        assert ImageChops.difference(frame.convert("1"), upright).getbbox() is not None


class TestSetupScreen:
    """No key configured at all: the setup screen, no API call, exit 0."""

    @pytest.fixture
    def setup_screens(self, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
        """Captures (url, hostname) of every setup screen drawn, without stubbing the drawing."""
        seen: list[tuple[str, str]] = []
        real_render_setup = main_module.render_setup

        def spy(url: str, language: str, hostname: str, *args: Any, **kwargs: Any) -> Image.Image:
            seen.append((url, hostname))
            return real_render_setup(url, language, hostname, *args, **kwargs)

        monkeypatch.setattr(main_module, "render_setup", spy)
        return seen

    def test_run_without_a_key_shows_the_setup_screen_and_calls_nothing(
        self,
        env: dict[str, Path],
        rendered: list[DashboardData],
        error_screens: list[tuple[str, str]],
        setup_screens: list[tuple[str, str]],
        monkeypatch: pytest.MonkeyPatch,
        tz: ZoneInfo,
    ) -> None:
        monkeypatch.setenv("STUDYLIFE_API_KEY", "")
        monkeypatch.setenv("DISPLAY_SETUP_URL", "http://pi.local:8795/connect")
        with respx.mock(assert_all_called=False) as router:
            assert main(["run"]) == 0
            assert not router.calls
        assert rendered == []
        assert error_screens == []
        assert [url for url, _ in setup_screens] == ["http://pi.local:8795/connect"]
        assert env["frame"].exists()
        current = load_current_frame(env["state"].parent, tz)
        assert current is not None
        assert (current.kind, current.layout) == ("setup", None)
        recorded = json.loads((env["state"].parent / "status.json").read_text(encoding="utf-8"))
        assert recorded["last_panel_update_at"] is not None
        assert not env["state"].exists()  # nothing was fetched, so nothing was cached

    @respx.mock
    def test_a_configured_but_rejected_key_still_gets_the_rejected_screen(
        self,
        env: dict[str, Path],
        error_screens: list[tuple[str, str]],
        setup_screens: list[tuple[str, str]],
    ) -> None:
        respx.get(f"{BASE_URL}/api/metrics/summary").mock(return_value=httpx.Response(401))
        assert main(["run"]) == 1
        assert setup_screens == []
        assert error_screens == [("rejected", "HTTP 401")]


class TestCurrentFrame:
    """Every frame that reaches the panel is kept upright as current.png for the web UI."""

    @respx.mock
    def test_a_shown_dashboard_is_kept_with_its_metadata(
        self, env: dict[str, Path], sample: Any, monkeypatch: pytest.MonkeyPatch, tz: ZoneInfo
    ) -> None:
        monkeypatch.setenv("DISPLAY_LAYOUT", "week")
        mock_api(sample)
        assert main(["run"]) == 0
        state_dir = env["state"].parent
        current = load_current_frame(state_dir, tz)
        assert current is not None
        assert (current.kind, current.layout) == ("dashboard", "week")
        assert abs((datetime.now(tz) - current.shown_at).total_seconds()) < 60
        with Image.open(state_dir / "current.png") as copy, Image.open(env["frame"]) as shown:
            assert copy.size == (800, 480)
            assert ImageChops.difference(copy.convert("1"), shown.convert("1")).getbbox() is None
        assert not (state_dir / "current.png.tmp").exists()
        assert not (state_dir / "current.json.tmp").exists()

    @respx.mock
    def test_an_error_screen_is_kept_too(
        self, env: dict[str, Path], error_screens: list[tuple[str, str]], tz: ZoneInfo
    ) -> None:
        respx.get(f"{BASE_URL}/api/metrics/summary").mock(side_effect=httpx.ConnectError("down"))
        assert main(["run"]) == 1
        current = load_current_frame(env["state"].parent, tz)
        assert current is not None
        assert (current.kind, current.layout) == ("error", None)
        assert error_screens == [("no_data", "down")]

    @respx.mock
    def test_the_copy_stays_upright_when_the_panel_is_rotated(
        self,
        env: dict[str, Path],
        sample: Any,
        rendered: list[DashboardData],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DISPLAY_ROTATE", "180")
        monkeypatch.setenv("DISPLAY_CLEAR_AT", "")
        monkeypatch.setenv("DISPLAY_LAYOUT", "classic")
        mock_api(sample)
        assert main(["run"]) == 0
        upright = main_module.render(rendered[0], "de", "classic")
        with Image.open(env["state"].parent / "current.png") as copy:
            assert ImageChops.difference(copy.convert("1"), upright).getbbox() is None
        with Image.open(env["frame"]) as shown:
            turned = shown.convert("1")
        assert ImageChops.difference(turned, upright).getbbox() is not None
        assert (
            ImageChops.difference(turned, upright.transpose(Image.Transpose.ROTATE_180)).getbbox()
            is None
        )

    def test_preview_with_sample_data_leaves_the_state_dir_untouched(
        self, env: dict[str, Path], tmp_path: Path
    ) -> None:
        out = tmp_path / "preview.png"
        assert main(["preview", "--sample", "--out", str(out)]) == 0
        assert out.exists()
        assert not env["state"].parent.exists()

    @respx.mock
    def test_preview_against_the_instance_does_not_count_as_shown(
        self, env: dict[str, Path], sample: Any, tmp_path: Path, tz: ZoneInfo
    ) -> None:
        mock_api(sample)
        out = tmp_path / "live.png"
        assert main(["preview", "--out", str(out)]) == 0
        assert out.exists()
        assert load_current_frame(env["state"].parent, tz) is None
        assert not (env["state"].parent / "current.png").exists()
