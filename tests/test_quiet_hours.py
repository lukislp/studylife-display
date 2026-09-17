from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from studylife_display.quiet_hours import (
    in_quiet_hours,
    parse_clock,
    parse_quiet_hours,
    quiet_hours_end,
)

BERLIN = ZoneInfo("Europe/Berlin")


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 17, hour, minute, 30, tzinfo=BERLIN)


class TestParsing:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [("7", time(7)), ("07", time(7)), ("7:30", time(7, 30)), ("23:59", time(23, 59))],
    )
    def test_clock_values(self, text: str, expected: time) -> None:
        assert parse_clock(text) == expected

    @pytest.mark.parametrize("text", ["", "24", "7:60", "seven", "7:", ":30", "7:3x", "-1"])
    def test_clock_rejects_garbage(self, text: str) -> None:
        with pytest.raises(ValueError):
            parse_clock(text)

    def test_empty_means_off(self) -> None:
        assert parse_quiet_hours("") is None
        assert parse_quiet_hours("   ") is None

    def test_both_notations(self) -> None:
        assert parse_quiet_hours("23-7") == (time(23), time(7))
        assert parse_quiet_hours("22:30-06:15") == (time(22, 30), time(6, 15))
        assert parse_quiet_hours(" 1 - 5 ") == (time(1), time(5))

    @pytest.mark.parametrize("spec", ["23", "23-", "-7", "23-7-9", "7-7", "07:00-7", "a-b"])
    def test_rejects_malformed_or_empty_windows(self, spec: str) -> None:
        with pytest.raises(ValueError):
            parse_quiet_hours(spec)


class TestInQuietHours:
    def test_off_is_never_quiet(self) -> None:
        assert in_quiet_hours(at(3), "") is False

    def test_same_day_window_with_boundaries(self) -> None:
        spec = "13-15"
        assert in_quiet_hours(at(12, 59), spec) is False
        assert in_quiet_hours(at(13, 0), spec) is True  # start inclusive
        assert in_quiet_hours(at(14, 30), spec) is True
        assert in_quiet_hours(at(14, 59), spec) is True
        assert in_quiet_hours(at(15, 0), spec) is False  # end exclusive
        assert in_quiet_hours(at(3), spec) is False

    def test_midnight_wrap_with_boundaries(self) -> None:
        spec = "23-7"
        assert in_quiet_hours(at(22, 59), spec) is False
        assert in_quiet_hours(at(23, 0), spec) is True
        assert in_quiet_hours(at(23, 59), spec) is True
        assert in_quiet_hours(at(0, 0), spec) is True
        assert in_quiet_hours(at(3, 15), spec) is True
        assert in_quiet_hours(at(6, 59), spec) is True
        assert in_quiet_hours(at(7, 0), spec) is False
        assert in_quiet_hours(at(12), spec) is False

    def test_minute_precision(self) -> None:
        spec = "22:30-06:15"
        assert in_quiet_hours(at(22, 29), spec) is False
        assert in_quiet_hours(at(22, 30), spec) is True
        assert in_quiet_hours(at(6, 14), spec) is True
        assert in_quiet_hours(at(6, 15), spec) is False


class TestQuietHoursEnd:
    def test_outside_is_none(self) -> None:
        assert quiet_hours_end(at(12), "23-7") is None
        assert quiet_hours_end(at(12), "") is None

    def test_end_later_today(self) -> None:
        end = quiet_hours_end(at(3), "23-7")
        assert end == datetime(2026, 9, 17, 7, 0, tzinfo=BERLIN)

    def test_end_tomorrow_when_started_before_midnight(self) -> None:
        end = quiet_hours_end(at(23, 30), "23-7")
        assert end == datetime(2026, 9, 18, 7, 0, tzinfo=BERLIN)
