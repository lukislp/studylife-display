"""The minimal night frame.

The header as always (it carries the stale marker), then mostly white: the weekday and the
date large and centred, the streak under it, and tomorrow's first planned session ("Morgen
08:00 Betriebssysteme") or "nothing planned tomorrow". No footer line, no bars - a frame that
can sit on the panel through the night without drawing the eye. When the session list could
not be fetched (a key without the `Sessions.GetAll` scope), the tomorrow line is simply
left out.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from studylife_display.layouts.common import (
    MARGIN,
    WIDTH,
    Fonts,
    draw_header,
    draw_text,
    ellipsize,
    finish,
    format_streak,
    load_fonts,
    new_canvas,
)
from studylife_display.layouts.common import TEXT as COMMON_TEXT
from studylife_display.model import DashboardData

TEXT: dict[str, dict[str, str]] = {
    "de": {
        "tomorrow_first": "Morgen {time} {course}",
        "tomorrow_none": "morgen nichts geplant",
    },
    "en": {
        "tomorrow_first": "Tomorrow {time} {course}",
        "tomorrow_none": "nothing planned tomorrow",
    },
}

DATE_BASELINE = 212
STREAK_BASELINE = 290
TOMORROW_BASELINE = 352
# Where the tomorrow line stands: white when the session list is unavailable.
TOMORROW_BOX = (MARGIN, TOMORROW_BASELINE - 26, WIDTH - MARGIN, TOMORROW_BASELINE + 8)


def date_line(data: DashboardData, c: dict[str, str]) -> str:
    """ "Donnerstag 17.09." / "Thursday 17 Sep"."""
    weekday = c["weekdays"].split(",")[data.now.weekday()]
    return f"{weekday} {data.now.strftime(c['date_format'])}"


def tomorrow_line(data: DashboardData, t: dict[str, str], c: dict[str, str]) -> str | None:
    """Tomorrow's first session, the placeholder, or None when the list is unavailable."""
    if "sessions" in data.unavailable:
        return None
    if not data.tomorrow:
        return t["tomorrow_none"]
    first = data.tomorrow[0]
    time = first.start.astimezone(data.now.tzinfo).strftime("%H:%M")
    return t["tomorrow_first"].format(time=time, course=first.course_name or c["course_unknown"])


def render(data: DashboardData, language: str) -> Image.Image:
    t = TEXT[language]
    c = COMMON_TEXT[language]
    fonts = load_fonts()
    canvas, draw = new_canvas()
    draw_header(draw, data, fonts, c)

    centre = WIDTH / 2
    max_width = WIDTH - 2 * MARGIN
    draw_text(draw, (centre, DATE_BASELINE), date_line(data, c), fonts.title, anchor="ms")
    streak = f"{c['streak_label']} {format_streak(data.streak_days, c)}"
    draw_text(draw, (centre, STREAK_BASELINE), streak, fonts.value, anchor="ms")
    tomorrow = tomorrow_line(data, t, c)
    if tomorrow is not None:
        tomorrow = ellipsize(tomorrow, fonts.body, max_width)
        draw_text(draw, (centre, TOMORROW_BASELINE), tomorrow, fonts.body, anchor="ms")
    return finish(canvas)


def render_pane(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    data: DashboardData,
    fonts: Fonts,
    language: str,
    box: tuple[int, int, int, int],
) -> None:
    """Weekday and date on two lines, the streak and the tomorrow line, centred in the box."""
    t = TEXT[language]
    c = COMMON_TEXT[language]
    left, top, right, bottom = box
    centre = (left + right) / 2
    middle = (top + bottom) / 2
    max_width = right - left

    weekday = c["weekdays"].split(",")[data.now.weekday()]
    draw_text(draw, (centre, middle - 70), weekday, fonts.value, anchor="ms")
    date = data.now.strftime(c["date_format"])
    draw_text(draw, (centre, middle - 6), date, fonts.title, anchor="ms")
    streak = f"{c['streak_label']} {format_streak(data.streak_days, c)}"
    draw_text(draw, (centre, middle + 48), streak, fonts.body, anchor="ms")
    tomorrow = tomorrow_line(data, t, c)
    if tomorrow is not None:
        tomorrow = ellipsize(tomorrow, fonts.body, max_width)
        draw_text(draw, (centre, middle + 92), tomorrow, fonts.body, anchor="ms")
