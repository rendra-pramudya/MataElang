from datetime import UTC, datetime

import pytest

from mataelang.fetchers import openmeteo
from mataelang.fetchers.openmeteo import OpenMeteoFetcher, WatchPoint


def test_parse_points_and_slug():
    pts = openmeteo.parse_points("Jakarta,-6.21,106.85; Banda Aceh,5.55,95.32")
    assert [p.name for p in pts] == ["Jakarta", "Banda Aceh"]
    assert pts[1].slug == "banda-aceh"


@pytest.mark.parametrize(
    "raw",
    [
        "Nowhere,not-a-number,1",  # unparseable
        "TooFew,1",  # wrong arity
        "OffGlobe,91,0",  # out of range latitude
        "AlsoOff,0,181",  # out of range longitude
        "   ",  # blank
    ],
)
def test_parse_points_skips_malformed(raw):
    # A typo in .env should cost one watch point, not the whole layer.
    assert openmeteo.parse_points(f"Jakarta,-6.21,106.85;{raw}") == [
        WatchPoint("Jakarta", -6.21, 106.85)
    ]


# Acceptance test §7.2 — the exact Beaufort/Saffir-Simpson boundaries from phase-1 §3.3.
@pytest.mark.parametrize(
    "gust,sev",
    [
        (0, 0),
        (38.9, 0),
        (39, 1),
        (61.9, 1),
        (62, 2),
        (88.9, 2),
        (89, 3),
        (117.9, 3),
        (118, 4),
        (154.9, 4),
        (155, 5),
        (300, 5),
        (None, 0),
    ],
)
def test_gust_severity_boundaries(gust, sev):
    assert openmeteo.severity_for_gust(gust) == sev


@pytest.mark.parametrize(
    "mm,sev",
    [
        (0, 0),
        (3.9, 0),
        (4, 1),
        (9.9, 1),
        (10, 2),
        (19.9, 2),
        (20, 3),
        (39.9, 3),
        (40, 4),
        (59.9, 4),
        (60, 5),
        (None, 0),
    ],
)
def test_precip_severity_boundaries(mm, sev):
    assert openmeteo.severity_for_precip(mm) == sev


@pytest.mark.parametrize(
    "code,sev",
    [
        (0, 0),
        (3, 0),
        (45, 1),
        (55, 1),
        (65, 2),
        (75, 2),
        (82, 3),
        (86, 3),
        (95, 4),
        (99, 5),
        (None, 0),
    ],
)
def test_code_severity(code, sev):
    assert openmeteo.severity_for_code(code) == sev


def test_severity_is_max_of_three_rows():
    assert openmeteo.severity_for(40, 0, 0) == 1  # gust alone
    assert openmeteo.severity_for(0, 45, 0) == 4  # precip alone
    assert openmeteo.severity_for(40, 5, 99) == 5  # code wins


def _result(**current):
    base = {
        "time": "2026-08-27T04:00",
        "temperature_2m": 25.4,
        "relative_humidity_2m": 94,
        "precipitation": 44.0,
        "weather_code": 95,
        "wind_gusts_10m": 121.0,
    }
    base.update(current)
    return {"latitude": -6.2, "longitude": 106.8, "utc_offset_seconds": 0, "current": base}


def test_parse_current_builds_event():
    p = WatchPoint("Jakarta", -6.21, 106.85)
    ev = openmeteo.parse_current(_result(), p, 3600)
    assert ev.id == "openmeteo:jakarta" and ev.type == "weather"
    assert ev.severity == 4  # gust 121 → 4, precip 44 → 4, code 95 → 4
    assert ev.ts == datetime(2026, 8, 27, 4, 0, tzinfo=UTC)
    # The marker sits on the configured point, not the grid cell Open-Meteo snapped to.
    assert (ev.lat, ev.lon) == (-6.21, 106.85)
    assert ev.payload["grid_lat"] == -6.2
    assert "gusts 121 km/h" in ev.title and "thunderstorm" in ev.title


def test_parse_current_drops_calm_points():
    # A quiet watch point is not an event — see the module docstring.
    calm = _result(precipitation=0.0, weather_code=1, wind_gusts_10m=8.0)
    assert openmeteo.parse_current(calm, WatchPoint("Bern", 46.95, 7.45), 3600) is None


def test_parse_current_needs_a_timestamp():
    r = _result()
    del r["current"]["time"]
    assert openmeteo.parse_current(r, WatchPoint("X", 0, 1), 3600) is None


def test_parse_current_honours_utc_offset():
    r = _result(time="2026-08-27T11:00")
    r["utc_offset_seconds"] = 25200  # UTC+7
    ev = openmeteo.parse_current(r, WatchPoint("Jakarta", -6.21, 106.85), 3600)
    assert ev.ts == datetime(2026, 8, 27, 4, 0, tzinfo=UTC)


def test_normalise_results_handles_both_shapes():
    assert len(openmeteo.normalise_results({"a": 1})) == 1
    assert len(openmeteo.normalise_results([{"a": 1}, {"b": 2}])) == 2
    assert openmeteo.normalise_results("nonsense") == []


def test_fetcher_metadata():
    f = OpenMeteoFetcher()
    assert f.name == "openmeteo" and f.min_interval_s >= 60 and f.ttl_default == 3600
