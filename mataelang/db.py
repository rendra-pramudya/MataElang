"""SQLite persistence via aiosqlite. History is what makes the Phase 1 heat map possible,
so everything the bus sees is written here and kept for ``RETENTION_DAYS``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

from .models import MEvent

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, type TEXT, ts TEXT, lat REAL, lon REAL,
  severity INTEGER, title TEXT, source TEXT, url TEXT,
  ttl INTEGER, payload TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_type_ts ON events(type, ts);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
"""

UPSERT = """
INSERT INTO events (id, type, ts, lat, lon, severity, title, source, url, ttl, payload, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
  type=excluded.type, ts=excluded.ts, lat=excluded.lat, lon=excluded.lon,
  severity=excluded.severity, title=excluded.title, source=excluded.source,
  url=excluded.url, ttl=excluded.ttl, payload=excluded.payload, updated_at=excluded.updated_at
"""

COLUMNS = "id, type, ts, lat, lon, severity, title, source, url, ttl, payload"


def _iso(dt: datetime) -> str:
    # Fixed-width, second precision, explicit +00:00 so SQLite's datetime() can parse it
    # and lexical ordering equals chronological ordering.
    return dt.astimezone(UTC).replace(microsecond=0).isoformat()


def _row_to_event(row: aiosqlite.Row) -> MEvent:
    return MEvent(
        id=row["id"],
        type=row["type"],
        ts=datetime.fromisoformat(row["ts"]),
        lat=row["lat"],
        lon=row["lon"],
        severity=row["severity"],
        title=row["title"],
        source=row["source"],
        url=row["url"],
        ttl=row["ttl"],
        payload=json.loads(row["payload"] or "{}"),
    )


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        return self._conn

    async def connect(self) -> None:
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def upsert_many(self, events: list[MEvent]) -> None:
        if not events:
            return
        now = _iso(datetime.now(UTC))
        rows = [
            (
                e.id,
                e.type,
                _iso(e.ts),
                e.lat,
                e.lon,
                e.severity,
                e.title,
                e.source,
                e.url,
                e.ttl,
                json.dumps(e.payload, separators=(",", ":"), default=str),
                now,
            )
            for e in events
        ]
        await self.conn.executemany(UPSERT, rows)
        await self.conn.commit()

    async def load_live(self, now: datetime | None = None) -> list[MEvent]:
        """Everything whose ts + ttl is still in the future — the replay-on-connect set."""
        now_s = _iso(now or datetime.now(UTC))
        cur = await self.conn.execute(
            f"SELECT {COLUMNS} FROM events "
            "WHERE datetime(ts, '+' || ttl || ' seconds') > datetime(?) ORDER BY ts",
            (now_s,),
        )
        return [_row_to_event(r) for r in await cur.fetchall()]

    async def query(
        self,
        *,
        type_: str | None = None,
        since: datetime | None = None,
        bbox: tuple[float, float, float, float] | None = None,
        limit: int = 1000,
    ) -> list[MEvent]:
        clauses: list[str] = []
        args: list[Any] = []
        if type_:
            clauses.append("type = ?")
            args.append(type_)
        if since:
            clauses.append("ts >= ?")
            args.append(_iso(since))
        if bbox:
            w, s, e, n = bbox
            clauses.append("lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?")
            args.extend([w, e, s, n])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cur = await self.conn.execute(
            f"SELECT {COLUMNS} FROM events {where} ORDER BY ts DESC LIMIT ?",
            (*args, limit),
        )
        return [_row_to_event(r) for r in await cur.fetchall()]

    async def count(self) -> int:
        cur = await self.conn.execute("SELECT COUNT(*) AS n FROM events")
        row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def purge_older_than(self, days: int) -> int:
        cutoff = _iso(datetime.now(UTC) - timedelta(days=days))
        cur = await self.conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
        await self.conn.commit()
        return cur.rowcount
