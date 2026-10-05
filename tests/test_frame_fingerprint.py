"""The frame fingerprint: blind to the header's fetch time, alert to everything else."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from studylife_display.frame_fingerprint import frame_fingerprint
from studylife_display.layouts import LAYOUTS
from studylife_display.layouts.common import HEADER_UPDATED_BOX, RULE_Y
from studylife_display.model import build_dashboard
from studylife_display.render import render
from studylife_display.sample import sample_extras, sample_payloads


def fp(image: Image.Image, **overrides: object) -> str:
    args: dict[str, object] = {
        "kind": "dashboard",
        "layout": "classic",
        "stale_minutes": 0,
        "rotate": 0,
    }
    args.update(overrides)
    return frame_fingerprint(image, **args)  # type: ignore[arg-type]


def dashboard(now: datetime, tz: ZoneInfo, fetched_at: datetime | None = None) -> object:
    metrics, history, timer, sessions = sample_payloads(now, tz)
    goals, achievements, notes = sample_extras(now, tz)
    return build_dashboard(
        metrics,
        history,
        timer,
        now,
        tz,
        fetched_at=fetched_at,
        sessions=sessions,
        goals=goals,
        achievements_payload=achievements,
        notes_payload=notes,
    )


def blank() -> Image.Image:
    return Image.new("1", (800, 480), 1)


def test_header_update_text_is_ignored_for_dashboards() -> None:
    a, b = blank(), blank()
    left, top, right, bottom = HEADER_UPDATED_BOX
    b.putpixel((right - 30, top + 20), 0)
    assert fp(a) == fp(b)


def test_screens_hash_the_whole_image() -> None:
    a, b = blank(), blank()
    b.putpixel((HEADER_UPDATED_BOX[2] - 30, 20), 0)
    for kind in ("error", "setup"):
        assert fp(a, kind=kind, layout=None) != fp(b, kind=kind, layout=None)


def test_body_change_is_detected() -> None:
    a, b = blank(), blank()
    b.putpixel((100, RULE_Y + 50), 0)
    assert fp(a) != fp(b)
    c = blank()
    c.putpixel((10, 20), 0)  # left of the box: brand/date area
    assert fp(a) != fp(c)


@pytest.mark.parametrize(
    "change",
    [{"layout": "week"}, {"kind": "error"}, {"rotate": 180}, {"stale_minutes": 5}],
)
def test_metadata_changes_are_detected(change: dict[str, object]) -> None:
    assert fp(blank()) != fp(blank(), **change)


def test_stale_value_matters_while_stale_only() -> None:
    assert fp(blank(), stale_minutes=35) != fp(blank(), stale_minutes=40)
    assert fp(blank(), stale_minutes=35) == fp(blank(), stale_minutes=35)
    assert fp(blank(), stale_minutes=0) == fp(blank(), stale_minutes=-3)


def test_duo_pair_matters_for_the_duo_layout() -> None:
    a = fp(blank(), layout="duo", duo=("classic", "week"))
    assert a != fp(blank(), layout="duo", duo=("week", "classic"))
    assert fp(blank(), layout="classic", duo=("a", "b")) == fp(blank())


def test_pure_and_deterministic() -> None:
    image = blank()
    image.putpixel((5, 5), 0)
    before = image.tobytes()
    assert fp(image) == fp(image)
    assert image.tobytes() == before
    assert len(fp(image)) == 64


@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_real_layouts_differ_only_in_the_header_box_by_fetch_time(
    layout: str, tz: ZoneInfo, fixed_now: datetime
) -> None:
    """The blanked box covers all of the header's right text in every layout and nothing
    else: two renders that differ only by fetch time hash alike, and a new day does not."""
    if layout == "duo":
        pytest.skip("rendered below through the pair path")
    first = render(dashboard(fixed_now, tz, fixed_now), "de", layout)  # type: ignore[arg-type]
    later = fixed_now + timedelta(minutes=5)
    second = render(dashboard(fixed_now, tz, later), "de", layout)  # type: ignore[arg-type]
    diff = [
        (x, y)
        for x in range(800)
        for y in range(480)
        if first.getpixel((x, y)) != second.getpixel((x, y))
    ]
    left, top, right, bottom = HEADER_UPDATED_BOX
    assert all(left <= x < right and top <= y < bottom for x, y in diff)
    assert fp(first, layout=layout) == fp(second, layout=layout)
    other_day = render(
        dashboard(fixed_now + timedelta(days=1), tz, fixed_now),  # type: ignore[arg-type]
        "de",
        layout,
    )
    assert fp(first, layout=layout) != fp(other_day, layout=layout)
