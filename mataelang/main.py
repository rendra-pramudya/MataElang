"""FastAPI app. Lifespan owns the shared httpx client, SQLite, bus and scheduler.

Routes (spine §4.4):
    GET  /                    static web/
    GET  /api/events          history query
    GET  /api/status          same shape as WS status
    GET  /api/boundaries      list of GeoJSON overrides in data/boundaries
    POST /api/fetch/{name}    manual trigger (used by the dedupe acceptance test)
    GET  /healthz
    WS   /ws
    /tiles/*                  Caddy — never reaches here
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import TypeAdapter, ValidationError

from .analysis import bin_events
from .analysis.heat import clamp_resolution
from .bus import Bus
from .config import Settings, load_settings
from .db import Database
from .fetchers import ALL as FETCHERS
from .fetchers import FetchContext
from .models import ClientMessage, Hello, Ping, Pong
from .scheduler import Scheduler

log = logging.getLogger("mataelang")
_client_msg = TypeAdapter(ClientMessage)


class JsonLineFormatter(logging.Formatter):
    """One JSON object per line on stdout. No structlog in Phase 0."""

    def format(self, record: logging.LogRecord) -> str:
        obj: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        return json.dumps(obj, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if any(isinstance(h.formatter, JsonLineFormatter) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLineFormatter())
    root.handlers[:] = [handler]
    root.setLevel(level)
    # uvicorn installs its own handlers before importing the app; fold them into the JSON stream.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers[:] = [handler]
        lg.propagate = False
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _parse_bbox(bbox: str | None) -> tuple[float, float, float, float] | None:
    if not bbox:
        return None
    try:
        w, s, e, n = (float(x) for x in bbox.split(","))
    except ValueError as exc:
        raise HTTPException(400, "bbox must be west,south,east,north") from exc
    return (w, s, e, n)


def create_app(settings: Settings | None = None, *, start_scheduler: bool = True) -> FastAPI:
    settings = settings or load_settings()
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        http = httpx.AsyncClient(
            headers={"User-Agent": settings.user_agent},
            timeout=httpx.Timeout(settings.http_timeout_s),
            follow_redirects=True,
        )
        db = Database(settings.db_path)
        await db.connect()
        bus = Bus(db)
        n = await bus.load_from_db()
        ctx = FetchContext(
            http=http,
            log=logging.getLogger("mataelang.fetch"),
            settings=settings,
            fixture_mode=settings.fixture_mode,
        )
        scheduler = Scheduler(bus=bus, db=db, ctx=ctx, fetchers=FETCHERS)
        app.state.settings = settings
        app.state.db = db
        app.state.bus = bus
        app.state.scheduler = scheduler
        log.info(
            "mataelang up: fixture_mode=%s replayed=%d db=%s",
            settings.fixture_mode,
            n,
            settings.db_path,
        )
        if start_scheduler:
            scheduler.start()
        try:
            yield
        finally:
            scheduler.shutdown()
            await http.aclose()
            await db.close()

    app = FastAPI(title="MataElang", version="0.1.0", lifespan=lifespan)

    # -- HTTP --------------------------------------------------------------

    @app.get("/healthz")
    async def healthz(request: Request) -> JSONResponse:
        bus: Bus | None = getattr(request.app.state, "bus", None)
        if bus is None:
            return JSONResponse({"ok": False}, status_code=503)
        return JSONResponse({"ok": True, "live": bus.live_count, "clients": bus.client_count})

    @app.get("/api/status")
    async def api_status(request: Request) -> JSONResponse:
        sched: Scheduler = request.app.state.scheduler
        return JSONResponse(sched.status().model_dump(mode="json"))

    @app.get("/api/events")
    async def api_events(
        request: Request,
        type: str | None = None,  # noqa: A002 — matches the spec's query name
        since: datetime | None = None,
        bbox: str | None = Query(default=None, description="west,south,east,north"),
        limit: int = Query(default=1000, ge=1, le=10000),
    ) -> JSONResponse:
        box = _parse_bbox(bbox)
        db: Database = request.app.state.db
        events = await db.query(type_=type, since=since, bbox=box, limit=limit)
        return JSONResponse({"events": [e.model_dump(mode="json") for e in events]})

    @app.get("/api/heat")
    async def api_heat(
        request: Request,
        type: str = "conflict",  # noqa: A002 — matches /api/events' query name
        res: int | None = None,
        since: datetime | None = None,
        bbox: str | None = Query(default=None, description="west,south,east,north"),
    ) -> JSONResponse:
        """H3-binned density (phase-1 §5.2). Aggregates reporting, not ground truth."""
        cfg: Settings = request.app.state.settings
        box = _parse_bbox(bbox)
        resolution = clamp_resolution(
            cfg.heat_default_resolution if res is None else res, cfg.heat_max_resolution
        )
        db: Database = request.app.state.db
        events = await db.query(type_=type, since=since, bbox=box, limit=cfg.heat_query_limit)
        cells = await asyncio.to_thread(bin_events, events, resolution)
        return JSONResponse(
            {
                "type": type,
                "resolution": resolution,
                "events": len(events),
                "cells": [c.to_dict() for c in cells],
            }
        )

    @app.get("/api/boundaries")
    async def api_boundaries(request: Request) -> JSONResponse:
        d = request.app.state.settings.boundaries_dir
        files = sorted(p.name for p in d.glob("*.geojson")) if d.exists() else []
        return JSONResponse({"files": files})

    @app.post("/api/fetch/{name}")
    async def api_fetch(request: Request, name: str) -> JSONResponse:
        sched: Scheduler = request.app.state.scheduler
        if name not in sched.fetchers:
            raise HTTPException(404, f"unknown fetcher {name!r}")
        events = await sched.run_fetcher(name, force=True)
        return JSONResponse({"fetcher": name, "accepted": len(events)})

    # -- WebSocket ---------------------------------------------------------

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        bus: Bus = ws.app.state.bus
        sched: Scheduler = ws.app.state.scheduler
        settings_: Settings = ws.app.state.settings
        await ws.accept()

        # Wait for hello (with optional `since`), then reply with a snapshot.
        hello = Hello()
        try:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=10)
            msg = _client_msg.validate_json(raw)
            if isinstance(msg, Hello):
                hello = msg
        except (TimeoutError, ValidationError):
            pass  # a silent or malformed client still gets the full snapshot
        except WebSocketDisconnect:
            return

        await bus.connect(ws, hello.since)
        await ws.send_text(sched.status().model_dump_json())

        missed = 0
        ping_json = Ping().model_dump_json()

        async def pinger() -> None:
            nonlocal missed
            while True:
                await asyncio.sleep(settings_.ws_ping_interval_s)
                if missed >= 2:
                    await ws.close(code=1001)
                    return
                missed += 1
                await ws.send_text(ping_json)

        ping_task = asyncio.create_task(pinger())
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = _client_msg.validate_json(raw)
                except ValidationError:
                    continue
                if isinstance(msg, Pong):
                    missed = 0
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # noqa: BLE001 — never let a client kill the server
            log.info("ws client error: %s", exc)
        finally:
            ping_task.cancel()
            bus.disconnect(ws)

    # -- static (mounted last so /api and /ws win) ---------------------------

    if settings.boundaries_dir.exists():
        app.mount("/boundaries", StaticFiles(directory=settings.boundaries_dir), name="boundaries")
    app.mount("/", StaticFiles(directory=settings.web_dir, html=True), name="web")
    return app


app = create_app()
