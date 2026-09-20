"""Pins the JSON field names the dashboard reads to the verified StudyLife wire format.

StudyLife never errors on an unknown field - it simply is not there - so a typo like
`streak.curent` would silently render as 0 forever. Two assertions guard against that:

1. every entry in model.USED_FIELDS is on the verified list for its endpoint, and
2. what build_dashboard actually reads (recorded through dict subclasses) is exactly
   USED_FIELDS - so the constant cannot drift from the code either.
"""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from studylife_display.model import USED_FIELDS, build_dashboard

# Verified against the server (see the README), dotted notation, "[]" = list element.
VERIFIED: dict[str, frozenset[str]] = {
    "metrics": frozenset(
        {
            "streak",
            "streak.current",
            "streak.longest",
            "hours",
            "hours.week",
            "hours.month",
            "hours.total",
            "hours.totalSessions",
            "weekQuota",
            "weekQuota.hours",
            "weekQuota.targetMin",
            "weekQuota.targetMax",
            "weekQuota.percent",
            "weekQuota.warning",
            "monthQuota",
            "ects",
            "ects.earned",
            "ects.total",
            "averageGrade",
            "forecast",
            "forecast.available",
            "forecast.alreadyDone",
            "forecast.date",
            "forecast.recentWeeklyHours",
            "monthComparison",
            "neglectedCourse",
            "neglectedCourse.courseId",
            "neglectedCourse.courseName",
            "neglectedCourse.lastStudied",
            "neglectedCourse.daysSince",
            "courseHours",
            "topics",
            "topics.completed",
            "topics.total",
            "program",
            "program.name",
            "upcomingCourseGoals",
            "upcomingCourseGoals[].courseId",
            "upcomingCourseGoals[].courseName",
            "upcomingCourseGoals[].targetDate",
            "upcomingCourseGoals[].daysLeft",
            "weeklyReport",
            "weeklyReport.weekId",
            "weeklyReport.hours",
            "weeklyReport.deltaVsPreviousWeek",
            "weeklyReport.topCourseName",
            "weeklyReport.sessionCount",
            "asOf",
        }
    ),
    "history": frozenset(
        {
            "[].id",
            "[].courseId",
            "[].courseName",
            "[].courseColor",
            "[].startTime",
            "[].endTime",
            "[].topic",
            "[].isCompleted",
            "[].timerModeId",
        }
    ),
    "timer": frozenset(
        {
            "sessionId",
            "isRunning",
            "isBreak",
            "currentRound",
            "timerModeId",
            "phaseEndsAt",
            "updatedAt",
            "serverNow",
        }
    ),
    # GET /api/sessions: StudySessionDto, the same shape as the history plus notes and the
    # recurrence group.
    "sessions": frozenset(
        {
            "[].id",
            "[].courseId",
            "[].courseName",
            "[].courseColor",
            "[].startTime",
            "[].endTime",
            "[].topic",
            "[].notes",
            "[].isCompleted",
            "[].timerModeId",
            "[].recurrenceGroupId",
        }
    ),
}

# Fields that do NOT exist and must never appear in USED_FIELDS (they were plausible enough
# to be invented once).
FORBIDDEN = {
    "hours.today",
    "isPaused",
    "courseId",
    "ects.percent",
    "forecast.graduationDate",
    "neglectedCourse.name",
    "topics.done",
    "weeklyReport.delta",
    "weeklyReport.sessions",
    "[].title",
    "[].completed",
    "[].start",
    "[].end",
}


class Recorder(dict[str, Any]):
    """A dict that logs every key it hands out, wrapping nested dicts and lists so that
    nested reads are logged with their full dotted path."""

    def __init__(self, data: dict[str, Any], log: set[str], prefix: str = "") -> None:
        super().__init__(data)
        self._log = log
        self._prefix = prefix

    def _wrap(self, key: str, value: Any) -> Any:
        path = f"{self._prefix}{key}"
        self._log.add(path)
        if isinstance(value, dict):
            return Recorder(value, self._log, path + ".")
        if isinstance(value, list):
            return [
                Recorder(item, self._log, path + "[].") if isinstance(item, dict) else item
                for item in value
            ]
        return value

    def __getitem__(self, key: str) -> Any:
        return self._wrap(key, super().__getitem__(key))

    def get(self, key: str, default: Any = None) -> Any:
        return self._wrap(key, super().get(key, default))


def test_used_fields_are_all_verified() -> None:
    for endpoint, used in USED_FIELDS.items():
        unknown = used - VERIFIED[endpoint]
        assert not unknown, f"{endpoint}: not on the verified wire format: {sorted(unknown)}"


def test_used_fields_contain_no_invented_names() -> None:
    for used in USED_FIELDS.values():
        assert not (used & FORBIDDEN)


def test_build_dashboard_reads_exactly_used_fields(
    sample: tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]],
    fixed_now: datetime,
    tz: ZoneInfo,
) -> None:
    metrics, history, timer, sessions = sample
    logs: dict[str, set[str]] = {
        "metrics": set(),
        "history": set(),
        "timer": set(),
        "sessions": set(),
    }
    build_dashboard(
        Recorder(metrics, logs["metrics"]),
        [Recorder(item, logs["history"], "[].") for item in history],
        Recorder(timer, logs["timer"]),
        fixed_now,
        tz,
        sessions=[Recorder(item, logs["sessions"], "[].") for item in sessions],
    )
    for endpoint, read in logs.items():
        assert read == set(USED_FIELDS[endpoint]), endpoint


def test_every_endpoint_has_a_used_and_a_verified_list() -> None:
    assert set(USED_FIELDS) == set(VERIFIED) == {"metrics", "history", "timer", "sessions"}


def test_sample_payloads_use_only_verified_names(
    sample: tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]],
) -> None:
    """The sample data doubles as documentation of the wire format; keep it honest."""
    metrics, history, timer, sessions = sample

    def paths(value: Any, prefix: str = "") -> set[str]:
        found: set[str] = set()
        if isinstance(value, dict):
            for key, child in value.items():
                found.add(prefix + key)
                found |= paths(child, prefix + key + ".")
        elif isinstance(value, list):
            for child in value:
                found |= paths(child, prefix.removesuffix(".") + "[].")
        return found

    assert paths(metrics) <= VERIFIED["metrics"]
    assert paths(history) <= VERIFIED["history"]
    assert paths(timer) <= VERIFIED["timer"]
    assert paths(sessions) <= VERIFIED["sessions"]
    # The sample sessions carry every field the DTO has, so the list above stays complete.
    assert paths(sessions) == VERIFIED["sessions"]
