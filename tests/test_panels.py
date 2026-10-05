import sys
import types
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from PIL import Image
from pydantic import ValidationError

from studylife_display.config import Settings
from studylife_display.current_frame import load_current_frame
from studylife_display.driver import (
    FileDisplay,
    InkyDisplay,
    WaveshareDisplay,
    WaveshareTriColourDisplay,
    hardware_display,
    oriented,
)
from studylife_display.frame_fingerprint import frame_fingerprint
from studylife_display.layouts.common import HEIGHT, WIDTH
from studylife_display.main import main, make_display
from studylife_display.panels import (
    DEFAULT_PANEL,
    PANELS,
    frame_for_panel,
    get_panel,
)
from studylife_display.render import render
from studylife_display.sample import sample_extras, sample_payloads

BASE_URL = "https://studylife.test"
BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 9, 17, 16, 45, tzinfo=BERLIN)

SCALED_KEYS = ["waveshare_7in5_v1", "waveshare_7in5_hd"]


def settings_for(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    monkeypatch.setenv("STUDYLIFE_BASE_URL", BASE_URL)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


# ---------------------------------------------------------------- registry


def test_the_logical_canvas_matches_the_layouts() -> None:
    from studylife_display import panels

    assert (panels.WIDTH, panels.HEIGHT) == (WIDTH, HEIGHT) == (800, 480)


def test_registry_keys_are_unique_and_match_their_profiles() -> None:
    assert DEFAULT_PANEL in PANELS
    for key, profile in PANELS.items():
        assert profile.key == key
        assert key == key.lower()
        assert profile.label
    assert len({profile.key for profile in PANELS.values()}) == len(PANELS)


def test_the_documented_panels_and_sizes() -> None:
    sizes = {key: profile.native_size for key, profile in PANELS.items()}
    assert sizes == {
        "waveshare_7in5_v2": (800, 480),
        "waveshare_7in5b_v2": (800, 480),
        "waveshare_7in5_v1": (640, 384),
        "waveshare_7in5_hd": (880, 528),
        "waveshare_7in3f": (800, 480),
        "inky_impression_7in3": (800, 480),
    }


def test_every_panel_is_the_five_to_three_canvas_so_scaling_never_distorts() -> None:
    for profile in PANELS.values():
        assert profile.width * 3 == profile.height * 5
    assert pytest.approx(0.8) == 640 / WIDTH
    assert pytest.approx(1.1) == 880 / WIDTH
    assert pytest.approx(0.8) == 384 / HEIGHT
    assert pytest.approx(1.1) == 528 / HEIGHT


def test_only_the_non_native_panels_are_scaled() -> None:
    assert {key for key, profile in PANELS.items() if profile.scaled} == set(SCALED_KEYS)


def test_inky_has_its_own_extra_and_waveshare_models_share_pi() -> None:
    assert PANELS["inky_impression_7in3"].extra == "inky"
    assert {p.extra for p in PANELS.values() if p.family != "inky"} == {"pi"}


def test_get_panel_lists_the_known_keys_on_an_unknown_one() -> None:
    with pytest.raises(ValueError) as caught:
        get_panel("nope")
    for key in PANELS:
        assert key in str(caught.value)


# ------------------------------------------------------------------ config


def test_the_default_panel_is_todays_hardware(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = settings_for(monkeypatch)
    assert settings.display_panel == "waveshare_7in5_v2"
    assert settings.panel.native_size == (800, 480)


def test_the_panel_key_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = settings_for(monkeypatch, DISPLAY_PANEL=" Waveshare_7in5_HD ")
    assert settings.panel.key == "waveshare_7in5_hd"


def test_an_unknown_panel_is_refused_with_the_valid_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError) as caught:
        settings_for(monkeypatch, DISPLAY_PANEL="epd_9000")
    message = str(caught.value)
    assert "epd_9000" in message
    assert all(key in message for key in PANELS)


# ----------------------------------------------------------------- scaling


def dashboard_frame(layout: str = "classic") -> Image.Image:
    from studylife_display.model import build_dashboard

    metrics, history, timer, sessions = sample_payloads(NOW, BERLIN)
    goals, achievements, notes = sample_extras(NOW, BERLIN)
    data = build_dashboard(
        metrics,
        history,
        timer,
        NOW,
        BERLIN,
        sessions=sessions,
        goals=goals,
        achievements_payload=achievements,
        notes_payload=notes,
    )
    return render(data, "de", layout)


def test_the_native_panel_gets_the_very_same_frame() -> None:
    frame = Image.new("1", (WIDTH, HEIGHT), 1)
    assert frame_for_panel(frame, PANELS[DEFAULT_PANEL]) is frame


@pytest.mark.parametrize("key", SCALED_KEYS)
def test_scaling_gives_the_native_size_in_one_bit_and_is_deterministic(key: str) -> None:
    frame = dashboard_frame()
    panel = PANELS[key]
    first = frame_for_panel(frame, panel)
    assert first.size == panel.native_size
    assert first.mode == "1"
    assert first.tobytes() == frame_for_panel(frame, panel).tobytes()
    histogram = first.convert("L").histogram()
    assert histogram[0] + histogram[255] == panel.width * panel.height  # no grey


@pytest.mark.parametrize("key", SCALED_KEYS)
def test_scaling_keeps_white_white_and_black_black(key: str) -> None:
    panel = PANELS[key]
    white = frame_for_panel(Image.new("1", (WIDTH, HEIGHT), 1), panel)
    black = frame_for_panel(Image.new("1", (WIDTH, HEIGHT), 0), panel)
    assert white.convert("L").getextrema() == (255, 255)
    assert black.convert("L").getextrema() == (0, 0)


def test_scaling_refuses_a_frame_of_a_foreign_size() -> None:
    with pytest.raises(ValueError):
        frame_for_panel(Image.new("1", (100, 100), 1), PANELS["waveshare_7in5_v1"])


def test_rotation_turns_the_already_scaled_frame() -> None:
    # Rotation lives in the driver (`oriented`) and is applied after the scaling: the
    # upside-down panel shows the scaled picture turned, at the panel's size.
    frame = Image.new("1", (WIDTH, HEIGHT), 1)
    frame.paste(0, (0, 0, 100, 60))
    panel = PANELS["waveshare_7in5_v1"]
    scaled = frame_for_panel(frame, panel)
    turned = oriented(scaled, 180)
    assert turned.size == panel.native_size
    assert turned.getpixel((panel.width - 1, panel.height - 1)) == 0
    assert turned.getpixel((0, 0)) != 0


# ------------------------------------------------- vendor classes (faked)


class FakeEpd:
    """The vendor's EPD surface; records what the driver does and with which sizes."""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.calls: list[str] = []
        self.display_args: tuple[Any, ...] = ()
        self.frames: list[Image.Image] = []

    def init(self) -> None:
        self.calls.append("init")

    def getbuffer(self, image: Image.Image) -> bytearray:
        self.calls.append("getbuffer")
        self.frames.append(image)
        return bytearray(image.tobytes())

    def display(self, *buffers: Any) -> None:
        self.display_args = buffers
        self.calls.append("display:" + "+".join(str(len(b)) for b in buffers))

    def sleep(self) -> None:
        self.calls.append("sleep")

    def Clear(self) -> None:  # noqa: N802 - the vendor's spelling
        self.calls.append("Clear")


@pytest.fixture
def vendor(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], FakeEpd]:
    """Injects `waveshare_epd.<module>` for one module name and returns its EPD."""
    package = types.ModuleType("waveshare_epd")
    monkeypatch.setitem(sys.modules, "waveshare_epd", package)

    def install(name: str) -> FakeEpd:
        profile = next(p for p in PANELS.values() if p.module == name)
        epd = FakeEpd(profile.width, profile.height)
        module = types.ModuleType(f"waveshare_epd.{name}")
        module.EPD = lambda: epd  # type: ignore[attr-defined]
        setattr(package, name, module)
        monkeypatch.setitem(sys.modules, f"waveshare_epd.{name}", module)
        return epd

    return install


