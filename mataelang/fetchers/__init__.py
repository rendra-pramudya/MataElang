"""Fetchers. Register Phase 0 sources here; the scheduler iterates ``ALL``."""

from .base import FetchContext, Fetcher, FetchError
from .gdelt import GdeltFetcher
from .usgs import UsgsFetcher

ALL: list[Fetcher] = [UsgsFetcher(), GdeltFetcher()]

__all__ = ["ALL", "FetchContext", "FetchError", "Fetcher", "GdeltFetcher", "UsgsFetcher"]
