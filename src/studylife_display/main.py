"""Command line entry point: `studylife-display run|preview|check`."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from studylife_display.config import Settings
from studylife_display.driver import Display, FileDisplay, WaveshareDisplay
from studylife_display.model import DashboardData, build_dashboard
from studylife_display.render import render
from studylife_display.sample import sample_payloads
from studylife_display.studylife_client import StudyLifeApiError, StudyLifeClient
from studylife_display.times import zone

log = logging.getLogger("studylife_display")

HISTORY_DAYS = 28


@dataclass(frozen=True)
class Snapshot:
    """The three raw payloads plus when they were obtained - exactly what gets cached."""

    metrics: dict[str, Any]
    history: list[dict[str, Any]]
    timer: dict[str, Any]
    fetched_at: datetime


def fetch_snapshot(client: StudyLifeClient, now: datetime) -> Snapshot:
    return Snapshot(
        metrics=client.get_metrics_summary(),
        history=client.get_session_history(days=HISTORY_DAYS),
        timer=client.get_timer_state(),
        fetched_at=now,
    )


def save_snapshot(path: Path, snapshot: Snapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": snapshot.fetched_at.isoformat(),
        "metrics": snapshot.metrics,
        "history": snapshot.history,
        "timer": snapshot.timer,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


def load_snapshot(path: Path, tz: ZoneInfo) -> Snapshot | None:
    """The cached snapshot, or None when there is none or it cannot be read."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        fetched_at = datetime.fromisoformat(raw["fetched_at"])
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=tz)
        return Snapshot(
            metrics=dict(raw["metrics"]),
            history=list(raw["history"]),
            timer=dict(raw["timer"]),
            fetched_at=fetched_at.astimezone(tz),
        )
    except (KeyError, TypeError, ValueError):
        return None


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


def show(display: Display, data: DashboardData, language: str) -> None:
    try:
        display.show(render(data, language))
    finally:
        display.sleep()


def _client(settings: Settings) -> StudyLifeClient:
    return StudyLifeClient(
        str(settings.studylife_base_url),
        settings.studylife_api_key,
        timeout=settings.http_timeout_seconds,
    )


def command_run(settings: Settings, output_override: str | None = None) -> int:
    """Fetch -> build -> render -> show. A fetch failure falls back to the cached snapshot
    (rendered with the stale marker) and still exits 0; only "no data at all" is an error."""
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
    show(make_display(settings, output_override), data, settings.display_language)
    log.info(
        "shown: today %.2f h, streak %d, stale %d min",
        data.today_hours,
        data.streak_days,
        data.stale_minutes,
    )
    return 0


def command_preview(settings: Settings, output: str, use_sample: bool) -> int:
    """Renders to a PNG regardless of DISPLAY_DRIVER. With --sample no API is contacted."""
    if not use_sample:
        return command_run(settings, output_override=output)
    tz = zone(settings.studylife_timezone)
    now = datetime.now(tz)
    metrics, history, timer = sample_payloads(now, tz)
    data = build_dashboard(metrics, history, timer, now, tz)
    show(FileDisplay(output), data, settings.display_language)
    return 0


def command_check(settings: Settings) -> int:
    """Calls the three endpoints and prints what the dashboard would be built from."""
    tz = zone(settings.studylife_timezone)
    now = datetime.now(tz)
    with _client(settings) as client:
        snapshot = fetch_snapshot(client, now)
    data = build(snapshot, now, tz)
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
        },
        "program_name": data.program_name,
        "history_sessions": len(snapshot.history),
        "heatmap": [[round(h, 2) for h in row] for row in data.heatmap],
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


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

    sub.add_parser("check", help="call the API and print what it returned; no display")
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
        return command_preview(settings, args.out, use_sample)
    return command_check(settings)


if __name__ == "__main__":
    sys.exit(main())