@pytest.mark.parametrize(
    "key",
    ["waveshare_7in5_v2", "waveshare_7in5_v1", "waveshare_7in5_hd", "waveshare_7in3f"],
)
def test_single_buffer_vendors_run_init_display_sleep(
    vendor: Callable[[str], FakeEpd], key: str
) -> None:
    panel = PANELS[key]
    epd = vendor(panel.module)
    display = hardware_display(panel)
    assert isinstance(display, WaveshareDisplay)
    display.show(frame_for_panel(Image.new("1", (WIDTH, HEIGHT), 1), panel))
    display.sleep()
    size = panel.width * panel.height // 8  # the fake hands back the packed 1-bit frame
    assert epd.calls == ["init", "getbuffer", f"display:{size}", "sleep"]
    assert epd.frames[0].size == panel.native_size


def test_a_single_buffer_vendor_refuses_an_unscaled_frame(
    vendor: Callable[[str], FakeEpd],
) -> None:
    panel = PANELS["waveshare_7in5_hd"]
    epd = vendor(panel.module)
    with pytest.raises(ValueError):
        hardware_display(panel).show(Image.new("1", (WIDTH, HEIGHT), 1))
    assert epd.calls == []


def test_the_tricolour_model_gets_the_frame_in_black_and_a_blank_red_plane(
    vendor: Callable[[str], FakeEpd],
) -> None:
    panel = PANELS["waveshare_7in5b_v2"]
    epd = vendor(panel.module)
    display = hardware_display(panel)
    assert isinstance(display, WaveshareTriColourDisplay)
    frame = Image.new("1", (WIDTH, HEIGHT), 1)
    frame.putpixel((0, 0), 0)
    display.show(frame)
    display.sleep()
    size = 800 * 480 // 8
    assert epd.calls == ["init", "getbuffer", f"display:{size}+{size}", "sleep"]
    black, red = epd.display_args
    assert black[0] == 0x7F  # the fake's PIL bytes: first pixel black, the rest white
    assert set(red) == {0}  # the vendor's own "no red" value: nothing is drawn red


