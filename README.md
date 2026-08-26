# MataElang

**Eagle Eye.** A single-user, self-hosted world monitor: a dark MapLibre map over a local planet
basemap, with live event layers fed by free/open APIs only. Runs on your own hardware, for your
own situational awareness. Read-only by design.

This is **Phase 0 — the spine**: local PMTiles basemap, USGS earthquakes and GDELT conflict
events flowing through one normalised contract to live map markers, with history in SQLite.
See [`CLAUDE.md`](CLAUDE.md) for the guardrails and [`docs/phase-0-spine.md`](docs/phase-0-spine.md)
for the build brief and acceptance test.

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
| `POST /api/fetch/usgs` | run a fetcher now (dedupe test §7.5) |
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
- **OpenStreetMap** via Protomaps planet PMTiles served from local disk. Never `tile.openstreetmap.org`.

Endpoints were last confirmed 2026-08-26; the date is noted in each fetcher's docstring.
