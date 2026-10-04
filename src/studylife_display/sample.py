"""Sample payloads in the exact wire format, for `preview --sample`, the goldens and tests.

Timestamps are generated relative to `now` so that a preview always shows a plausible
"today"; the tests pass a fixed `now` to keep the goldens reproducible. `sample_payloads`
returns the four dashboard payloads, `sample_extras` the three optional ones (course goals,
achievements, notes).
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

# The year before those 28 days: a repeating fortnight with a quiet stretch (a semester
# break) so the year heatmap has texture; minutes per day, oldest first within the pattern.
_OLDER_PATTERN = [0, 60, 90, 0, 120, 45, 0, 150, 0, 75, 180, 0, 30, 0]
# Days 29..371 back are covered; this many of the oldest ones stay empty (the break).
_HISTORY_DAYS = 371
_BREAK_DAYS = 42

_COURSES = [
    ("Betriebssysteme", "#1D4ED8"),
    ("Lineare Algebra", "#047857"),
    ("Datenbanken", "#B45309"),
]


def _naive(moment: datetime) -> str:
    return moment.replace(tzinfo=None).isoformat(timespec="seconds")


def _minutes_per_day() -> list[int]:
    """Minutes per day for the whole history window, oldest first (last = today)."""
    older_count = _HISTORY_DAYS - len(_SESSION_MINUTES)
    older = [
        0 if index < _BREAK_DAYS else _OLDER_PATTERN[index % len(_OLDER_PATTERN)]
        for index in range(older_count)
    ]
    return older + _SESSION_MINUTES


def sample_payloads(
    now: datetime, tz: ZoneInfo
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """(metrics, history, timer, sessions) as the four endpoints would return them."""
    today = local_day(now, tz)
    history: list[dict[str, Any]] = []
    session_id = 1
    for offset, minutes in enumerate(reversed(_minutes_per_day())):
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
            "minPercent": 83.3,
            "warning": False,
            "missingHours": 2.5,
        },
        "monthQuota": {
            "hours": 41.0,
            "targetMin": 60.0,
            "targetMax": 80.0,
            "percent": 51.3,
            "minPercent": 68.3,
            "warning": False,
            "missingHours": 19.0,
        },
        "program": {"id": None, "name": "B.Sc. Informatik", "isBuiltIn": True},
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
            {
                "courseId": 3,
                "courseName": "Datenbanken",
                "targetDate": _naive(
                    datetime.combine(today + timedelta(days=41), datetime.min.time(), tzinfo=tz)
                ),
                "daysLeft": 41,
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
        "monthComparison": {
            "currentMonthHours": 41.0,
            "previousMonthHours": 52.5,
            "deltaVsPreviousMonth": -11.5,
            "hasYearData": True,
            "sameMonthLastYearHours": 36.0,
            "deltaVsLastYear": 5.0,
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
        "courseHours": [
            {
                "courseId": 1,
                "courseName": "Betriebssysteme",
                "courseColor": "#1D4ED8",
                "hours": 128.5,
                "sessionCount": 74,
            },
            {
                "courseId": 2,
                "courseName": "Lineare Algebra",
                "courseColor": "#047857",
                "hours": 112.0,
                "sessionCount": 68,
            },
            {
                "courseId": 3,
                "courseName": "Datenbanken",
                "courseColor": "#B45309",
                "hours": 48.0,
                "sessionCount": 31,
            },
            {
                "courseId": 4,
                "courseName": "Theoretische Informatik",
                "courseColor": "#7C3AED",
                "hours": 24.0,
                "sessionCount": 14,
            },
        ],
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
    # running and two still ahead, whatever time the preview is rendered. Then tomorrow's
    # plan for the tomorrow layout, at fixed hours.
    local_now = now.astimezone(tz)
    morning = datetime.combine(today, datetime.min.time(), tzinfo=tz) + timedelta(hours=8)
    tomorrow = morning + timedelta(days=1)
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
        (
            tomorrow + timedelta(hours=1),
            tomorrow + timedelta(hours=2, minutes=30),
            0,
            "Deadlocks",
            False,
        ),
        (tomorrow + timedelta(hours=5), tomorrow + timedelta(hours=6), 1, "Eigenwerte", False),
        (tomorrow + timedelta(hours=10), tomorrow + timedelta(hours=11), 2, "Normalformen", False),
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


def sample_extras(
    now: datetime, tz: ZoneInfo
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """(goals, achievements, notes) as GET /api/coursegoals, GET /api/metrics/achievements
    and GET /api/notes would return them."""
    today = local_day(now, tz)

    def on(day_offset: int, hour: int = 0) -> str:
        return _naive(
            datetime.combine(today + timedelta(days=day_offset), datetime.min.time(), tzinfo=tz)
            + timedelta(hours=hour)
        )

    goals: list[dict[str, Any]] = [
        {
            "courseId": 1,
            "courseName": "Betriebssysteme",
            "targetDate": on(9),
            "completionNote": None,
            "completedAt": None,
            "grade": None,
            "completedTopics": "Prozesse,Threads",
            "tag": "Klausur",
        },
        {
            "courseId": 2,
            "courseName": "Lineare Algebra",
            "targetDate": on(23),
            "completionNote": None,
            "completedAt": None,
            "grade": None,
            "completedTopics": "",
            "tag": None,
        },
        {
            "courseId": 3,
            "courseName": "Datenbanken",
            "targetDate": on(41),
            "completionNote": None,
            "completedAt": None,
            "grade": None,
            "completedTopics": "",
            "tag": "Projekt",
        },
        {
            "courseId": 5,
            "courseName": "Programmieren 1",
            "targetDate": on(-60),
            "completionNote": "Bestanden",
            "completedAt": on(-58, 11),
            "grade": 1.7,
            "completedTopics": "",
            "tag": None,
        },
        {
            "courseId": 6,
            "courseName": "Mathematik 1",
            "targetDate": on(-120),
            "completionNote": None,
            "completedAt": on(-118, 10),
            "grade": 2.3,
            "completedTopics": "",
            "tag": None,
        },
    ]

    def tiers(category: str, thresholds: list[float], current: float) -> list[dict[str, Any]]:
        return [
            {
                "category": category,
                "threshold": threshold,
                "unlocked": current >= threshold,
                "current": current,
            }
            for threshold in thresholds
        ]

    all_tiers = (
        tiers("hours", [10, 50, 100, 250, 500, 1000], 312.5)
        + tiers("streak", [3, 7, 14, 30, 60, 100], 12)
        + tiers("sessions", [10, 50, 100, 250, 500], 187)
        + tiers("courses", [1, 3, 5, 10], 2)
        + tiers("earlybird", [5, 20, 50], 14)
        + tiers("nightowl", [5, 20, 50], 3)
        + tiers("weekend", [5, 20, 50], 22)
        + tiers("marathon", [1, 5, 10], 4)
        + tiers("perfectweek", [1, 4, 12], 2)
        + tiers("notes", [5, 25, 100], 31)
        + tiers("coursediversity", [2, 4, 6], 3)
        + tiers("programs", [1, 2], 1)
    )
    achievements: dict[str, Any] = {
        "unlocked": sum(1 for tier in all_tiers if tier["unlocked"]),
        "total": len(all_tiers),
        "tiers": all_tiers,
    }

    notes: list[dict[str, Any]] = [
        {
            "id": 31,
            "title": "Scheduling-Strategien",
            "content": (
                "# Scheduling\n\n- **FCFS**: einfach, aber Konvoi-Effekt\n- **SJF**: optimal "
                "für die mittlere Wartezeit, braucht Laufzeitschätzung\n- **Round Robin**: "
                "Zeitscheibe q; zu klein = Overhead, zu groß = FCFS\n- Mehrstufige "
                "Warteschlangen mit Feedback kombinieren beides.\n"
            ),
            "createdAt": on(-1, 18),
            "updatedAt": on(0, 10),
            "courseId": 1,
            "sessionId": None,
            "isMarkdown": True,
            "sourceUrl": None,
            "tags": "betriebssysteme,scheduling",
            "summary": None,
            "relatedNoteIds": [],
        },
        {
            "id": 30,
            "title": "Eigenwerte wiederholen",
            "content": (
                "Charakteristisches Polynom det(A - λI) = 0; Eigenräume; Diagonalisierbarkeit."
            ),
            "createdAt": on(-3, 9),
            "updatedAt": on(-2, 20),
            "courseId": 2,
            "sessionId": None,
            "isMarkdown": False,
            "sourceUrl": None,
            "tags": None,
            "summary": (
                "Eigenwerte über das charakteristische Polynom, Eigenräume und das Kriterium "
                "für Diagonalisierbarkeit."
            ),
            "relatedNoteIds": [],
        },
        {
            "id": 29,
            "title": "Normalformen",
            "content": "1NF, 2NF, 3NF, BCNF - funktionale Abhängigkeiten und Zerlegung.",
            "createdAt": on(-6, 9),
            "updatedAt": on(-6, 9),
            "courseId": 3,
            "sessionId": None,
            "isMarkdown": False,
            "sourceUrl": None,
            "tags": None,
            "summary": None,
            "relatedNoteIds": [],
        },
    ]
    return goals, achievements, notes
