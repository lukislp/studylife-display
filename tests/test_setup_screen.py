"""The first-run setup screen and the connect URL it shows."""

from __future__ import annotations

import socket
from datetime import datetime

import pytest
import segno
from PIL import Image, ImageChops
from pydantic import ValidationError

from studylife_display.config import Settings
from studylife_display.connect import local_hostname, setup_connect_url
from studylife_display.layouts.setup import (
    QR_BOX_LEFT,
    QR_BOX_SIZE,
    QR_BOX_TOP,
    QR_ERROR_LEVEL,
    QR_QUIET_ZONE,
    QrPlacement,
    qr_placement,
    render_setup,
)

URL = "http://studylife-display.local:8795/connect"
BASE_URL = "https://studylife.test"


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"studylife_base_url": BASE_URL, "studylife_api_key": ""}
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def black_pixels(image: Image.Image) -> int:
    return image.histogram()[0]


def qr_region(image: Image.Image) -> Image.Image:
    return image.crop(
        (QR_BOX_LEFT, QR_BOX_TOP, QR_BOX_LEFT + QR_BOX_SIZE, QR_BOX_TOP + QR_BOX_SIZE)
    )


@pytest.mark.parametrize("language", ["de", "en"])
def test_renders_a_panel_sized_frame_with_a_qr_code(language: str, fixed_now: datetime) -> None:
    image = render_setup(URL, language, "studylife-display", fixed_now)
    assert image.size == (800, 480)
    assert image.mode == "1"
    region = qr_region(image)
    dark = black_pixels(region)
    # A QR code is roughly half dark, half light; text alone or a blank box is neither.
    assert QR_BOX_SIZE * QR_BOX_SIZE * 0.2 < dark < QR_BOX_SIZE * QR_BOX_SIZE * 0.8
    # The text column on the left has the headline, the line and the URL.
    assert black_pixels(image.crop((0, 60, QR_BOX_LEFT - 24, 480))) > 2000


def test_the_languages_differ_and_rendering_is_deterministic(fixed_now: datetime) -> None:
    de = render_setup(URL, "de", "pi", fixed_now)
    en = render_setup(URL, "en", "pi", fixed_now)
    again = render_setup(URL, "de", "pi", fixed_now)
    assert ImageChops.difference(de, en).getbbox() is not None
    assert ImageChops.difference(de, again).getbbox() is None
    # Only the text differs between the languages; the QR region is identical.
    assert ImageChops.difference(qr_region(de), qr_region(en)).getbbox() is None


def test_the_drawn_modules_equal_segnos_matrix() -> None:
    """Every module of segno's own matrix is where `qr_placement` says, dark modules black,
    light ones white, and the quiet zone around the symbol stays white."""
    image = render_setup(URL, "en", "pi")
    placement = qr_placement(URL)
    expected = segno.make(URL, error=QR_ERROR_LEVEL, micro=False)
    assert placement.qr.matrix == expected.matrix
    assert placement.scale >= 3  # a phone camera needs a few pixels per module
    assert placement.size <= QR_BOX_SIZE
    for row_index, row in enumerate(expected.matrix):
        for column, dark in enumerate(row):
            left, top, right, bottom = placement.module_box(column, row_index)
            centre = image.getpixel(((left + right) // 2, (top + bottom) // 2))
            assert (centre == 0) == bool(dark), (column, row_index)
    # The quiet zone: QR_QUIET_ZONE modules of white on every side of the symbol.
    zone = placement.quiet_zone * placement.scale
    assert zone == QR_QUIET_ZONE * placement.scale
    outer = image.crop(
        (
            placement.left,
            placement.top,
            placement.left + placement.size,
            placement.top + placement.size,
        )
    )
    inner = outer.crop((zone, zone, placement.size - zone, placement.size - zone))
    assert black_pixels(outer) == black_pixels(inner)
    assert black_pixels(inner) > 0


def test_the_qr_code_is_centred_in_its_box() -> None:
    placement = qr_placement(URL)
    assert isinstance(placement, QrPlacement)
    slack = QR_BOX_SIZE - placement.size
    assert 0 <= slack < placement.modules  # the largest whole-pixel scale that fits
    assert placement.left - QR_BOX_LEFT == slack // 2
    assert placement.top - QR_BOX_TOP == slack // 2


def test_a_phone_would_decode_it() -> None:
    """Only where a decoder happens to be installed; the matrix test above is what CI runs."""
    cv2 = pytest.importorskip("cv2")
    numpy = pytest.importorskip("numpy")
    image = render_setup(URL, "de", "pi")
    text, _, _ = cv2.QRCodeDetector().detectAndDecode(numpy.array(image.convert("L")))
    assert text == URL


def test_a_long_url_stays_inside_the_text_column() -> None:
    long_url = "https://studylife-display.some.very.long.tailnet-name.ts.net:8795/connect"
    image = render_setup(long_url, "en", "a-hostname-that-goes-on-and-on-and-on-and-on")
    # Nothing may cross into the gap before the QR box or touch the margins.
    assert black_pixels(image.crop((QR_BOX_LEFT - 24, 60, QR_BOX_LEFT, 480))) == 0
    assert black_pixels(image.crop((0, 60, 12, 480))) == 0


class TestConnectUrl:
    def test_from_the_hostname_and_the_web_port(self) -> None:
        assert setup_connect_url(settings(), hostname="pi") == "http://pi.local:8795/connect"
        custom = settings(display_web_bind="127.0.0.1:9000")
        assert setup_connect_url(custom, hostname="studylife") == (
            "http://studylife.local:9000/connect"
        )

    def test_from_the_public_base_url(self) -> None:
        public = settings(display_public_base_url="https://pi.tail.example.ts.net/")
        assert setup_connect_url(public, hostname="pi") == (
            "https://pi.tail.example.ts.net/connect"
        )

    def test_the_explicit_override_wins(self) -> None:
        explicit = settings(
            display_public_base_url="https://pi.tail.example.ts.net",
            display_setup_url="http://192.168.1.20:8795/connect",
        )
        assert setup_connect_url(explicit, hostname="pi") == "http://192.168.1.20:8795/connect"

    def test_the_override_has_to_be_a_url(self) -> None:
        with pytest.raises(ValidationError):
            settings(display_setup_url="pi.local:8795")
        assert settings(display_setup_url="  ").display_setup_url == ""

    def test_local_hostname_strips_a_trailing_local(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(socket, "gethostname", lambda: "Studylife-Display.local")
        assert local_hostname() == "Studylife-Display"
        monkeypatch.setattr(socket, "gethostname", lambda: "pi")
        assert local_hostname() == "pi"
        assert setup_connect_url(settings()) == "http://pi.local:8795/connect"
        monkeypatch.setattr(socket, "gethostname", lambda: "")
        assert local_hostname() == "raspberrypi"
