# MataElang — CLAUDE.md

Repo-root guardrail. Read this before touching anything. If a task conflicts with this file, stop and ask.

## What this is

MataElang ("Eagle Eye") is a **single-user, self-hosted world monitor**: a dark-themed MapLibre map with live event layers (news, conflict, quakes, weather, ships, aircraft, markets, elections) fed by free/open APIs only, with a Python backend built to grow into spatial inference. It runs on Adi's own hardware for Adi's own situational awareness.

It is **not** an on-air graphics product, not multi-tenant, not a commercial OSINT tool, and not connected to Viz Engine. Keep it that way unless explicitly told otherwise.

## Non-negotiables

1. **Free/open data only.** No paid feeds, no scraped paywalls, no keys that cost money. Free API keys live in `.env`, never in code.
2. **Local basemap.** Planet-scale OSM vector tiles (PMTiles) served from local disk. Never point MapLibre at `tile.openstreetmap.org` — it violates OSM's tile usage policy.
3. **Read-only monitor.** MataElang observes. It never trades, never posts, never sends messages, never triggers external systems. Alerts (Phase 3) are notifications to Adi only.
4. **Human approval gate.** Any auto-generated change to source configs, thresholds, filters or boundary overrides is written as a *proposal* and applied only after Adi approves. Never auto-apply.
5. **"Military movement" is inference, not fact.** Layers derived from ADS-B/AIS/GDELT are labelled as proxies in the UI. Never present a heuristic as confirmed troop movement.
6. **Respect source terms.** Honour rate limits and attribution (OSM, USGS, GDELT, AIS/ADS-B providers). Cache aggressively; poll politely.

## Stack (fixed)

| Layer | Choice |
|---|---|
| Runtime | Python 3.12+, `uv` for env and deps |
| Server | FastAPI + uvicorn (native WebSocket), fully async |
| Scheduling | APScheduler (AsyncIOScheduler), one job per fetcher |
| HTTP client | httpx async, shared client with timeouts |
| DB | SQLite via `aiosqlite`, WAL mode |
| Validation | Pydantic v2 for the event contract |
| Tiles | `planet.pmtiles` served by Caddy (or `pmtiles serve`) — **not** by uvicorn |
| Frontend | Vanilla ESM, MapLibre GL JS, no framework, no build step |
| Analysis (Phase 2+) | GeoPandas, Shapely, H3, pandas |
| Style | Dark broadcast look. Teal `#00B4CC`, near-black `#111316`, alert orange `#F58220` |

Do not add Django, Celery, Redis, a message queue, or a JS build toolchain without a stated reason. Sync `requests` is banned inside fetchers — everything is async.

## Repo layout

```
mataelang/
├── CLAUDE.md
├── pyproject.toml                 # uv-managed
├── Caddyfile                      # serves /tiles with range support, proxies / to uvicorn
├── docs/phase-0-spine.md          # build brief + acceptance test
├── mataelang/
│   ├── __init__.py
│   ├── main.py                    # FastAPI app, lifespan, WS endpoint
│   ├── config.py                  # pydantic-settings, reads .env
│   ├── models.py                  # MEvent + WS message models
│   ├── bus.py                     # in-memory store → WS fanout
│   ├── db.py                      # aiosqlite schema + upsert/query
│   ├── scheduler.py               # registers fetchers with APScheduler
│   ├── fetchers/
│   │   ├── base.py                # Fetcher protocol + FetchContext
│   │   ├── usgs.py
│   │   ├── gdelt.py
│   │   ├── openmeteo.py           # Phase 1
│   │   ├── ais.py                 # Phase 1
│   │   ├── adsb.py                # Phase 1
│   │   ├── markets.py             # Phase 1
│   │   ├── rss.py                 # Phase 1
│   │   └── _fixtures/             # <name>.json per fetcher
│   ├── analysis/                  # Phase 2, GeoPandas/H3 lives here
│   └── proposals/                 # Phase 2, human-approval gate
├── web/
│   ├── index.html
│   ├── app.js                     # WS client, layer registry
│   ├── style.css
│   ├── map/style.json             # MapLibre style (dark)
│   └── layers/                    # one module per event type
├── data/
│   ├── tiles/planet.pmtiles       # gitignored, ~120 GB
│   ├── boundaries/                # GeoJSON overrides (editorial borders)
│   └── mataelang.sqlite           # gitignored
├── tests/
└── .env.example
```

## The one contract that matters

Every fetcher yields **normalised events**. Nothing else crosses the bus.

```python
class MEvent(BaseModel):
    id: str  # "<source>:<native-id>", stable across polls
    type: Literal[
        "quake", "weather", "conflict", "news", "ship", "aircraft", "market", "election", "notam"
    ]
    ts: datetime  # UTC, when it happened, not when fetched
    lat: float
    lon: float
    severity: int  # 0–5, source-normalised, documented per fetcher
    title: str  # one line, ≤120 chars
    source: str  # "USGS", "GDELT" … shown in UI
    url: str | None = None
    ttl: int  # seconds; client drops after ts + ttl
    payload: dict = {}  # source-specific, opaque to shared code
```

Full spec in `docs/phase-0-spine.md`. Change the contract there first, then in `models.py`, then everywhere.

## Working conventions

- `uv run` for everything. `ruff` for lint+format, `pytest` + `pytest-asyncio` for tests. Type hints everywhere; `mypy --strict` on `models.py` and `bus.py` at minimum.
- Every fetcher ships with a fixture in `fetchers/_fixtures/<name>.json` so the UI can be developed offline.
- Logging via stdlib `logging`, JSON lines to stdout. No structlog/loguru in Phase 0.
- Small commits, one fetcher or one layer per change.
- Comments explain *why*, not *what*.
- When unsure about a source's current terms or endpoint, say so and check — several ADS-B/AIS feeds change access rules without notice. Note the check date in the fetcher docstring.

## Phases

- **Phase 0 — Spine.** Map renders from local PMTiles; USGS + GDELT flow end-to-end through the contract. Acceptance test in the spine doc.
- **Phase 1 — Layers.** Weather, AIS, ADS-B, RSS, markets. Heat map for conflict density (H3). Time-decay.
- **Phase 2 — Inference & gate.** Military-proxy filters (callsigns, hex ranges, dark-ship detection), boundary overrides, proposal/approval workflow. GeoPandas enters here.
- **Phase 3 — Alerts.** Telegram push on threshold rules. Still read-only.
- **Phase 4 — Elections & Claude.** Per-event election adapters; Claude API contextual summaries of what's on screen.

Do not start a phase before the previous one's acceptance test passes.
