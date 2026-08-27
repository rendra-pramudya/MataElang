# MataElang

**Eagle Eye.** A single-user, self-hosted world monitor: a dark MapLibre map over a local planet
basemap, with live event layers fed by free/open APIs only. Runs on your own hardware, for your
own situational awareness. Read-only by design.

**Phase 0 — the spine** (tagged `v0.1.0-spine`): local PMTiles basemap, USGS earthquakes and
GDELT conflict events flowing through one normalised contract to live map markers, with history
in SQLite.

**Phase 1 — layers** (in progress): Open-Meteo severe weather and RSS news through the same
contract, an H3 conflict-density heat map over the accumulated history, time-decay on every
marker, and a layer toggle.

See [`CLAUDE.md`](CLAUDE.md) for the guardrails, [`docs/phase-0-spine.md`](docs/phase-0-spine.md)
and [`docs/phase-1-layers.md`](docs/phase-1-layers.md) for the build briefs and acceptance tests.

## Run it

```bash
uv sync
cp .env.example .env

# 1. Fixture mode — no network, no tiles needed; proves the pipeline end to end.
FIXTURE_MODE=true uv run uvicorn mataelang.main:app --port 8000
# → http://localhost:8000   (basemap will be blank without planet.pmtiles; markers still show)

# 2. Real mode with the planet basemap.
scripts/fetch-planet.sh <url from https://maps.protomaps.com/builds/>   # ~120 GB, NVMe strongly preferred
caddy run                                # serves /tiles with Range support on :8080, proxies the rest to :8000
uv run uvicorn mataelang.main:app --port 8000
# → http://localhost:8080
```

On Windows, `scripts\start.bat` handles the setup and launch — it checks for `uv`, seeds `.env`
from `.env.example` on first run, syncs dependencies and starts uvicorn. Double-click it, or:

```bat
scripts\start.bat              :: mode from .env, port 8000
scripts\start.bat fixture      :: force fixture mode
scripts\start.bat fixture 8001 :: ...on another port
```

It starts FastAPI only — run `caddy run` alongside it for the basemap, then use
http://localhost:8080. It binds to `127.0.0.1`: MataElang has no authentication and is not
meant to be reachable from your network.

Labels ship with a Latin/Cyrillic/Greek glyph subset in `web/fonts/`. For every script run
`scripts/fetch-fonts.sh` once.

## Check it

```bash
uv run pytest          # contract, bus dedupe, fixture fetch per source, dead-source, WS round trip
uv run ruff check .    # lint
uv run mypy            # --strict on models.py and bus.py
```

Useful while walking the acceptance test:

| | |
|---|---|
| `GET /healthz` | live event count and connected clients |
| `GET /api/status` | per-source `last_ok` / `last_error` / `count` |
| `GET /api/events?type=quake&since=2026-08-01T00:00:00Z&bbox=95,-11,141,6` | history from SQLite |
| `POST /api/fetch/usgs` | run a fetcher now (dedupe test §7.5); also `gdelt`, `openmeteo`, `rss` |
| `GET /api/heat?type=conflict&res=3` | H3-binned reported-conflict density |
| `window.mataelang.counts()` in the browser console | marker count per layer |

## Layout

```
mataelang/          FastAPI app, bus, db, scheduler, fetchers (+ _fixtures/)
web/                vanilla ESM client; vendor/ holds MapLibre + PMTiles so fixture mode works offline
data/tiles/         planet.pmtiles (gitignored)
data/boundaries/    editorial GeoJSON border overrides, drawn above OSM boundaries
docs/               phase briefs
```

## Data sources and terms

- **USGS** earthquake GeoJSON feeds — public domain. Polled every 2 min with a descriptive User-Agent.
- **GDELT 2.0** event stream — free, attribution shown on every marker. One 15-min export per poll.
- **Open-Meteo** forecast API — free for non-commercial use, no key. Every watch point is covered
  by a single call every 10 min, well inside fair use.
- **RSS/Atom** feeds — terms belong to each publisher. The defaults are a starting point; swap
  them in `.env` for whatever you actually read.
- **OpenStreetMap** via Protomaps planet PMTiles served from local disk. Never `tile.openstreetmap.org`.

Endpoints were last confirmed 2026-08-27; the date is noted in each fetcher's docstring.

## What is inferred, and what is measured

MataElang labels its guesses (`CLAUDE.md` rule 5):

- **News locations are inferred** from the headline against a bundled offline gazetteer. A city
  match is high confidence; a country match is a centroid placeholder, drawn hollow, and its
  popup says so. Items that match nothing are dropped rather than parked at 0,0.
- **The heat map shows reported density**, not conflict. It aggregates GDELT, which is media
  coverage — a dense cell means dense reporting.
- **Weather is watch points, not coverage.** Severe conditions appear only where you pointed it.
