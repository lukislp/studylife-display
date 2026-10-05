"""`credentials-apply`: moves the key the web interface obtained into the environment
file. Run by the root-only `studylife-display-credentials.service`, which
`studylife-display-credentials.path` starts as soon as `credentials.pending.json` exists in
the state directory.

The web service is unprivileged and must never be able to write `/etc`, so the hand-over
goes through that file: the unit validates it, rewrites exactly the `STUDYLIFE_API_KEY=`
line of `/etc/studylife-display.env` (added when missing, every other byte untouched),
replaces the file atomically with its mode and owner preserved, deletes the pending file,
restarts the web service (so it runs with the new key) and starts one refresh.

The pending file is removed whatever happens once it has been looked at: a path unit with
`PathExists=` would otherwise start this service again and again for a file it cannot use.
"""

from __future__ import annotations

import logging
import os
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path

from studylife_display.config import Settings
from studylife_display.connect import pending_path, read_pending

log = logging.getLogger(__name__)

ENV_FILE = "/etc/studylife-display.env"
ENV_KEY = "STUDYLIFE_API_KEY"
ENV_KEY_INSTANCE = "STUDYLIFE_API_KEY_INSTANCE"
WEB_UNIT = "studylife-display-web.service"
REFRESH_UNIT = "studylife-display.service"

Systemctl = Callable[[list[str]], None]


def rewrite_env_line(text: str, key: str, value: str) -> str:
    """`text` with the first `KEY=...` line (at column 0, as install.sh writes it) replaced
    by `KEY=value`, its line ending kept; appended as a new line when there is none. Every
    other line is returned byte for byte."""
    lines = text.splitlines(keepends=True)
    prefix = key + "="
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            body = line.rstrip("\r\n")
            lines[index] = f"{prefix}{value}{line[len(body) :]}"
            return "".join(lines)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.append(f"{prefix}{value}\n")
    return "".join(lines)


def replace_env_file(path: Path, key: str, value: str) -> None:
    """Rewrites one line of the environment file through a temp file and a rename, so a
    power cut leaves the old file or the new one, never a truncated one that would stop
    every unit from starting. Mode and owner are copied from the existing file (0640
    root:pi as the installer sets it); the file has to exist."""
    replace_env_values(path, {key: value})


def replace_env_values(path: Path, values: dict[str, str]) -> None:
    """replace_env_file for several lines at once, in ONE atomic replacement: the key and
    the server it belongs to can never be seen apart."""
    original = path.read_bytes()
    text = original.decode("utf-8")
    for key, value in values.items():
        text = rewrite_env_line(text, key, value)
    updated = text.encode("utf-8")
    current = path.stat()
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(updated)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp, stat.S_IMODE(current.st_mode))
    chown = getattr(os, "chown", None)
    geteuid = getattr(os, "geteuid", None)
    if chown is not None and geteuid is not None and geteuid() == 0:
        chown(tmp, current.st_uid, current.st_gid)
    tmp.replace(path)


def run_systemctl(args: list[str]) -> None:
    subprocess.run(["systemctl", *args], check=True)


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.error("could not remove %s: %s", path, exc)


def apply_pending_credentials(
    settings: Settings, env_path: Path, systemctl: Systemctl | None = None
) -> int:
    """Exit status: 0 when the key was applied or there was nothing to apply, 1 when the
    pending file was unusable, the environment file could not be rewritten or a unit could
    not be started. The pending file is gone afterwards in every case."""
    systemctl = systemctl if systemctl is not None else run_systemctl
    pending = pending_path(Path(settings.display_state_path).parent)
    try:
        api_key, server = read_pending(pending)
    except FileNotFoundError:
        log.info("no %s, nothing to apply", pending)
        return 0
    except (OSError, ValueError) as exc:
        log.error("refusing %s: %s", pending, exc)
        _remove(pending)
        return 1
    values = {ENV_KEY: api_key}
    if settings.studylife_base_url is None:
        # The server comes from settings.json: record which one issued this key. With
        # STUDYLIFE_BASE_URL set the file stays exactly as it always was.
        values[ENV_KEY_INSTANCE] = server
    try:
        replace_env_values(env_path, values)
    except OSError as exc:
        log.error("could not write %s: %s", env_path, exc)
        _remove(pending)
        return 1
    _remove(pending)
    log.info("%s updated in %s", ENV_KEY, env_path)
    try:
        systemctl(["restart", "--no-block", WEB_UNIT])
        systemctl(["start", "--no-block", REFRESH_UNIT])
    except (OSError, subprocess.CalledProcessError) as exc:
        log.error("key applied, but restarting the units failed: %s", exc)
        return 1
    return 0
