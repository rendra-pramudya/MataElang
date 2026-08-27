"""Runtime settings. Everything comes from ``.env`` or the environment; nothing is hardcoded
so the same code runs in fixture mode on a laptop and live on the monitor box.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    port: int = 8000
    fixture_mode: bool = False
    db_path: Path = REPO_ROOT / "data" / "mataelang.sqlite"
    retention_days: int = 90
    web_dir: Path = REPO_ROOT / "web"
    boundaries_dir: Path = REPO_ROOT / "data" / "boundaries"
    user_agent: str = "MataElang/0.1 (personal monitor)"
    http_timeout_s: float = 20.0

    # USGS — endpoint confirmed 2026-08-26.
    usgs_feed: str = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson"

    # GDELT 2.0 — endpoint confirmed 2026-08-26. lastupdate.txt points at the newest 15-min export.
    gdelt_lastupdate_url: str = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"
    gdelt_min_goldstein: float = -3.0  # keep events at or below this (more negative = worse)
    gdelt_max_events: int = 400  # per 15-min file, most-mentioned first

    # Open-Meteo — endpoint confirmed 2026-08-27. Free, no key. One call covers every point.
    openmeteo_url: str = "https://api.open-meteo.com/v1/forecast"
    # "name,lat,lon" separated by ";". These are watch points, not coverage — phase-1 §3.1.
    openmeteo_points: str = (
        "Jakarta,-6.21,106.85;Surabaya,-7.25,112.75;Medan,3.59,98.67;"
        "Makassar,-5.15,119.43;Banda Aceh,5.55,95.32;Jayapura,-2.53,140.72;"
        "Singapore,1.35,103.82;Manila,14.60,120.98;Tokyo,35.68,139.69;Sydney,-33.87,151.21"
    )

    # RSS — publisher terms vary per feed; keep the default list short and replaceable.
    rss_feeds: str = (
        "https://feeds.bbci.co.uk/news/world/rss.xml;"
        "https://www.aljazeera.com/xml/rss/all.xml;"
        "https://rss.dw.com/rdf/rss-en-world"
    )
    rss_max_items_per_feed: int = 60

    # Heat map (phase-1 §5). Resolution 3 is ~59 km edge — country-region scale.
    heat_default_resolution: int = 3
    heat_max_resolution: int = 7
    heat_query_limit: int = 10000

    # Expiry sweep / status cadence, seconds. Spec: 60 and 30.
    sweep_interval_s: int = 60
    status_interval_s: int = 30
    ws_ping_interval_s: int = 20


def load_settings() -> Settings:
    return Settings()
