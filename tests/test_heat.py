import h3
import pytest

from mataelang.analysis import bin_events
from mataelang.analysis.heat import clamp_resolution

from .conftest import make_event


def _ev(i, lat, lon, severity=0):
    return make_event(id=f"gdelt:{i}", type="conflict", lat=lat, lon=lon, severity=severity)


def test_bin_events_groups_neighbours_into_one_cell():
    # Two points ~1 km apart share a resolution-3 cell (~59 km edge).
    cells = bin_events([_ev(1, 48.45, 35.05), _ev(2, 48.46, 35.06)], 3)
    assert len(cells) == 1 and cells[0].count == 2
    assert cells[0].h3 == h3.latlng_to_cell(48.45, 35.05, 3)


def test_bin_events_separates_distant_points():
    cells = bin_events([_ev(1, 48.45, 35.05), _ev(2, -6.21, 106.85)], 3)
    assert len(cells) == 2


def test_weight_is_severity_weighted_not_a_raw_count():
    # One severity-5 event must outweigh three severity-0 ones: 6 > 3.
    heavy = bin_events([_ev(1, 10.0, 10.0, severity=5)], 3)[0]
    light = bin_events([_ev(i, -30.0, -60.0) for i in range(3)], 3)[0]
    assert heavy.weight == 6 and light.weight == 3
    assert heavy.count == 1 and light.count == 3


def test_cells_are_sorted_heaviest_first():
    events = [_ev(1, 10.0, 10.0, severity=5), *[_ev(i + 2, -30.0, -60.0) for i in range(2)]]
    cells = bin_events(events, 3)
    assert [c.weight for c in cells] == [6, 2]


def test_max_severity_is_the_peak_not_the_mean():
    cells = bin_events([_ev(1, 10.0, 10.0, severity=0), _ev(2, 10.01, 10.01, severity=4)], 3)
    assert cells[0].max_severity == 4


def test_counts_sum_to_input_length():
    # Acceptance §7.6: cell counts must account for every event in range.
    events = [_ev(i, 40 + i * 0.5, 30 + i * 0.5, severity=i % 6) for i in range(40)]
    cells = bin_events(events, 3)
    assert sum(c.count for c in cells) == 40


def test_empty_input_is_empty_output():
    assert bin_events([], 3) == []


def test_cell_centre_is_returned():
    cell = bin_events([_ev(1, 48.45, 35.05)], 3)[0]
    lat, lon = h3.cell_to_latlng(cell.h3)
    assert (cell.lat, cell.lon) == (lat, lon)


@pytest.mark.parametrize("res,expected", [(-5, 0), (0, 0), (3, 3), (7, 7), (15, 7)])
def test_clamp_resolution(res, expected):
    assert clamp_resolution(res) == expected


def test_clamp_resolution_honours_a_lower_ceiling():
    assert clamp_resolution(7, ceiling=4) == 4


def test_bin_events_is_deterministic():
    events = [_ev(i, 10 + i, 20 + i, severity=i % 6) for i in range(10)]
    assert [c.h3 for c in bin_events(events, 3)] == [c.h3 for c in bin_events(events, 3)]
