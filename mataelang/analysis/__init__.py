"""Spatial analysis.

Opened one phase early for the Phase 1 conflict heat map (docs/phase-1-layers.md §5.1), which
needs exactly one pure function. Only ``h3`` is a dependency here — GeoPandas and Shapely stay
in Phase 2, where CLAUDE.md puts them.
"""

from .heat import HeatCell, bin_events

__all__ = ["HeatCell", "bin_events"]
