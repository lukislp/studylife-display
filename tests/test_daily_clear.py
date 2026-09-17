from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from studylife_display.daily_clear import (
    clear_due,
    last_scheduled,
    load_last_clear,
    parse_clear_at,
    save_last_clear,
)

BERLIN = ZoneInfo("Europe/Berlin")
FOUR = time(4, 0)


def on(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=BERLIN)


class TestParseClearAt:
    def test_values(self) -> None:
        assert parse_clear_at("04:00") == FOUR
        assert parse_clear_at("4") == FOUR
        assert parse_clear_at(" 23:30 ") == time(23, 30)

    def test_empty_is_off(self) -> None:
        assert parse_clear_at("") is None
        assert parse_clear_at("  ") is None

    @pytest.mark.parametrize("spec", ["24:00", "4:60", "four", "04-00"])
    def test_rejects_garbage(self, spec: str) -> None:
        with pytest.raises(ValueError):
            parse_clear_at(spec)


class TestLastScheduled:
    def test_today_once_the_time_has_passed(self) -> None:
        assert last_scheduled(on(17, 4, 0), FOUR) == on(17, 4)
        assert last_scheduled(on(17, 16, 45), FOUR) == on(17, 4)

    def test_yesterday_before_the_time(self) -> None:
        assert last_scheduled(on(17, 3, 59), FOUR) == on(16, 4)


class TestClearDue:
    def test_off_is_never_due(self) -> None:
        assert clear_due(on(17, 4), None, None) is False

    def test_never_cleared_is_due(self) -> None:
        assert clear_due(on(17, 12), None, FOUR) is True

    def test_once_per_day_across_the_boundary(self) -> None:
        # Cleared at yesterday's slot; every run before today's 04:00 is not due ...
        last = on(16, 4, 1)
        assert clear_due(on(16, 4, 6), last, FOUR) is False
        assert clear_due(on(16, 23, 55), last, FOUR) is False
        assert clear_due(on(17, 0, 0), last, FOUR) is False
        assert clear_due(on(17, 3, 59), last, FOUR) is False
        # ... the first one at or after it is ...
        assert clear_due(on(17, 4, 0), last, FOUR) is True
        assert clear_due(on(17, 4, 5), last, FOUR) is True
        # ... and once it happened, the rest of the day is quiet again, until tomorrow.
        cleared = on(17, 4, 5)
        assert clear_due(on(17, 4, 10), cleared, FOUR) is False
        assert clear_due(on(17, 23, 59), cleared, FOUR) is False
        assert clear_due(on(18, 3, 59), cleared, FOUR) is False
        assert clear_due(on(18, 4, 0), cleared, FOUR) is True

    def test_a_missed_slot_is_caught_up_later_in_the_day(self) -> None:
        # The Pi was off at 04:00; the first run of the day, whenever it is, clears.
        assert clear_due(on(17, 15, 0), on(16, 4, 2), FOUR) is True

    def test_a_clear_recorded_exactly_at_the_slot_counts(self) -> None:
        assert clear_due(on(17, 4, 30), on(17, 4, 0), FOUR) is False

    def test_a_slot_late_in_the_evening(self) -> None:
        late = time(23, 30)
        assert clear_due(on(17, 23, 29), on(16, 23, 31), late) is False
        assert clear_due(on(17, 23, 30), on(16, 23, 31), late) is True
        assert clear_due(on(18, 0, 5), on(17, 23, 32), late) is False


class TestPersistence:
    def test_round_trip(self, tmp_path: Path) -> None:
        moment = on(17, 4, 1)
        save_last_clear(tmp_path / "state", moment)
        assert load_last_clear(tmp_path / "state", BERLIN) == moment
        assert (tmp_path / "state" / "last_clear").exists()

    def test_missing_or_damaged_reads_as_none(self, tmp_path: Path) -> None:
        assert load_last_clear(tmp_path, BERLIN) is None
        (tmp_path / "last_clear").write_text("yesterday-ish", encoding="utf-8")
        assert load_last_clear(tmp_path, BERLIN) is None

    def test_naive_timestamp_is_taken_as_local(self, tmp_path: Path) -> None:
        (tmp_path / "last_clear").write_text("2026-09-17T04:01:00", encoding="utf-8")
        assert load_last_clear(tmp_path, BERLIN) == on(17, 4, 1)
