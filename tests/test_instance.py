from __future__ import annotations

import hashlib
import re
from pathlib import Path

from studylife_display.instance import FALLBACK_FILE, instance_id

HEX16 = re.compile(r"^[0-9a-f]{16}$")


def sha16(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def machine_id_file(tmp_path: Path, content: str = "abc123\n") -> Path:
    path = tmp_path / "machine-id"
    path.write_text(content, encoding="ascii", newline="")
    return path


def add_iface(
    net: Path, name: str, mac: str | None = "aa:bb:cc:dd:ee:01", physical: bool = True
) -> None:
    (net / name).mkdir(parents=True)
    if mac is not None:
        (net / name / "address").write_text(mac + "\n", encoding="ascii")
    if physical:
        (net / name / "device").mkdir()


def test_machine_id_alone_is_hashed_when_there_is_no_interface(tmp_path: Path) -> None:
    mid = machine_id_file(tmp_path)
    result = instance_id(tmp_path / "state", mid, tmp_path / "no-net")
    assert result == sha16("abc123")
    assert HEX16.match(result)
    assert "abc123" not in result


def test_whitespace_and_newlines_in_the_machine_id_are_stripped(tmp_path: Path) -> None:
    net = tmp_path / "net"
    net.mkdir()
    plain = instance_id(tmp_path, machine_id_file(tmp_path, "abc123"), net)
    padded = instance_id(tmp_path, machine_id_file(tmp_path, "  abc123 \r\n"), net)
    assert plain == padded == sha16("abc123")


def test_mac_is_mixed_in_and_same_inputs_give_the_same_id(tmp_path: Path) -> None:
    net = tmp_path / "net"
    add_iface(net, "wlan0", "AA:BB:CC:DD:EE:01")
    mid = machine_id_file(tmp_path)
    first = instance_id(tmp_path, mid, net)
    assert first == instance_id(tmp_path, mid, net)
    assert first == sha16("abc123|aa:bb:cc:dd:ee:01")


def test_cloned_machine_id_with_different_macs_gives_different_ids(tmp_path: Path) -> None:
    mid = machine_id_file(tmp_path)
    net_a, net_b = tmp_path / "a", tmp_path / "b"
    add_iface(net_a, "wlan0", "aa:bb:cc:dd:ee:01")
    add_iface(net_b, "wlan0", "aa:bb:cc:dd:ee:02")
    assert instance_id(tmp_path, mid, net_a) != instance_id(tmp_path, mid, net_b)


def test_interface_preference_wlan0_then_eth0_then_alphabetical(tmp_path: Path) -> None:
    mid = machine_id_file(tmp_path)
    net = tmp_path / "net"
    add_iface(net, "enp1s0", "00:00:00:00:00:03")
    add_iface(net, "eth0", "00:00:00:00:00:02")
    add_iface(net, "wlan0", "00:00:00:00:00:01")
    assert instance_id(tmp_path, mid, net) == sha16("abc123|00:00:00:00:00:01")
    (net / "wlan0" / "device").rmdir()  # no longer physical
    assert instance_id(tmp_path, mid, net) == sha16("abc123|00:00:00:00:00:02")
    (net / "eth0" / "device").rmdir()
    assert instance_id(tmp_path, mid, net) == sha16("abc123|00:00:00:00:00:03")


def test_first_remaining_interface_is_alphabetical(tmp_path: Path) -> None:
    net = tmp_path / "net"
    add_iface(net, "wlp3s0", "00:00:00:00:00:09")
    add_iface(net, "enp1s0", "00:00:00:00:00:08")
    assert instance_id(tmp_path, machine_id_file(tmp_path), net) == sha16(
        "abc123|00:00:00:00:00:08"
    )


def test_virtual_and_zero_interfaces_are_skipped(tmp_path: Path) -> None:
    net = tmp_path / "net"
    add_iface(net, "lo", "00:00:00:00:00:00")
    for name in ("veth1", "docker0", "br-1", "virbr0", "tun0", "tap0", "wg0"):
        add_iface(net, name, "02:00:00:00:00:aa")
    add_iface(net, "dummy0", "02:00:00:00:00:bb", physical=False)
    add_iface(net, "eth0", "00:00:00:00:00:00")  # all zero
    add_iface(net, "eth1", None)  # unreadable address
    mid = machine_id_file(tmp_path)
    assert instance_id(tmp_path, mid, net) == sha16("abc123")
    add_iface(net, "zzz0", "02:00:00:00:00:cc")
    assert instance_id(tmp_path, mid, net) == sha16("abc123|02:00:00:00:00:cc")


def test_missing_machine_id_persists_a_random_id_and_reuses_it(tmp_path: Path) -> None:
    state = tmp_path / "state"
    missing = tmp_path / "nope"
    first = instance_id(state, missing, tmp_path / "net")
    assert HEX16.match(first)
    assert (state / FALLBACK_FILE).read_text().strip() == first
    assert instance_id(state, missing, tmp_path / "net") == first
    other = instance_id(tmp_path / "state2", missing, tmp_path / "net")
    assert other != first


def test_empty_machine_id_counts_as_missing(tmp_path: Path) -> None:
    mid = machine_id_file(tmp_path, "\n")
    result = instance_id(tmp_path / "state", mid, tmp_path / "net")
    assert (tmp_path / "state" / FALLBACK_FILE).read_text().strip() == result


def test_unwritable_state_dir_does_not_raise(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    result = instance_id(blocker / "state", tmp_path / "nope", tmp_path / "net")
    assert HEX16.match(result)
