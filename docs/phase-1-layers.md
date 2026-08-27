# MataElang — Phase 1: Layers

**Goal:** turn the spine into something worth leaving on a screen. Two new sources (weather,
news) through the unchanged contract, a conflict-density heat map over the GDELT history the
spine has been quietly accumulating, and time-decay so an eight-hour-old marker no longer
shouts as loudly as a fresh one.

Phase 0 proved the machine runs. Phase 1 is the first proof that "add another module to a
working machine" is actually true — if either new fetcher needs a contract change, that is a
finding, and it goes in §8 rather than into `models.py`.

---

## 1. Scope

**In:**
- Open-Meteo fetcher → `weather` events (severe conditions only, no key)
- RSS fetcher → `news` events (offline gazetteer geocoding, no key)
- H3 conflict-density heat map: `analysis/heat.py`, `GET /api/heat`, `web/layers/heat.js`
- Time-decay: marker opacity falls with age across every circle layer
- A layer toggle in the UI (five layers is past the point where "all on" is usable)

**Out (later phases):**
- AIS, ADS-B, markets — deferred, see §7
- Clustering, proposals/approval gate, alerts, elections
- Any change to `MEvent`

**Deferred deliberately.** `CLAUDE.md` lists AIS, ADS-B and markets in Phase 1. They are held
back to Phase 1b for one reason each, and the reason is access terms, not effort:

| Source | Why held |
|---|---|
| AIS | No free planet-wide feed with terms that survive contact. AISStream/AISHub need an account and give partial coverage; both have changed access rules inside the last two years. Needs a terms check with a recorded date before a line is written. |
| ADS-B | Same shape. OpenSky's anonymous tier is heavily rate-limited and its policy has moved; adsb.fi and adsb.lol are friendlier but community-run. Pick one, record the check date, honour its limit. |
| Markets | Not free at planet scale without a key that eventually costs money. Also barely spatial — a market event has no honest lat/lon, and inventing one violates §3 harder than the geocoding compromise in §4.2. Wants a non-map UI element, which is a Phase 3 conversation. |

Weather and RSS ship first precisely because neither needs a key, an account, or a terms
gamble. They establish the add-a-source path; the harder three follow it.

---

## 2. What does not change

The contract. `weather` and `news` are already in `EventType`, both new sources produce a
stable `id`, a real `ts`, coordinates and a severity. `models.py` is untouched by this phase.

The fetcher protocol is untouched. Both new fetchers are ordinary `Fetcher` implementations:
they yield, they catch their own exceptions, they honour `min_interval_s`, they ship a
fixture, they never touch the DB or the WebSocket.

---

## 3. Open-Meteo → `weather`

**Source:** `https://api.open-meteo.com/v1/forecast` (endpoint and terms checked 2026-08-27).
Free for non-commercial use, no API key, no account. Asks for attribution — the UI shows
"Open-Meteo" on every marker. Fair-use limit is roughly 10 000 calls/day; one call every ten
minutes covering every watch point costs ~144/day.

### 3.1 Watch points, not a grid

Open-Meteo is a point-forecast API. Polling a global grid would be both rude and useless, so
Phase 1 polls a configured list of watch points — `OPENMETEO_POINTS`, a semicolon-separated
`name,lat,lon` list defaulting to a spread of Indonesian and regional cities. The API takes
comma-separated `latitude`/`longitude` lists and returns an array of per-location results, so
**all points cost exactly one HTTP call**.

This is a deliberate limitation and the UI must not imply otherwise: MataElang shows severe
weather *at the places it was told to watch*, not everywhere it is happening.

### 3.2 Only notable weather becomes an event

A calm watch point yields nothing. An `MEvent` is emitted only at severity ≥ 1, which keeps
the map readable and the DB from filling with "Jakarta, 27°C, fine".

`id` is `openmeteo:<slug>` — stable per watch point, so a worsening storm updates one marker
instead of laying down a trail. `ts` is the observation time Open-Meteo reports (`current.time`),
not fetch time.

### 3.3 Severity

Wind uses Beaufort/Saffir-Simpson boundaries rather than invented numbers, so the mapping can
be defended and is documented in the module docstring. Final severity is the **max** of the
three rows.

