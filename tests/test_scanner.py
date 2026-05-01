"""Tests for scanner.py"""

import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.scanner import scan_directory, _extract_ign_coords, RASTER_EXTENSIONS


def test_extract_ign_coords_standard():
    gx, gy = _extract_ign_coords("SC25_TOUR_0700_6220_L93_E100.jp2")
    assert gx == 700
    assert gy == 6220


def test_extract_ign_coords_no_match():
    gx, gy = _extract_ign_coords("random_image.tif")
    assert gx is None
    assert gy is None


def test_scan_empty_dir():
    with tempfile.TemporaryDirectory() as tmp:
        result = scan_directory(tmp)
        assert result.total_files == 0
        assert result.has_vrt is False


def test_scan_nonexistent():
    result = scan_directory("/nonexistent/path/xyz")
    assert result.total_files == 0
    assert len(result.errors) > 0


def test_scan_finds_raster_files():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        # Create dummy raster files
        for ext in (".jp2", ".tif", ".png"):
            (tmp_p / f"tile{ext}").write_bytes(b"\x00" * 10)
        # Create files that should be ignored
        (tmp_p / "checksum.md5").write_text("abc123")
        (tmp_p / "info.pdf").write_bytes(b"%PDF")

        result = scan_directory(tmp)
        assert result.total_files == 3
        exts = {f.extension for f in result.raster_files}
        assert ".jp2" in exts
        assert ".tif" in exts
        assert ".png" in exts


def test_scan_finds_tab_and_vrt():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        (tmp_p / "tile.tif").write_bytes(b"\x00" * 10)
        (tmp_p / "tile.tab").write_text("dummy tab")
        (tmp_p / "mosaique.vrt").write_text("<VRTDataset/>")

        result = scan_directory(tmp)
        assert len(result.tab_files) == 1
        assert len(result.vrt_files) == 1

        # The .tif should have been associated with the .tab
        tif = next((f for f in result.raster_files if f.extension == ".tif"), None)
        assert tif is not None
        assert tif.tab_file is not None


def test_scan_sorts_by_grid():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        # Create tiles with known grid coords
        names = [
            "SC25_TOUR_0800_6220_L93_E100.tif",
            "SC25_TOUR_0700_6220_L93_E100.tif",
            "SC25_TOUR_0700_6320_L93_E100.tif",
        ]
        for name in names:
            (tmp_p / name).write_bytes(b"\x00" * 10)

        result = scan_directory(tmp)
        assert result.total_files == 3
        # First file should have highest y (6320)
        assert result.raster_files[0].grid_y == 6320


def test_get_grid_bounds():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        for name in [
            "SC25_TOUR_0700_6220_L93_E100.tif",
            "SC25_TOUR_0800_6220_L93_E100.tif",
            "SC25_TOUR_0700_6320_L93_E100.tif",
        ]:
            (tmp_p / name).write_bytes(b"\x00" * 10)

        result = scan_directory(tmp)
        bounds = result.get_grid_bounds()
        assert bounds is not None
        min_x, min_y, max_x, max_y = bounds
        assert min_x == 700
        assert max_x == 800
        assert min_y == 6220
        assert max_y == 6320



