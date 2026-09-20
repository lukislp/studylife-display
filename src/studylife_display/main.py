"""Command line entry point: `studylife-display run|preview|check|serve`, plus the
`persist-export|persist-import|credentials-apply` commands the root-only systemd units
call."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from PIL import Image

from studylife_display import package_version
from studylife_display.config import Settings
from studylife_display.connect import local_hostname, setup_connect_url
from studylife_display.credentials import ENV_FILE, apply_pending_credentials
from studylife_display.current_frame import DASHBOARD, ERROR, SETUP, save_current_frame
from studylife_display.daily_clear import (
    clear_due,
    load_last_clear,
    parse_clear_at,
    save_last_clear,
)
from studylife_display.driver import Display, FileDisplay, WaveshareDisplay
from studylife_display.layouts.auto import resolve_layout, rules_from_settings
from studylife_display.layouts.error import format_age, render_error
from studylife_display.layouts.setup import render_setup
from studylife_display.model import DashboardData, build_dashboard
from studylife_display.panel_lock import PanelLockTimeout, panel_lock
from studylife_display.quiet_hours import in_quiet_hours, quiet_hours_end
from studylife_display.render import render
from studylife_display.sample import sample_payloads
from studylife_display.settings_store import (
    effective_settings,
    export_layout_choice,
    import_layout_choice,
    load_layout_choice,
    valid_choices,
)
from studylife_display.snapshot import Snapshot, fetch_snapshot, load_snapshot, save_snapshot
from studylife_display.status_store import (
    NO_DATA,
    REJECTED,
    STALE,
    TRANSIENT,
    LastError,
    update_status,
)
from studylife_display.studylife_client import StudyLifeApiError, StudyLifeClient
from studylife_display.times import zone
from studylife_display.web import MIN_TOKEN_LENGTH, serve_web

log = logging.getLogger("studylife_display")

# HTTP statuses that mean "the key is wrong or lacks a scope": not transient, never healed
# by waiting, so the panel says so instead of showing an ageing cached dashboard.
REJECTED_STATUSES = frozenset({401, 403})


def make_display(settings: Settings, output_override: str | None = None) -> Display:
    """The configured driver with the configured rotation; an explicit output path (the
    `preview` command) always means an upright PNG, whatever the panel's mounting."""
    if output_override is not None:
        return FileDisplay(output_override)
    if settings.display_driver == "file":
        return FileDisplay(settings.display_output_path, settings.display_rotate)
    return WaveshareDisplay(settings.display_rotate)


def build(snapshot: Snapshot, now: datetime, tz: ZoneInfo) -> DashboardData:
    return build_dashboard(
        snapshot.metrics,
        snapshot.history,
        snapshot.timer,
        now,
        tz,
        fetched_at=snapshot.fetched_at,
        sessions=snapshot.sessions,
    )


def present(display: Display, image: Image.Image, clear_first: bool = False) -> None:
    """Puts one frame on the panel (after a full clear when asked) and always sleeps it."""
    try:
        if clear_first:
            display.clear()
        display.show(image)
    finally:
        display.sleep()


def show(
    display: Display,
    data: DashboardData,
    language: str,
    layout: str = "classic",
    clear_first: bool = False,
) -> None:
    present(display, render(data, language, layout), clear_first)


def _client(settings: Settings) -> StudyLifeClient:
    return StudyLifeClient(
        str(settings.studylife_base_url),
        settings.studylife_api_key,
        timeout=settings.http_timeout_seconds,
    )


def _record(state_dir: Path, tz: ZoneInfo, **changes: object) -> None:
    """Updates status.json; a state directory that cannot be written is a warning, the
    panel still gets its frame."""
    try:
        update_status(state_dir, tz, **changes)
    except OSError as exc:
        log.warning("could not write the status file in %s: %s", state_dir, exc)


