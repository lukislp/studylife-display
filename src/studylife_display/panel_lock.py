"""One panel, two drivers: the five-minute timer (`run`) and the web interface (`serve`)
may both want to refresh it. An exclusive lock file in the state directory serialises them;
a full refresh takes a few seconds, so the loser simply waits its turn.

`fcntl.flock` on POSIX, `msvcrt.locking` on Windows (development only). Both are released
by the kernel when the process dies, so a crashed refresh never leaves a stale lock behind.
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

log = logging.getLogger(__name__)

LOCK_FILE = "panel.lock"
DEFAULT_TIMEOUT = 60.0
POLL_SECONDS = 0.2

if sys.platform == "win32":
    import msvcrt

    def _try_lock(handle: IO[bytes]) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(handle: IO[bytes]) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(handle: IO[bytes]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle: IO[bytes]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class PanelLockTimeout(TimeoutError):
    """Another refresh held the panel for longer than the timeout."""


@contextmanager
def panel_lock(
    state_dir: Path, timeout: float = DEFAULT_TIMEOUT, poll: float = POLL_SECONDS
) -> Iterator[None]:
    """Holds `<state_dir>/panel.lock` exclusively for the duration of the block, waiting up
    to `timeout` seconds for another holder and raising PanelLockTimeout after that."""
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / LOCK_FILE
    with path.open("a+b") as handle:
        deadline = time.monotonic() + timeout
        announced = False
        while True:
            try:
                _try_lock(handle)
                break
            except OSError:
                if not announced:
                    log.info(
                        "panel busy (another refresh holds %s), waiting up to %.0f s", path, timeout
                    )
                    announced = True
                if time.monotonic() >= deadline:
                    raise PanelLockTimeout(
                        f"could not acquire {path} within {timeout:.0f} s"
                    ) from None
                time.sleep(poll)
        try:
            yield
        finally:
            _unlock(handle)
