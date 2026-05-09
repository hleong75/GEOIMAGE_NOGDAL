"""Tests for UI selection-region mapping helpers."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ui.main_window import _map_region_between_sizes


def test_map_region_between_sizes_scales_coordinates():
    region = (30, 20, 60, 40)
    mapped = _map_region_between_sizes(region, src_size=(300, 200), dst_size=(3000, 2000))
    assert mapped == (300, 200, 600, 400)


def test_map_region_between_sizes_clamps_to_destination_bounds():
    region = (90, 90, 20, 20)
    mapped = _map_region_between_sizes(region, src_size=(100, 100), dst_size=(1000, 1000))
    assert mapped == (900, 900, 100, 100)
