"""GDELT 2.0 Event stream → ``conflict`` events.

Source: http://data.gdeltproject.org/gdeltv2/lastupdate.txt (checked 2026-08-26). The file
lists the newest 15-minute export; the first line is the ``*.export.CSV.zip`` we want.
Free, no key. GDELT asks only for attribution — the UI shows "GDELT" on every marker.

Pipeline (all parsing runs in a thread — a 15-min file is 50k+ rows and would stall the
event loop and every WS client with it):

1. Read lastupdate.txt, pick the export zip URL.
2. Download, unzip in memory, split tab-separated rows (61 columns, no header).
3. Keep rows with ``QuadClass`` ∈ {3, 4} (verbal / material conflict), ``GoldsteinScale``
   ≤ ``GDELT_MIN_GOLDSTEIN`` and a real ``ActionGeo`` lat/lon.
4. Sort by ``NumMentions`` desc, cap at ``GDELT_MAX_EVENTS``.

Severity (spine §3.1), Goldstein scale:
    0: ≥ 0      1: −1 … −3    2: −3 … −5
    3: −5 … −7  4: −7 … −9    5: ≤ −9

``ts`` is ``DATEADDED`` (15-min resolution, the moment GDELT saw the event). ``SQLDATE`` has
only day precision and is kept in ``payload.sqldate``. Phase 0 accepts country-centroid
geocoding noise; Phase 1 filters ``ActionGeo_Type >= 3``.
"""

from __future__ import annotations

import asyncio
import io
import zipfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx

from ..models import MEvent
from .base import FetchContext, FetchError, iter_fixture

NAME = "gdelt"
SOURCE_LABEL = "GDELT"

# GDELT 2.0 Event table column indices (61 columns, tab-separated, no header).
COL_GLOBALEVENTID = 0
COL_SQLDATE = 1
COL_ACTOR1_NAME = 6
COL_ACTOR1_COUNTRY = 7
COL_ACTOR2_NAME = 16
COL_ACTOR2_COUNTRY = 17
COL_EVENT_CODE = 26
COL_EVENT_ROOT = 28
COL_QUADCLASS = 29
COL_GOLDSTEIN = 30
COL_NUM_MENTIONS = 31
COL_NUM_SOURCES = 32
COL_NUM_ARTICLES = 33
COL_AVG_TONE = 34
COL_ACTION_GEO_TYPE = 51
COL_ACTION_GEO_NAME = 52
COL_ACTION_GEO_COUNTRY = 53
COL_ACTION_GEO_LAT = 56
COL_ACTION_GEO_LON = 57
COL_DATEADDED = 59
COL_SOURCEURL = 60
N_COLS = 61

CONFLICT_QUADCLASSES = {"3", "4"}

# CAMEO root codes — short labels for the marker title.
CAMEO_ROOT = {
    "01": "Public statement",
    "02": "Appeal",
    "03": "Intent to cooperate",
    "04": "Consult",
    "05": "Diplomatic cooperation",
    "06": "Material cooperation",
    "07": "Aid",
    "08": "Yield",
    "09": "Investigate",
    "10": "Demand",
    "11": "Disapprove",
    "12": "Reject",
    "13": "Threaten",
    "14": "Protest",
    "15": "Show of force",
    "16": "Reduce relations",
    "17": "Coerce",
    "18": "Assault",
    "19": "Fight",
    "20": "Mass violence",
}


def severity_for(goldstein: float) -> int:
    if goldstein >= 0:
        return 0
    if goldstein > -3:
        return 1
    if goldstein > -5:
        return 2
    if goldstein > -7:
        return 3
    if goldstein > -9:
        return 4
    return 5


def _parse_dateadded(raw: str) -> datetime:
    # YYYYMMDDHHMMSS
    return datetime.strptime(raw.strip(), "%Y%m%d%H%M%S").replace(tzinfo=UTC)


