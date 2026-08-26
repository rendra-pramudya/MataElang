# MataElang — Phase 0: Spine (Python)

**Goal:** the thinnest possible vertical slice that proves the architecture. A local planet basemap on screen, two real sources (USGS quakes, GDELT events) flowing through the normalised contract to live map markers, with history persisted. Everything after this is "add another module to a working machine."

Nothing in Phase 0 is polished. It is a spine, not a body.

---

## 1. Scope

**In:**
- FastAPI app with one WebSocket endpoint, fully async
- Caddy in front: serves PMTiles with range requests, proxies everything else to uvicorn
- MapLibre dark style with OSM boundaries, coastlines, low-zoom roads, place labels
- Fetcher protocol + two fetchers (USGS, GDELT)
- Event bus → WS fanout → client layer registry
- SQLite persistence + replay-on-connect
- Fixture mode (run the whole UI with no network)

**Out (later phases):**
- AIS, ADS-B, weather, RSS, markets, elections
- Heat map, time-decay animation, clustering
- Proposals / approval gate
- Alerts
- Any `analysis/` code

---

## 2. Basemap

### 2.1 Getting the tiles

Phase 0 uses the Protomaps planet build. Download to `data/tiles/planet.pmtiles` (~120 GB). NVMe strongly preferred.

```bash
# check https://maps.protomaps.com/builds/ for the current daily file
wget -c -O data/tiles/planet.pmtiles <build-url>
```

A self-built Planetiler PMTiles drops in at the same path later with no code change.

### 2.2 Serving

uvicorn should never hand out 120 GB range requests. Caddy does it:

```
:8080 {
    handle_path /tiles/* {
        root * ./data/tiles
        file_server
    }
    handle {
        reverse_proxy localhost:8000
    }
}
```

Caddy's `file_server` supports `Range` natively. Alternative: `pmtiles serve data/tiles/` on its own port and point the style at it.

MapLibre uses the `pmtiles://` protocol via the `pmtiles` JS library:

```js
import { Protocol } from 'pmtiles';
maplibregl.addProtocol('pmtiles', new Protocol().tile);
// style.json source url: "pmtiles:///tiles/planet.pmtiles"
```

### 2.3 Style

`web/map/style.json`, Protomaps schema. Phase 0 layers, in draw order:

| Layer | Source layer | Rule |
|---|---|---|
| background | — | `#111316` |
| water | `water` | `#0a0d10` |
| land use | `landuse` | very low alpha, optional |
| roads | `roads` | motorway/trunk only below z8; more from z10 |
| admin boundaries | `boundaries` | country solid `#3a4048`, state dashed, from z2 |
| place labels | `places` | capitals from z2, cities from z5 |

Teal `#00B4CC` is reserved for UI and event markers, never for basemap features.

### 2.4 Boundary overrides

`data/boundaries/*.geojson` loaded as GeoJSON sources and drawn *above* the OSM boundary layer. Phase 0 ships an empty folder and the loader; the political decisions come later.

---

## 3. The event contract (canonical)

`mataelang/models.py`:

```python
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field

EventType = Literal[
    "quake", "weather", "conflict", "news", "ship", "aircraft", "market", "election", "notam"
]


class MEvent(BaseModel):
    id: str
    type: EventType
    ts: datetime  # tz-aware UTC
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    severity: int = Field(ge=0, le=5)
    title: str = Field(max_length=120)
    source: str
    url: str | None = None
    ttl: int = Field(gt=0)
    payload: dict = Field(default_factory=dict)

    @property
    def expires_at(self) -> datetime: ...
```

**Rules**
- Same `id` on a later poll = update, not duplicate. Bus dedupes on `id`; DB upserts.
- Events with no coordinates are dropped at the fetcher, never yielded. Pydantic will reject them anyway — the fetcher must filter first so validation errors mean bugs, not bad data.
- `severity` mapping is documented in each fetcher's module docstring.
- Fetchers never touch the DB or WS. They only yield.

### 3.1 Severity guide (Phase 0)

| Source | 0 | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|
| USGS | <M3 | M3–3.9 | M4–4.9 | M5–5.9 | M6–6.9 | ≥M7 or tsunami flag |
| GDELT | Goldstein ≥ 0 | −1 to −3 | −3 to −5 | −5 to −7 | −7 to −9 | ≤ −9 |

---

## 4. Server contracts

### 4.1 Fetcher protocol (`fetchers/base.py`)

```python
from dataclasses import dataclass
from typing import AsyncIterator, Protocol
import httpx, logging


@dataclass
class FetchContext:
    http: httpx.AsyncClient  # shared, UA + timeouts preset
    log: logging.Logger
    settings: "Settings"
    fixture_mode: bool


class Fetcher(Protocol):
    name: str  # matches id prefix
    schedule: str  # cron expression
    ttl_default: int
    min_interval_s: int  # overlap guard

    async def fetch(self, ctx: FetchContext) -> AsyncIterator[MEvent]: ...
```

Fetchers must:
- catch their own exceptions, log, and return — one dead source never stops the scheduler
- honour `min_interval_s` via a per-fetcher `asyncio.Lock` + last-run timestamp in `scheduler.py`
- in `fixture_mode`, yield from `_fixtures/<name>.json` and make no network call
- do heavy parsing in a thread (`asyncio.to_thread`) if it exceeds ~50 ms — GDELT CSVs qualify

Shared `httpx.AsyncClient` is created in the FastAPI lifespan with `headers={'User-Agent': 'MataElang/0.1 (personal monitor)'}` and `timeout=httpx.Timeout(20.0)`.

### 4.2 Bus (`bus.py`)

