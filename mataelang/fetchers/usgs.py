"""USGS earthquake feed → ``quake`` events.

Source: https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson
(endpoint and terms checked 2026-08-26; public domain, no key, asks for a descriptive UA
and polite polling — the feed regenerates every minute, we poll every 2).

Severity (spine §3.1):
    0: < M3       1: M3–3.9     2: M4–4.9
    3: M5–5.9     4: M6–6.9     5: ≥ M7 or tsunami flag set

``ts`` is the origin time (``properties.time``, epoch ms), not the fetch time.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx

from ..models import MEvent
from .base import FetchContext, FetchError, iter_fixture

NAME = "usgs"
SOURCE_LABEL = "USGS"


def severity_for(mag: float | None, tsunami: int | bool = 0) -> int:
    if tsunami:
        return 5
    if mag is None:
        return 0
    if mag >= 7:
        return 5
    if mag >= 6:
        return 4
    if mag >= 5:
        return 3
    if mag >= 4:
        return 2
    if mag >= 3:
        return 1
    return 0


def parse_feature(feature: dict[str, Any], ttl: int) -> MEvent | None:
    """One GeoJSON feature → MEvent, or None if it has no usable coordinates."""
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates") or []
    if len(coords) < 2 or coords[0] is None or coords[1] is None:
        return None
    props = feature.get("properties") or {}
    native_id = feature.get("id") or props.get("code")
    if not native_id or props.get("time") is None:
        return None
    mag = props.get("mag")
    place = props.get("place") or "unknown location"
    mag_txt = f"M{mag:.1f}" if isinstance(mag, int | float) else "M?"
    return MEvent(
        id=f"{NAME}:{native_id}",
        type="quake",
        ts=datetime.fromtimestamp(props["time"] / 1000, tz=UTC),
        lat=float(coords[1]),
        lon=float(coords[0]),
        severity=severity_for(mag, props.get("tsunami", 0)),
        title=f"{mag_txt} — {place}"[:120],
        source=SOURCE_LABEL,
        url=props.get("url"),
        ttl=ttl,
        payload={
            "mag": mag,
            "depth_km": coords[2] if len(coords) > 2 else None,
            "tsunami": props.get("tsunami", 0),
            "alert": props.get("alert"),
            "felt": props.get("felt"),
            "sig": props.get("sig"),
            "status": props.get("status"),
        },
    )


class UsgsFetcher:
    name = NAME
    schedule = "*/2 * * * *"
    ttl_default = 3 * 24 * 3600  # keep a quake on the map for 3 days
    min_interval_s = 60

    async def fetch(self, ctx: FetchContext) -> AsyncIterator[MEvent]:
        if ctx.fixture_mode:
            async for e in iter_fixture(self.name):
                yield e
            return

        url = ctx.settings.usgs_feed
        try:
            resp = await ctx.http.get(url)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            ctx.log.warning("usgs fetch failed: %s", exc)
            raise FetchError(f"{type(exc).__name__}: {exc}"[:200]) from exc
        except ValueError as exc:
            ctx.log.warning("usgs bad JSON: %s", exc)
            raise FetchError("invalid JSON from USGS") from exc

        features = data.get("features") or []
        dropped = 0
        for feature in features:
            ev = parse_feature(feature, self.ttl_default)
            if ev is None:
                dropped += 1
                continue
            yield ev
        if dropped:
            ctx.log.info("usgs dropped %d features without coordinates", dropped)