def parse_row(cols: list[str], ttl: int) -> MEvent | None:
    """One GDELT row → MEvent, or None if it isn't a geolocated conflict event."""
    if len(cols) < N_COLS:
        return None
    try:
        lat = float(cols[COL_ACTION_GEO_LAT])
        lon = float(cols[COL_ACTION_GEO_LON])
        goldstein = float(cols[COL_GOLDSTEIN] or "nan")
        ts = _parse_dateadded(cols[COL_DATEADDED])
    except ValueError:
        return None
    if goldstein != goldstein:  # NaN
        return None
    if lat == 0.0 and lon == 0.0:
        # Gulf of Guinea null island — GDELT uses 0,0 for "unknown".
        return None

    a1 = cols[COL_ACTOR1_NAME].strip().title() or cols[COL_ACTOR1_COUNTRY].strip() or "?"
    a2 = cols[COL_ACTOR2_NAME].strip().title() or cols[COL_ACTOR2_COUNTRY].strip()
    root = cols[COL_EVENT_ROOT].strip().zfill(2)
    action = CAMEO_ROOT.get(root, f"CAMEO {cols[COL_EVENT_CODE]}")
    place = cols[COL_ACTION_GEO_NAME].strip()
    actors = f"{a1} → {a2}" if a2 else a1
    title = f"{action}: {actors}"
    if place:
        title += f" ({place})"

    def _int(v: str) -> int | None:
        try:
            return int(v)
        except ValueError:
            return None

    def _float(v: str) -> float | None:
        try:
            return float(v)
        except ValueError:
            return None

    return MEvent(
        id=f"{NAME}:{cols[COL_GLOBALEVENTID].strip()}",
        type="conflict",
        ts=ts,
        lat=lat,
        lon=lon,
        severity=severity_for(goldstein),
        title=title[:120],
        source=SOURCE_LABEL,
        url=cols[COL_SOURCEURL].strip() or None,
        ttl=ttl,
        payload={
            "goldstein": goldstein,
            "quadclass": _int(cols[COL_QUADCLASS]),
            "event_code": cols[COL_EVENT_CODE].strip(),
            "event_root": root,
            "actor1": a1,
            "actor2": a2 or None,
            "num_mentions": _int(cols[COL_NUM_MENTIONS]),
            "num_sources": _int(cols[COL_NUM_SOURCES]),
            "num_articles": _int(cols[COL_NUM_ARTICLES]),
            "avg_tone": _float(cols[COL_AVG_TONE]),
            "geo_type": _int(cols[COL_ACTION_GEO_TYPE]),
            "geo_name": place or None,
            "geo_country": cols[COL_ACTION_GEO_COUNTRY].strip() or None,
            "sqldate": cols[COL_SQLDATE].strip(),
        },
    )


def parse_export(
    data: bytes, *, min_goldstein: float, max_events: int, ttl: int
) -> tuple[list[MEvent], dict[str, int]]:
    """Blocking. Unzip + filter + build. Call via ``asyncio.to_thread``."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not names:
            raise FetchError("export zip contains no CSV")
        text = zf.read(names[0]).decode("utf-8", errors="replace")

    stats = {"rows": 0, "conflict": 0, "kept": 0}
    candidates: list[tuple[int, MEvent]] = []
    for line in text.split("\n"):
        if not line:
            continue
        stats["rows"] += 1
        cols = line.split("\t")
        if len(cols) < N_COLS or cols[COL_QUADCLASS] not in CONFLICT_QUADCLASSES:
            continue
        stats["conflict"] += 1
        try:
            if float(cols[COL_GOLDSTEIN]) > min_goldstein:
                continue
        except ValueError:
            continue
        ev = parse_row(cols, ttl)
        if ev is None:
            continue
        mentions = ev.payload.get("num_mentions") or 0
        candidates.append((mentions, ev))

    candidates.sort(key=lambda t: t[0], reverse=True)
    kept = [ev for _, ev in candidates[:max_events]]
    stats["kept"] = len(kept)
    return kept, stats


def pick_export_url(lastupdate: str) -> str:
    """lastupdate.txt lines are ``<size> <md5> <url>``; the first is the export zip."""
    for line in lastupdate.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[-1].endswith(".export.CSV.zip"):
            return parts[-1]
    raise FetchError("no export.CSV.zip in lastupdate.txt")


class GdeltFetcher:
    name = NAME
    schedule = "*/15 * * * *"
    ttl_default = 6 * 3600
    min_interval_s = 600
    _last_export_url: str | None = None

    async def fetch(self, ctx: FetchContext) -> AsyncIterator[MEvent]:
        if ctx.fixture_mode:
            async for e in iter_fixture(self.name):
                yield e
            return

        s = ctx.settings
        try:
            lu = await ctx.http.get(s.gdelt_lastupdate_url)
            lu.raise_for_status()
            export_url = pick_export_url(lu.text)
            if export_url == self._last_export_url:
                ctx.log.info("gdelt: no new export yet (%s)", export_url.rsplit("/", 1)[-1])
                return
            resp = await ctx.http.get(export_url, timeout=httpx.Timeout(60.0))
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            ctx.log.warning("gdelt fetch failed: %s", exc)
            raise FetchError(f"{type(exc).__name__}: {exc}"[:200]) from exc

        try:
            events, stats = await asyncio.to_thread(
                parse_export,
                resp.content,
                min_goldstein=s.gdelt_min_goldstein,
                max_events=s.gdelt_max_events,
                ttl=self.ttl_default,
            )
        except (zipfile.BadZipFile, FetchError) as exc:
            ctx.log.warning("gdelt parse failed: %s", exc)
            raise FetchError(f"bad export: {exc}"[:200]) from exc

        self._last_export_url = export_url
        ctx.log.info("gdelt %s: %s", export_url.rsplit("/", 1)[-1], stats)
        for ev in events:
            yield ev
