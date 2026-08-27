"""Open-Meteo current conditions → ``weather`` events.

Source: https://api.open-meteo.com/v1/forecast (endpoint and terms checked 2026-08-27).
Free for non-commercial use, no key, no account. Attribution asked for — the UI shows
"Open-Meteo" on every marker. Fair use is ~10k calls/day; we spend ~144.

Open-Meteo is a *point* API, so we poll a configured list of watch points
(``OPENMETEO_POINTS``) rather than a grid. The API accepts comma-separated latitude/longitude
lists and returns one result object per point, so every point costs a single HTTP request.

This means the layer shows severe weather **at the places it was told to watch**, not
everywhere it is happening. See docs/phase-1-layers.md §3.1 — the UI must not imply otherwise.

Only notable weather becomes an event: severity 0 is dropped at the fetcher, so a calm watch
point yields nothing and the map stays readable.

Severity (phase-1 §3.3) is the max of three rows. Wind uses Beaufort/Saffir-Simpson
boundaries rather than invented numbers:
    gust km/h   0: <39   1: 39-61   2: 62-88   3: 89-117   4: 118-154   5: >=155
    precip mm/h 0: <4    1: 4-9.9   2: 10-19.9 3: 20-39.9  4: 40-59.9   5: >=60
    WMO code    1: fog/drizzle  2: rain/snow  3: violent or snow showers
                4: thunderstorm (95)  5: thunderstorm with hail (96/99)

``ts`` is the observation time Open-Meteo reports, not the fetch time. ``id`` is
``openmeteo:<slug>`` — stable per watch point, so a worsening storm updates one marker
instead of laying down a trail.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ..models import MEvent
from .base import FetchContext, FetchError, iter_fixture

NAME = "openmeteo"
SOURCE_LABEL = "Open-Meteo"

CURRENT_FIELDS = "temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_gusts_10m"

# WMO 4677 present-weather codes, trimmed to what Open-Meteo actually emits.
WMO_TEXT = {
    0: "clear",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "rime fog",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    56: "freezing drizzle",
    57: "heavy freezing drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    66: "freezing rain",
    67: "heavy freezing rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    77: "snow grains",
    80: "rain showers",
    81: "heavy rain showers",
    82: "violent rain showers",
    85: "snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "thunderstorm with heavy hail",
}

WMO_SEVERITY = {
    45: 1, 48: 1,
    51: 1, 53: 1, 55: 1, 56: 1, 57: 1,
    61: 2, 63: 2, 65: 2, 66: 2, 67: 2,
    71: 2, 73: 2, 75: 2, 77: 2,
    80: 2, 81: 2,
    82: 3, 85: 3, 86: 3,
    95: 4,
    96: 5, 99: 5,
}  # fmt: skip


@dataclass(frozen=True)
class WatchPoint:
    name: str
    lat: float
    lon: float

    @property
    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-") or "point"


def parse_points(raw: str) -> list[WatchPoint]:
    """``"Jakarta,-6.2,106.8; Tokyo,35.7,139.7"`` → watch points.

    Malformed entries are skipped rather than raising: a typo in .env should cost one point,
    not the whole layer.
    """
    points: list[WatchPoint] = []
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [p.strip() for p in chunk.split(",")]
        if len(parts) != 3:
            continue
        try:
            lat, lon = float(parts[1]), float(parts[2])
        except ValueError:
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        points.append(WatchPoint(name=parts[0], lat=lat, lon=lon))
    return points


def severity_for_gust(kmh: float | None) -> int:
    if kmh is None:
        return 0
    if kmh >= 155:  # Saffir-Simpson category 2
        return 5
    if kmh >= 118:  # Beaufort 12, hurricane force
        return 4
    if kmh >= 89:  # Beaufort 10, storm
        return 3
    if kmh >= 62:  # Beaufort 8, gale
        return 2
    if kmh >= 39:  # Beaufort 6, strong breeze
        return 1
    return 0


def severity_for_precip(mm: float | None) -> int:
    if mm is None:
        return 0
    if mm >= 60:
        return 5
    if mm >= 40:
        return 4
    if mm >= 20:
        return 3
    if mm >= 10:
        return 2
    if mm >= 4:
        return 1
    return 0


def severity_for_code(code: int | None) -> int:
    return WMO_SEVERITY.get(code, 0) if code is not None else 0


def severity_for(gust: float | None, precip: float | None, code: int | None) -> int:
    return max(severity_for_gust(gust), severity_for_precip(precip), severity_for_code(code))


def _parse_time(raw: str, utc_offset_s: int) -> datetime:
    """Open-Meteo returns local wall-clock without an offset; ``utc_offset_seconds`` carries
    the shift. We request timezone=UTC, so the offset is normally 0 — but honour it anyway
    rather than silently mislabelling a timestamp."""
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC)
    return parsed.replace(tzinfo=UTC) - timedelta(seconds=utc_offset_s)


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) else None


def parse_current(result: dict[str, Any], point: WatchPoint, ttl: int) -> MEvent | None:
    """One Open-Meteo result object → MEvent, or None if calm or unusable.

    Returns None at severity 0 — see the module docstring; a quiet watch point is not an event.
    """
    current = result.get("current") or {}
    when = current.get("time")
    if not when:
        return None
    try:
        ts = _parse_time(str(when), int(result.get("utc_offset_seconds") or 0))
    except ValueError:
        return None

    gust = _num(current.get("wind_gusts_10m"))
    precip = _num(current.get("precipitation"))
    raw_code = current.get("weather_code")
    code = int(raw_code) if isinstance(raw_code, int | float) else None
    severity = severity_for(gust, precip, code)
    if severity == 0:
        return None

    temp = _num(current.get("temperature_2m"))
    bits = [WMO_TEXT.get(code, f"code {code}") if code is not None else "severe conditions"]
    if gust is not None and gust >= 39:
        bits.append(f"gusts {gust:.0f} km/h")
    if precip is not None and precip >= 4:
        bits.append(f"{precip:.1f} mm/h")
    title = f"{point.name} — {', '.join(bits)}"

    return MEvent(
        id=f"{NAME}:{point.slug}",
        type="weather",
        ts=ts,
        # Trust the configured point over the grid cell Open-Meteo snapped to, so the marker
        # sits on the place the user named.
        lat=point.lat,
        lon=point.lon,
        severity=severity,
        title=title[:120],
        source=SOURCE_LABEL,
        url=None,
        ttl=ttl,
        payload={
            "place": point.name,
            "temperature_c": temp,
            "humidity_pct": _num(current.get("relative_humidity_2m")),
            "precip_mm_h": precip,
            "gust_kmh": gust,
            "weather_code": code,
            "weather_text": WMO_TEXT.get(code) if code is not None else None,
            "grid_lat": _num(result.get("latitude")),
            "grid_lon": _num(result.get("longitude")),
        },
    )


def normalise_results(data: Any) -> list[dict[str, Any]]:
    """Open-Meteo returns a bare object for one location and an array for many."""
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    return [data] if isinstance(data, dict) else []


class OpenMeteoFetcher:
    name = NAME
    schedule = "*/10 * * * *"
    ttl_default = 3600  # weather is a "now" statement; a stale one is worse than none
    min_interval_s = 300

    async def fetch(self, ctx: FetchContext) -> AsyncIterator[MEvent]:
        if ctx.fixture_mode:
            async for e in iter_fixture(self.name):
                yield e
            return

        points = parse_points(ctx.settings.openmeteo_points)
        if not points:
            ctx.log.info("openmeteo: no watch points configured, nothing to do")
            return

        params = {
            "latitude": ",".join(f"{p.lat:g}" for p in points),
            "longitude": ",".join(f"{p.lon:g}" for p in points),
            "current": CURRENT_FIELDS,
            "timezone": "UTC",
            "wind_speed_unit": "kmh",
            "precipitation_unit": "mm",
        }
        try:
            resp = await ctx.http.get(ctx.settings.openmeteo_url, params=params)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            ctx.log.warning("openmeteo fetch failed: %s", exc)
            raise FetchError(f"{type(exc).__name__}: {exc}"[:200]) from exc
        except ValueError as exc:
            ctx.log.warning("openmeteo bad JSON: %s", exc)
            raise FetchError("invalid JSON from Open-Meteo") from exc

        results = normalise_results(data)
        if len(results) != len(points):
            # Ordering is positional, so a length mismatch means we cannot trust the pairing.
            ctx.log.warning(
                "openmeteo: %d results for %d points, skipping", len(results), len(points)
            )
            raise FetchError(f"expected {len(points)} results, got {len(results)}")

        notable = 0
        for point, result in zip(points, results, strict=True):
            ev = parse_current(result, point, self.ttl_default)
            if ev is None:
                continue
            notable += 1
            yield ev
        ctx.log.info("openmeteo: %d/%d watch points notable", notable, len(points))