| | 0 | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|
| Gust km/h | < 39 | 39–61 | 62–88 | 89–117 | 118–154 | ≥ 155 |
| Precip mm/h | < 4 | 4–9.9 | 10–19.9 | 20–39.9 | 40–59.9 | ≥ 60 |
| WMO code | — | 45/48 fog, 51–57 drizzle | 61–65 rain, 71–75 snow | 82 violent showers, 85–86 snow showers | 95 thunderstorm | 96/99 thunderstorm with hail |

Boundaries: Beaufort 6 (strong breeze) at 39 km/h, Beaufort 8 (gale) at 62, Beaufort 10
(storm) at 89, Beaufort 12 (hurricane force) at 118, Saffir-Simpson category 2 at 155.

`ttl` is 3 600 s. Weather is a *now* statement; a stale one is worse than none.

---

## 4. RSS → `news`

**Source:** a configured list of feed URLs, `RSS_FEEDS`, defaulting to a small set of
public wire feeds. RSS is a format, not a service — terms belong to each publisher, so the
default list stays short, reputable, and easy for Adi to replace. No key, no account.

Parsing uses stdlib `xml.etree.ElementTree`. **No new dependency** — `feedparser` would buy
tolerance for malformed feeds that a `try/except` around each item buys more cheaply.

### 4.1 RSS is not spatial, and the contract demands coordinates

This is the real design problem of Phase 1. `MEvent` requires `lat`/`lon`; a headline has
neither. Three options were considered:

1. **Drop the coordinate requirement for `news`.** Rejected — it breaks the one contract that
   matters for every consumer, to serve one source.
2. **Online geocoding** (Nominatim et al.). Rejected — it is a second network dependency per
   item, it has usage policies that a polling monitor would strain, and it breaks fixture mode.
3. **Bundled offline gazetteer.** Chosen.

`mataelang/fetchers/_data/gazetteer.json` ships ~150 entries: every capital worth watching,
plus cities that appear in conflict reporting, plus country centroids as fallback. Matching is
longest-name-first against the headline and summary, word-boundary anchored so "Chad" does not
match "Chadwick".

### 4.2 Geocoding is inference, and is labelled

Per `CLAUDE.md` non-negotiable 5, a guess is never presented as a fact:

- `payload.geocode` records `{"match": "Kyiv", "kind": "city", "confidence": "high"}`
- `kind` is `city` (high) or `country` (low — a country centroid is a placeholder, not a place)
- The client renders low-confidence markers hollow, and the popup says
  *"location inferred from headline"*
- **An item that matches nothing is dropped**, per spine §3: never yield without coordinates,
  and never park an event at 0,0

Expect this to be the noisiest part of Phase 1. It is honest noise, and it is visibly labelled.

### 4.3 Severity

There is no Goldstein score for a headline, so severity is keyword-tiered, and the tiers are
listed in the module docstring so the mapping is auditable rather than magic.

| Sev | Trigger |
|---|---|
| 0 | (unused — a `news` event is at least 1) |
| 1 | default: it was published, nothing matched |
| 2 | protest, strike, unrest, evacuation, sanctions, coup attempt |
| 3 | clash, shelling, airstrike, explosion, casualties, quake, flood |
| 4 | massacre, offensive, invasion, mass casualties, state of emergency |
| 5 | war declared, nuclear, chemical weapons, genocide |

`ttl` is 12 h. `id` is `rss:<sha1(link)[:16]>` — the link is the only stable identifier RSS
reliably gives, and hashing keeps ids short and free of separator collisions.

---

## 5. H3 conflict-density heat map

Individual GDELT markers answer "what happened here". They cannot answer "where is it getting
worse", which is the question the map exists for. Ninety days of history are already in SQLite;
this phase finally reads them.

### 5.1 Where the code lives, and the one new dependency

`CLAUDE.md` puts H3 in the Phase 2+ analysis stack, but also lists the H3 heat map in Phase 1.
Resolution: `mataelang/analysis/heat.py` opens the `analysis/` package one phase early for
**one pure function and nothing else**, and only `h3` is added — not GeoPandas, not Shapely.
Those stay in Phase 2 where the guardrail puts them.

Stated reason, as the guardrail requires: hex binning at a fixed resolution is what makes
density comparable across latitudes. Square degree-bins make Norway look like a war zone
because the cells shrink toward the poles. Rolling our own hex grid to avoid a 200 kB
dependency would be strictly worse code.

`bin_events()` is pure and synchronous: events in, cells out, no I/O, no globals. Trivially
testable, and it moves to a thread if it ever exceeds ~50 ms (spine §8).

### 5.2 Endpoint

