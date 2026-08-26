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

    # Expiry sweep / status cadence, seconds. Spec: 60 and 30.
    sweep_interval_s: int = 60
    status_interval_s: int = 30
    ws_ping_interval_s: int = 20


def load_settings() -> Settings:
    return Settings()
