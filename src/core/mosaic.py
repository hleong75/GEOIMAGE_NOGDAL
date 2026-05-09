"""
mosaic.py — Mosaic reconstruction without GDAL.

Three strategies:
  A. Georef-based   — exact Lambert 93 positioning from .tab / GeoTIFF headers (priority).
  B. Filename-based — extract XXXX/YYYY from tile names, sort into grid, stitch.
  C. VRT-based      — parse mosaique.vrt XML for exact positions and sizes.

Memory model: tiles are NOT loaded all at once.  The Mosaic object exposes
``get_region(x_off, y_off, width, height)`` which loads only the required
tiles and returns a PIL Image crop.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

if TYPE_CHECKING:
    from .georef import GeoInfo

logger = logging.getLogger(__name__)

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import tifffile
    TIFFFILE_AVAILABLE = True
except ImportError:
    TIFFFILE_AVAILABLE = False

try:
    import glymur
    GLYMUR_AVAILABLE = True
except ImportError:
    GLYMUR_AVAILABLE = False


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TileInfo:
    """One tile in a mosaic."""
    path: Path
    # Pixel offset of this tile's top-left corner in the full mosaic
    x_off: int = 0
    y_off: int = 0
    width: int = 0
    height: int = 0
    # Lambert 93 grid coordinates (informational)
    grid_x: Optional[int] = None
    grid_y: Optional[int] = None
    # Full geographic info (from .tab / GeoTIFF), may be None
    geo: Optional["GeoInfo"] = None
    # Pixel offset in the source image for cropped virtual tiles
    src_x_off: int = 0
    src_y_off: int = 0


@dataclass
class MosaicLayout:
    """Complete mosaic geometry (pixel space)."""
    tiles: List[TileInfo] = field(default_factory=list)
    total_width: int = 0
    total_height: int = 0
    # Pixel size in metres (from georef, may be 0 if unknown)
    pixel_size_m: float = 0.0
    # Geographic extent of the full mosaic in Lambert 93 (may be None)
    geo_extent: Optional["GeoInfo"] = None

    def tiles_in_region(
        self, x_off: int, y_off: int, width: int, height: int
    ) -> List[TileInfo]:
        """Return tiles that overlap the given pixel region."""
        result = []
        x1, y1 = x_off, y_off
        x2, y2 = x_off + width, y_off + height
        for tile in self.tiles:
            tx1, ty1 = tile.x_off, tile.y_off
            tx2, ty2 = tile.x_off + tile.width, tile.y_off + tile.height
            if tx2 > x1 and tx1 < x2 and ty2 > y1 and ty1 < y2:
                result.append(tile)
        return result


# ---------------------------------------------------------------------------
# Image loading helpers
# ---------------------------------------------------------------------------

def _normalize_to_uint8(arr: "np.ndarray") -> "np.ndarray":
    """Linearly scale a numpy array to uint8 [0, 255]."""
    import numpy as np
    # Replace NaN/Inf before computing statistics so they don't corrupt the
    # output: NaN and -Inf map to 0 (black) to indicate missing/invalid data,
    # +Inf maps to 255 (white/saturated) as the maximum representable value.
    arr = np.nan_to_num(arr, nan=0.0, posinf=255.0, neginf=0.0)
    lo, hi = arr.min(), arr.max()
    if lo == hi:
        return np.zeros_like(arr, dtype="uint8")
    return ((arr - lo) / (hi - lo) * 255).astype("uint8")


def _open_image(path: Path) -> "Image.Image":
    """Open a raster file as a PIL Image (JPEG2000, GeoTIFF, or standard)."""
    ext = path.suffix.lower()

    if ext == ".jp2":
        if GLYMUR_AVAILABLE:
            try:
                import numpy as np
                jp2 = glymur.Jp2k(str(path))
                arr = jp2[:]
                if arr.ndim == 2:
                    return Image.fromarray(arr, mode="L")
                elif arr.shape[2] == 4:
                    return Image.fromarray(arr, mode="RGBA").convert("RGB")
                else:
                    return Image.fromarray(arr, mode="RGB")
            except Exception as exc:
                logger.debug("glymur failed for %s, falling back to PIL: %s", path, exc)
        # Fallback: try Pillow's built-in JPEG2000 (needs openjpeg)
        if PIL_AVAILABLE:
            return Image.open(str(path)).convert("RGB")
        raise RuntimeError(f"Cannot open JP2 file — install glymur: {path}")

    if ext in (".tif", ".tiff"):
        if TIFFFILE_AVAILABLE:
            try:
                import numpy as np
                arr = tifffile.imread(str(path))
                if arr.ndim == 2:
                    if arr.dtype != "uint8":
                        arr = _normalize_to_uint8(arr)
                    return Image.fromarray(arr, mode="L")
                if arr.ndim == 3:
                    if arr.shape[0] in (3, 4) and arr.shape[0] < arr.shape[2]:
                        arr = arr.transpose(1, 2, 0)
                    # Squeeze single-band 3D arrays to 2D grayscale
                    if arr.shape[2] == 1:
                        arr = arr[:, :, 0]
                        if arr.dtype != "uint8":
                            arr = _normalize_to_uint8(arr)
                        return Image.fromarray(arr, mode="L")
                    if arr.dtype != "uint8":
                        arr = _normalize_to_uint8(arr)
                    mode = "RGB" if arr.shape[2] == 3 else "RGBA"
                    img = Image.fromarray(arr, mode=mode)
                    return img.convert("RGB")
            except (OSError, ValueError, RuntimeError, Exception) as exc:
                # tifffile can raise tifffile.TiffFileError (a subclass of Exception)
                # or other errors for unsupported formats / corrupted files.
                # Fall back to PIL for any failure so the tile is not silently dropped.
                logger.debug("tifffile failed for %s, falling back to PIL: %s", path, exc)
        if PIL_AVAILABLE:
            return Image.open(str(path)).convert("RGB")

    if PIL_AVAILABLE:
        return Image.open(str(path)).convert("RGB")

    raise RuntimeError(f"No image library available to open: {path}")


def _get_tile_size(path: Path) -> Tuple[int, int]:
    """Return (width, height) of a tile WITHOUT loading pixel data."""
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow is required to read image dimensions.")
    ext = path.suffix.lower()
    if ext == ".jp2" and GLYMUR_AVAILABLE:
        jp2 = glymur.Jp2k(str(path))
        shape = jp2.shape
        return shape[1], shape[0]  # width, height
    with Image.open(str(path)) as img:
        return img.size  # (width, height)


# ---------------------------------------------------------------------------
# Strategy A: filename-based grid assembly
# ---------------------------------------------------------------------------

_IGN_TILE_RE = re.compile(
    r"SC25[_\-].*?[_\-](\d{4})[_\-](\d{4})[_\-]",
    re.IGNORECASE,
)


def build_mosaic_from_filenames(
    tile_paths: List[Path],
    pixel_size_m: float = 0.0,
) -> MosaicLayout:
    """
    Build a MosaicLayout by extracting Lambert 93 grid coords from filenames.

    Tiles are sorted by (y DESC, x ASC) and placed side by side.
    The size of the first tile is used as the reference tile size.
    """
    if not tile_paths:
        return MosaicLayout()

    parsed: list[tuple[int, int, Path]] = []
    unknown: list[Path] = []

    for p in tile_paths:
        m = _IGN_TILE_RE.search(p.name)
        if m:
            gx, gy = int(m.group(1)), int(m.group(2))
            parsed.append((gx, gy, p))
        else:
            unknown.append(p)

    if not parsed:
        # Fall back to a single-column layout
        return _linear_layout(tile_paths, pixel_size_m)

    # Get representative tile size
    ref_w, ref_h = _get_tile_size(parsed[0][2])

    # Build grid
    xs = sorted(set(gx for gx, _, _ in parsed))
    ys = sorted(set(gy for _, gy, _ in parsed), reverse=True)
    x_idx = {v: i for i, v in enumerate(xs)}
    y_idx = {v: i for i, v in enumerate(ys)}

    tiles: list[TileInfo] = []
    for gx, gy, p in parsed:
        col = x_idx[gx]
        row = y_idx[gy]
        tiles.append(
            TileInfo(
                path=p,
                x_off=col * ref_w,
                y_off=row * ref_h,
                width=ref_w,
                height=ref_h,
                grid_x=gx,
                grid_y=gy,
            )
        )

    # Append unknown files after the grid
    x_unknown = len(xs) * ref_w
    for i, p in enumerate(unknown):
        w, h = _get_tile_size(p)
        tiles.append(
            TileInfo(
                path=p,
                x_off=x_unknown,
                y_off=i * ref_h,
                width=w,
                height=h,
            )
        )

    total_w = len(xs) * ref_w + (ref_w if unknown else 0)
    total_h = len(ys) * ref_h

    return MosaicLayout(
        tiles=tiles,
        total_width=total_w,
        total_height=total_h,
        pixel_size_m=pixel_size_m,
    )


def _linear_layout(paths: List[Path], pixel_size_m: float) -> MosaicLayout:
    """Arrange tiles in a single row (fallback when no grid coords found)."""
    tiles = []
    x_off = 0
    max_h = 0
    for p in paths:
        w, h = _get_tile_size(p)
        tiles.append(TileInfo(path=p, x_off=x_off, y_off=0, width=w, height=h))
        x_off += w
        max_h = max(max_h, h)
    return MosaicLayout(tiles=tiles, total_width=x_off, total_height=max_h, pixel_size_m=pixel_size_m)


# ---------------------------------------------------------------------------
# Strategy C: georeferencing-based assembly (highest precision)
# ---------------------------------------------------------------------------

def build_mosaic_from_georef_files(tile_paths: List[Path]) -> Optional[MosaicLayout]:
    """
    Build a MosaicLayout by reading Lambert 93 extents from .tab / GeoTIFF headers.

    This is the most accurate strategy: tile positions are derived from real
    geographic coordinates rather than filename patterns.  Returns ``None`` when
    no georeferencing data can be extracted from any tile.
    """
    from .georef import get_georef, GeoInfo as _GeoInfo

    geo_tiles: list[tuple[Path, "_GeoInfo"]] = []
    for path in tile_paths:
        geo = get_georef(path)
        if geo and geo.is_valid():
            geo_tiles.append((path, geo))

    if not geo_tiles:
        return None

    # Overall Lambert 93 extent
    min_x = min(g.min_x for _, g in geo_tiles)
    min_y = min(g.min_y for _, g in geo_tiles)
    max_x = max(g.max_x for _, g in geo_tiles)
    max_y = max(g.max_y for _, g in geo_tiles)

    # Reference pixel size (use the first valid tile; all tiles in the same
    # series have the same resolution)
    pixel_size_m = geo_tiles[0][1].pixel_size_x
    if pixel_size_m <= 0:
        pixel_size_m = 1.0

    total_w_px = max(1, int(round((max_x - min_x) / pixel_size_m)))
    total_h_px = max(1, int(round((max_y - min_y) / pixel_size_m)))

    # Build global GeoInfo for the full mosaic
    from .georef import GeoInfo as _GeoInfo2
    global_geo = _GeoInfo2(
        min_x=min_x,
        min_y=min_y,
        max_x=max_x,
        max_y=max_y,
        pixel_size_x=pixel_size_m,
        pixel_size_y=pixel_size_m,
        width_px=total_w_px,
        height_px=total_h_px,
        source="georef_mosaic",
    )

    tiles: list[TileInfo] = []
    for path, geo in geo_tiles:
        x_off = int(round((geo.min_x - min_x) / pixel_size_m))
        y_off = int(round((max_y - geo.max_y) / pixel_size_m))
        tiles.append(
            TileInfo(
                path=path,
                x_off=x_off,
                y_off=y_off,
                width=geo.width_px,
                height=geo.height_px,
                geo=geo,
            )
        )

    return MosaicLayout(
        tiles=tiles,
        total_width=total_w_px,
        total_height=total_h_px,
        pixel_size_m=pixel_size_m,
        geo_extent=global_geo,
    )


# ---------------------------------------------------------------------------
# Strategy B: VRT-based assembly (priority)
# ---------------------------------------------------------------------------

def build_mosaic_from_vrt(vrt_path: str | Path) -> Optional[MosaicLayout]:
    """
    Parse a GDAL .vrt file and build a MosaicLayout.

    Supports both flat VRT and nested VRTRasterBand/SimpleSource layouts.
    """
    vrt_path = Path(vrt_path)
    try:
        tree = ET.parse(str(vrt_path))
    except (ET.ParseError, OSError):
        return None

    root = tree.getroot()

    try:
        total_width = int(root.attrib.get("rasterXSize", 0))
        total_height = int(root.attrib.get("rasterYSize", 0))
    except ValueError:
        return None

    if total_width == 0 or total_height == 0:
        return None

    # GeoTransform for pixel size
    pixel_size_m = 0.0
    gt_el = root.find("GeoTransform")
    if gt_el is not None and gt_el.text:
        try:
            gt = [float(v.strip()) for v in gt_el.text.split(",")]
            if len(gt) >= 2:
                pixel_size_m = abs(gt[1])
        except ValueError:
            pass

    tiles: list[TileInfo] = []
    base_dir = vrt_path.parent

    seen_tiles: set[tuple[str, int, int, int, int, int, int]] = set()

    # Iterate over all SimpleSource / ComplexSource elements
    for band_el in root.findall(".//VRTRasterBand"):
        for src_tag in ("SimpleSource", "ComplexSource"):
            for src_el in band_el.findall(src_tag):
                tile = _parse_vrt_source(src_el, base_dir)
                if tile:
                    key = (
                        str(tile.path),
                        tile.x_off,
                        tile.y_off,
                        tile.width,
                        tile.height,
                        tile.src_x_off,
                        tile.src_y_off,
                    )
                    if key not in seen_tiles:
                        tiles.append(tile)
                        seen_tiles.add(key)

    # Also handle top-level sources (some VRT variants)
    for src_tag in ("SimpleSource", "ComplexSource"):
        for src_el in root.findall(src_tag):
            tile = _parse_vrt_source(src_el, base_dir)
            if tile:
                key = (
                    str(tile.path),
                    tile.x_off,
                    tile.y_off,
                    tile.width,
                    tile.height,
                    tile.src_x_off,
                    tile.src_y_off,
                )
                if key not in seen_tiles:
                    tiles.append(tile)
                    seen_tiles.add(key)

    if not tiles:
        return None

    return MosaicLayout(
        tiles=tiles,
        total_width=total_width,
        total_height=total_height,
        pixel_size_m=pixel_size_m,
    )


def _parse_vrt_source(src_el: ET.Element, base_dir: Path) -> Optional[TileInfo]:
    """Parse one SimpleSource/ComplexSource XML element into a TileInfo."""
    src_fn_el = src_el.find("SourceFilename")
    if src_fn_el is None or not src_fn_el.text:
        return None

    rel_path = src_fn_el.text.strip()
    tile_path = (base_dir / rel_path).resolve()

    dst_rect_el = src_el.find("DstRect")
    if dst_rect_el is None:
        return None

    try:
        x_off = int(float(dst_rect_el.attrib.get("xOff", 0)))
        y_off = int(float(dst_rect_el.attrib.get("yOff", 0)))
        width = int(float(dst_rect_el.attrib.get("xSize", 0)))
        height = int(float(dst_rect_el.attrib.get("ySize", 0)))
    except (ValueError, KeyError):
        return None

    if width == 0 or height == 0:
        return None

    src_rect_el = src_el.find("SrcRect")
    src_x_off = 0
    src_y_off = 0
    if src_rect_el is not None:
        try:
            src_x_off = int(float(src_rect_el.attrib.get("xOff", 0)))
            src_y_off = int(float(src_rect_el.attrib.get("yOff", 0)))
        except (ValueError, KeyError):
            src_x_off = 0
            src_y_off = 0

    return TileInfo(
        path=tile_path,
        x_off=x_off,
        y_off=y_off,
        width=width,
        height=height,
        src_x_off=src_x_off,
        src_y_off=src_y_off,
    )


# ---------------------------------------------------------------------------
# Mosaic render helper
# ---------------------------------------------------------------------------

class Mosaic:
    """
    High-level mosaic access object.

    Renders pixel regions on demand without loading the full mosaic into RAM.
    """

    def __init__(self, layout: MosaicLayout) -> None:
        self.layout = layout

    @classmethod
    def from_vrt(cls, vrt_path: str | Path) -> Optional["Mosaic"]:
        layout = build_mosaic_from_vrt(vrt_path)
        return cls(layout) if layout else None

    @classmethod
    def from_files(
        cls,
        tile_paths: List[Path],
        pixel_size_m: float = 0.0,
        try_georef: bool = True,
    ) -> "Mosaic":
        """
        Build a Mosaic from a list of tile paths.

        When *try_georef* is True (default), attempts to read Lambert 93
        extents from ``.tab`` / GeoTIFF headers first for exact positioning.
        Falls back to filename-based grid assembly when georef is unavailable.
        """
        if try_georef and tile_paths:
            layout = build_mosaic_from_georef_files(tile_paths)
            if layout is not None:
                return cls(layout)
        layout = build_mosaic_from_filenames(tile_paths, pixel_size_m)
        return cls(layout)

    @property
    def width(self) -> int:
        return self.layout.total_width

    @property
    def height(self) -> int:
        return self.layout.total_height

    @property
    def pixel_size_m(self) -> float:
        return self.layout.pixel_size_m

    @property
    def geo_extent(self) -> "Optional[GeoInfo]":
        """Lambert 93 bounding box of the full mosaic, or None."""
        return self.layout.geo_extent

    def cropped(self, x_off: int, y_off: int, width: int, height: int) -> "Mosaic":
        """
        Return a new Mosaic limited to the requested pixel region.

        The resulting mosaic keeps tile references to source files and only
        adjusts tile offsets/sizes so rendering remains tile-on-demand.
        """
        if width <= 0 or height <= 0:
            raise ValueError(
                "La zone sélectionnée est invalide : largeur et hauteur doivent être > 0 "
                f"(reçu largeur={width}, hauteur={height})."
            )

        x1 = max(0, x_off)
        y1 = max(0, y_off)
        x2 = min(self.width, x1 + width)
        y2 = min(self.height, y1 + height)
        if x2 <= x1 or y2 <= y1:
            raise ValueError("La zone sélectionnée est hors de la mosaïque.")

        cropped_w = x2 - x1
        cropped_h = y2 - y1
        relevant = self.layout.tiles_in_region(x1, y1, cropped_w, cropped_h)
        new_tiles: list[TileInfo] = []

        for tile in relevant:
            tx1, ty1 = tile.x_off, tile.y_off
            tx2, ty2 = tile.x_off + tile.width, tile.y_off + tile.height
            ix1 = max(x1, tx1)
            iy1 = max(y1, ty1)
            ix2 = min(x2, tx2)
            iy2 = min(y2, ty2)
            if ix2 <= ix1 or iy2 <= iy1:
                continue

            new_tiles.append(
                TileInfo(
                    path=tile.path,
                    x_off=ix1 - x1,
                    y_off=iy1 - y1,
                    width=ix2 - ix1,
                    height=iy2 - iy1,
                    grid_x=tile.grid_x,
                    grid_y=tile.grid_y,
                    geo=tile.geo,
                    src_x_off=tile.src_x_off + (ix1 - tx1),
                    src_y_off=tile.src_y_off + (iy1 - ty1),
                )
            )

        new_geo = None
        if self.geo_extent is not None and self.geo_extent.is_valid() and self.pixel_size_m > 0:
            from .georef import GeoInfo as _GeoInfo

            min_x = self.geo_extent.min_x + x1 * self.pixel_size_m
            max_x = min_x + cropped_w * self.pixel_size_m
            max_y = self.geo_extent.max_y - y1 * self.pixel_size_m
            min_y = max_y - cropped_h * self.pixel_size_m
            new_geo = _GeoInfo(
                min_x=min_x,
                min_y=min_y,
                max_x=max_x,
                max_y=max_y,
                pixel_size_x=self.pixel_size_m,
                pixel_size_y=self.pixel_size_m,
                width_px=cropped_w,
                height_px=cropped_h,
                source="cropped_mosaic",
            )

        return Mosaic(
            MosaicLayout(
                tiles=new_tiles,
                total_width=cropped_w,
                total_height=cropped_h,
                pixel_size_m=self.pixel_size_m,
                geo_extent=new_geo,
            )
        )

    def get_region(
        self,
        x_off: int,
        y_off: int,
        width: int,
        height: int,
        progress_callback=None,
    ) -> "Image.Image":
        """
        Render a rectangular pixel region from the mosaic.

        Only tiles that overlap the requested region are loaded.
        Returns a PIL Image (RGB).
        """
        if not PIL_AVAILABLE:
            raise RuntimeError("Pillow is required for rendering.")

        canvas = Image.new("RGB", (width, height), color=(255, 255, 255))
        relevant = self.layout.tiles_in_region(x_off, y_off, width, height)

        for i, tile in enumerate(relevant):
            try:
                tile_img = _open_image(tile.path)
                # Crop to the intersection with the requested region.
                # Tile geometry is in mosaic coordinates; crop coordinates are
                # converted to source-image coordinates via src_*_off.
                ix1 = max(x_off, tile.x_off)
                iy1 = max(y_off, tile.y_off)
                ix2 = min(x_off + width, tile.x_off + tile.width)
                iy2 = min(y_off + height, tile.y_off + tile.height)
                crop_x1 = tile.src_x_off + (ix1 - tile.x_off)
                crop_y1 = tile.src_y_off + (iy1 - tile.y_off)
                crop_x2 = tile.src_x_off + (ix2 - tile.x_off)
                crop_y2 = tile.src_y_off + (iy2 - tile.y_off)

                if crop_x2 <= crop_x1 or crop_y2 <= crop_y1:
                    continue

                cropped = tile_img.crop((crop_x1, crop_y1, crop_x2, crop_y2))
                # Ensure RGB for consistent pasting onto RGB canvas
                if cropped.mode != "RGB":
                    cropped = cropped.convert("RGB")

                # Paste position on canvas
                paste_x = ix1 - x_off
                paste_y = iy1 - y_off
                canvas.paste(cropped, (paste_x, paste_y))
            except Exception as exc:
                logger.warning("Impossible de charger la tuile %s: %s", tile.path, exc)

            if progress_callback:
                progress_callback(i + 1, len(relevant))

        return canvas

    def get_thumbnail(
        self,
        max_size: Tuple[int, int] = (512, 512),
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> "Image.Image":
        """Return a downsampled preview of the full mosaic."""
        if not PIL_AVAILABLE:
            raise RuntimeError("Pillow is required.")

        tw, th = max_size
        scale = min(tw / max(self.width, 1), th / max(self.height, 1))
        thumb_w = max(1, int(self.width * scale))
        thumb_h = max(1, int(self.height * scale))

        # Keep no-data background neutral/white in previews.
        canvas = Image.new("RGB", (thumb_w, thumb_h), color=(255, 255, 255))

        total_tiles = len(self.layout.tiles)
        for i, tile in enumerate(self.layout.tiles, start=1):
            try:
                img = _open_image(tile.path)
                img.thumbnail((max(1, int(tile.width * scale)), max(1, int(tile.height * scale))), Image.LANCZOS)
                px = max(0, int(tile.x_off * scale))
                py = max(0, int(tile.y_off * scale))
                canvas.paste(img, (px, py))
            except Exception:
                pass
            if progress_callback:
                progress_callback(i, total_tiles)

        return canvas
