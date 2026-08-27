"""H3 hex binning for the conflict-density heat map (docs/phase-1-layers.md §5).

Individual markers answer "what happened here". They cannot answer "where is it getting
worse", which is the question the map exists for. This bins events into H3 cells so density is
comparable across latitudes — square degree-bins shrink toward the poles and would make
Norway look busier than it is.

``bin_events`` is pure and synchronous: events in, cells out. No I/O, no globals, no clock.
If it ever exceeds ~50 ms, the caller moves it to ``asyncio.to_thread`` (spine §8).

**What the output means.** Weighting aggregates GDELT, which is *media coverage*, not ground
truth: a dense cell means dense reporting. CLAUDE.md rule 5 applies — the client legend says
"reported conflict density", never "conflict".
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import h3

from ..models import MEvent

# H3 resolution 3 is ~59 km edge — country-region scale, the right altitude for the question.
MIN_RESOLUTION = 0
MAX_RESOLUTION = 7


def clamp_resolution(res: int, ceiling: int = MAX_RESOLUTION) -> int:
    return max(MIN_RESOLUTION, min(res, min(ceiling, MAX_RESOLUTION)))


@dataclass(frozen=True)
class HeatCell:
    h3: str
    lat: float
    lon: float
    count: int
    weight: int
    max_severity: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "h3": self.h3,
            "lat": self.lat,
            "lon": self.lon,
            "count": self.count,
            "weight": self.weight,
            "max_severity": self.max_severity,
        }


def bin_events(events: Iterable[MEvent], resolution: int) -> list[HeatCell]:
    """Group events into H3 cells at ``resolution``, heaviest cell first.

    ``weight`` is ``sum(1 + severity)`` rather than a raw count, so one cell of five severity-5
    events outranks ten severity-0 ones. The client's heatmap weights by this. The +1 keeps a
    severity-0 event counting for something — it still happened.
    """
    res = clamp_resolution(resolution)
    counts: dict[str, int] = {}
    weights: dict[str, int] = {}
    peaks: dict[str, int] = {}

    for ev in events:
        cell = h3.latlng_to_cell(ev.lat, ev.lon, res)
        counts[cell] = counts.get(cell, 0) + 1
        weights[cell] = weights.get(cell, 0) + 1 + ev.severity
        peaks[cell] = max(peaks.get(cell, 0), ev.severity)

    cells: list[HeatCell] = []
    for cell, count in counts.items():
        lat, lon = h3.cell_to_latlng(cell)
        cells.append(
            HeatCell(
                h3=cell,
                lat=lat,
                lon=lon,
                count=count,
                weight=weights[cell],
                max_severity=peaks[cell],
            )
        )
    # Deterministic order: heaviest first, then cell id so equal weights never shuffle.
    cells.sort(key=lambda c: (-c.weight, c.h3))
    return cells
