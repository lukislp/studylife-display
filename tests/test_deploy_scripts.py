"""The shell scripts under deploy/: syntax-checked with `bash -n` wherever bash exists, plus
the two properties of update.sh that a syntax check cannot see, and the behaviour of
the mDNS advertisement script."""

import shutil
import subprocess
import xml.etree.ElementTree as ET
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


def test_auto_update_is_off_by_default_and_wired_into_both_scripts() -> None:
    service = (DEPLOY / "studylife-display-update.service").read_text(encoding="utf-8")
    timer = (DEPLOY / "studylife-display-update.timer").read_text(encoding="utf-8")
    assert 'ExecCondition=/bin/sh -c \'[ "${DISPLAY_AUTO_UPDATE:-false}" = "true" ]\'' in service
    assert "ExecStart=/usr/bin/bash /opt/studylife-display/src/deploy/update.sh" in service
    assert "Unit=studylife-display-update.service" in timer
    for script in ("install.sh", "update.sh"):
        text = (DEPLOY / script).read_text(encoding="utf-8")
        assert "studylife-display-update.timer" in text


# --- the mDNS advertisement -------------------------------------------------------------

AVAHI_SCRIPT = DEPLOY / "avahi-service.sh"


def run_avahi_script(
    tmp_path: Path,
    env_text: str | None,
    version: str = "1.2.3",
    with_dir: bool = True,
    instance: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Runs deploy/avahi-service.sh against a scratch services directory and env file. The
    script is copied with LF line endings first: a Windows checkout may hold it with CRLF,
    which bash would read as part of the commands."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash on this machine")
    script = tmp_path / "avahi-service.sh"
    script.write_bytes(AVAHI_SCRIPT.read_bytes().replace(b"\r\n", b"\n"))
    services = tmp_path / "services"
    if with_dir:
        services.mkdir(exist_ok=True)
    env_file = tmp_path / "studylife-display.env"
    if env_text is not None:
        env_file.write_text(env_text, encoding="utf-8", newline="\n")
    return subprocess.run(
        [
            bash,
            script.as_posix(),
            "--dir",
            services.as_posix(),
            "--env-file",
            env_file.as_posix(),
            "--version",
            version,
            *([] if instance is None else ["--id", instance]),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def advertised(tmp_path: Path) -> ET.Element:
    """The service element of the file the script wrote (parsed, so it is well-formed XML)."""
    root = ET.parse(tmp_path / "services" / "studylife-display.service").getroot()
    service = root.find("service")
    assert service is not None
    return service


def txt_records(service: ET.Element) -> dict[str, str]:
    records = [(node.text or "").partition("=") for node in service.findall("txt-record")]
    return {key: value for key, _, value in records}


def test_avahi_service_advertises_the_default_install(tmp_path: Path) -> None:
    result = run_avahi_script(tmp_path, "STUDYLIFE_BASE_URL=https://studylife.test\n")
    assert result.returncode == 0, result.stderr
    root = ET.parse(tmp_path / "services" / "studylife-display.service").getroot()
    name = root.find("name")
    assert name is not None
    assert name.text == "StudyLife Display (%h)"
    assert name.get("replace-wildcards") == "yes"
    service = advertised(tmp_path)
    assert (service.findtext("type") or "") == "_studylife-display._tcp"
    assert (service.findtext("port") or "") == "8795"
    # Exactly the four keys Home Assistant's discovery reads (no id: none could be computed).
    assert txt_records(service) == {"version": "1.2.3", "tls": "false", "api": "false", "path": "/"}
    # Written as the file avahi reads, not as a scratch file left behind.
    assert sorted(path.name for path in (tmp_path / "services").iterdir()) == [
        "studylife-display.service"
    ]


def test_avahi_service_advertises_the_instance_id(tmp_path: Path) -> None:
    result = run_avahi_script(tmp_path, "", instance="0123456789abcdef")
    assert result.returncode == 0, result.stderr
    assert txt_records(advertised(tmp_path)) == {
        "version": "1.2.3",
        "tls": "false",
        "api": "false",
        "path": "/",
        "id": "0123456789abcdef",
    }


def test_avahi_service_drops_an_invalid_instance_id(tmp_path: Path) -> None:
    result = run_avahi_script(tmp_path, "", instance="<bad>&")
    assert result.returncode == 0, result.stderr
    assert "id" not in txt_records(advertised(tmp_path))


def test_avahi_service_follows_port_tls_and_api_token(tmp_path: Path) -> None:
    env = (
        "# comment\n"
        "DISPLAY_WEB_BIND=127.0.0.1:9100\n"
        'DISPLAY_TLS="True"\n'
        "DISPLAY_API_TOKEN=s3cret-t0ken-with-$pecial#chars\n"
    )
    result = run_avahi_script(tmp_path, env, version="1.12.0")
    assert result.returncode == 0, result.stderr
    service = advertised(tmp_path)
    assert (service.findtext("port") or "") == "9100"
    assert txt_records(service) == {"version": "1.12.0", "tls": "true", "api": "true", "path": "/"}
    # Only that a token is set is advertised, never the token itself.
    text = (tmp_path / "services" / "studylife-display.service").read_text(encoding="utf-8")
    assert "s3cret" not in text


@pytest.mark.parametrize(
    ("bind", "port"),
    [("8795", "8795"), ("[::]:8800", "8800"), ("0.0.0.0:8795", "8795"), ("nonsense", "8795")],
)
def test_avahi_service_reads_the_port_from_the_bind_address(
    tmp_path: Path, bind: str, port: str
) -> None:
    run_avahi_script(tmp_path, f"DISPLAY_WEB_BIND={bind}\n")
    assert (advertised(tmp_path).findtext("port") or "") == port


@pytest.mark.parametrize(
    ("value", "tls"),
    [("true", "true"), ("1", "true"), ("on", "true"), ("false", "false"), ("", "false")],
)
def test_avahi_service_tls_flag_values(tmp_path: Path, value: str, tls: str) -> None:
    run_avahi_script(tmp_path, f"DISPLAY_TLS={value}\n")
    assert txt_records(advertised(tmp_path))["tls"] == tls


def test_avahi_service_without_an_env_file_uses_the_defaults(tmp_path: Path) -> None:
    result = run_avahi_script(tmp_path, None)
    assert result.returncode == 0
    service = advertised(tmp_path)
    assert (service.findtext("port") or "") == "8795"
    assert txt_records(service)["api"] == "false"


def test_avahi_service_is_rewritten_when_something_changed_and_left_alone_otherwise(
    tmp_path: Path,
) -> None:
    run_avahi_script(tmp_path, "DISPLAY_WEB_BIND=0.0.0.0:8795\n")
    target = tmp_path / "services" / "studylife-display.service"
    first = target.read_text(encoding="utf-8")
    stamp = target.stat().st_mtime_ns
    again = run_avahi_script(tmp_path, "DISPLAY_WEB_BIND=0.0.0.0:8795\n")
    assert "up to date" in again.stdout
    assert target.stat().st_mtime_ns == stamp
    assert target.read_text(encoding="utf-8") == first
    # A new version, a new port, TLS switched on: the update rewrites it.
    updated = run_avahi_script(
        tmp_path, "DISPLAY_WEB_BIND=0.0.0.0:9001\nDISPLAY_TLS=true\n", version="2.0.0"
    )
    assert "written" in updated.stdout
    assert (advertised(tmp_path).findtext("port") or "") == "9001"
    assert txt_records(advertised(tmp_path))["version"] == "2.0.0"


def test_avahi_service_skips_silently_without_the_services_directory(tmp_path: Path) -> None:
    result = run_avahi_script(tmp_path, "DISPLAY_TLS=true\n", with_dir=False)
    assert result.returncode == 0
    assert "skipping the mDNS advertisement" in result.stdout
    assert not (tmp_path / "services").exists()


def test_avahi_service_escapes_the_version_for_xml(tmp_path: Path) -> None:
    run_avahi_script(tmp_path, "", version="1.0+g<a&b>")
    assert txt_records(advertised(tmp_path))["version"] == "1.0+g<a&b>"


def test_install_and_update_both_write_the_advertisement() -> None:
    for script in ("install.sh", "update.sh"):
        text = (DEPLOY / script).read_text(encoding="utf-8")
        assert 'bash "$SRC/deploy/avahi-service.sh" || echo' in text, script
    # update.sh runs it after the new checkout and before the web service restarts, and the
    # call sits inside main() so the replaced file still ends on the last line.
    update = (DEPLOY / "update.sh").read_text(encoding="utf-8")
    checkout = update.index('checkout --force --detach --quiet "$target"')
    assert checkout < update.index("avahi-service.sh")
    assert update.index("avahi-service.sh") < update.index(
        "systemctl restart studylife-display-web"
    )
    assert update.splitlines()[-1] == 'main "$@"'


def test_avahi_script_never_fails_its_caller() -> None:
    text = AVAHI_SCRIPT.read_text(encoding="utf-8")
    assert "_studylife-display._tcp" in text
    assert text.rstrip().splitlines()[-1] == "exit 0"
    assert "set -euo" not in text  # no errexit: nothing aborts the script halfway