def _put_on_panel(
    settings: Settings,
    state_dir: Path,
    tz: ZoneInfo,
    now: datetime,
    image: Image.Image,
    output_override: str | None,
    clear_first: bool,
    *,
    kind: str,
    layout: str | None = None,
) -> bool:
    """Shows `image` under the panel lock and records the moment; False when another
    refresh held the lock for too long (nothing was drawn then). A frame that went to the
    panel (not to a `preview` PNG) is also kept as current.png/current.json in the state
    directory for the web interface, upright, tagged with `kind` and `layout`."""
    display = make_display(settings, output_override)
    try:
        with panel_lock(state_dir):
            present(display, image, clear_first)
    except PanelLockTimeout as exc:
        log.error("%s - another refresh is stuck, giving up", exc)
        return False
    if clear_first:
        try:
            save_last_clear(state_dir, now)
        except OSError as exc:
            log.warning("could not record the clear in %s: %s", state_dir, exc)
    if output_override is None:
        try:
            save_current_frame(state_dir, image, now, layout, kind)
        except OSError as exc:
            log.warning("could not keep a copy of the frame in %s: %s", state_dir, exc)
    _record(state_dir, tz, last_panel_update_at=now)
    return True


def _show_error(
    settings: Settings,
    state_dir: Path,
    tz: ZoneInfo,
    now: datetime,
    kind: str,
    detail: str,
    last_error: str,
    output_override: str | None,
    clear_first: bool,
) -> bool:
    image = render_error(kind, detail, settings.display_language, now, last_error)
    return _put_on_panel(
        settings, state_dir, tz, now, image, output_override, clear_first, kind=ERROR
    )


def _show_setup(
    settings: Settings,
    state_dir: Path,
    tz: ZoneInfo,
    now: datetime,
    output_override: str | None,
    clear_first: bool,
) -> bool:
    url = setup_connect_url(settings)
    log.info("no API key configured yet - showing the setup screen (%s)", url)
    image = render_setup(url, settings.display_language, local_hostname(), now)
    return _put_on_panel(
        settings, state_dir, tz, now, image, output_override, clear_first, kind=SETUP
    )


