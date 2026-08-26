from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from mataelang.models import MEvent, Status

from .conftest import make_event


def test_valid_event_roundtrip():
    e = make_event()
    assert MEvent.model_validate_json(e.model_dump_json()) == e


def test_naive_ts_rejected():
    with pytest.raises(ValidationError):
        make_event(ts=datetime(2026, 1, 1))


def test_ts_normalised_to_utc():
    from datetime import timezone

    e = make_event(ts=datetime(2026, 1, 1, 12, tzinfo=timezone(timedelta(hours=7))))
    assert e.ts == datetime(2026, 1, 1, 5, tzinfo=UTC)


@pytest.mark.parametrize("bad", [dict(lat=91), dict(lon=-181), dict(severity=6), dict(ttl=0)])
def test_range_validation(bad):
    with pytest.raises(ValidationError):
        make_event(**bad)


def test_title_max_120():
    with pytest.raises(ValidationError):
        make_event(title="x" * 121)


def test_id_needs_prefix():
    with pytest.raises(ValidationError):
        make_event(id="noprefix")


def test_unknown_type_rejected():
    with pytest.raises(ValidationError):
        make_event(type="ufo")


def test_expiry():
    now = datetime.now(UTC)
    e = make_event(ts=now - timedelta(seconds=100), ttl=50)
    assert e.is_expired(now)
    assert e.expires_at == e.ts + timedelta(seconds=50)
    assert not make_event(ts=now, ttl=50).is_expired(now)


def test_status_shape():
    s = Status(sources={"usgs": {"count": 3}})
    d = s.model_dump(mode="json")
    assert d["op"] == "status" and d["sources"]["usgs"]["last_error"] is None
