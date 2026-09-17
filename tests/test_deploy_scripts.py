"""The shell scripts under deploy/: syntax-checked with `bash -n` wherever bash exists, plus
the two properties of update.sh that a syntax check cannot see."""

import shutil
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
SCRIPTS = sorted(DEPLOY.glob("*.sh"))


@pytest.mark.parametrize("script", SCRIPTS, ids=[script.name for script in SCRIPTS])
def test_scripts_parse(script: Path) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash on this machine")
    result = subprocess.run(
        [bash, "-n", script.as_posix()], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_update_script_is_safe_to_replace_while_running() -> None:
    # The checkout half-way through replaces update.sh itself; bash reads scripts
    # incrementally, so all the work has to live in a function called on the last line.
    lines = [line for line in (DEPLOY / "update.sh").read_text(encoding="utf-8").splitlines()]
    assert lines[-1] == 'main "$@"'
    assert "set -euo pipefail" in lines


def test_credentials_units_watch_the_pending_file_and_apply_it() -> None:
    path_unit = (DEPLOY / "studylife-display-credentials.path").read_text(encoding="utf-8")
    service = (DEPLOY / "studylife-display-credentials.service").read_text(encoding="utf-8")
    assert "PathExists=/var/lib/studylife-display/credentials.pending.json" in path_unit
    assert "Unit=studylife-display-credentials.service" in path_unit
    assert "studylife-display credentials-apply" in service
    assert "ReadWritePaths=/etc /var/lib/studylife-display" in service
    assert "ProtectSystem=strict" in service
    for script in ("install.sh", "update.sh"):
        text = (DEPLOY / script).read_text(encoding="utf-8")
        assert "studylife-display-credentials.path" in text


def test_install_script_checks_out_a_release_by_default() -> None:
    text = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    assert "--main" in text
    assert "fetch --tags" in text
    assert "--sort=-version:refname" in text