def refresh_panel(
    settings: Settings,
    layout_choice: str | None = None,
    output_override: str | None = None,
    clear_first: bool = False,
) -> int:
    """Fetch -> build -> resolve layout -> render -> show, under the panel lock.

    With no API key configured at all (a fresh install), nothing is fetched: the setup screen
    with the connect URL and its QR code goes on the panel and the exit code is 0 - a panel
    that is not set up yet is not a failure. A key that IS configured but rejected is one.

    What goes on the panel when the fetch fails: a 401/403 means the key is rejected and the
    "rejected" screen is shown right away (exit 1) - a cached dashboard would only hide the
    problem. Any other failure falls back to the cached snapshot with the stale marker
    (exit 0) until the cache is older than DISPLAY_STALE_ERROR_HOURS, from when on the
    "stale" screen is shown instead (still exit 0, the outage may end). With no cache at all
    the "no data" screen is shown and the exit code is 1. A lock timeout draws nothing and
    exits 1. The outcome is recorded in status.json for the web interface and /healthz.
    `layout_choice` defaults to the persisted choice (settings.json, else DISPLAY_LAYOUT);
    `clear_first` does the daily full clear before the frame."""
    tz = zone(settings.studylife_timezone)
    now = datetime.now(tz)
    state_path = Path(settings.display_state_path)
    state_dir = state_path.parent
    language = settings.display_language

    if not settings.studylife_api_key:
        shown = _show_setup(settings, state_dir, tz, now, output_override, clear_first)
        return 0 if shown else 1

    snapshot: Snapshot | None
    # The cache is read up front: its session-list ETag lets the fetch ask with
    # If-None-Match, and it is the fallback when the fetch fails.
    cached = load_snapshot(state_path, tz)
    try:
        with _client(settings) as client:
            snapshot = fetch_snapshot(client, now, cached)
    except (StudyLifeApiError, httpx.HTTPError) as exc:
        status = exc.status_code if isinstance(exc, StudyLifeApiError) else None
        message = str(exc)
        if status in REJECTED_STATUSES:
            log.error("StudyLife rejected the API key (%s) - showing the error screen", exc)
            _record(
                state_dir,
                tz,
                last_fetch_ok=False,
                last_error=LastError(REJECTED, status, message, now),
            )
            _show_error(
                settings,
                state_dir,
                tz,
                now,
                REJECTED,
                f"HTTP {status}",
                message,
                output_override,
                clear_first,
            )
            return 1
        log.warning("fetch failed (%s), falling back to the cached snapshot", exc)
        snapshot = cached
        if snapshot is None:
            log.error("no cached snapshot at %s - showing the error screen", state_path)
            _record(
                state_dir,
                tz,
                last_fetch_ok=False,
                last_error=LastError(NO_DATA, status, message, now),
            )
            _show_error(
                settings,
                state_dir,
                tz,
                now,
                NO_DATA,
                message,
                message,
                output_override,
                clear_first,
            )
            return 1
        age_minutes = int((now.timestamp() - snapshot.fetched_at.timestamp()) // 60)
        if age_minutes >= settings.display_stale_error_hours * 60:
            log.error(
                "cached snapshot is %d min old (limit %.0f h) - showing the stale screen",
                age_minutes,
                settings.display_stale_error_hours,
            )
            _record(
                state_dir,
                tz,
                last_fetch_ok=False,
                last_error=LastError(STALE, status, message, now),
            )
            shown = _show_error(
                settings,
                state_dir,
                tz,
                now,
                STALE,
                format_age(age_minutes, language),
                message,
                output_override,
                clear_first,
            )
            return 0 if shown else 1
        _record(
            state_dir,
            tz,
            last_fetch_ok=False,
            last_error=LastError(TRANSIENT, status, message, now),
        )
    else:
        try:
            save_snapshot(state_path, snapshot)
        except OSError as exc:
            log.warning("could not cache the snapshot at %s: %s", state_path, exc)
        _record(
            state_dir,
            tz,
            last_fetch_ok=True,
            last_fetch_at=now,
            last_error=None,
            sessions_ok=snapshot.sessions_error is None,
            sessions_error=snapshot.sessions_error,
        )

    data = build(snapshot, now, tz)
    choice = layout_choice if layout_choice is not None else load_layout_choice(settings)
    layout = resolve_layout(choice, data, rules_from_settings(settings))
    image = render(data, language, layout)
    if not _put_on_panel(
        settings,
        state_dir,
        tz,
        now,
        image,
        output_override,
        clear_first,
        kind=DASHBOARD,
        layout=layout,
    ):
        return 1
    log.info(
        "shown %s (%s): today %.2f h, streak %d, stale %d min",
        layout,
        choice,
        data.today_hours,
        data.streak_days,
        data.stale_minutes,
    )
    return 0


def command_run(settings: Settings, output_override: str | None = None) -> int:
    """What the systemd timer calls: one refresh with the persisted layout choice - unless
    quiet hours are on, in which case nothing happens (exit 0). The daily clear is the one
    exception: when it is due, the refresh runs even inside quiet hours, clearing first."""
    tz = zone(settings.studylife_timezone)
    now = datetime.now(tz)
    state_dir = Path(settings.display_state_path).parent
    clear_at = parse_clear_at(settings.display_clear_at)
    due = clear_due(now, load_last_clear(state_dir, tz), clear_at)
    if in_quiet_hours(now, settings.display_quiet_hours) and not due:
        end = quiet_hours_end(now, settings.display_quiet_hours)
        log.info(
            "quiet hours (%s) until %s - not refreshing",
            settings.display_quiet_hours,
            end.strftime("%H:%M") if end is not None else "?",
        )
        return 0
    if due:
        log.info("daily clear due (DISPLAY_CLEAR_AT=%s), clearing before the frame", clear_at)
    return refresh_panel(settings, output_override=output_override, clear_first=due)


def command_preview(
    settings: Settings, output: str, use_sample: bool, layout_choice: str | None
) -> int:
    """Renders to a PNG regardless of DISPLAY_DRIVER. With --sample no API is contacted and
    no lock is taken (a PNG somewhere else does not contend with the panel). Neither mode
    touches current.png: only frames that reach the panel count as shown."""
    if not use_sample:
        return refresh_panel(settings, layout_choice, output_override=output)
    tz = zone(settings.studylife_timezone)
    now = datetime.now(tz)
    metrics, history, timer, sessions = sample_payloads(now, tz)
    data = build_dashboard(metrics, history, timer, now, tz, sessions=sessions)
    choice = layout_choice if layout_choice is not None else load_layout_choice(settings)
    layout = resolve_layout(choice, data, rules_from_settings(settings))
    show(FileDisplay(output), data, settings.display_language, layout)
    log.info("rendered %s (%s) to %s", layout, choice, output)
    return 0


def command_check(settings: Settings) -> int:
    """Calls the four endpoints and prints what the dashboard would be built from."""
    tz = zone(settings.studylife_timezone)
    now = datetime.now(tz)
    with _client(settings) as client:
        snapshot = fetch_snapshot(client, now)
    data = build(snapshot, now, tz)
    choice = load_layout_choice(settings)
    report = {
        "version": package_version(),
        "fetched_at": data.fetched_at.isoformat(),
        "today_hours": round(data.today_hours, 2),
        "week_hours": data.week_hours,
        "streak_days": data.streak_days,
        "next_goal": None
        if data.next_goal is None
        else {
            "course_name": data.next_goal.course_name,
            "days_left": data.next_goal.days_left,
            "target_date": data.next_goal.target_date.isoformat()
            if data.next_goal.target_date
            else None,
        },
        "week_quota": {
            "hours": data.week_quota.hours,
            "target_min": data.week_quota.target_min,
            "target_max": data.week_quota.target_max,
            "percent": data.week_quota.percent,
        },
        "timer": None
        if data.timer is None
        else {
            "is_running": data.timer.is_running,
            "is_break": data.timer.is_break,
            "phase_ends_at": data.timer.phase_ends_at.isoformat()
            if data.timer.phase_ends_at
            else None,
            "current_round": data.timer.current_round,
        },
        "program_name": data.program_name,
        "ects": {"earned": data.ects.earned, "total": data.ects.total},
        "average_grade": data.average_grade,
        "forecast": {
            "available": data.forecast.available,
            "already_done": data.forecast.already_done,
            "date": data.forecast.date.isoformat() if data.forecast.date else None,
        },
        "neglected_course": None
        if data.neglected_course is None
        else {
            "course_name": data.neglected_course.course_name,
            "days_since": data.neglected_course.days_since,
        },
        "topics": {"completed": data.topics.completed, "total": data.topics.total},
        "history_sessions": len(snapshot.history),
        "sessions": len(snapshot.sessions),
        "sessions_ok": snapshot.sessions_error is None,
        "sessions_error": snapshot.sessions_error,
        "agenda": [
            {
                "start": item.start.isoformat(),
                "end": item.end.isoformat(),
                "course_name": item.course_name,
                "topic": item.topic,
                "is_completed": item.is_completed,
                "is_running_now": item.is_running_now,
            }
            for item in data.agenda
        ],
        "weekly_report": {
            "week_id": data.weekly_report.week_id,
            "hours": data.weekly_report.hours,
            "delta_vs_previous_week": data.weekly_report.delta_vs_previous_week,
            "top_course_name": data.weekly_report.top_course_name,
            "session_count": data.weekly_report.session_count,
        },
        "this_week": {
            "week_id": data.this_week.week_id,
            "hours": round(data.this_week.hours, 2),
            "delta_vs_previous_week": round(data.this_week.delta_vs_previous_week, 2),
            "top_course_name": data.this_week.top_course_name,
            "session_count": data.this_week.session_count,
        },
        "heatmap": [[round(h, 2) for h in row] for row in data.heatmap],
        "course_hours": [[name, round(hours, 2)] for name, hours in data.course_hours],
        "layout_choice": choice,
        "layout": resolve_layout(choice, data, rules_from_settings(settings)),
        "quiet_hours_active": in_quiet_hours(now, settings.display_quiet_hours),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def command_serve(settings: Settings) -> int:
    """The web interface. Refuses to bind without a proper access token: the person
    installing chooses it (deploy/install.sh suggests one), the code never defaults it.
    `settings` are the environment values; the pages and the refresh they trigger read
    settings.json on top of them at request time, so a change made on the settings page
    applies without a restart."""
    if len(settings.display_web_token) < MIN_TOKEN_LENGTH:
        log.error(
            "DISPLAY_WEB_TOKEN is %s; set one with at least %d characters in the environment "
            "file before starting the web interface",
            "empty" if not settings.display_web_token else "too short",
            MIN_TOKEN_LENGTH,
        )
        return 2
    return serve_web(settings, lambda: refresh_panel(effective_settings(settings)))


def command_credentials_apply(settings: Settings, env_file: str) -> int:
    """What the root-only credentials unit calls: move the key the web interface obtained
    into the environment file (see `credentials.py`)."""
    return apply_pending_credentials(settings, Path(env_file))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="studylife-display",
        description="Render the StudyLife dashboard onto the Waveshare 7.5 inch e-Paper HAT.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument("--version", action="version", version=f"%(prog)s {package_version()}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="fetch, render and show once (what the systemd timer calls)")

    preview = sub.add_parser("preview", help="render to a PNG instead of the panel")
    preview.add_argument("--out", default="frame.png", help="output path (default: frame.png)")
    preview.add_argument(
        "--sample", action="store_true", help="use built-in sample data, do not call the API"
    )
    preview.add_argument(
        "--layout",
        choices=sorted(valid_choices()),
        help="layout to render (default: the persisted choice, else DISPLAY_LAYOUT)",
    )

    sub.add_parser("check", help="call the API and print what it returned; no display")
    sub.add_parser("serve", help="the web interface for switching layouts (needs a token)")
    # Run by the root-only systemd units, never by the web service: mirror the layout choice
    # to the boot partition and back so it survives a reboot with the overlay filesystem on.
    sub.add_parser(
        "persist-export",
        help="copy settings.json to DISPLAY_PERSIST_PATH (the boot partition); systemd path unit",
    )
    sub.add_parser(
        "persist-import",
        help="restore settings.json from DISPLAY_PERSIST_PATH; run once at boot",
    )
    apply = sub.add_parser(
        "credentials-apply",
        help="move credentials.pending.json into the environment file; systemd path unit (root)",
    )
    apply.add_argument(
        "--env-file", default=ENV_FILE, help=f"environment file to rewrite (default: {ENV_FILE})"
    )
    return parser


def _settings(use_sample: bool) -> Settings:
    if use_sample:
        # Sample previews need no instance at all; supply placeholders for the two required
        # settings so a fresh checkout can render docs/preview.png without a .env.
        return Settings(studylife_base_url="http://sample.invalid", studylife_api_key="sample")  # type: ignore[arg-type]
    return Settings()  # type: ignore[call-arg]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    use_sample = args.command == "preview" and args.sample
    settings = _settings(use_sample)
    if args.command == "persist-export":
        return export_layout_choice(settings)
    if args.command == "persist-import":
        return import_layout_choice(settings)
    if args.command == "credentials-apply":
        return command_credentials_apply(settings, args.env_file)
    if args.command == "serve":
        return command_serve(settings)
    # Everything that renders reads the web interface's choices on top of the environment.
    settings = effective_settings(settings)
    if args.command == "run":
        return command_run(settings)
    if args.command == "preview":
        return command_preview(settings, args.out, use_sample, args.layout)
    return command_check(settings)


if __name__ == "__main__":
    sys.exit(main())
