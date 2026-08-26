import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from mataelang.config import Settings
from mataelang.db import Database
from mataelang.fetchers import FetchContext
from mataelang.models import MEvent


def make_event(**kw) -> MEvent:
    base = dict(
        id="test:1",
        type="quake",
        ts=datetime.now(UTC) - timedelta(minutes=5),
        lat=-6.2,
        lon=106.8,
        severity=2,
        title="test event",
        source="TEST",
        ttl=3600,
    )
    base.update(kw)
    return MEvent(**base)


@pytest.fixture
async def db():
    d = Database(":memory:")
    await d.connect()
    yield d
    await d.close()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(fixture_mode=True, db_path=tmp_path / "t.sqlite", _env_file=None)


@pytest.fixture
async def ctx(settings):
    async with httpx.AsyncClient() as http:
        yield FetchContext(
            http=http, log=logging.getLogger("test"), settings=settings, fixture_mode=True
        )
