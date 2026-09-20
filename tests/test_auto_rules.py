from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from studylife_display.auto_rules import RuleWindow, in_rule_window, parse_rule_window

BERLIN = ZoneInfo("Europe/Berlin")


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """September 2026: the 14th is a Monday, the 20th a Sunday."""
    return datetime(2026, 9, day, hour, minute, 30, tzinfo=BERLIN)


class TestParsing:
    def test_empty_means_off(self) -> None:
        assert parse_rule_window("") is None
        assert parse_rule_window("   ") is None

    def test_window_without_weekdays(self) -> None:
        assert parse_rule_window("06-12") == RuleWindow(None, 6 * 60, 12 * 60)
        assert parse_rule_window("6:30-11:45") == RuleWindow(None, 6 * 60 + 30, 11 * 60 + 45)

    def test_weekday_forms(self) -> None:
        assert parse_rule_window("sun 18-24") == RuleWindow(frozenset({6}), 18 * 60, 24 * 60)
        assert parse_rule_window("Sat,Sun 9-12") == RuleWindow(frozenset({5, 6}), 9 * 60, 12 * 60)
        assert parse_rule_window("mon-fri 06-09") == RuleWindow(
            frozenset({0, 1, 2, 3, 4}), 6 * 60, 9 * 60
        )
        assert parse_rule_window("  sun   18:00-23:59 ") == RuleWindow(
            frozenset({6}), 18 * 60, 23 * 60 + 59
        )

    def test_midnight_only_as_the_end(self) -> None:
        assert parse_rule_window("22-24:00") == RuleWindow(None, 22 * 60, 24 * 60)
        with pytest.raises(ValueError):
            parse_rule_window("24-2")
        with pytest.raises(ValueError):
            parse_rule_window("22-24:30")

    @pytest.mark.parametrize(
        "spec",
        [
            "18",
            "18-",
            "-24",
            "sun",
            "sun 18",
            "18-18",
            "23-7",  # a wrap past midnight is not allowed here
            "fri-mon 18-20",
            "funday 18-20",
            "sun 25-26",
            "sun 18-20-22",
            "7:60-9",
        ],
    )
    def test_rejects_garbage(self, spec: str) -> None:
        with pytest.raises(ValueError):
            parse_rule_window(spec)


class TestInRuleWindow:
    def test_off_never_matches(self) -> None:
        assert in_rule_window(at(20, 19), "") is False

    def test_sunday_evening_boundaries(self) -> None:
        spec = "sun 18-24"
        assert in_rule_window(at(20, 17, 59), spec) is False
        assert in_rule_window(at(20, 18, 0), spec) is True
        assert in_rule_window(at(20, 21, 30), spec) is True
        assert in_rule_window(at(20, 23, 59), spec) is True
        assert in_rule_window(at(21, 0, 0), spec) is False  # Monday 00:00
        assert in_rule_window(at(19, 20, 0), spec) is False  # Saturday evening
        assert in_rule_window(at(14, 20, 0), spec) is False  # Monday evening

    def test_daily_window_boundaries(self) -> None:
        spec = "06-12"
        assert in_rule_window(at(17, 5, 59), spec) is False
        assert in_rule_window(at(17, 6, 0), spec) is True
        assert in_rule_window(at(17, 11, 59), spec) is True
        assert in_rule_window(at(17, 12, 0), spec) is False
        assert in_rule_window(at(20, 8, 0), spec) is True  # any weekday

    def test_weekday_range(self) -> None:
        spec = "mon-fri 06:30-09:00"
        assert in_rule_window(at(14, 6, 30), spec) is True
        assert in_rule_window(at(18, 8, 59), spec) is True
        assert in_rule_window(at(19, 8, 0), spec) is False  # Saturday
        assert in_rule_window(at(14, 6, 29), spec) is False

    def test_seconds_do_not_matter(self) -> None:
        moment = datetime(2026, 9, 20, 23, 59, 59, tzinfo=BERLIN)
        assert in_rule_window(moment, "sun 18-24") is True
