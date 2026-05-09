"""Tests for mosaic.py — VRT parsing and layout construction."""

import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.mosaic import (
    build_mosaic_from_vrt,
    build_mosaic_from_filenames,
    Mosaic,
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


# ---------------------------------------------------------------------------
# Georef-based mosaic assembly
# ---------------------------------------------------------------------------

def test_georef_mosaic_nonexistent_files():
    """build_mosaic_from_georef_files returns None when no georef can be read."""
    from src.core.mosaic import build_mosaic_from_georef_files
    result = build_mosaic_from_georef_files([Path("/nonexistent/tile.tif")])
    assert result is None


def test_georef_mosaic_from_tab_files():
    """build_mosaic_from_georef_files uses .tab side-car files for positioning."""
    import tempfile
    from src.core.mosaic import build_mosaic_from_georef_files

    _TAB_TEMPLATE = """!table
!version 300
!charset WindowsLatin1
Definition Table
  File "{name}.tif"
  Type "RASTER"
  ({xmin},{ymax}) (0,0) Label "Pt 1"
  ({xmax},{ymax}) ({wpx},0) Label "Pt 2"
  ({xmax},{ymin}) ({wpx},{hpx}) Label "Pt 3"
  ({xmin},{ymin}) (0,{hpx}) Label "Pt 4"
  CoordSys Earth Projection 3, 33, "m", 3, 46.5
"""
    # Two tiles side by side: tile1 at x=700..800 km, tile2 at x=800..900 km
    tiles_data = [
        dict(name="tile1", xmin=700000, xmax=800000, ymin=6120000, ymax=6220000, wpx=10000, hpx=10000),
        dict(name="tile2", xmin=800000, xmax=900000, ymin=6120000, ymax=6220000, wpx=10000, hpx=10000),
    ]

    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        tile_paths = []
        for td in tiles_data:
            tif_path = tmp_p / f"{td['name']}.tif"
            tab_path = tmp_p / f"{td['name']}.tab"
            tif_path.write_bytes(b"\x00" * 16)   # dummy image
            tab_path.write_text(_TAB_TEMPLATE.format(**td), encoding="latin-1")
            tile_paths.append(tif_path)

        layout = build_mosaic_from_georef_files(tile_paths)

    assert layout is not None
    # Total width = 200 km at 10 m/px = 20 000 px; height = 100 km = 10 000 px
    assert layout.total_width == 20000
    assert layout.total_height == 10000
    assert len(layout.tiles) == 2
    assert abs(layout.pixel_size_m - 10.0) < 0.1

    # tile1 at x_off=0, tile2 at x_off=10000
    t1 = next(t for t in layout.tiles if t.x_off == 0)
    t2 = next(t for t in layout.tiles if t.x_off == 10000)
    assert t1.width == 10000
    assert t2.width == 10000

    # Global geo extent
    assert layout.geo_extent is not None
    assert abs(layout.geo_extent.min_x - 700000.0) < 1.0
    assert abs(layout.geo_extent.max_x - 900000.0) < 1.0


def test_mosaic_from_files_tries_georef():
    """Mosaic.from_files with try_georef=False falls back to filename layout."""
    import tempfile
    from src.core.mosaic import Mosaic
    from PIL import Image as _PIL_Image

    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        # Create a minimal valid 1×1 TIFF with IGN naming (no .tab file)
        tif = tmp_p / "SC25_TOUR_0700_6220_L93_E100.tif"
        _PIL_Image.new("RGB", (4, 4), color=(128, 128, 128)).save(str(tif))

        # With try_georef=False → goes directly to filename-based layout
        mosaic = Mosaic.from_files([tif], pixel_size_m=2.5, try_georef=False)

    assert mosaic is not None
    # No geo_extent: filename-based layout doesn't set one
    assert mosaic.geo_extent is None


def test_mosaic_cropped_limits_to_selected_region():
    layout = _make_layout()
    mosaic = Mosaic(layout)

    cropped = mosaic.cropped(50, 40, 120, 110)

    assert cropped.width == 120
    assert cropped.height == 110
    assert len(cropped.layout.tiles) == 4

    # top-left tile in crop starts at origin
    t0 = next(t for t in cropped.layout.tiles if t.path == Path("a.tif"))
    assert t0.x_off == 0
    assert t0.y_off == 0
    assert t0.width == 50
    assert t0.height == 60


def test_mosaic_cropped_rejects_empty_region():
    mosaic = Mosaic(_make_layout())

    with pytest.raises(ValueError):
        mosaic.cropped(0, 0, 0, 100)

    with pytest.raises(ValueError):
        mosaic.cropped(0, 0, 100, 0)


def test_mosaic_cropped_preserves_source_offsets():
    """Cropping must keep source-image offsets so exported pixels match selection."""
    from PIL import Image

    with tempfile.TemporaryDirectory() as tmp:
        img_path = Path(tmp) / "tile.png"
        img = Image.new("RGB", (10, 4), color=(0, 0, 0))
        for x in range(10):
            for y in range(4):
                img.putpixel((x, y), (x * 20, y * 50, 0))
        img.save(img_path)

        layout = MosaicLayout(
            tiles=[TileInfo(path=img_path, x_off=0, y_off=0, width=10, height=4)],
            total_width=10,
            total_height=4,
        )
        mosaic = Mosaic(layout)

        cropped = mosaic.cropped(6, 0, 3, 4)
        region = cropped.get_region(0, 0, 3, 4)

    assert region.size == (3, 4)
    # Selected x-range starts at source x=6, so first output pixel must match x=6.
    assert region.getpixel((0, 0)) == (120, 0, 0)
    assert region.getpixel((2, 3)) == (160, 150, 0)
