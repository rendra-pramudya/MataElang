"""The one contract that matters.

Every fetcher yields ``MEvent``. Nothing else crosses the bus. Change the spec in
``docs/phase-0-spine.md`` first, then here, then everywhere else.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

EventType = Literal[
    "quake",
    "weather",
    "conflict",
    "news",
    "ship",
    "aircraft",
    "market",
    "election",
    "notam",
]


class MEvent(BaseModel):
    id: str = Field(min_length=3, description='"<source>:<native-id>", stable across polls')
    type: EventType
    ts: datetime = Field(description="tz-aware UTC; when it happened, not when fetched")
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    severity: int = Field(ge=0, le=5)
    title: str = Field(max_length=120)
    source: str
    url: str | None = None
    ttl: int = Field(gt=0, description="seconds; client drops after ts + ttl")
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("ts")
    @classmethod
    def _require_tz(cls, v: datetime) -> datetime:
        # A naive timestamp is a bug in a fetcher, not bad data — refuse it loudly.
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("ts must be timezone-aware")
        return v.astimezone(UTC)

    @field_validator("id")
    @classmethod
    def _require_prefix(cls, v: str) -> str:
        if ":" not in v:
            raise ValueError('id must be "<source>:<native-id>"')
        return v

    @property
    def expires_at(self) -> datetime:
        return self.ts + timedelta(seconds=self.ttl)

    def is_expired(self, now: datetime | None = None) -> bool:
        return self.expires_at <= (now or datetime.now(UTC))


# ---------------------------------------------------------------------------
# WebSocket protocol, discriminated on "op"
# ---------------------------------------------------------------------------


class Hello(BaseModel):
    op: Literal["hello"] = "hello"
    since: datetime | None = None


class Pong(BaseModel):
    op: Literal["pong"] = "pong"


ClientMessage = Annotated[Hello | Pong, Field(discriminator="op")]


class Snapshot(BaseModel):
    op: Literal["snapshot"] = "snapshot"
    events: list[MEvent]


class Upsert(BaseModel):
    op: Literal["upsert"] = "upsert"
    events: list[MEvent]


class Expire(BaseModel):
    op: Literal["expire"] = "expire"
    ids: list[str]


class SourceStatus(BaseModel):
    last_ok: datetime | None = None
    last_error: str | None = None
    count: int = 0


class Status(BaseModel):
    op: Literal["status"] = "status"
    fixture_mode: bool = False
    sources: dict[str, SourceStatus]


class Ping(BaseModel):
    op: Literal["ping"] = "ping"


ServerMessage = Annotated[Snapshot | Upsert | Expire | Status | Ping, Field(discriminator="op")]
