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


def test_install_script_checks_out_a_release_by_default() -> None:
    text = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    assert "--main" in text
    assert "fetch --tags" in text
    assert "--sort=-version:refname" in text
