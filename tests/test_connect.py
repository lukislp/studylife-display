"""The connect flow's pieces: PKCE, the connect URL's exact wire shape, the pasted-URL
parsing and its checks, the assertion exchange, the pending credentials file and the
overlay detection. The pages themselves are covered in test_web.py."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from studylife_display.connect import (
    CLIENT_ID,
    STATE_LIFETIME,
    CallbackResult,
    ConnectError,
    build_connect_url,
    check_callback,
    exchange_assertion,
    loopback_redirect_uri,
    new_pkce_pair,
    new_state,
    parse_callback,
    pending_path,
    public_redirect_uri,
    read_pending_credentials,
    root_is_overlay,
    start_connect,
    whoami,
    write_pending_credentials,
)

INSTANCE = "https://studylife.test"
REDIRECT = "http://localhost:8795/connect/callback"


class TestPkce:
    def test_challenge_is_the_unpadded_base64url_sha256_of_the_verifier(self) -> None:
        verifier, challenge = new_pkce_pair()
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        assert challenge == expected
        assert "=" not in challenge
        assert 43 <= len(verifier) <= 128

    def test_every_pair_and_state_is_different(self) -> None:
        assert new_pkce_pair()[0] != new_pkce_pair()[0]
        states = {new_state() for _ in range(100)}
        assert len(states) == 100
        assert all(len(state) >= 40 for state in states)


class TestConnectUrl:
    def test_exact_wire_shape(self) -> None:
        # The same four query parameters, in this order, as studylife-cli's login sends.
        url = build_connect_url(INSTANCE + "/", CLIENT_ID, REDIRECT, "S", "C")
        assert url == (
            f"{INSTANCE}/connect/client/studylife-display"
            "?redirect_uri=http%3A%2F%2Flocalhost%3A8795%2Fconnect%2Fcallback"
            "&state=S&code_challenge=C&code_challenge_method=S256"
        )

    def test_redirect_uris(self) -> None:
        assert loopback_redirect_uri(8795) == REDIRECT
        assert public_redirect_uri("https://pi.tail.ts.net/") == (
            "https://pi.tail.ts.net/connect/callback"
        )

    def test_start_connect_binds_everything(self, fixed_now: datetime) -> None:
        pending = start_connect(INSTANCE, REDIRECT, fixed_now)
        assert pending.state in pending.connect_url
        assert pending.verifier not in pending.connect_url
        assert pending.redirect_uri == REDIRECT
        assert pending.expires_at == fixed_now + STATE_LIFETIME
        assert not pending.expired(fixed_now + timedelta(minutes=9))
        assert pending.expired(fixed_now + timedelta(minutes=10))


class TestParseCallback:
    def test_whole_url(self) -> None:
        result = parse_callback(f" {REDIRECT}?assertion=A%2Bb&state=S1 ")
        assert result == CallbackResult(state="S1", assertion="A+b")

    def test_query_string_alone(self) -> None:
        assert parse_callback("assertion=A&state=S") == CallbackResult("S", "A")

    @pytest.mark.parametrize("text", ["", "   ", REDIRECT, f"{REDIRECT}?state=S", "state=S"])
    def test_missing_assertion(self, text: str) -> None:
        with pytest.raises(ConnectError) as info:
            parse_callback(text)
        assert info.value.key == "missing_assertion"

    def test_checks(self, fixed_now: datetime) -> None:
        pending = start_connect(INSTANCE, REDIRECT, fixed_now)
        good = CallbackResult(pending.state, "A")
        check_callback(pending, good, fixed_now + timedelta(minutes=1))
        with pytest.raises(ConnectError) as info:
            check_callback(None, good, fixed_now)
        assert info.value.key == "no_pending"
        with pytest.raises(ConnectError) as info:
            check_callback(pending, good, fixed_now + STATE_LIFETIME)
        assert info.value.key == "expired"
        with pytest.raises(ConnectError) as info:
            check_callback(pending, replace(good, state=pending.state + "x"), fixed_now)
        assert info.value.key == "state_mismatch"
        with pytest.raises(ConnectError) as info:
            check_callback(pending, replace(good, state="ü"), fixed_now)
        assert info.value.key == "state_mismatch"


class TestExchange:
    @respx.mock
    def test_posts_exactly_the_generic_exchange_body(self) -> None:
        route = respx.post(f"{INSTANCE}/api/auth/assertion-exchange").mock(
            return_value=httpx.Response(200, json={"userId": 7, "apiKey": "k-123"})
        )
        assert exchange_assertion(INSTANCE + "/", CLIENT_ID, "A", "V") == ("k-123", 7)
        request = route.calls.last.request
        assert json.loads(request.content) == {
            "clientId": "studylife-display",
            "assertion": "A",
            "codeVerifier": "V",
        }
        assert "x-api-key" not in {name.lower() for name in request.headers}

    @respx.mock
    @pytest.mark.parametrize(
        ("status", "key"), [(401, "exchange_rejected"), (400, "exchange_failed")]
    )
    def test_refusals(self, status: int, key: str) -> None:
        respx.post(f"{INSTANCE}/api/auth/assertion-exchange").mock(
            return_value=httpx.Response(status, text="no")
        )
        with pytest.raises(ConnectError) as info:
            exchange_assertion(INSTANCE, CLIENT_ID, "A", "V")
        assert info.value.key == key

    @respx.mock
    def test_unreachable_and_odd_answers(self) -> None:
        respx.post(f"{INSTANCE}/api/auth/assertion-exchange").mock(
            side_effect=httpx.ConnectError("down")
        )
        with pytest.raises(ConnectError) as info:
            exchange_assertion(INSTANCE, CLIENT_ID, "A", "V")
        assert info.value.key == "unreachable"
        respx.post(f"{INSTANCE}/api/auth/assertion-exchange").mock(
            return_value=httpx.Response(200, json={"userId": 7, "apiKey": "has space"})
        )
        with pytest.raises(ConnectError) as info:
            exchange_assertion(INSTANCE, CLIENT_ID, "A", "V")
        assert info.value.key == "exchange_failed"


class TestPendingFile:
    def test_written_owner_only_and_readable_back(
        self, tmp_path: Path, fixed_now: datetime
    ) -> None:
        state_dir = tmp_path / "state"
        path = write_pending_credentials(state_dir, "k-123", INSTANCE + "/", fixed_now)
        assert path == pending_path(state_dir)
        assert json.loads(path.read_text(encoding="utf-8")) == {
            "apiKey": "k-123",
            "instance": INSTANCE,
            "obtainedAt": fixed_now.isoformat(),
        }
        assert not path.with_suffix(".json.tmp").exists()
        if sys.platform != "win32":
            assert oct(os.stat(path).st_mode & 0o777) == oct(0o600)
        assert read_pending_credentials(path) == "k-123"

    @pytest.mark.parametrize(
        "content", ["{not json", "[]", "{}", '{"apiKey": ""}', '{"apiKey": "a b"}', '{"apiKey": 5}']
    )
    def test_invalid_content_is_refused(self, tmp_path: Path, content: str) -> None:
        path = tmp_path / "credentials.pending.json"
        path.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError):
            read_pending_credentials(path)

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            read_pending_credentials(tmp_path / "credentials.pending.json")


class TestWhoami:
    @respx.mock
    def test_sends_the_key_and_returns_the_two_fields(self) -> None:
        route = respx.get(f"{INSTANCE}/api/auth/whoami").mock(
            return_value=httpx.Response(
                200, json={"userId": 7, "credential": "client:studylife-display"}
            )
        )
        assert whoami(INSTANCE, "k") == {"userId": 7, "credential": "client:studylife-display"}
        assert route.calls.last.request.headers["X-Api-Key"] == "k"

    @respx.mock
    @pytest.mark.parametrize(
        ("status", "key"), [(401, "rejected"), (403, "rejected"), (500, "unreachable")]
    )
    def test_failures(self, status: int, key: str) -> None:
        respx.get(f"{INSTANCE}/api/auth/whoami").mock(return_value=httpx.Response(status))
        with pytest.raises(ConnectError) as info:
            whoami(INSTANCE, "k")
        assert info.value.key == key


class TestOverlay:
    def test_overlay_root(self, tmp_path: Path) -> None:
        mounts = tmp_path / "mounts"
        mounts.write_text(
            "overlay / overlay rw,relatime,lowerdir=/lower,upperdir=/upper 0 0\n"
            "/dev/mmcblk0p1 /boot/firmware vfat rw 0 0\n",
            encoding="utf-8",
        )
        assert root_is_overlay(mounts) is True

    def test_plain_root(self, tmp_path: Path) -> None:
        mounts = tmp_path / "mounts"
        mounts.write_text(
            "/dev/mmcblk0p2 / ext4 rw,noatime 0 0\n"
            "overlay /var/lib/docker/overlay2/x overlay rw 0 0\n",
            encoding="utf-8",
        )
        assert root_is_overlay(mounts) is False

    def test_unreadable_means_no(self, tmp_path: Path) -> None:
        assert root_is_overlay(tmp_path / "missing") is False
