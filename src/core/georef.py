"""
georef.py — Georeferencing parser for IGN data.

Supports:
  * MapInfo .tab files  (simple 4-corner bounding box)
  * VRT XML files       (reads SRS and GeoTransform)
  * GeoTIFF tag reading via tifffile (if available)
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple
from xml.etree import ElementTree as ET


@dataclass
class GeoInfo:
    """Georeferencing information for a raster dataset."""

    # Bounding box in native CRS (usually Lambert 93, EPSG:2154)
    min_x: float = 0.0
    min_y: float = 0.0
    max_x: float = 0.0
    max_y: float = 0.0

    # Pixel size in CRS units (usually metres)
    pixel_size_x: float = 1.0
    pixel_size_y: float = 1.0

    # Image dimensions (pixels)
    width_px: int = 0
    height_px: int = 0

    # CRS string (WKT or PROJ)
    crs: str = "EPSG:2154"

    source: str = "unknown"

    @property
    def width_m(self) -> float:
        return self.max_x - self.min_x

    @property
    def height_m(self) -> float:
        return self.max_y - self.min_y

    @property
    def scale_denominator(self) -> float:
        """Approximate map scale denominator (e.g. 25000 for 1:25 000)."""
        if self.pixel_size_x > 0:
            # IGN SCAN25: 1 pixel = 1 metre at 1:1, so pixel_size_x ≈ denominator/dpi*0.0254
            # For SCAN25 at 254 dpi: pixel_size = 25000/254*0.0254 = ~2.5 m
            # Return raw value so callers can compute as needed
            return self.pixel_size_x
        return 1.0

    def is_valid(self) -> bool:
        return self.max_x > self.min_x and self.max_y > self.min_y


# ---------------------------------------------------------------------------
# .tab parser (MapInfo format)
# ---------------------------------------------------------------------------

_COORD_RE = re.compile(
    r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)",
    re.IGNORECASE,
)


def parse_tab_file(tab_path: str | Path) -> Optional[GeoInfo]:
    """
    Parse a MapInfo .tab file and extract bounding box.

    IGN .tab files typically look like::

        !table
        !version 300
        !charset WindowsLatin1
        Definition Table
          File "SC25_TOUR_0700_6220_L93_E100.tif"
          Type "RASTER"
          (700000,6220000) (0,0) Label "Pt 1", ...
          (800000,6220000) (10000,0) Label "Pt 2", ...
          (800000,6120000) (10000,10000) Label "Pt 3", ...
          (700000,6120000) (0,10000) Label "Pt 4", ...
          CoordSys Earth Projection 3, 33, "m", 3, 46.5, ...
    """
    tab_path = Path(tab_path)
    try:
        text = tab_path.read_text(encoding="latin-1", errors="replace")
    except OSError:
        return None

    # Extract all coordinate pairs (geo_x, geo_y) from control point lines
    geo_coords: list[Tuple[float, float]] = []
    pixel_coords: list[Tuple[float, float]] = []

    lines = text.splitlines()
    in_definition = False
    for line in lines:
        stripped = line.strip()
        if "Definition Table" in stripped:
            in_definition = True
            continue
        if not in_definition:
            continue

        # Each control point line has two coordinate pairs
        matches = _COORD_RE.findall(stripped)
        if len(matches) >= 2:
            gx, gy = float(matches[0][0]), float(matches[0][1])
            px, py = float(matches[1][0]), float(matches[1][1])
            geo_coords.append((gx, gy))
            pixel_coords.append((px, py))

    if len(geo_coords) < 2:
        return None

    xs = [c[0] for c in geo_coords]
    ys = [c[1] for c in geo_coords]
    pxs = [c[0] for c in pixel_coords]
    pys = [c[1] for c in pixel_coords]

    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    width_px = int(max(pxs))
    height_px = int(max(pys))

    pixel_size_x = (max_x - min_x) / width_px if width_px else 1.0
    pixel_size_y = (max_y - min_y) / height_px if height_px else 1.0

    return GeoInfo(
        min_x=min_x,
        min_y=min_y,
        max_x=max_x,
        max_y=max_y,
        pixel_size_x=pixel_size_x,
        pixel_size_y=pixel_size_y,
        width_px=width_px,
        height_px=height_px,
        source="tab",
    )


# ---------------------------------------------------------------------------
# VRT XML parser
# ---------------------------------------------------------------------------

def parse_vrt_georef(vrt_path: str | Path) -> Optional[GeoInfo]:
    """
    Extract georeferencing from a GDAL .vrt XML file.

    Reads GeoTransform, RasterXSize, RasterYSize and (optionally) SRS.
    """
    vrt_path = Path(vrt_path)
    try:
        tree = ET.parse(str(vrt_path))
    except (ET.ParseError, OSError):
        return None

    root = tree.getroot()

    # Dimensions
    try:
        width_px = int(root.attrib.get("rasterXSize", 0))
        height_px = int(root.attrib.get("rasterYSize", 0))
    except (ValueError, AttributeError):
        return None

    # GeoTransform: "origin_x, pixel_size_x, 0, origin_y, 0, -pixel_size_y"
    gt_el = root.find("GeoTransform")
    if gt_el is None or not gt_el.text:
        return None

    try:
        gt = [float(v.strip()) for v in gt_el.text.split(",")]
    except ValueError:
        return None

    if len(gt) < 6:
        return None

    origin_x, pixel_size_x, _, origin_y, _, pixel_size_y_neg = gt[:6]
    pixel_size_y = abs(pixel_size_y_neg)

    min_x = origin_x
    max_x = origin_x + width_px * pixel_size_x
    max_y = origin_y
    min_y = origin_y + height_px * pixel_size_y_neg  # pixel_size_y_neg is negative

    # SRS
    crs = "EPSG:2154"
    srs_el = root.find("SRS")
    if srs_el is not None and srs_el.text:
        crs = srs_el.text.strip()

    return GeoInfo(
        min_x=min_x,
        min_y=min_y,
        max_x=max_x,
        max_y=max_y,
        pixel_size_x=pixel_size_x,
        pixel_size_y=pixel_size_y,
        width_px=width_px,
        height_px=height_px,
        crs=crs,
        source="vrt",
    )


# ---------------------------------------------------------------------------
# GeoTIFF minimal reader (no GDAL, no libtiff dependency)
# ---------------------------------------------------------------------------

_TIFF_MAGIC_LE = b"II\x2a\x00"
_TIFF_MAGIC_BE = b"MM\x00\x2a"

# TIFF tag IDs
_TAG_IMAGE_WIDTH = 256
_TAG_IMAGE_HEIGHT = 257
_TAG_MODEL_PIXEL_SCALE = 33550   # GeoTIFF ModelPixelScaleTag
_TAG_MODEL_TIEPOINT = 33922      # GeoTIFF ModelTiepointTag


def _read_uint16(data: bytes, offset: int, le: bool) -> int:
    fmt = "<H" if le else ">H"
    return struct.unpack_from(fmt, data, offset)[0]


def _read_uint32(data: bytes, offset: int, le: bool) -> int:
    fmt = "<I" if le else ">I"
    return struct.unpack_from(fmt, data, offset)[0]


def _read_float64(data: bytes, offset: int, le: bool) -> float:
    fmt = "<d" if le else ">d"
    return struct.unpack_from(fmt, data, offset)[0]


def parse_geotiff_georef(tif_path: str | Path) -> Optional[GeoInfo]:
    """
    Extract basic georeferencing from a GeoTIFF without external libraries.

    Reads ModelPixelScaleTag (33550) and ModelTiepointTag (33922) from
    the TIFF IFD to reconstruct bounding box.
    """
    tif_path = Path(tif_path)
    try:
        with open(tif_path, "rb") as fh:
            header = fh.read(8)
            if len(header) < 8:
                return None

            if header[:4] == _TIFF_MAGIC_LE:
                le = True
            elif header[:4] == _TIFF_MAGIC_BE:
                le = False
            else:
                return None

            ifd_offset = _read_uint32(header, 4, le)
            fh.seek(ifd_offset)
            ifd_count_bytes = fh.read(2)
            if len(ifd_count_bytes) < 2:
                return None
            n_entries = _read_uint16(ifd_count_bytes, 0, le)

            tags: dict[int, tuple] = {}
            for _ in range(n_entries):
                entry = fh.read(12)
                if len(entry) < 12:
                    break
                tag_id = _read_uint16(entry, 0, le)
                data_type = _read_uint16(entry, 2, le)
                count = _read_uint32(entry, 4, le)
                value_offset = _read_uint32(entry, 8, le)
                tags[tag_id] = (data_type, count, value_offset)

            def read_doubles(offset: int, count: int) -> list[float]:
                fh.seek(offset)
                return [_read_float64(fh.read(8), 0, le) for _ in range(count)]

            def read_uint32_val(entry_tuple: tuple) -> int:
                _, count, val_or_offset = entry_tuple
                if count == 1:
                    return val_or_offset
                return val_or_offset

            width_px = height_px = 0
            if _TAG_IMAGE_WIDTH in tags:
                width_px = read_uint32_val(tags[_TAG_IMAGE_WIDTH])
            if _TAG_IMAGE_HEIGHT in tags:
                height_px = read_uint32_val(tags[_TAG_IMAGE_HEIGHT])

            pixel_size_x = pixel_size_y = 1.0
            if _TAG_MODEL_PIXEL_SCALE in tags:
                _, count, offset = tags[_TAG_MODEL_PIXEL_SCALE]
                scales = read_doubles(offset, min(count, 3))
                if len(scales) >= 2:
                    pixel_size_x = scales[0]
                    pixel_size_y = scales[1]

            origin_x = origin_y = 0.0
            if _TAG_MODEL_TIEPOINT in tags:
                _, count, offset = tags[_TAG_MODEL_TIEPOINT]
                tps = read_doubles(offset, min(count, 6))
                if len(tps) >= 6:
                    # pixel_x, pixel_y, pixel_z, geo_x, geo_y, geo_z
                    px_tp, py_tp = tps[0], tps[1]
                    origin_x = tps[3] - px_tp * pixel_size_x
                    origin_y = tps[4] + py_tp * pixel_size_y

    except (OSError, struct.error):
        return None

    if width_px == 0 or height_px == 0:
        return None

    min_x = origin_x
    max_x = origin_x + width_px * pixel_size_x
    max_y = origin_y
    min_y = origin_y - height_px * pixel_size_y

    return GeoInfo(
        min_x=min_x,
        min_y=min_y,
        max_x=max_x,
        max_y=max_y,
        pixel_size_x=pixel_size_x,
        pixel_size_y=pixel_size_y,
        width_px=width_px,
        height_px=height_px,
        source="geotiff",
    )


def get_georef(raster_path: str | Path, tab_path: Optional[str | Path] = None) -> Optional[GeoInfo]:
    """
    Best-effort georeferencing: tries .tab first, then GeoTIFF tags.

    Parameters
    ----------
    raster_path:
        Path to the raster image.
    tab_path:
        Optional explicit path to a .tab file. If None, looks for a
        same-stem .tab next to the raster.
    """
    raster_path = Path(raster_path)

    # 1) Try explicit or sibling .tab file
    if tab_path is None:
        candidate = raster_path.with_suffix(".tab")
        if candidate.exists():
            tab_path = candidate

    if tab_path is not None:
        info = parse_tab_file(tab_path)
        if info and info.is_valid():
            return info

    # 2) Try GeoTIFF internal tags
    if raster_path.suffix.lower() in (".tif", ".tiff"):
        info = parse_geotiff_georef(raster_path)
        if info and info.is_valid():
            return info

    return None
