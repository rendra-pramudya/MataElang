"""RSS/Atom feeds → ``news`` events.

Sources: whatever is in ``RSS_FEEDS`` (checked 2026-08-27 for the defaults). RSS is a format,
not a service — terms belong to each publisher, so the default list stays short, reputable and
easy to replace. No key, no account.

Parsing is stdlib ``xml.etree`` — deliberately no ``feedparser``. Tolerance for malformed
feeds is bought more cheaply with a ``try/except`` around each item than with a dependency.

**Geocoding is inference, not fact** (CLAUDE.md non-negotiable 5). A headline has no
coordinates and ``MEvent`` requires them, so each item is matched against a bundled offline
gazetteer (``_data/gazetteer.json``). See docs/phase-1-layers.md §4.1 for why an online
geocoder was rejected. Consequences, all deliberate:

* City match → ``confidence: "high"``. Country match → ``"low"``, because a country centroid
  is a placeholder, not a place; the client renders those hollow and says so in the popup.
* **An item matching nothing is dropped.** Never yield without coordinates, never park an
  event at 0,0. The drop count is logged every run — a sparse news layer must be explainable.

Severity is keyword-tiered (no Goldstein score exists for a headline). The full lists live in
``SEVERITY_TIERS`` below so the mapping is auditable rather than magic:

    1  default — it was published, nothing matched
    2  protest, strike, unrest, evacuation, sanctions, riot, blockade, ceasefire
    3  clash, shelling, airstrike, explosion, casualties, quake, flood, wildfire, killed
    4  massacre, offensive, invasion, mass casualties, state of emergency, martial law, coup
    5  war declared, nuclear, chemical weapons, genocide, ethnic cleansing

``ts`` is the item's publication date; when a feed omits one we fall back to fetch time and
record ``payload.ts_source = "fetch"`` so the guess is visible. ``id`` is
``rss:<sha1(link)[:16]>`` — the link is the only stable identifier RSS reliably provides, and
hashing keeps ids short and free of separator collisions.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx

from ..models import MEvent
from .base import FetchContext, FetchError, iter_fixture

NAME = "rss"
DATA_DIR = Path(__file__).resolve().parent / "_data"

# Aliases this short are matched case-sensitively — "US" is a country, "us" is a pronoun.
SHORT_ALIAS_LEN = 3

SEVERITY_TIERS: list[tuple[int, tuple[str, ...]]] = [
    (
        5,
        (
            "declares war",
            "declaration of war",
            "nuclear",
            "chemical weapon",
            "biological weapon",
            "genocide",
            "ethnic cleansing",
        ),
    ),
    (
        4,
        (
            "massacre",
            "offensive",
            "invasion",
            "invades",
            "invaded",
            "mass casualties",
            "state of emergency",
            "martial law",
            "coup",
            "atrocity",
            "war crime",
        ),
    ),
    (
        3,
        (
            "clash",
            "shelling",
            "airstrike",
            "air strike",
            "explosion",
            "bombing",
            "casualties",
            "earthquake",
            "quake",
            "flood",
            "wildfire",
            "killed",
            "gunmen",
            "shot dead",
            "hostage",
        ),
    ),
    (
        2,
        (
            "protest",
            "strike",
            "unrest",
            "evacuat",
            "sanction",
            "riot",
            "blockade",
            "ceasefire",
            "crackdown",
            "detained",
            "expel",
        ),
    ),
]


@dataclass(frozen=True)
class Place:
    name: str
    lat: float
    lon: float
    kind: str  # "city" | "country"

    @property
    def confidence(self) -> str:
        return "high" if self.kind == "city" else "low"


class _Matcher:
    """Word-boundary alternation over a set of surface forms.

    Boundaries use lookarounds rather than ``\\b`` so forms ending in punctuation ("U.S.")
    still anchor correctly. Longest alternative first, so "South Sudan" wins over "Sudan".
    """

    def __init__(self, forms: dict[str, str]) -> None:
        ci = {k: v for k, v in forms.items() if len(k) > SHORT_ALIAS_LEN}
        cs = {k: v for k, v in forms.items() if len(k) <= SHORT_ALIAS_LEN}
        self._ci_map = {k.lower(): v for k, v in ci.items()}
        self._cs_map = dict(cs)
        self._ci = self._compile(ci, re.IGNORECASE)
        self._cs = self._compile(cs, 0)

    @staticmethod
    def _compile(forms: Iterable[str], flags: int) -> re.Pattern[str] | None:
        alts = sorted(forms, key=len, reverse=True)
        if not alts:
            return None
        return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(a) for a in alts) + r")(?!\w)", flags)

    def search(self, text: str) -> tuple[str, int] | None:
        """Return (canonical name, match position), preferring the earliest then longest."""
        best: tuple[str, int] | None = None
        candidates = ((self._ci, self._ci_map, True), (self._cs, self._cs_map, False))
        for pattern, mapping, lower in candidates:
            if pattern is None:
                continue
            m = pattern.search(text)
            if m is None:
                continue
            key = m.group(0).lower() if lower else m.group(0)
            canonical = mapping.get(key)
            if canonical is None:
                continue
            cand = (canonical, m.start())
            if best is None or cand[1] < best[1]:
                best = cand
        return best


class Gazetteer:
    """Offline place lookup. Cities are checked before countries — a headline naming both
    should land on the city, which is the more specific claim."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self.places: dict[str, Place] = {}
        for kind, key in (("city", "cities"), ("country", "countries")):
            for name, lat, lon in raw.get(key, []):
                self.places.setdefault(name, Place(name=name, lat=lat, lon=lon, kind=kind))

        city_forms = {n: n for n, p in self.places.items() if p.kind == "city"}
        country_forms = {n: n for n, p in self.places.items() if p.kind == "country"}
        for alias, canonical in raw.get("aliases", {}).items():
            target = self.places.get(canonical)
            if target is None:
                continue
            (city_forms if target.kind == "city" else country_forms)[alias] = canonical

        self._city = _Matcher(city_forms)
        self._country = _Matcher(country_forms)

    @classmethod
    def load(cls, path: Path | None = None) -> Gazetteer:
        p = path or (DATA_DIR / "gazetteer.json")
        return cls(json.loads(p.read_text("utf-8")))

    def locate(self, text: str) -> Place | None:
        for matcher in (self._city, self._country):
            hit = matcher.search(text)
            if hit is not None:
                return self.places[hit[0]]
        return None


