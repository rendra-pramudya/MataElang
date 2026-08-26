"""Registers fetchers with APScheduler and tracks per-source health.

One job per fetcher, ``coalesce=True, max_instances=1, misfire_grace_time=60``. Start offsets
are staggered 15 s apart so sources never fire together. Each fetcher additionally has an
``asyncio.Lock`` + last-run timestamp enforcing ``min_interval_s`` (belt and braces — a manual
trigger from the API must not stampede a source either).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .bus import Bus
from .db import Database
from .fetchers import FetchContext, Fetcher, FetchError
from .models import MEvent, SourceStatus, Status

log = logging.getLogger("mataelang.scheduler")


@dataclass
class SourceState:
    last_ok: datetime | None = None
    last_error: str | None = None
    count: int = 0
    last_run_mono: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def to_status(self) -> SourceStatus:
        return SourceStatus(last_ok=self.last_ok, last_error=self.last_error, count=self.count)


class Scheduler:
    def __init__(
        self,
        *,
        bus: Bus,
        db: Database,
        ctx: FetchContext,
        fetchers: list[Fetcher],
    ) -> None:
        self.bus = bus
        self.db = db
        self.ctx = ctx
        self.fetchers = {f.name: f for f in fetchers}
        self.state = {f.name: SourceState() for f in fetchers}
        self._aps = AsyncIOScheduler(timezone="UTC")

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        s = self.ctx.settings
        now = datetime.now(UTC)
        for i, f in enumerate(self.fetchers.values()):
            self._aps.add_job(
                self.run_fetcher,
                CronTrigger.from_crontab(f.schedule, timezone="UTC"),
                args=[f.name],
                id=f"fetch:{f.name}",
                coalesce=True,
                max_instances=1,
                misfire_grace_time=60,
                # First run staggered 15 s per fetcher; cron takes over after that.
                next_run_time=now + timedelta(seconds=2 + 15 * i),
            )
        self._aps.add_job(
            self.bus.sweep,
            IntervalTrigger(seconds=s.sweep_interval_s),
            id="sweep",
            coalesce=True,
            max_instances=1,
        )
        self._aps.add_job(
            self.broadcast_status,
            IntervalTrigger(seconds=s.status_interval_s),
            id="status",
            coalesce=True,
            max_instances=1,
        )
        self._aps.add_job(
            self.retention,
            CronTrigger.from_crontab("30 3 * * *", timezone="UTC"),
            id="retention",
            coalesce=True,
            max_instances=1,
            misfire_grace_time=3600,
        )
        self._aps.start()
        log.info("scheduler started: %s", ", ".join(self.fetchers))

    def shutdown(self) -> None:
        if self._aps.running:
            self._aps.shutdown(wait=False)

    # -- jobs --------------------------------------------------------------

    async def run_fetcher(self, name: str, *, force: bool = False) -> list[MEvent]:
        """Run one fetcher to completion and push its events onto the bus.

        Returns the accepted events (useful for tests and the manual-trigger route).
        """
        fetcher = self.fetchers[name]
        state = self.state[name]
        if state.lock.locked():
            log.info("%s: already running, skipping", name)
            return []
        async with state.lock:
            since_last = time.monotonic() - state.last_run_mono
            if not force and state.last_run_mono and since_last < fetcher.min_interval_s:
                log.info(
                    "%s: ran %.0fs ago (< %ds), skipping", name, since_last, fetcher.min_interval_s
                )
                return []
            state.last_run_mono = time.monotonic()

            events: list[MEvent] = []
            try:
                async for ev in fetcher.fetch(self.ctx):
                    events.append(ev)
            except FetchError as exc:
                state.last_error = str(exc)
                log.warning("%s: fetch error: %s", name, exc)
                return []
            except Exception as exc:  # noqa: BLE001 — a bug in one source must not kill the loop
                state.last_error = f"{type(exc).__name__}: {exc}"[:200]
                log.exception("%s: unhandled exception in fetcher", name)
                return []

            accepted = await self.bus.upsert(events)
            state.last_ok = datetime.now(UTC)
            state.last_error = None
            state.count += len(accepted)
            log.info("%s: %d events (%d accepted)", name, len(events), len(accepted))
            return accepted

    async def broadcast_status(self) -> None:
        await self.bus.broadcast(self.status())

    async def retention(self) -> None:
        n = await self.db.purge_older_than(self.ctx.settings.retention_days)
        log.info(
            "retention: purged %d rows older than %d days", n, self.ctx.settings.retention_days
        )

    # -- introspection -----------------------------------------------------

    def status(self) -> Status:
        return Status(
            fixture_mode=self.ctx.fixture_mode,
            sources={name: st.to_status() for name, st in self.state.items()},
        )
