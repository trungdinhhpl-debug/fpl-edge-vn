"""Official FPL API client (Tier-1 data source, highest trust weight).

Public endpoints only — no auth, no scraping behind logins (spec §22).
Endpoints used:
  /bootstrap-static/                      players, teams, events, game settings
  /fixtures/                              all fixtures (+ ?event=)
  /element-summary/{player_id}/           per-player history + upcoming
  /event/{gw}/live/                       live points during matches
  /entry/{team_id}/                       a manager's entry summary
  /entry/{team_id}/event/{gw}/picks/      a manager's squad for a GW
  /entry/{team_id}/history/               a manager's season history
  /entry/{team_id}/transfers/             a manager's transfers
"""
from __future__ import annotations

import time
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings

_HEADERS = {
    "User-Agent": (
        "FPL-Edge-VN/0.1 (independent fan project; contact via repo). "
        "Python-httpx"
    ),
    "Accept": "application/json",
}


class FPLNotFound(Exception):
    """The FPL API answered 404 — the resource genuinely isn't public (yet)."""


class FPLClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.fpl_base_url).rstrip("/")
        self._client = httpx.Client(
            headers=_HEADERS,
            timeout=settings.http_timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "FPLClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=0.6, max=8),
        # a 404 is a definitive answer (e.g. picks hidden until the deadline),
        # not a transient failure — don't burn retries on it, and let callers
        # see the real exception rather than tenacity's RetryError wrapper.
        retry=retry_if_not_exception_type(FPLNotFound),
        reraise=True,
    )
    def _get(self, path: str) -> Any:
        resp = self._client.get(f"{self.base_url}{path}")
        if resp.status_code == 404:
            raise FPLNotFound(path)
        resp.raise_for_status()
        return resp.json()

    # ---- Tier-1 endpoints ----
    def bootstrap_static(self) -> dict[str, Any]:
        return self._get("/bootstrap-static/")

    def fixtures(self, event: int | None = None) -> list[dict[str, Any]]:
        path = "/fixtures/"
        if event is not None:
            path += f"?event={event}"
        return self._get(path)

    def element_summary(self, player_id: int) -> dict[str, Any]:
        return self._get(f"/element-summary/{player_id}/")

    def event_live(self, gameweek: int) -> dict[str, Any]:
        return self._get(f"/event/{gameweek}/live/")

    # ---- Manager (team import) ----
    def entry(self, team_id: int) -> dict[str, Any]:
        return self._get(f"/entry/{team_id}/")

    def entry_picks(self, team_id: int, gameweek: int) -> dict[str, Any]:
        return self._get(f"/entry/{team_id}/event/{gameweek}/picks/")

    def entry_history(self, team_id: int) -> dict[str, Any]:
        return self._get(f"/entry/{team_id}/history/")

    def entry_transfers(self, team_id: int) -> list[dict[str, Any]]:
        return self._get(f"/entry/{team_id}/transfers/")

    # ---- helper: pull many element-summaries politely ----
    def element_summaries(self, player_ids: list[int]) -> dict[int, dict[str, Any]]:
        out: dict[int, dict[str, Any]] = {}
        delay = settings.fpl_request_delay_ms / 1000.0
        for pid in player_ids:
            try:
                out[pid] = self.element_summary(pid)
            except Exception:
                continue
            time.sleep(delay)
        return out