def severity_for(text: str) -> int:
    """Highest matching tier, or 1. Matched case-insensitively as substrings — "evacuat"
    deliberately covers evacuate/evacuated/evacuation."""
    low = text.lower()
    for severity, keywords in SEVERITY_TIERS:
        if any(k in low for k in keywords):
            return severity
    return 1


# ---------------------------------------------------------------------------
# Feed parsing
# ---------------------------------------------------------------------------


def _local(tag: str) -> str:
    """``{http://purl.org/rss/1.0/}item`` → ``item``. Feeds disagree wildly about
    namespaces; matching on local name covers RSS 2.0, RDF and Atom in one pass."""
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(item: ElementTree.Element, names: tuple[str, ...]) -> str | None:
    for child in item:
        if _local(child.tag) in names and child.text and child.text.strip():
            return child.text.strip()
    return None


def _link_of(item: ElementTree.Element) -> str | None:
    # Atom puts the URL in an attribute; RSS puts it in the element text.
    for child in item:
        if _local(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        rel = child.attrib.get("rel", "alternate")
        if href and rel == "alternate":
            return href.strip()
        if child.text and child.text.strip():
            return child.text.strip()
    return None


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:  # RFC 822, the RSS 2.0 form
        return parsedate_to_datetime(raw).astimezone(UTC)
    except (TypeError, ValueError):
        pass
    try:  # ISO 8601, the Atom form
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def strip_html(raw: str | None) -> str:
    if not raw:
        return ""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()


def feed_label(root: ElementTree.Element, url: str) -> str:
    """Prefer the feed's own channel title; fall back to its host."""
    for el in root.iter():
        if _local(el.tag) == "channel" or _local(el.tag) == "feed":
            title = _child_text(el, ("title",))
            if title:
                return title[:40]
    for el in root.iter():
        if _local(el.tag) == "title" and el.text and el.text.strip():
            return el.text.strip()[:40]
    return urlparse(url).netloc or "RSS"


def parse_feed(
    xml_text: str,
    url: str,
    *,
    gaz: Gazetteer,
    ttl: int,
    max_items: int,
    now: datetime | None = None,
) -> tuple[list[MEvent], dict[str, int]]:
    """Blocking. One feed document → events. Call via ``asyncio.to_thread``."""
    now = now or datetime.now(UTC)
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise FetchError(f"malformed XML from {urlparse(url).netloc}: {exc}"[:200]) from exc

    label = feed_label(root, url)
    stats = {"items": 0, "kept": 0, "no_location": 0, "no_title": 0}
    events: list[MEvent] = []

    for item in root.iter():
        if _local(item.tag) not in ("item", "entry"):
            continue
        stats["items"] += 1
        if stats["kept"] >= max_items:
            break

        title = strip_html(_child_text(item, ("title",)))
        if not title:
            stats["no_title"] += 1
            continue
        summary = strip_html(_child_text(item, ("description", "summary", "content", "encoded")))
        link = _link_of(item)

        haystack = f"{title} {summary}"
        place = gaz.locate(haystack)
        if place is None:
            stats["no_location"] += 1
            continue

        published = _parse_date(_child_text(item, ("pubdate", "published", "updated", "date")))
        native = link or title
        events.append(
            MEvent(
                id=f"{NAME}:{hashlib.sha1(native.encode('utf-8')).hexdigest()[:16]}",
                type="news",
                ts=published or now,
                lat=place.lat,
                lon=place.lon,
                severity=severity_for(haystack),
                title=title[:120],
                source=label,
                url=link,
                ttl=ttl,
                payload={
                    "feed": url,
                    "summary": summary[:400] or None,
                    "geocode": {
                        "match": place.name,
                        "kind": place.kind,
                        "confidence": place.confidence,
                    },
                    "ts_source": "feed" if published else "fetch",
                },
            )
        )
        stats["kept"] += 1

    return events, stats


class RssFetcher:
    name = NAME
    schedule = "*/5 * * * *"
    ttl_default = 12 * 3600
    min_interval_s = 120

    def __init__(self, gazetteer: Gazetteer | None = None) -> None:
        # Loaded once — compiling the alternations on every poll would be wasteful.
        self._gaz = gazetteer or Gazetteer.load()

    async def fetch(self, ctx: FetchContext) -> AsyncIterator[MEvent]:
        if ctx.fixture_mode:
            async for e in iter_fixture(self.name):
                yield e
            return

        urls = [u.strip() for u in ctx.settings.rss_feeds.split(";") if u.strip()]
        if not urls:
            ctx.log.info("rss: no feeds configured, nothing to do")
            return

        seen: set[str] = set()
        totals = {"items": 0, "kept": 0, "no_location": 0, "no_title": 0}
        failures: list[str] = []

        for url in urls:
            try:
                resp = await ctx.http.get(url)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                # One dead publisher must not blank the layer; only a total wipeout is an error.
                ctx.log.warning("rss %s failed: %s", urlparse(url).netloc, exc)
                failures.append(f"{urlparse(url).netloc}: {type(exc).__name__}")
                continue

            try:
                events, stats = await asyncio.to_thread(
                    parse_feed,
                    resp.text,
                    url,
                    gaz=self._gaz,
                    ttl=self.ttl_default,
                    max_items=ctx.settings.rss_max_items_per_feed,
                )
            except FetchError as exc:
                ctx.log.warning("rss parse failed for %s: %s", urlparse(url).netloc, exc)
                failures.append(str(exc)[:80])
                continue

            for k, v in stats.items():
                totals[k] += v
            for ev in events:
                if ev.id in seen:  # wires syndicate the same story to several feeds
                    continue
                seen.add(ev.id)
                yield ev

        if len(failures) == len(urls):
            raise FetchError("; ".join(failures)[:200] or "all feeds failed")
        ctx.log.info("rss: %s from %d feed(s)", totals, len(urls) - len(failures))
