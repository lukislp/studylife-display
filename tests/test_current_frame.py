"""The copy of the frame on the panel (current.png + current.json) as a store."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from studylife_display.current_frame import (
    DASHBOARD,
    ERROR,
    SETUP,
    CurrentFrame,
    current_json_path,
    current_png_path,
    load_current_frame,
    read_current_png,
    save_current_frame,
)


def frame() -> Image.Image:
    image = Image.new("1", (800, 480), 1)
    image.putpixel((3, 3), 0)
    return image


def test_round_trip(tmp_path: Path, tz: ZoneInfo, fixed_now: datetime) -> None:
    state_dir = tmp_path / "state"
    save_current_frame(state_dir, frame(), fixed_now, "classic", DASHBOARD)
    assert load_current_frame(state_dir, tz) == CurrentFrame(fixed_now, "classic", DASHBOARD)
    raw = json.loads(current_json_path(state_dir).read_text(encoding="utf-8"))
    assert raw == {"shown_at": fixed_now.isoformat(), "layout": "classic", "kind": "dashboard"}
    png = read_current_png(state_dir)
    assert png is not None and png == current_png_path(state_dir).read_bytes()
    with Image.open(current_png_path(state_dir)) as saved:
        assert saved.size == (800, 480)
        assert saved.convert("1").getpixel((3, 3)) == 0
    # No temp files left behind by the atomic writes.
    assert sorted(p.name for p in state_dir.iterdir()) == ["current.json", "current.png"]


def test_nothing_shown_yet_and_damaged_files(tmp_path: Path, tz: ZoneInfo) -> None:
    assert load_current_frame(tmp_path, tz) is None
    assert read_current_png(tmp_path) is None
    current_png_path(tmp_path).write_bytes(b"png")
    assert load_current_frame(tmp_path, tz) is None  # no metadata
    current_json_path(tmp_path).write_text("{not json", encoding="utf-8")
    assert load_current_frame(tmp_path, tz) is None
    current_json_path(tmp_path).write_text('{"shown_at": 5}', encoding="utf-8")
    assert load_current_frame(tmp_path, tz) is None
    current_json_path(tmp_path).write_text(
        '{"shown_at": "2026-09-17T16:45:00", "kind": "meltdown"}', encoding="utf-8"
    )
    loaded = load_current_frame(tmp_path, tz)
    assert loaded is not None
    assert loaded.kind == DASHBOARD and loaded.layout is None
    assert loaded.shown_at.tzinfo is not None  # a naive stamp reads in the display's zone


@pytest.mark.parametrize("kind", [ERROR, SETUP])
def test_screens_carry_no_layout(
    tmp_path: Path, tz: ZoneInfo, fixed_now: datetime, kind: str
) -> None:
    save_current_frame(tmp_path, frame(), fixed_now, None, kind)
    loaded = load_current_frame(tmp_path, tz)
    assert loaded is not None
    assert (loaded.kind, loaded.layout) == (kind, None)


def test_unknown_kind_is_refused(tmp_path: Path, fixed_now: datetime) -> None:
    with pytest.raises(ValueError):
        save_current_frame(tmp_path, frame(), fixed_now, None, "meltdown")
    assert not current_png_path(tmp_path).exists()
