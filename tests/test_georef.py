"""Tests for georef.py"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.georef import parse_tab_file, parse_vrt_georef, GeoInfo


# ---------------------------------------------------------------------------
# .tab parser tests
# ---------------------------------------------------------------------------

_SAMPLE_TAB = """!table
!version 300
!charset WindowsLatin1
Definition Table
  File "SC25_TOUR_0700_6220_L93_E100.tif"
  Type "RASTER"
  (700000,6220000) (0,0) Label "Pt 1", ...
  (800000,6220000) (10000,0) Label "Pt 2", ...
  (800000,6120000) (10000,10000) Label "Pt 3", ...
  (700000,6120000) (0,10000) Label "Pt 4", ...
  CoordSys Earth Projection 3, 33, "m", 3, 46.5
"""


def test_parse_tab_basic():
    with tempfile.NamedTemporaryFile(suffix=".tab", mode="w", delete=False, encoding="latin-1") as f:
        f.write(_SAMPLE_TAB)
        path = Path(f.name)

    try:
        info = parse_tab_file(path)
        assert info is not None
        assert info.min_x == 700000.0
        assert info.max_x == 800000.0
        assert info.min_y == 6120000.0
        assert info.max_y == 6220000.0
        assert info.width_px == 10000
        assert info.height_px == 10000
        assert abs(info.pixel_size_x - 10.0) < 0.01  # 100km / 10000 px = 10 m/px
        assert info.source == "tab"
        assert info.is_valid()
    finally:
        path.unlink(missing_ok=True)


def test_parse_tab_nonexistent():
    result = parse_tab_file("/nonexistent/file.tab")
    assert result is None


# ---------------------------------------------------------------------------
# VRT parser tests
# ---------------------------------------------------------------------------

_SAMPLE_VRT = """<VRTDataset rasterXSize="20000" rasterYSize="10000">
  <SRS>EPSG:2154</SRS>
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


def test_parse_vrt_georef():
    with tempfile.NamedTemporaryFile(suffix=".vrt", mode="w", delete=False) as f:
        f.write(_SAMPLE_VRT)
        path = Path(f.name)

    try:
        info = parse_vrt_georef(path)
        assert info is not None
        assert info.width_px == 20000
        assert info.height_px == 10000
        assert abs(info.pixel_size_x - 5.0) < 0.001
        assert abs(info.pixel_size_y - 5.0) < 0.001
        assert info.min_x == 700000.0
        assert abs(info.max_x - 800000.0) < 1.0
        assert info.source == "vrt"
        assert info.is_valid()
    finally:
        path.unlink(missing_ok=True)


def test_parse_vrt_nonexistent():
    result = parse_vrt_georef("/nonexistent/file.vrt")
    assert result is None


def test_geoinfo_properties():
    info = GeoInfo(
        min_x=0, min_y=0, max_x=100000, max_y=50000,
        pixel_size_x=5.0, pixel_size_y=5.0,
        width_px=20000, height_px=10000,
    )
    assert info.width_m == 100000
    assert info.height_m == 50000
    assert info.is_valid()


def test_geoinfo_invalid():
    info = GeoInfo()
    assert not info.is_valid()



