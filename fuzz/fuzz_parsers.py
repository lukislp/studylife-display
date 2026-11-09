"""Atheris fuzz harness for the parsers of operator- and network-supplied text.

Contract under test: validate_server_url() either returns a canonical http(s) base URL (which
validates to itself again) or raises InvalidServerUrl; the quiet-hours parser either returns a
window or raises ValueError, and for any valid spec the window end is a moment strictly after
`now` that lies outside the window; parse_announcement() turns any mDNS TXT record set into an
Announcement or None. Anything else is a finding.

Run locally (Linux, needs the atheris wheel):
    uv sync --frozen --group fuzz
    uv run python fuzz/fuzz_parsers.py -max_total_time=60
CI runs the same harness for a short, fixed time budget (see .github/workflows/ci.yml).
"""

from __future__ import annotations

import contextlib
import sys
from datetime import datetime

import atheris

from studylife_display.discovery import InvalidServerUrl, parse_announcement, validate_server_url
from studylife_display.quiet_hours import in_quiet_hours, parse_quiet_hours, quiet_hours_end


def test_one_input(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)

    url = fdp.ConsumeUnicodeNoSurrogates(120)
    try:
        canonical = validate_server_url(url)
    except InvalidServerUrl:
        pass
    else:
        if not canonical.startswith(("http://", "https://")) or validate_server_url(canonical) != (
            canonical
        ):
            raise AssertionError(f"validate_server_url({url!r}) returned {canonical!r}")

    properties: dict[bytes | str, bytes | str | None] = {}
    for _ in range(fdp.ConsumeIntInRange(0, 4)):
        key = fdp.ConsumeBytes(8) if fdp.ConsumeBool() else fdp.ConsumeUnicodeNoSurrogates(8)
        value = fdp.ConsumeBytes(80) if fdp.ConsumeBool() else fdp.ConsumeUnicodeNoSurrogates(80)
        properties[key] = None if fdp.ConsumeBool() else value
    properties["url"] = url
    parse_announcement(fdp.ConsumeUnicodeNoSurrogates(40), properties)

    spec = fdp.ConsumeUnicodeNoSurrogates(24)
    now = datetime(
        2026,
        fdp.ConsumeIntInRange(1, 12),
        fdp.ConsumeIntInRange(1, 28),
        fdp.ConsumeIntInRange(0, 23),
        fdp.ConsumeIntInRange(0, 59),
        fdp.ConsumeIntInRange(0, 59),
    )
    with contextlib.suppress(ValueError):
        window = parse_quiet_hours(spec)
        inside = in_quiet_hours(now, spec)
        end = quiet_hours_end(now, spec)
        if window is None and (inside or end is not None):
            raise AssertionError(f"quiet hours off but {spec!r} reports a window")
        if (end is not None) != inside:
            raise AssertionError(f"quiet_hours_end disagrees with in_quiet_hours for {spec!r}")
        if end is not None and (end <= now or in_quiet_hours(end, spec)):
            raise AssertionError(f"quiet_hours_end({now}, {spec!r}) = {end} is not outside")


if __name__ == "__main__":
    # instrument_all() instead of instrument_imports(): the package is loaded through uv's
    # editable-install loader, which the import hook does not see (no coverage feedback).
    atheris.instrument_all()
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()
