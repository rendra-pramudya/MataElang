"""In-memory live store → SQLite → WebSocket fanout.

Single instance held on ``app.state.bus``. Fetchers never touch this directly; the scheduler
hands their events in. Dedupe is by ``MEvent.id`` — a repeated id is an update, not a new marker.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel

from .models import Expire, MEvent, Snapshot, Upsert

log = logging.getLogger("mataelang.bus")


class EventStore(Protocol):
    async def upsert_many(self, events: list[MEvent]) -> None: ...
    async def load_live(self, now: datetime | None = None) -> list[MEvent]: ...


class Client(Protocol):
    """The subset of starlette.websockets.WebSocket the bus needs. Kept minimal so tests
    can pass a fake."""

    async def send_text(self, data: str) -> None: ...


class Bus:
    def __init__(self, db: EventStore) -> None:
        self._db = db
        self._live: dict[str, MEvent] = {}
        self._clients: set[Client] = set()
        self._lock = asyncio.Lock()

    # -- lifecycle ---------------------------------------------------------

    async def load_from_db(self) -> int:
        events = await self._db.load_live()
        async with self._lock:
            for e in events:
                self._live[e.id] = e
        return len(events)

    # -- clients -----------------------------------------------------------

    def snapshot(self, since: datetime | None = None) -> list[MEvent]:
        now = datetime.now(UTC)
        events = [e for e in self._live.values() if not e.is_expired(now)]
        if since is not None:
            events = [e for e in events if e.ts > since]
        return sorted(events, key=lambda e: e.ts)

    async def connect(self, client: Client, since: datetime | None = None) -> None:
        snap = Snapshot(events=self.snapshot(since))
        await client.send_text(snap.model_dump_json())
        self._clients.add(client)

    def disconnect(self, client: Client) -> None:
        self._clients.discard(client)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    @property
    def live_count(self) -> int:
        return len(self._live)

    # -- events ------------------------------------------------------------

    async def upsert(self, events: list[MEvent]) -> list[MEvent]:
        """Merge, persist, broadcast. Returns the events actually accepted (non-expired)."""
        if not events:
            return []
        now = datetime.now(UTC)
        accepted: list[MEvent] = []
        async with self._lock:
            for e in events:
                if e.is_expired(now):
                    continue
                self._live[e.id] = e
                accepted.append(e)
        # Persist everything, including expired ones — they are still history.
        await self._db.upsert_many(events)
        if accepted:
            await self.broadcast(Upsert(events=accepted))
        return accepted

    async def sweep(self, now: datetime | None = None) -> list[str]:
        """Drop expired events from the live set and tell clients. Runs every 60 s."""
        now = now or datetime.now(UTC)
        async with self._lock:
            dead = [k for k, e in self._live.items() if e.is_expired(now)]
            for k in dead:
                del self._live[k]
        if dead:
            await self.broadcast(Expire(ids=dead))
        return dead

    async def broadcast(self, msg: BaseModel) -> None:
        if not self._clients:
            return
        data = msg.model_dump_json()
        targets = list(self._clients)
        results = await asyncio.gather(
            *(c.send_text(data) for c in targets), return_exceptions=True
        )
        for client, result in zip(targets, results, strict=True):
            if isinstance(result, BaseException):
                log.info("dropping dead client: %s", result)
                self._clients.discard(client)
