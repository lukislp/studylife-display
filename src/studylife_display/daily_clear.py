"""One full clear per day against ghosting.

E-paper panels keep faint traces of previous frames; a clear to white now and then wipes
them. `DISPLAY_CLEAR_AT` names a time of day, and the first scheduled `run` at or after it
clears the panel before drawing the frame. The moment of the last clear is kept in the
state directory (`last_clear`, ISO 8601), so a `run` every five minutes clears exactly once
per day, and a day whose scheduled time passed while the Pi was off is caught up on the next
run rather than skipped.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from studylife_display.quiet_hours import parse_clock

LAST_CLEAR_FILE = "last_clear"


def parse_clear_at(spec: str) -> time | None:
    """`04:00` (or `4`) -> time; empty -> None (daily clear off); ValueError otherwise."""
    spec = spec.strip()
    if not spec:
        return None
    return parse_clock(spec)


def last_scheduled(now: datetime, clear_at: time) -> datetime:
    """The most recent moment at `clear_at` that is not after `now`: today's if the time has
    passed (or is right now), yesterday's otherwise."""
    today = now.replace(hour=clear_at.hour, minute=clear_at.minute, second=0, microsecond=0)
    return today if today <= now else today - timedelta(days=1)


def clear_due(now: datetime, last_clear: datetime | None, clear_at: time | None) -> bool:
    """Whether a clear is owed: the schedule is on and the last clear (if any) happened
    before the most recent scheduled moment."""
    if clear_at is None:
        return False
    return last_clear is None or last_clear < last_scheduled(now, clear_at)


def last_clear_path(state_dir: Path) -> Path:
    return state_dir / LAST_CLEAR_FILE


def load_last_clear(state_dir: Path, tz: ZoneInfo) -> datetime | None:
    """The recorded moment of the last clear, or None when there is none or it is unreadable
    (which simply means one more clear, never a crash)."""
    try:
        text = last_clear_path(state_dir).read_text(encoding="utf-8").strip()
        moment = datetime.fromisoformat(text)
    except (OSError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=tz)
    return moment.astimezone(tz)


def save_last_clear(state_dir: Path, moment: datetime) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = last_clear_path(state_dir)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(moment.isoformat(), encoding="utf-8")
    tmp.replace(path)
