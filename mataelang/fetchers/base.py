"""Fetcher protocol + FetchContext.

Rules for every fetcher (see docs/phase-0-spine.md §4.1):

* Only ever yield ``MEvent``. Never touch the DB or WebSocket.
* Drop anything without coordinates *before* building an ``MEvent`` — validation errors
  should mean bugs, not bad data.
* Catch your own exceptions and log them. Then raise ``FetchError`` so the scheduler can
  record ``last_error`` for the status strip. The scheduler catches everything, so one dead
  source never stops the others.
* In ``fixture_mode`` yield from ``_fixtures/<name>.json`` and make no network call.
* Heavy parsing (>~50 ms) goes through ``asyncio.to_thread``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import httpx

from ..models import MEvent

if TYPE_CHECKING:
    from ..config import Settings

FIXTURES_DIR = Path(__file__).resolve().parent / "_fixtures"


class FetchError(Exception):
    """Raised by a fetcher after it has logged its own failure. Carries a short message
    suitable for the UI status strip."""


@dataclass
class FetchContext:
    http: httpx.AsyncClient
    log: logging.Logger
    settings: Settings
    fixture_mode: bool


@runtime_checkable
class Fetcher(Protocol):
    name: str  # matches id prefix, e.g. "usgs"
    schedule: str  # cron expression
    ttl_default: int
    min_interval_s: int  # overlap guard

    def fetch(self, ctx: FetchContext) -> AsyncIterator[MEvent]: ...


def load_fixture(name: str, now: datetime | None = None) -> list[MEvent]:
    """Read ``_fixtures/<name>.json`` — a list of MEvent dicts.

    A record may carry ``"ts_offset_s": -300`` instead of a real ``ts``; it is resolved
    relative to *now* so fixtures never age out between commits.
    """
    now = now or datetime.now(UTC)
    raw: list[dict[str, Any]] = json.loads((FIXTURES_DIR / f"{name}.json").read_text("utf-8"))
    events: list[MEvent] = []
    for rec in raw:
        rec = dict(rec)
        offset = rec.pop("ts_offset_s", None)
        if offset is not None:
            rec["ts"] = (now + timedelta(seconds=float(offset))).isoformat()
        events.append(MEvent.model_validate(rec))
    return events


async def iter_fixture(name: str) -> AsyncIterator[MEvent]:
    for e in load_fixture(name):
        yield e
