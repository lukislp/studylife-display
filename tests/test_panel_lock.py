import logging
import threading
import time
from pathlib import Path

import pytest

from studylife_display.panel_lock import LOCK_FILE, PanelLockTimeout, panel_lock


def test_creates_the_state_directory_and_lock_file(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    with panel_lock(state_dir, timeout=1):
        assert (state_dir / LOCK_FILE).exists()


def test_second_acquirer_waits_until_the_first_releases(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    acquired_at: list[float] = []
    started = threading.Event()

    def second() -> None:
        started.set()
        with panel_lock(tmp_path, timeout=5, poll=0.05):
            acquired_at.append(time.monotonic())

    with caplog.at_level(logging.INFO, logger="studylife_display.panel_lock"):
        with panel_lock(tmp_path, timeout=1):
            worker = threading.Thread(target=second)
            worker.start()
            started.wait()
            time.sleep(0.4)
            released_at = time.monotonic()
        worker.join(timeout=5)
    assert not worker.is_alive()
    assert acquired_at and acquired_at[0] >= released_at
    assert any("panel busy" in record.getMessage() for record in caplog.records)


def test_timeout_raises(tmp_path: Path) -> None:
    outcome: list[BaseException | None] = []

    def second() -> None:
        try:
            with panel_lock(tmp_path, timeout=0.3, poll=0.05):
                outcome.append(None)
        except PanelLockTimeout as exc:
            outcome.append(exc)

    with panel_lock(tmp_path, timeout=1):
        worker = threading.Thread(target=second)
        worker.start()
        worker.join(timeout=5)
    assert isinstance(outcome[0], PanelLockTimeout)
    assert isinstance(outcome[0], TimeoutError)


def test_released_lock_can_be_taken_again(tmp_path: Path) -> None:
    with panel_lock(tmp_path, timeout=1):
        pass
    with panel_lock(tmp_path, timeout=1):
        pass
