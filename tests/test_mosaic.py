"""Tests for mosaic.py — VRT parsing and layout construction."""

import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.mosaic import (
    build_mosaic_from_vrt,
    build_mosaic_from_filenames,
    MosaicLayout,
    TileInfo,
)


# ---------------------------------------------------------------------------
# VRT-based mosaic
# ---------------------------------------------------------------------------

_SAMPLE_VRT = """<VRTDataset rasterXSize="20000" rasterYSize="10000">
  <GeoTransform>700000.0, 5.0, 0.0, 6220000.0, 0.0, -5.0</GeoTransform>
  <VRTRasterBand dataType="Byte" band="1">
    <SimpleSource>
      <SourceFilename relativeToVRT="1">tile1.tif</SourceFilename>
      <SrcRect xOff="0" yOff="0" xSize="10000" ySize="10000"/>
      <DstRect xOff="0" yOff="0" xSize="10000" ySize="10000"/>
    </SimpleSource>
    <SimpleSource>
      <SourceFilename relativeToVRT="1">tile2.tif</SourceFilename>
      <SrcRect xOff="0" yOff="0" xSize="10000" ySize="10000"/>
      <DstRect xOff="10000" yOff="0" xSize="10000" ySize="10000"/>
    </SimpleSource>
  </VRTRasterBand>
</VRTDataset>
"""


def test_vrt_mosaic_layout():
    with tempfile.NamedTemporaryFile(suffix=".vrt", mode="w", delete=False) as f:
        f.write(_SAMPLE_VRT)
        vrt_path = Path(f.name)

    try:
        layout = build_mosaic_from_vrt(vrt_path)
        assert layout is not None
        assert layout.total_width == 20000
        assert layout.total_height == 10000
        assert len(layout.tiles) == 2
        assert abs(layout.pixel_size_m - 5.0) < 0.001

        # First tile at (0,0)
        t0 = next(t for t in layout.tiles if t.x_off == 0)
        assert t0.width == 10000
        assert t0.height == 10000

        # Second tile at (10000, 0)
        t1 = next(t for t in layout.tiles if t.x_off == 10000)
        assert t1.width == 10000
    finally:
        vrt_path.unlink(missing_ok=True)


def test_vrt_mosaic_nonexistent():
    layout = build_mosaic_from_vrt("/nonexistent/file.vrt")
    assert layout is None


# ---------------------------------------------------------------------------
# tiles_in_region
# ---------------------------------------------------------------------------

def _make_layout() -> MosaicLayout:
    tiles = [
        TileInfo(path=Path("a.tif"), x_off=0, y_off=0, width=100, height=100),
        TileInfo(path=Path("b.tif"), x_off=100, y_off=0, width=100, height=100),
        TileInfo(path=Path("c.tif"), x_off=0, y_off=100, width=100, height=100),
        TileInfo(path=Path("d.tif"), x_off=100, y_off=100, width=100, height=100),
    ]
    return MosaicLayout(tiles=tiles, total_width=200, total_height=200)


def test_tiles_in_region_full():
    layout = _make_layout()
    found = layout.tiles_in_region(0, 0, 200, 200)
    assert len(found) == 4


def test_tiles_in_region_top_left():
    layout = _make_layout()
    found = layout.tiles_in_region(0, 0, 50, 50)
    assert len(found) == 1
    assert found[0].path == Path("a.tif")


def test_tiles_in_region_bottom_right():
    layout = _make_layout()
    found = layout.tiles_in_region(150, 150, 50, 50)
    assert len(found) == 1
    assert found[0].path == Path("d.tif")


def test_tiles_in_region_spanning_boundary():
    layout = _make_layout()
    found = layout.tiles_in_region(80, 0, 40, 100)
    # Should overlap tiles a (0..100) and b (100..200)
    paths = {t.path for t in found}
    assert Path("a.tif") in paths
    assert Path("b.tif") in paths


# ---------------------------------------------------------------------------
# Filename-based layout (no actual image loading needed)
# ---------------------------------------------------------------------------

def test_filename_mosaic_empty():
    layout = build_mosaic_from_filenames([])
    assert layout.total_width == 0
    assert layout.total_height == 0
    assert len(layout.tiles) == 0