def test_clear_runs_the_vendors_full_clear(vendor: Callable[[str], FakeEpd]) -> None:
    panel = PANELS["waveshare_7in5_hd"]
    epd = vendor(panel.module)
    display = hardware_display(panel)
    display.clear()
    display.sleep()
    assert epd.calls == ["init", "Clear", "sleep"]


def test_rotation_is_applied_to_the_scaled_frame_by_the_driver(
    vendor: Callable[[str], FakeEpd],
) -> None:
    panel = PANELS["waveshare_7in5_v1"]
    epd = vendor(panel.module)
    frame = frame_for_panel(Image.new("1", (WIDTH, HEIGHT), 1), panel)
    frame.putpixel((0, 0), 0)
    hardware_display(panel, rotate=180).show(frame)
    sent = epd.frames[0]
    assert sent.size == panel.native_size
    assert sent.getpixel((panel.width - 1, panel.height - 1)) == 0


class FakeInky:
    def __init__(self, width: int = 800, height: int = 480) -> None:
        self.width = width
        self.height = height
        self.calls: list[str] = []
        self.image: Image.Image | None = None
        self.saturation: float | None = None

    def set_image(self, image: Image.Image, saturation: float = 0.5) -> None:
        self.calls.append(f"set_image:{image.size[0]}x{image.size[1]}:{image.mode}")
        self.image = image
        self.saturation = saturation

    def show(self) -> None:
        self.calls.append("show")


@pytest.fixture
def inky(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeInky]:
    def install(width: int = 800, height: int = 480) -> FakeInky:
        board = FakeInky(width, height)
        package = types.ModuleType("inky")
        auto = types.ModuleType("inky.auto")
        auto.auto = lambda: board  # type: ignore[attr-defined]
        package.auto = auto  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "inky", package)
        monkeypatch.setitem(sys.modules, "inky.auto", auto)
        return board

    return install


def test_inky_shows_the_frame_as_rgb_without_added_saturation(
    inky: Callable[..., FakeInky],
) -> None:
    board = inky()
    display = hardware_display(PANELS["inky_impression_7in3"])
    assert isinstance(display, InkyDisplay)
    display.show(Image.new("1", (WIDTH, HEIGHT), 1))
    display.sleep()
    assert board.calls == ["set_image:800x480:RGB", "show"]
    assert board.saturation == 0.0


def test_inky_clear_is_a_full_white_frame(inky: Callable[..., FakeInky]) -> None:
    board = inky()
    hardware_display(PANELS["inky_impression_7in3"]).clear()
    assert board.calls == ["set_image:800x480:RGB", "show"]
    assert board.image is not None
    assert board.image.getextrema() == ((255, 255), (255, 255), (255, 255))


