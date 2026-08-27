import json

from fastapi.testclient import TestClient

from mataelang.main import create_app


def test_end_to_end_fixture_mode(settings):
    app = create_app(settings, start_scheduler=False)
    with TestClient(app) as c:
        assert c.get("/healthz").json()["ok"] is True

        # manual trigger = the scheduler path, minus the cron
        r = c.post("/api/fetch/usgs")
        assert r.status_code == 200 and r.json()["accepted"] >= 3
        n1 = len(c.get("/api/events?type=quake").json()["events"])
        c.post("/api/fetch/usgs")
        n2 = len(c.get("/api/events?type=quake").json()["events"])
        assert n1 == n2  # dedupe: same ids, same row count

        st = c.get("/api/status").json()
        assert st["fixture_mode"] is True and st["sources"]["usgs"]["last_error"] is None

        with c.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"op": "hello"}))
            snap = json.loads(ws.receive_text())
            assert snap["op"] == "snapshot" and len(snap["events"]) == n1
            status = json.loads(ws.receive_text())
            assert status["op"] == "status"

            c.post("/api/fetch/gdelt")
            up = json.loads(ws.receive_text())
            assert up["op"] == "upsert" and up["events"][0]["type"] == "conflict"

        assert c.post("/api/fetch/nope").status_code == 404
        assert c.get("/").status_code == 200


def test_heat_endpoint(settings):
    app = create_app(settings, start_scheduler=False)
    with TestClient(app) as c:
        c.post("/api/fetch/gdelt")
        events = c.get("/api/events?type=conflict").json()["events"]
        assert events

        body = c.get("/api/heat?type=conflict").json()
        assert body["resolution"] == settings.heat_default_resolution
        # Acceptance §7.6: every event in range is accounted for by some cell.
        assert sum(cell["count"] for cell in body["cells"]) == len(events)
        assert body["cells"] == sorted(body["cells"], key=lambda c: -c["weight"])
        first = body["cells"][0]
        assert {"h3", "lat", "lon", "count", "weight", "max_severity"} <= set(first)

        # Resolution is clamped, never trusted from the query string.
        assert c.get("/api/heat?res=99").json()["resolution"] == settings.heat_max_resolution
        assert c.get("/api/heat?res=-4").json()["resolution"] == 0

        # A type with no rows is an empty heat map, not an error.
        assert c.get("/api/heat?type=market").json()["cells"] == []
        assert c.get("/api/heat?bbox=bad").status_code == 400


def test_new_sources_reach_the_map(settings):
    app = create_app(settings, start_scheduler=False)
    with TestClient(app) as c:
        assert c.post("/api/fetch/openmeteo").json()["accepted"] >= 1
        assert c.post("/api/fetch/rss").json()["accepted"] >= 1

        weather = c.get("/api/events?type=weather").json()["events"]
        news = c.get("/api/events?type=news").json()["events"]
        assert weather and news
        # Geocoding is inference and must stay labelled all the way to the client.
        assert all(e["payload"]["geocode"]["confidence"] in ("high", "low") for e in news)
        # Nothing is ever parked at null island.
        assert not [e for e in news if e["lat"] == 0 and e["lon"] == 0]


def test_dead_source_keeps_server_up(settings):
    settings.fixture_mode = False
    settings.usgs_feed = "http://127.0.0.1:9/nope.geojson"  # refused connection
    app = create_app(settings, start_scheduler=False)
    with TestClient(app) as c:
        assert c.post("/api/fetch/usgs").json()["accepted"] == 0
        st = c.get("/api/status").json()["sources"]["usgs"]
        assert st["last_error"] and st["last_ok"] is None
        assert c.get("/healthz").status_code == 200
