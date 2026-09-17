import sys
import types
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from studylife_display.driver import FileDisplay, WaveshareDisplay, oriented


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

    def Clear(self) -> None:  # noqa: N802 - the vendor's spelling
        self.calls.append("Clear")


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


def marked_frame() -> Image.Image:
    """A white frame with a single black pixel in the top-left corner."""
    image = Image.new("1", (800, 480), 1)
    image.putpixel((0, 0), 0)
    return image


def test_oriented_180_flips_both_ways_and_0_is_identity() -> None:
    image = marked_frame()
    turned = oriented(image, 180)
    flipped = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT).transpose(
        Image.Transpose.FLIP_TOP_BOTTOM
    )
    assert ImageChops.difference(turned, flipped).getbbox() is None
    assert turned.getpixel((799, 479)) == 0
    assert turned.getpixel((0, 0)) != 0
    assert oriented(image, 0) is image
    with pytest.raises(ValueError):
        oriented(image, 90)


def test_file_display_applies_the_rotation_when_writing(tmp_path: Path) -> None:
    target = tmp_path / "frame.png"
    FileDisplay(target, rotate=180).show(marked_frame())
    with Image.open(target) as saved:
        assert saved.convert("1").getpixel((799, 479)) == 0
        assert saved.convert("1").getpixel((0, 0)) != 0


def test_file_display_clear_writes_a_white_frame_next_to_the_output(tmp_path: Path) -> None:
    display = FileDisplay(tmp_path / "out" / "frame.png")
    display.clear()
    assert display.clear_path == tmp_path / "out" / "frame-clear.png"
    with Image.open(display.clear_path) as cleared:
        assert cleared.size == (800, 480)
        assert cleared.convert("1").histogram()[0] == 0  # not one black pixel


def test_waveshare_display_rotates_the_buffer_it_sends(fake_waveshare: FakeEpd) -> None:
    sent: list[bytes] = []
    fake_waveshare.getbuffer = lambda image: sent.append(image.tobytes()) or b""  # type: ignore[method-assign]
    WaveshareDisplay(rotate=180).show(marked_frame())
    assert sent == [marked_frame().transpose(Image.Transpose.ROTATE_180).tobytes()]


def test_waveshare_display_clear_inits_and_clears(fake_waveshare: FakeEpd) -> None:
    display = WaveshareDisplay()
    display.clear()
    display.show(Image.new("1", (800, 480), 1))
    display.sleep()
    assert fake_waveshare.calls == ["init", "Clear", "init", "getbuffer", "display:48000", "sleep"]