def test_inky_refuses_a_board_of_another_size(inky: Callable[..., FakeInky]) -> None:
    inky(600, 448)  # an Inky Impression 5.7
    with pytest.raises(ValueError, match="600x448"):
        hardware_display(PANELS["inky_impression_7in3"])


def test_importing_the_driver_never_imports_a_vendor_package() -> None:
    import studylife_display.driver  # noqa: F401

    assert "inky" not in sys.modules
    assert "waveshare_epd" not in sys.modules


# ---------------------------------------------------- file emulation


def test_the_file_driver_emulates_the_panels_size(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = settings_for(
        monkeypatch,
        DISPLAY_DRIVER="file",
        DISPLAY_PANEL="waveshare_7in5_hd",
        DISPLAY_OUTPUT_PATH=str(tmp_path / "frame.png"),
    )
    display = make_display(settings)
    assert isinstance(display, FileDisplay)
    display.clear()
    with Image.open(display.clear_path) as cleared:
        assert cleared.size == (880, 528)


def test_the_hardware_driver_is_chosen_by_the_panel_key(
    monkeypatch: pytest.MonkeyPatch, inky: Callable[..., FakeInky]
) -> None:
    inky()
    settings = settings_for(monkeypatch, DISPLAY_PANEL="inky_impression_7in3")
    assert isinstance(make_display(settings), InkyDisplay)


# ------------------------------------------------ pipeline end to end


@respx.mock
@pytest.mark.parametrize("key", SCALED_KEYS)
def test_a_scaled_panel_gets_native_frames_and_skips_an_unchanged_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, key: str
) -> None:
    frame = tmp_path / "frame.png"
    state = tmp_path / "state" / "last.json"
    monkeypatch.setenv("STUDYLIFE_BASE_URL", BASE_URL)
    monkeypatch.setenv("STUDYLIFE_API_KEY", "test-key")
    monkeypatch.setenv("STUDYLIFE_TIMEZONE", "Europe/Berlin")
    monkeypatch.setenv("DISPLAY_DRIVER", "file")
    monkeypatch.setenv("DISPLAY_PANEL", key)
    monkeypatch.setenv("DISPLAY_OUTPUT_PATH", str(frame))
    monkeypatch.setenv("DISPLAY_STATE_PATH", str(state))
    metrics, history, timer, sessions = sample_payloads(NOW, BERLIN)
    for path, body in (
        ("/api/metrics/summary", metrics),
        ("/api/sessions/history", history),
        ("/api/timerstate", timer),
        ("/api/sessions", sessions),
    ):
        respx.get(f"{BASE_URL}{path}").mock(return_value=httpx.Response(200, json=body))
    goals, achievements, notes = sample_extras(NOW, BERLIN)
    respx.get(f"{BASE_URL}/api/coursegoals").mock(return_value=httpx.Response(200, json=goals))
    respx.get(f"{BASE_URL}/api/metrics/achievements").mock(
        return_value=httpx.Response(200, json=achievements)
    )
    respx.get(f"{BASE_URL}/api/notes").mock(return_value=httpx.Response(200, json=notes))

    native = PANELS[key].native_size
    assert main(["run"]) == 0
    with Image.open(frame) as written:
        assert written.size == native
    with Image.open(state.parent / "current.png") as kept:
        assert kept.size == native  # current.png is what is on the panel
    first = load_current_frame(state.parent, BERLIN)
    assert first is not None and first.fingerprint

    written_at = frame.stat().st_mtime_ns
    assert main(["run"]) == 0  # the header's "updated" time moved, the picture did not
    assert frame.stat().st_mtime_ns == written_at
    assert load_current_frame(state.parent, BERLIN) == first


def test_the_fingerprint_changes_when_the_panel_does() -> None:
    image = Image.new("1", (WIDTH, HEIGHT), 1)
    kwargs: dict[str, Any] = {"kind": "error", "layout": None, "stale_minutes": 0, "rotate": 0}
    default = frame_fingerprint(image, **kwargs)
    assert frame_fingerprint(image, panel=None, **kwargs) == default
    assert frame_fingerprint(image, panel="waveshare_7in5_hd", **kwargs) != default
