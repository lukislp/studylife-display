import sys
import types
from pathlib import Path

import pytest
from PIL import Image

from studylife_display.driver import FileDisplay, WaveshareDisplay


def test_file_display_writes_a_png(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "frame.png"
    display = FileDisplay(target)
    display.show(Image.new("1", (800, 480), 1))
    display.sleep()
    assert target.exists()
    with Image.open(target) as saved:
        assert saved.format == "PNG"
        assert saved.size == (800, 480)


class FakeEpd:
    width = 800
    height = 480

    def __init__(self) -> None:
        self.calls: list[str] = []

    def init(self) -> None:
        self.calls.append("init")

    def getbuffer(self, image: Image.Image) -> bytes:
        self.calls.append("getbuffer")
        return image.tobytes()

    def display(self, buffer: bytes) -> None:
        self.calls.append(f"display:{len(buffer)}")

    def sleep(self) -> None:
        self.calls.append("sleep")


@pytest.fixture
def fake_waveshare(monkeypatch: pytest.MonkeyPatch) -> FakeEpd:
    """Injects a stand-in for the vendor package, which only builds on a Pi."""
    epd = FakeEpd()
    module = types.ModuleType("waveshare_epd.epd7in5_V2")
    module.EPD = lambda: epd  # type: ignore[attr-defined]
    package = types.ModuleType("waveshare_epd")
    package.epd7in5_V2 = module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "waveshare_epd", package)
    monkeypatch.setitem(sys.modules, "waveshare_epd.epd7in5_V2", module)
    return epd


def test_waveshare_display_does_a_full_refresh_then_sleeps(fake_waveshare: FakeEpd) -> None:
    display = WaveshareDisplay()
    display.show(Image.new("1", (800, 480), 1))
    display.sleep()
    assert fake_waveshare.calls == ["init", "getbuffer", "display:48000", "sleep"]


def test_waveshare_display_refuses_a_frame_of_the_wrong_size(fake_waveshare: FakeEpd) -> None:
    display = WaveshareDisplay()
    with pytest.raises(ValueError):
        display.show(Image.new("1", (480, 800), 1))
    assert fake_waveshare.calls == []


def test_importing_the_driver_module_never_imports_the_vendor_package() -> None:
    # The import lives inside WaveshareDisplay.__init__, so the module (and everything the
    # tests use) loads on machines without the `pi` extra.
    assert "waveshare_epd" not in sys.modules
