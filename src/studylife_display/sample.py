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
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """(metrics, history, timer, sessions) as the four endpoints would return them."""
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
        # The server's report is always the last COMPLETED Monday-to-Sunday week.
        "weeklyReport": {
            "weekId": (today - timedelta(days=7)).strftime("%G-W%V"),
            "hours": 12.5,
            "deltaVsPreviousWeek": 2.5,
            "topCourseName": "Betriebssysteme",
            "sessionCount": 7,
        },
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

    # Today's plan for the agenda layout: (start, end, course index, topic, completed). The
    # first is at a fixed hour of the day; the others hang off `now` so that one is always
    # running and two still ahead, whatever time the preview is rendered.
    local_now = now.astimezone(tz)
    morning = datetime.combine(today, datetime.min.time(), tzinfo=tz) + timedelta(hours=8)
    plan = [
        (morning, morning + timedelta(minutes=90), 1, "Kapitel 3", True),
        (
            local_now - timedelta(minutes=45),
            local_now + timedelta(minutes=45),
            0,
            "Scheduling",
            False,
        ),
        (
            local_now + timedelta(hours=1, minutes=15),
            local_now + timedelta(hours=2, minutes=15),
            2,
            "SQL-Joins",
            False,
        ),
        (
            local_now + timedelta(hours=3, minutes=15),
            local_now + timedelta(hours=4, minutes=15),
            1,
            "Übungsblatt 5",
            False,
        ),
    ]
    sessions: list[dict[str, Any]] = []
    for index, (start, end, course, topic, completed) in enumerate(plan, start=500):
        course_name, colour = _COURSES[course]
        sessions.append(
            {
                "id": index,
                "courseId": course + 1,
                "courseName": course_name,
                "courseColor": colour,
                "startTime": _naive(start),
                "endTime": _naive(end),
                "topic": topic,
                "notes": None,
                "isCompleted": completed,
                "timerModeId": 1,
                "recurrenceGroupId": None,
            }
        )
    return metrics, history, timer, sessions
