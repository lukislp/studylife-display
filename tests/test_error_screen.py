from datetime import datetime

import pytest
from PIL import Image, ImageChops

from studylife_display.layouts.error import ERROR_KINDS, format_age, render_error


def black_pixels(image: Image.Image) -> int:
    return image.histogram()[0]


@pytest.mark.parametrize("kind", sorted(ERROR_KINDS))
@pytest.mark.parametrize("language", ["de", "en"])
def test_every_kind_renders_a_panel_sized_frame(kind: str, language: str) -> None:
    image = render_error(kind, "HTTP 403", language, last_error="StudyLife API returned 403")
    assert image.size == (800, 480)
    assert image.mode == "1"
    assert black_pixels(image) > 2000  # a headline and two lines of text, not a blank frame


def test_the_kinds_differ_from_each_other() -> None:
    frames = {kind: render_error(kind, "x", "en") for kind in ERROR_KINDS}
    kinds = sorted(frames)
    for first, second in zip(kinds, kinds[1:], strict=False):
        assert ImageChops.difference(frames[first], frames[second]).getbbox() is not None


def test_detail_time_and_last_error_change_the_frame(fixed_now: datetime) -> None:
    plain = render_error("rejected", "HTTP 403", "en")
    other_detail = render_error("rejected", "HTTP 401", "en")
    with_time = render_error("rejected", "HTTP 403", "en", now=fixed_now)
    with_error = render_error("rejected", "HTTP 403", "en", last_error="no scope")
    assert ImageChops.difference(plain, other_detail).getbbox() is not None
    assert ImageChops.difference(plain, with_time).getbbox() is not None
    assert ImageChops.difference(plain, with_error).getbbox() is not None


def test_is_deterministic(fixed_now: datetime) -> None:
    a = render_error("stale", "26 h", "de", fixed_now, "connection refused")
    b = render_error("stale", "26 h", "de", fixed_now, "connection refused")
    assert ImageChops.difference(a, b).getbbox() is None


def test_an_overlong_message_is_cut_not_wrapped_off_the_panel() -> None:
    image = render_error("no_data", "x" * 400, "en", last_error="y" * 400)
    assert image.size == (800, 480)
    # Nothing may touch the outer margin: every line is ellipsised to the drawable width.
    assert black_pixels(image.crop((0, 60, 12, 480))) == 0
    assert black_pixels(image.crop((788, 60, 800, 480))) == 0


def test_unknown_kind_is_refused() -> None:
    with pytest.raises(ValueError):
        render_error("meltdown", "", "en")


def test_format_age() -> None:
    assert format_age(90, "en") == "1 h"
    assert format_age(26 * 60, "de") == "26 h"
    assert format_age(3 * 24 * 60 + 5, "en") == "3 days"
    assert format_age(3 * 24 * 60 + 5, "de") == "3 Tage"
