"""A stable identity for this display, so Home Assistant can recognise "the same display"
after its IP address or host name changed.

The id is the first 16 hex characters of `sha256(f"{machine_id}|{mac}")`, where
`machine_id` is the stripped content of /etc/machine-id and `mac` the hardware address of
the first physical network interface (see `_primary_mac`). It is a hash on purpose: the
raw machine id is an application-unique secret-ish value that systemd asks not to leak, and
the id is published on an unauthenticated endpoint and over mDNS. The MAC is mixed in so a
cloned SD card (same /etc/machine-id on two displays) still yields two distinct ids. When
no usable MAC exists the id is `sha256(machine_id)[:16]`.

Without a readable /etc/machine-id (dev machines, Windows, tests) the id is a random one
generated once and persisted as `instance_id` in the state directory, so it is stable
across restarts either way. If even that cannot be persisted the random id is returned
unpersisted rather than raising: the id must never take /healthz down.
"""

from __future__ import annotations

import hashlib
import secrets
from pathlib import Path

ID_LENGTH = 16
FALLBACK_FILE = "instance_id"
ZERO_MAC = "00:00:00:00:00:00"
# Interface names that are virtual by convention, skipped even if a `device` link exists.
VIRTUAL_PREFIXES = ("veth", "docker", "br-", "virbr", "tun", "tap", "wg")
PREFERRED_INTERFACES = ("wlan0", "eth0")


def _read_mac(net_dir: Path, name: str) -> str | None:
    """The address of one interface when it is a usable physical one, else None."""
    if name == "lo" or name.startswith(VIRTUAL_PREFIXES):
        return None
    try:
        if not (net_dir / name / "device").exists():
            return None
        mac = (net_dir / name / "address").read_text(encoding="ascii").strip().lower()
    except (OSError, UnicodeDecodeError):
        return None
    return mac if mac and mac != ZERO_MAC else None


def _primary_mac(net_dir: Path) -> str | None:
    """The MAC of wlan0, else eth0, else the alphabetically first other physical interface;
    None when there is none. Deterministic, so the id survives reboots."""
    try:
        names = sorted(entry.name for entry in net_dir.iterdir())
    except OSError:
        return None
    ordered = [n for n in PREFERRED_INTERFACES if n in names]
    ordered += [n for n in names if n not in PREFERRED_INTERFACES]
    for name in ordered:
        mac = _read_mac(net_dir, name)
        if mac is not None:
            return mac
    return None


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:ID_LENGTH]


def _persisted_random_id(state_dir: Path) -> str:
    path = state_dir / FALLBACK_FILE
    try:
        stored = path.read_text(encoding="ascii").strip()
        if len(stored) == ID_LENGTH and all(c in "0123456789abcdef" for c in stored):
            return stored
    except (OSError, UnicodeDecodeError):
        pass
    new = secrets.token_hex(ID_LENGTH // 2)
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(new + "\n", encoding="ascii")
        tmp.chmod(0o644)
        tmp.replace(path)
    except OSError:
        pass  # unpersisted: still a valid id for this process
    return new


def instance_id(
    state_dir: Path,
    machine_id_path: Path = Path("/etc/machine-id"),
    net_dir: Path = Path("/sys/class/net"),
) -> str:
    """The 16-hex-character id of this display; see the module docstring."""
    try:
        machine_id = machine_id_path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        machine_id = ""
    if not machine_id:
        return _persisted_random_id(state_dir)
    mac = _primary_mac(net_dir)
    return _hash(machine_id if mac is None else f"{machine_id}|{mac}")
