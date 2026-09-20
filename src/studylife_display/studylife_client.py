"""Typed sync client for exactly the four read-only endpoints this display needs.

Every method corresponds to one scope the API key must carry (SCOPES, see the README), and
nothing here reaches an endpoint outside that set - a call added without the matching scope
authenticates fine and then fails with 403 on every request, which is a confusing way to find
out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

# The scopes the key must carry, one per endpoint below, in the order the README lists them.
SCOPES = ("Metrics.GetSummary", "Sessions.GetAll", "Sessions.GetHistory", "TimerState.Get")


@dataclass(frozen=True)
class SessionsPage:
    """What `list_sessions` returns: the sessions, the ETag the server stamped on them, and
    whether the server answered 304 (then `items` is empty and the caller keeps its copy)."""

    items: list[dict[str, Any]]
    etag: str | None
    not_modified: bool = False


class StudyLifeApiError(Exception):
    """Any non-2xx response, carrying the status so callers can tell a permission problem
    (403, never self-healing) from a transient one."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"StudyLife API returned {status_code}: {body.strip()[:200]}")
        self.status_code = status_code
        self.body = body


class StudyLifeClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 10.0) -> None:
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"X-Api-Key": api_key},
            timeout=timeout,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> StudyLifeClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self._http.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise StudyLifeApiError(response.status_code, response.text)
        return response

    def get_metrics_summary(self) -> dict[str, Any]:
        """Streak, weekly hours, week quota, upcoming course goals - the one place StudyLife
        calculates them (MetricsController). Note that the summary has no "today" figure;
        that one is derived from the session history below."""
        return dict(self._request("GET", "/api/metrics/summary").json())

    def get_session_history(self, days: int = 28) -> list[dict[str, Any]]:
        """Completed sessions of the last `days` days. Today's hours and the 4-week heatmap
        are summed from these on the Pi, per local calendar day."""
        params = {"days": days, "onlyCompleted": "true"}
        return list(self._request("GET", "/api/sessions/history", params=params).json())

    def get_timer_state(self) -> dict[str, Any]:
        return dict(self._request("GET", "/api/timerstate").json())

    def list_sessions(self, etag: str | None = None) -> SessionsPage:
        """Every session, planned ones included - the endpoint has no query parameters. The
        agenda layout picks today's out of it. The server hashes the body into an ETag and
        answers 304 to a matching `If-None-Match`, so a poll every five minutes costs no body
        while nothing changed; pass the ETag of the previous answer to use that."""
        headers = {"If-None-Match": etag} if etag else None
        response = self._request("GET", "/api/sessions", headers=headers)
        new_etag = response.headers.get("ETag")
        if response.status_code == 304:
            return SessionsPage(items=[], etag=new_etag or etag, not_modified=True)
        return SessionsPage(items=list(response.json()), etag=new_etag)
