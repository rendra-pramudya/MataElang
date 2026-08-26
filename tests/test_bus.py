import json
from datetime import UTC, datetime, timedelta

from mataelang.bus import Bus

from .conftest import make_event


class FakeClient:
    def __init__(self, fail=False):
        self.msgs = []
        self.fail = fail

    async def send_text(self, data):
        if self.fail:
            raise RuntimeError("socket closed")
        self.msgs.append(json.loads(data))


async def test_dedupe_same_id_updates(db):
    bus = Bus(db)
    c = FakeClient()
    await bus.connect(c)
    assert c.msgs[0]["op"] == "snapshot" and c.msgs[0]["events"] == []

    await bus.upsert([make_event(id="usgs:a", title="v1")])
    await bus.upsert([make_event(id="usgs:a", title="v2")])
    assert bus.live_count == 1
    assert bus.snapshot()[0].title == "v2"
    assert await db.count() == 1
    ups = [m for m in c.msgs if m["op"] == "upsert"]
    assert len(ups) == 2  # clients still get both upserts; marker count stays stable


async def test_expired_not_accepted_but_persisted(db):
    bus = Bus(db)
    old = make_event(id="t:old", ts=datetime.now(UTC) - timedelta(hours=2), ttl=60)
    accepted = await bus.upsert([old])
    assert accepted == []
    assert bus.live_count == 0
    assert await db.count() == 1


async def test_sweep_broadcasts_expire(db):
    bus = Bus(db)
    c = FakeClient()
    await bus.connect(c)
    await bus.upsert([make_event(id="t:short", ts=datetime.now(UTC), ttl=30)])
    dead = await bus.sweep(now=datetime.now(UTC) + timedelta(seconds=31))
    assert dead == ["t:short"]
    assert bus.live_count == 0
    assert c.msgs[-1] == {"op": "expire", "ids": ["t:short"]}


async def test_dead_client_dropped(db):
    bus = Bus(db)
    good, bad = FakeClient(), FakeClient(fail=True)
    await bus.connect(good)
    bus._clients.add(bad)
    await bus.upsert([make_event()])
    assert bus.client_count == 1
    assert good.msgs[-1]["op"] == "upsert"


async def test_snapshot_since_and_replay(db):
    bus = Bus(db)
    now = datetime.now(UTC)
    await bus.upsert(
        [
            make_event(id="t:1", ts=now - timedelta(minutes=10)),
            make_event(id="t:2", ts=now - timedelta(minutes=1)),
        ]
    )
    assert [e.id for e in bus.snapshot(since=now - timedelta(minutes=5))] == ["t:2"]

    bus2 = Bus(db)
    assert await bus2.load_from_db() == 2
    assert bus2.live_count == 2
