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


def test_dead_source_keeps_server_up(settings):
    settings.fixture_mode = False
    settings.usgs_feed = "http://127.0.0.1:9/nope.geojson"  # refused connection
    app = create_app(settings, start_scheduler=False)
    with TestClient(app) as c:
        assert c.post("/api/fetch/usgs").json()["accepted"] == 0
        st = c.get("/api/status").json()["sources"]["usgs"]
        assert st["last_error"] and st["last_ok"] is None
        assert c.get("/healthz").status_code == 200
