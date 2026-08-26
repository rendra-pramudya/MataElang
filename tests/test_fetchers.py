import io
import zipfile
from datetime import UTC, datetime

import pytest

from mataelang.fetchers import ALL, GdeltFetcher, UsgsFetcher, gdelt, usgs
from mataelang.fetchers.base import Fetcher, load_fixture


@pytest.mark.parametrize("fetcher", ALL, ids=lambda f: f.name)
async def test_fixture_mode_yields_valid_events(fetcher, ctx):
    assert isinstance(fetcher, Fetcher)
    events = [e async for e in fetcher.fetch(ctx)]
    assert events, f"{fetcher.name} fixture is empty"
    for e in events:
        assert e.id.startswith(fetcher.name + ":")
        assert e.ts.tzinfo is not None
    # fixtures use ts_offset_s, so nothing should be stale at load time
    assert all(not e.is_expired() for e in events)


def test_fixture_offsets_are_relative():
    a = load_fixture("usgs", now=datetime(2030, 1, 1, tzinfo=UTC))
    assert a[0].ts.year == 2029 or a[0].ts.year == 2030


@pytest.mark.parametrize(
    "mag,tsunami,sev",
    [
        (1.2, 0, 0),
        (3.0, 0, 1),
        (4.5, 0, 2),
        (5.9, 0, 3),
        (6.0, 0, 4),
        (7.1, 0, 5),
        (4.0, 1, 5),
        (None, 0, 0),
    ],
)
def test_usgs_severity(mag, tsunami, sev):
    assert usgs.severity_for(mag, tsunami) == sev


def test_usgs_parse_feature_and_drop():
    feat = {
        "type": "Feature",
        "id": "us7000abcd",
        "properties": {
            "mag": 5.2,
            "place": "10 km S of X",
            "time": 1700000000000,
            "url": "https://u",
            "tsunami": 0,
            "sig": 400,
        },
        "geometry": {"type": "Point", "coordinates": [106.8, -6.2, 12.5]},
    }
    e = usgs.parse_feature(feat, 100)
    assert e.id == "usgs:us7000abcd" and e.lat == -6.2 and e.lon == 106.8
    assert e.severity == 3 and e.title.startswith("M5.2 —") and e.payload["depth_km"] == 12.5
    assert e.ts == datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)
    assert usgs.parse_feature({**feat, "geometry": None}, 100) is None
    assert UsgsFetcher().schedule


@pytest.mark.parametrize(
    "g,sev",
    [
        (2.0, 0),
        (0.0, 0),
        (-1.0, 1),
        (-2.9, 1),
        (-3.0, 2),
        (-4.9, 2),
        (-5.0, 3),
        (-7.0, 4),
        (-9.0, 5),
        (-10, 5),
    ],
)
def test_gdelt_severity(g, sev):
    assert gdelt.severity_for(g) == sev


def _row(**over):
    cols = [""] * gdelt.N_COLS
    cols[gdelt.COL_GLOBALEVENTID] = "1234567890"
    cols[gdelt.COL_SQLDATE] = "20260826"
    cols[gdelt.COL_ACTOR1_NAME] = "MILITARY"
    cols[gdelt.COL_ACTOR2_NAME] = "CIVILIAN"
    cols[gdelt.COL_EVENT_CODE] = "193"
    cols[gdelt.COL_EVENT_ROOT] = "19"
    cols[gdelt.COL_QUADCLASS] = "4"
    cols[gdelt.COL_GOLDSTEIN] = "-10.0"
    cols[gdelt.COL_NUM_MENTIONS] = "7"
    cols[gdelt.COL_ACTION_GEO_TYPE] = "4"
    cols[gdelt.COL_ACTION_GEO_NAME] = "Dnipro, Ukraine"
    cols[gdelt.COL_ACTION_GEO_COUNTRY] = "UP"
    cols[gdelt.COL_ACTION_GEO_LAT] = "48.45"
    cols[gdelt.COL_ACTION_GEO_LON] = "35.05"
    cols[gdelt.COL_DATEADDED] = "20260826101500"
    cols[gdelt.COL_SOURCEURL] = "https://example.org/a"
    for k, v in over.items():
        cols[getattr(gdelt, k)] = v
    return cols


def test_gdelt_parse_row():
    e = gdelt.parse_row(_row(), 60)
    assert e.id == "gdelt:1234567890" and e.type == "conflict" and e.severity == 5
    assert e.title == "Fight: Military → Civilian (Dnipro, Ukraine)"
    assert e.ts == datetime(2026, 8, 26, 10, 15, tzinfo=UTC)
    assert e.payload["goldstein"] == -10.0 and e.payload["num_mentions"] == 7
    assert gdelt.parse_row(_row(COL_ACTION_GEO_LAT=""), 60) is None
    assert gdelt.parse_row(_row(COL_ACTION_GEO_LAT="0", COL_ACTION_GEO_LON="0"), 60) is None


def test_gdelt_parse_export_filters_and_caps():
    rows = [
        "\t".join(_row()),
        "\t".join(_row(COL_GLOBALEVENTID="2", COL_QUADCLASS="1")),  # cooperation → out
        "\t".join(_row(COL_GLOBALEVENTID="3", COL_GOLDSTEIN="-1.0")),  # above threshold → out
        "\t".join(_row(COL_GLOBALEVENTID="4", COL_NUM_MENTIONS="99")),  # kept, ranks first
        "\t".join(_row(COL_GLOBALEVENTID="5", COL_ACTION_GEO_LAT="")),  # no coords → out
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("20260826101500.export.CSV", "\n".join(rows) + "\n")
    events, stats = gdelt.parse_export(buf.getvalue(), min_goldstein=-3, max_events=1, ttl=60)
    assert stats == {"rows": 5, "conflict": 4, "kept": 1}
    assert [e.id for e in events] == ["gdelt:4"]


def test_gdelt_pick_export_url():
    txt = (
        "123 abc http://data.gdeltproject.org/gdeltv2/20260826101500.export.CSV.zip\n"
        "456 def http://data.gdeltproject.org/gdeltv2/20260826101500.mentions.CSV.zip\n"
    )
    assert gdelt.pick_export_url(txt).endswith("101500.export.CSV.zip")
    assert GdeltFetcher().min_interval_s >= 60