```python
class Bus:
    _live: dict[str, MEvent]
    _clients: set[WebSocket]

    async def upsert(self, events: list[MEvent]) -> None:
        # 1. merge into _live, drop expired
        # 2. await db.upsert_many(events)
        # 3. broadcast Upsert(events) to all clients; drop dead sockets
```

Single instance, held on `app.state.bus`. Expiry sweep runs every 60 s as an APScheduler job and broadcasts `Expire(ids)`.

### 4.3 WebSocket protocol

Pydantic models in `models.py`, discriminated on `op`.

Client → server: `{"op":"hello","since":"<iso>"}` (since optional)
Server → client, in order:
1. `{"op":"snapshot","events":[...]}` — all live events (or those after `since`)
2. `{"op":"upsert","events":[...]}` — ongoing
3. `{"op":"expire","ids":[...]}` — every 60 s
4. `{"op":"status","sources":{"usgs":{"last_ok":..,"last_error":..,"count":..}}}` — every 30 s

Path: `/ws`. JSON text frames. Server sends ping every 20 s; a client that misses two is dropped.

### 4.4 HTTP

| Route | Purpose |
|---|---|
| `GET /` | serves `web/` via `StaticFiles` |
| `GET /api/events?type=&since=&bbox=` | history query from SQLite |
| `GET /api/status` | same shape as WS status |
| `GET /healthz` | 200 if bus alive |
| `/tiles/*` | **Caddy**, never reaches FastAPI |

### 4.5 SQLite (`db.py`)

```sql
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, type TEXT, ts TEXT, lat REAL, lon REAL,
  severity INTEGER, title TEXT, source TEXT, url TEXT,
  ttl INTEGER, payload TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_type_ts ON events(type, ts);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
```

`PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;` on open. `upsert_many` uses `INSERT … ON CONFLICT(id) DO UPDATE` in one transaction. Retention job nightly: delete rows older than `RETENTION_DAYS`. History is what makes the Phase 1 heat map possible, so keep it.

### 4.6 Scheduler (`scheduler.py`)

`AsyncIOScheduler` started in lifespan. Each fetcher registered as a `CronTrigger` job with `coalesce=True, max_instances=1, misfire_grace_time=60`. Stagger start offsets by 15 s per fetcher so they never fire together.

---

## 5. Client contracts

Unchanged from the Node version — the frontend does not know what the server is written in.

### 5.1 Layer module interface (`web/layers/*.js`)

```js
export default {
  type: 'quake',
  init(map) { /* add source + layers */ },
  upsert(events) { /* update GeoJSON source */ },
  expire(ids) { },
  paint: { color: '#F58220', minRadius: 4, maxRadius: 18 }
};
```

`app.js` holds a registry `{ [type]: layerModule }` and routes WS messages by `event.type`. Unknown types are logged and ignored, never crash.

### 5.2 UI (Phase 0 only)

- Full-bleed map
- Top-left: app name, connection dot (teal = live, orange = reconnecting, grey = fixture)
- Bottom-left: source status strip from the `status` op
- Click marker → small popup: title, source, time ago, link
- No sidebar, no ticker, no filters yet

---

## 6. Config

`config.py` uses `pydantic-settings`. `.env.example`:

```
PORT=8000
FIXTURE_MODE=false
DB_PATH=./data/mataelang.sqlite
RETENTION_DAYS=90
USGS_FEED=https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson
GDELT_MIN_GOLDSTEIN=-3
```

`pyproject.toml` Phase 0 deps: `fastapi`, `uvicorn[standard]`, `httpx`, `aiosqlite`, `apscheduler`, `pydantic`, `pydantic-settings`. Dev: `ruff`, `mypy`, `pytest`, `pytest-asyncio`, `httpx` (for TestClient).

---

## 7. Acceptance test (all 8 must pass)

1. **Cold start.** `caddy run` + `uv run uvicorn mataelang.main:app` with a real `planet.pmtiles` → map renders Jakarta at z10 with roads, boundaries, labels, in under 3 s on LAN. No requests leave the machine for tiles.
2. **Fixture mode.** `FIXTURE_MODE=true` with network unplugged → map plus fixture quake and GDELT markers appear. Zero network errors in console or server log.
3. **Live USGS.** Real mode, within 5 min a USGS event appears on the map and in `events` with correct `severity` per §3.1.
4. **Live GDELT.** Within 15 min a GDELT conflict event appears, geocoded to a plausible location, Goldstein value in `payload`.
5. **Dedupe.** Trigger the USGS job twice by hand; `events` row count unchanged for unchanged ids; clients receive `upsert` but marker count stable.
6. **Replay.** Open a second browser tab → receives `snapshot` matching the first tab within 1 s.
7. **Expire.** Set a fixture event `ttl: 30`; marker disappears within 90 s and `expire` op observed.
8. **Dead source.** Point `USGS_FEED` at an invalid URL; GDELT continues, status strip shows USGS red with `last_error`, server stays up, no unhandled exception in the log.

Plus `uv run pytest` green with at least: contract validation, bus dedupe, fixture-mode fetch for each fetcher.

When all pass, tag `v0.1.0-spine` and open Phase 1.

---

## 8. Known risks going in

- **Disk I/O on PMTiles.** Planet file on spinning disk will feel broken. Test §7.1 on the real drive.
- **Blocking the event loop.** GDELT parsing and any future GeoPandas call must go through `asyncio.to_thread`. A blocked loop stalls every WS client at once.
- **GDELT geocoding noise.** Expect events landed on country centroids. Phase 0 accepts this; Phase 1 filters to `ActionGeo_Type ≥ 3`.
- **GDELT volume.** 15-min files can be 50k+ rows. Filter by Goldstein and `QuadClass` (3, 4 = conflict) *before* building `MEvent`s.
- **Source terms drift.** Confirm current GDELT and USGS endpoints at build time; note the check date in the fetcher docstring.
