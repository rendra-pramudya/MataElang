from datetime import UTC, datetime, timedelta

from .conftest import make_event


async def test_upsert_idempotent(db):
    e = make_event(id="usgs:x")
    await db.upsert_many([e])
    await db.upsert_many([e])
    await db.upsert_many([make_event(id="usgs:x", severity=5)])
    assert await db.count() == 1
    rows = await db.query(type_="quake")
    assert rows[0].severity == 5 and rows[0].payload == {}


async def test_load_live_respects_ttl(db):
    now = datetime.now(UTC)
    await db.upsert_many(
        [
            make_event(id="t:live", ts=now - timedelta(minutes=1), ttl=3600),
            make_event(id="t:dead", ts=now - timedelta(hours=2), ttl=60),
        ]
    )
    live = await db.load_live(now)
    assert [e.id for e in live] == ["t:live"]


async def test_query_filters(db):
    now = datetime.now(UTC)
    await db.upsert_many(
        [
            make_event(id="a:1", type="quake", lat=0, lon=0, ts=now - timedelta(days=2)),
            make_event(id="b:2", type="conflict", lat=50, lon=10, ts=now),
        ]
    )
    assert [e.id for e in await db.query(type_="conflict")] == ["b:2"]
    assert [e.id for e in await db.query(since=now - timedelta(days=1))] == ["b:2"]
    assert [e.id for e in await db.query(bbox=(-1, -1, 1, 1))] == ["a:1"]


async def test_purge(db):
    now = datetime.now(UTC)
    await db.upsert_many(
        [
            make_event(id="a:old", ts=now - timedelta(days=100)),
            make_event(id="a:new", ts=now),
        ]
    )
    assert await db.purge_older_than(90) == 1
    assert await db.count() == 1
