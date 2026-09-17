"""Sample payloads in the exact wire format, for `preview --sample`, the goldens and tests.

Timestamps are generated relative to `now` so that a preview always shows a plausible
"today"; the tests pass a fixed `now` to keep the goldens reproducible.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from studylife_display.times import local_day

_SESSION_MINUTES = [
    # Hours per day for the 28 heatmap days, oldest first (last entry = today).
    0, 45, 120, 0, 90, 180, 30,
    60, 0, 150, 200, 0, 0, 90,
    120, 240, 0, 60, 30, 0, 180,
    150, 90, 0, 60, 120, 210, 225,
]  # fmt: skip

_COURSES = [
    ("Betriebssysteme", "#1D4ED8"),
    ("Lineare Algebra", "#047857"),
    ("Datenbanken", "#B45309"),
]


def _naive(moment: datetime) -> str:
    return moment.replace(tzinfo=None).isoformat(timespec="seconds")


def sample_payloads(
    now: datetime, tz: ZoneInfo
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    today = local_day(now, tz)
    history: list[dict[str, Any]] = []
    session_id = 1
    for offset, minutes in enumerate(reversed(_SESSION_MINUTES)):
        if minutes == 0:
            continue
        day = today - timedelta(days=offset)
        start = datetime.combine(day, datetime.min.time(), tzinfo=tz) + timedelta(hours=9)
        course_name, colour = _COURSES[session_id % len(_COURSES)]
        history.append(
            {
                "id": session_id,
                "courseId": session_id % len(_COURSES) + 1,
                "courseName": course_name,
                "courseColor": colour,
                "startTime": _naive(start),
                "endTime": _naive(start + timedelta(minutes=minutes)),
                "topic": "Kapitel 3",
                "isCompleted": True,
                "timerModeId": 1,
            }
        )
        session_id += 1

    metrics: dict[str, Any] = {
        "streak": {"current": 12, "longest": 23},
        "hours": {"week": 12.5, "month": 41.0, "total": 312.5, "totalSessions": 187},
        "weekQuota": {
            "hours": 12.5,
            "targetMin": 15.0,
            "targetMax": 20.0,
            "percent": 62.5,
            "warning": False,
        },
        "program": {"name": "B.Sc. Informatik"},
        "upcomingCourseGoals": [
            {
                "courseId": 1,
                "courseName": "Betriebssysteme",
                "targetDate": _naive(
                    datetime.combine(today + timedelta(days=9), datetime.min.time(), tzinfo=tz)
                ),
                "daysLeft": 9,
            },
            {
                "courseId": 2,
                "courseName": "Lineare Algebra",
                "targetDate": _naive(
                    datetime.combine(today + timedelta(days=23), datetime.min.time(), tzinfo=tz)
                ),
                "daysLeft": 23,
            },
        ],
        "weeklyReport": {"hours": 12.5, "topCourseName": "Betriebssysteme"},
        "ects": {"earned": 65, "total": 180},
        "averageGrade": 2.3,
        "forecast": {
            "available": True,
            "alreadyDone": False,
            "date": _naive(
                datetime.combine(today + timedelta(days=790), datetime.min.time(), tzinfo=tz)
            ),
            "recentWeeklyHours": 12.5,
        },
        "neglectedCourse": {
            "courseId": 3,
            "courseName": "Datenbanken",
            "lastStudied": _naive(
                datetime.combine(today - timedelta(days=12), datetime.min.time(), tzinfo=tz)
                + timedelta(hours=9)
            ),
            "daysSince": 12,
        },
        "topics": {"completed": 34, "total": 52},
        "asOf": _naive(now.astimezone(tz)),
    }

    timer: dict[str, Any] = {
        "sessionId": 999,
        "isRunning": True,
        "isBreak": False,
        "currentRound": 2,
        "timerModeId": 1,
        "phaseEndsAt": _naive(now.astimezone(tz) + timedelta(minutes=18)),
        "updatedAt": _naive(now.astimezone(tz) - timedelta(minutes=7)),
        "serverNow": _naive(now.astimezone(tz)),
    }
    return metrics, history, timer
