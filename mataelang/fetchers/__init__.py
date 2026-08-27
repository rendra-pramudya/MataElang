"""Fetchers. Register sources here; the scheduler iterates ``ALL``."""

from .base import FetchContext, Fetcher, FetchError
from .gdelt import GdeltFetcher
from .openmeteo import OpenMeteoFetcher
from .rss import RssFetcher
from .usgs import UsgsFetcher

ALL: list[Fetcher] = [UsgsFetcher(), GdeltFetcher(), OpenMeteoFetcher(), RssFetcher()]

__all__ = [
    "ALL",
    "FetchContext",
    "FetchError",
    "Fetcher",
    "GdeltFetcher",
    "OpenMeteoFetcher",
    "RssFetcher",
    "UsgsFetcher",
]
