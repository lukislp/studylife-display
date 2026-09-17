"""Command line entry point: `studylife-display run|preview|check|serve`, plus the
`persist-export|persist-import` pair the root-only systemd units call."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from studylife_display.config import Settings
from studylife_display.driver import Display, FileDisplay, WaveshareDisplay
from studylife_display.layouts.auto import resolve_layout
from studylife_display.model import DashboardData, build_dashboard
from studylife_display.panel_lock import PanelLockTimeout, panel_lock
from studylife_display.render import render
from studylife_display.sample import sample_payloads
from studylife_display.settings_store import (
    export_layout_choice,
    import_layout_choice,
    load_layout_choice,
    valid_choices,
)
from studylife_display.snapshot import Snapshot, fetch_snapshot, load_snapshot, save_snapshot
from studylife_display.studylife_client import StudyLifeApiError, StudyLifeClient
from studylife_display.times import zone
from studylife_display.web import MIN_TOKEN_LENGTH, serve_web

log = logging.getLogger("studylife_display")


def make_display(settings: Settings, output_override: str | None = None) -> Display:
    if output_override is not None or settings.display_driver == "file":
        return FileDisplay(output_override or settings.display_output_path)
    return WaveshareDisplay()


def build(snapshot: Snapshot, now: datetime, tz: ZoneInfo) -> DashboardData:
    return build_dashboard(
        snapshot.metrics,
        snapshot.history,
        snapshot.timer,
        now,
        tz,
        fetched_at=snapshot.fetched_at,
    )


def show(display: Display, data: DashboardData, language: str, layout: str = "classic") -> None:
    try:
        display.show(render(data, language, layout))
    finally:
        display.sleep()


def _client(settings: Settings) -> StudyLifeClient:
    return StudyLifeClient(
        str(settings.studylife_base_url),
        settings.studylife_api_key,
        timeout=settings.http_timeout_seconds,
    )


def refresh_panel(
    settings: Settings,
    layout_choice: str | None = None,
    output_override: str | None = None,
) -> int:
    """Fetch -> build -> resolve layout -> render -> show, under the panel lock. A fetch
    failure falls back to the cached snapshot (rendered with the stale marker) and still
    returns 0; only "no data at all" and a lock timeout are errors. `layout_choice` defaults
    to the persisted choice (settings.json, else DISPLAY_LAYOUT)."""
    tz = zone(settings.studylife_timezone)
    now = datetime.now(tz)
    state_path = Path(settings.display_state_path)

    snapshot: Snapshot | None
    try:
        with _client(settings) as client:
            snapshot = fetch_snapshot(client, now)
    except (StudyLifeApiError, httpx.HTTPError) as exc:
        log.warning("fetch failed (%s), falling back to the cached snapshot", exc)
        snapshot = load_snapshot(state_path, tz)
        if snapshot is None:
            log.error("no cached snapshot at %s - nothing to show", state_path)
            return 1
    else:
        try:
            save_snapshot(state_path, snapshot)
        except OSError as exc:
            log.warning("could not cache the snapshot at %s: %s", state_path, exc)

    data = build(snapshot, now, tz)
    choice = layout_choice if layout_choice is not None else load_layout_choice(settings)
    layout = resolve_layout(choice, data)
    display = make_display(settings, output_override)
    try:
        with panel_lock(state_path.parent):
            show(display, data, settings.display_language, layout)
    except PanelLockTimeout as exc:
        log.error("%s - another refresh is stuck, giving up", exc)
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
    """What the systemd timer calls: one refresh with the persisted layout choice."""
    return refresh_panel(settings, output_override=output_override)


def command_preview(
    settings: Settings, output: str, use_sample: bool, layout_choice: str | None
) -> int:
    """Renders to a PNG regardless of DISPLAY_DRIVER. With --sample no API is contacted and
    no lock is taken (a PNG somewhere else does not contend with the panel)."""
    if not use_sample:
        return refresh_panel(settings, layout_choice, output_override=output)
    tz = zone(settings.studylife_timezone)
    now = datetime.now(tz)
    metrics, history, timer = sample_payloads(now, tz)
    data = build_dashboard(metrics, history, timer, now, tz)
    choice = layout_choice if layout_choice is not None else load_layout_choice(settings)
    layout = resolve_layout(choice, data)
    show(FileDisplay(output), data, settings.display_language, layout)
    log.info("rendered %s (%s) to %s", layout, choice, output)
    return 0


def command_check(settings: Settings) -> int:
    """Calls the three endpoints and prints what the dashboard would be built from."""
    tz = zone(settings.studylife_timezone)
    now = datetime.now(tz)
    with _client(settings) as client:
        snapshot = fetch_snapshot(client, now)
    data = build(snapshot, now, tz)
    choice = load_layout_choice(settings)
    report = {
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
        "history_sessions": len(snapshot.history),
        "heatmap": [[round(h, 2) for h in row] for row in data.heatmap],
        "course_hours": [[name, round(hours, 2)] for name, hours in data.course_hours],
        "layout_choice": choice,
        "layout": resolve_layout(choice, data),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def command_serve(settings: Settings) -> int:
    """The web interface. Refuses to bind without a proper access token: the person
    installing chooses it (deploy/install.sh suggests one), the code never defaults it."""
    if len(settings.display_web_token) < MIN_TOKEN_LENGTH:
        log.error(
            "DISPLAY_WEB_TOKEN is %s; set one with at least %d characters in the environment "
            "file before starting the web interface",
            "empty" if not settings.display_web_token else "too short",
            MIN_TOKEN_LENGTH,
        )
        return 2
    return serve_web(settings, lambda: refresh_panel(settings))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="studylife-display",
        description="Render the StudyLife dashboard onto the Waveshare 7.5 inch e-Paper HAT.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
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
    if args.command == "run":
        return command_run(settings)
    if args.command == "preview":
        return command_preview(settings, args.out, use_sample, args.layout)
    if args.command == "serve":
        return command_serve(settings)
    if args.command == "persist-export":
        return export_layout_choice(settings)
    if args.command == "persist-import":
        return import_layout_choice(settings)
    return command_check(settings)


if __name__ == "__main__":
    sys.exit(main())