`GET /api/heat?type=conflict&res=3&since=&bbox=` → `{"resolution": 3, "cells": [...]}`, each
cell `{"h3": "83...", "lat":, "lon":, "count":, "weight":, "max_severity":}`.

- `res` clamped to 0–7. Resolution 3 is ~59 km edge — country-region scale, the right altitude
  for "where is it getting worse".
- `weight` is `sum(1 + severity)`, so a cell of five severity-5 events outranks ten severity-0
  ones. MapLibre's heatmap weights by this, not by raw count.
- Reads SQLite through the existing `Database.query`, honouring `RETENTION_DAYS`.

### 5.3 Client

`web/layers/heat.js` is a *pseudo-layer*: it takes no WS events, and instead polls `/api/heat`
every 60 s and on `moveend`. It sits directly above the basemap and below every marker layer,
so markers stay clickable.

Off by default. A heat map that is always on is a background texture, not information.

---

## 6. Time-decay

An event's `ttl` already says when it dies. Between birth and death it currently looks
identical, which is wrong — a quake from four minutes ago and one from two days ago should not
read the same at a glance.

`_circle.js` gains an `age` property, refreshed every 30 s alongside the existing local expiry
sweep, and drives:

```
circle-opacity:      0.85 → 0.25   over age fraction 0 → 1
circle-stroke-opacity: 0.35 → 0.05
```

Fraction is `(now - ts) / ttl`, clamped to 0–1, computed client-side as a feature property so
MapLibre interpolates it without a per-frame JS pass. Radius does **not** decay — a magnitude 7
stays big until it expires, because size encodes severity and must not encode two things.

---

## 7. Acceptance test (all 9 must pass)

Phase 0's eight still have to pass; these are additional.

1. **Weather appears.** Real mode, within 10 min a `weather` marker appears at a configured
   watch point *or* the log shows every point below threshold. Both are a pass — inventing a
   storm to make the test green is not.
2. **Weather severity.** Force a point with a known gust value through `parse_current`; severity
   matches §3.3 exactly at each boundary (39/62/89/118/155 km/h).
3. **Weather is one call.** Log confirms a single HTTP request covers all watch points.
4. **News geocodes or drops.** Real mode: every emitted `news` event has a `payload.geocode`
   with a match; the log reports the drop count. Zero events with `lat==0 and lon==0`.
5. **Low confidence is visible.** A country-centroid match renders hollow and its popup says
   the location was inferred.
6. **Heat map.** With ≥ 100 GDELT rows in SQLite, `/api/heat?type=conflict&res=3` returns cells
   whose counts sum to the row count in range, and the layer renders with the toggle on.
7. **Heat map is off by default** and markers stay clickable through it.
8. **Time-decay.** A fixture event at 90 % of its ttl renders visibly fainter than a fresh one;
   both keep their severity radius.
9. **Dead source, again.** Point `RSS_FEEDS` at an invalid URL. Weather, USGS and GDELT
   continue; the status strip shows RSS red; no unhandled exception.

Plus `uv run pytest` green with: severity boundaries for both new sources, gazetteer matching
(including the word-boundary case), fixture-mode fetch per new source, H3 binning, and the
`/api/heat` route.

When all pass, tag `v0.2.0-layers` and open Phase 1b (AIS, ADS-B, markets).

---

## 8. Known risks going in

- **Watch points are not coverage.** The weather layer shows severe conditions only where it
  was told to look. If this reads as a global severe-weather layer, it is misleading, and the
  fix is UI labelling, not more polling.
- **Gazetteer misses are silent.** Items that match nothing are dropped. If the drop rate is
  high the news layer will look sparse for reasons the user cannot see, so the count is logged
  every run and belongs in the status strip.
- **Gazetteer false positives.** Word-boundary matching cuts "Chad"/"Chadwick", not
  "Georgia" the country vs. the US state. Country matches are marked low-confidence partly for
  this reason.
- **Heat map reads as ground truth.** It aggregates GDELT, which is media coverage, not
  events. Dense coverage means dense *reporting*. This is `CLAUDE.md` rule 5 territory and the
  legend must say "reported conflict density".
- **`/api/heat` cost.** A 90-day unbounded query re-binned every 60 s will not stay cheap. If
  it exceeds ~50 ms, bin in a thread and cache per `(type, res, since, bbox)`.
- **Five layers is a crowded map.** The toggle is in scope for this phase, not the next one.
