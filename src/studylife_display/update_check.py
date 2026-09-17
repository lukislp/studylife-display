"""Whether a newer release exists on GitHub - only when DISPLAY_UPDATE_CHECK=true.

The web interface's footer asks for this while rendering the layouts page. GitHub's
releases API is called at most once per CHECK_INTERVAL (the answer, or the failure, is
cached in `update_check.json` in the state directory), never from the scheduled refresh,
and a failure is silent: the footer then simply shows the running version alone. Nothing
else on the Pi talks to anything but the StudyLife instance.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger(__name__)

REPOSITORY = "lukislp/studylife-display"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
CHECK_INTERVAL = timedelta(hours=6)
CACHE_FILE = "update_check.json"
TIMEOUT_SECONDS = 5.0

Fetcher = Callable[[], str | None]

_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True)
class UpdateCheck:
    checked_at: datetime
    latest: str | None  # the tag, e.g. "v1.3.0"; None when the check failed


def parse_version(text: str) -> tuple[int, int, int] | None:
    """ "v1.2.3" or "1.2.3.dev4+g..." -> (1, 2, 3); None for anything unparseable."""
    match = _VERSION.match(text.strip())
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def is_newer(latest: str, running: str) -> bool:
    """Whether `latest` (a release tag) is a higher release than the `running` version. A
    development build between tags counts as its next release, so it is never "behind"."""
    latest_parsed, running_parsed = parse_version(latest), parse_version(running)
    if latest_parsed is None or running_parsed is None:
        return False
    return latest_parsed > running_parsed


def fetch_latest_tag() -> str | None:
    """The tag of the latest GitHub release, or None on any failure (silently)."""
    try:
        response = httpx.get(
            LATEST_RELEASE_URL,
            headers={"Accept": "application/vnd.github+json"},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        tag = response.json().get("tag_name")
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        log.debug("update check failed: %s", exc)
        return None
    return tag if isinstance(tag, str) and tag else None


def cache_path(state_dir: Path) -> Path:
    return state_dir / CACHE_FILE


def load_check(state_dir: Path, tz: ZoneInfo) -> UpdateCheck | None:
    try:
        raw = json.loads(cache_path(state_dir).read_text(encoding="utf-8"))
        checked_at = datetime.fromisoformat(raw["checked_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=tz)
    latest = raw.get("latest")
    return UpdateCheck(checked_at.astimezone(tz), latest if isinstance(latest, str) else None)


def save_check(state_dir: Path, check: UpdateCheck) -> None:
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        path = cache_path(state_dir)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps({"checked_at": check.checked_at.isoformat(), "latest": check.latest}),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError as exc:
        log.debug("could not cache the update check: %s", exc)


def latest_release(
    state_dir: Path, now: datetime, tz: ZoneInfo, fetch: Fetcher = fetch_latest_tag
) -> str | None:
    """The latest release tag, from the cache when it is younger than CHECK_INTERVAL and
    from GitHub otherwise; None when unknown. A failed check is cached too, so an offline
    Pi asks once per interval, not once per page view."""
    cached = load_check(state_dir, tz)
    if cached is not None and now - cached.checked_at < CHECK_INTERVAL:
        return cached.latest
    latest = fetch()
    save_check(state_dir, UpdateCheck(now, latest))
    return latest
