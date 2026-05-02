"""
pdf_converter.py — Convert IGN raster mosaic to A4 PDF at exact cartographic scale.

Uses ReportLab for PDF generation.  Images are rendered tile-by-tile to
keep memory usage low.

A4 = 210 × 297 mm (portrait)  /  297 × 210 mm (landscape)

Atlas layout (per folder):
  1. Cover page  — metadata summary + visual index (tile grid + page grid)
  2. Overview page — full-page mosaic thumbnail with overlays
  3. Content pages — one per A4 window of the mosaic at fixed scale
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, List, Optional, Tuple

try:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from .mosaic import Mosaic


class Orientation(str, Enum):
    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"


# A4 dimensions in mm
A4_W_MM = 210.0
A4_H_MM = 297.0

# 1 inch = 25.4 mm
MM_PER_INCH = 25.4

# ReportLab uses points (1/72 inch)
PT_PER_INCH = 72.0

# Atlas page layout constants (points)
# These define reserved areas on content pages for headers/footers.
_HEADER_H_PT = 18.0   # Top header bar (lot | page | col/row | scale)
_GEO_H_PT    = 11.0   # Lambert 93 extent row
_TILES_H_PT  = 11.0   # Tile list row
_FOOTER_H_PT = 12.0   # Bottom footer bar (source | pagination | "Impression à 100%")
# Total vertical overhead on content pages
_OVERHEAD_PT = _HEADER_H_PT + _GEO_H_PT + _TILES_H_PT + _FOOTER_H_PT

# Unit conversion helpers
_MM_PER_METER = 1000.0

# Overview page thumbnail bounds (pixels)
_MIN_THUMB_PX = 64
_MAX_THUMB_PX = 1024

# Approximate character width in points (used to truncate long tile lists)
_CHAR_WIDTH_APPROX_PT = 4.5


@dataclass
class PDFConfig:
    """Configuration for PDF export."""

    dpi: int = 300
    orientation: Orientation = Orientation.PORTRAIT
    margin_mm: float = 10.0
    overlap_mm: float = 5.0
    output_path: Path = Path("output.pdf")
    # Map scale denominator (e.g. 25000 for 1:25 000).
    # When > 0 and the mosaic carries georef data, atlas-style scale-based
    # page layout is used instead of the DPI-based pixel layout.
    scale: int = 25000
    # Whether to prepend cover + overview pages to each folder's section.
    atlas_pages: bool = True

    @property
    def page_w_mm(self) -> float:
        return A4_W_MM if self.orientation == Orientation.PORTRAIT else A4_H_MM

    @property
    def page_h_mm(self) -> float:
        return A4_H_MM if self.orientation == Orientation.PORTRAIT else A4_W_MM

    @property
    def printable_w_mm(self) -> float:
        return self.page_w_mm - 2 * self.margin_mm

    @property
    def printable_h_mm(self) -> float:
        return self.page_h_mm - 2 * self.margin_mm

    @property
    def printable_w_px(self) -> int:
        """Printable width in pixels at the configured DPI."""
        return int(self.printable_w_mm / MM_PER_INCH * self.dpi)

    @property
    def printable_h_px(self) -> int:
        """Printable height in pixels at the configured DPI."""
        return int(self.printable_h_mm / MM_PER_INCH * self.dpi)

    @property
    def overlap_px(self) -> int:
        """Overlap between adjacent pages in pixels."""
        return int(self.overlap_mm / MM_PER_INCH * self.dpi)

    @property
    def image_h_mm(self) -> float:
        """Height in mm available for the map image on atlas content pages.

        Subtracts the fixed header/footer/annotation overhead from the
        printable height so that the rendered image is at exactly 1:scale.
        """
        overhead_mm = _OVERHEAD_PT / PT_PER_INCH * MM_PER_INCH
        return max(10.0, self.printable_h_mm - overhead_mm)


@dataclass
class PageInfo:
    """Describes one page of the output PDF."""

    page_index: int
    col: int
    row: int
    # Source pixel region in the mosaic
    src_x: int
    src_y: int
    src_w: int
    src_h: int
    # Lambert 93 geographic extent (populated by compute_pages_at_scale)
    geo_min_x: float = 0.0
    geo_min_y: float = 0.0
    geo_max_x: float = 0.0
    geo_max_y: float = 0.0
    # Whether geo_min/max fields are valid
    has_geo: bool = False
    # Names (stems) of tiles visible in this page
    tile_names: List[str] = field(default_factory=list)


def compute_pages(mosaic: Mosaic, cfg: PDFConfig) -> List[PageInfo]:
    """
    Divide the mosaic into A4 pages with optional overlap.

    Each page covers a region of ``pw × ph`` pixels (printable area at the
    configured DPI).  Adjacent pages share ``overlap_px`` pixels on each side.
    Edge pages are shifted back so they always render a full-size region,
    eliminating blank margins on the last column/row.

    Returns a list of PageInfo objects, one per page.
    """
    pw = cfg.printable_w_px
    ph = cfg.printable_h_px
    overlap = cfg.overlap_px

    # Stride is the number of new pixels introduced per step.
    stride_x = max(1, pw - overlap)
    stride_y = max(1, ph - overlap)

    # Effective page region clamped to mosaic size (for mosaics smaller than 1 page)
    page_w = min(pw, mosaic.width)
    page_h = min(ph, mosaic.height)

    if mosaic.width <= pw:
        cols = 1
    else:
        cols = 1 + math.ceil((mosaic.width - pw) / stride_x)

    if mosaic.height <= ph:
        rows = 1
    else:
        rows = 1 + math.ceil((mosaic.height - ph) / stride_y)

    pages: list[PageInfo] = []
    idx = 0
    for row in range(rows):
        for col in range(cols):
            src_x = col * stride_x
            src_y = row * stride_y

            # Shift edge pages back so we always render a full page_w × page_h region.
            # This prevents blank margins on the last column/row.
            if src_x + page_w > mosaic.width:
                src_x = max(0, mosaic.width - page_w)
            if src_y + page_h > mosaic.height:
                src_y = max(0, mosaic.height - page_h)

            pages.append(
                PageInfo(
                    page_index=idx,
                    col=col,
                    row=row,
                    src_x=src_x,
                    src_y=src_y,
                    src_w=page_w,
                    src_h=page_h,
                )
            )
            idx += 1

    return pages


def compute_pages_at_scale(mosaic: Mosaic, cfg: PDFConfig) -> List[PageInfo]:
    """
    Divide the mosaic into A4 pages at the configured cartographic scale.

    Uses geographic extents (Lambert 93 from .tab / GeoTIFF) when available.
    The map image area on each page is ``cfg.printable_w_mm × cfg.image_h_mm``,
    which at scale 1:S covers a fixed ground area regardless of tile count.

    Falls back to ``compute_pages()`` when no georef or scale is available.
    """
    psm = mosaic.pixel_size_m
    if cfg.scale <= 0 or psm <= 0:
        return compute_pages(mosaic, cfg)

    # Ground area covered by one page's image area in metres
    ground_w = cfg.printable_w_mm / _MM_PER_METER * cfg.scale
    ground_h = cfg.image_h_mm / _MM_PER_METER * cfg.scale

    # Source pixels per page
    src_w = max(1, int(round(ground_w / psm)))
    src_h = max(1, int(round(ground_h / psm)))

    # Number of pages from geographic extent (preferred) or pixel size
    geo = mosaic.geo_extent
    if geo is not None and geo.is_valid():
        cols = max(1, math.ceil(geo.width_m / ground_w))
        rows = max(1, math.ceil(geo.height_m / ground_h))
    else:
        cols = max(1, math.ceil(mosaic.width / src_w))
        rows = max(1, math.ceil(mosaic.height / src_h))

    pages: list[PageInfo] = []
    idx = 0
    for row in range(rows):
        for col in range(cols):
            src_x = col * src_w
            src_y = row * src_h

            # Lambert 93 extents for this page
            has_geo = False
            page_min_x = page_min_y = page_max_x = page_max_y = 0.0
            if geo is not None and geo.is_valid():
                page_min_x = geo.min_x + col * ground_w
                page_max_x = page_min_x + ground_w
                page_max_y = geo.max_y - row * ground_h
                page_min_y = page_max_y - ground_h
                has_geo = True

            # Tiles visible in this page (names only, for annotation)
            visible = mosaic.layout.tiles_in_region(src_x, src_y, src_w, src_h)
            tile_names = [t.path.stem for t in visible]

            pages.append(
                PageInfo(
                    page_index=idx,
                    col=col,
                    row=row,
                    src_x=src_x,
                    src_y=src_y,
                    src_w=src_w,
                    src_h=src_h,
                    geo_min_x=page_min_x,
                    geo_min_y=page_min_y,
                    geo_max_x=page_max_x,
                    geo_max_y=page_max_y,
                    has_geo=has_geo,
                    tile_names=tile_names,
                )
            )
            idx += 1

    return pages


def _pil_to_bytes(img: "Image.Image", fmt: str = "JPEG", quality: int = 90) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt, quality=quality)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Visual index helper
# ---------------------------------------------------------------------------

def _draw_mosaic_index(
    c: "rl_canvas.Canvas",
    mosaic: Mosaic,
    pages: List[PageInfo],
    box_x: float,
    box_y: float,
    box_w: float,
    box_h: float,
) -> None:
    """
    Draw a proportional vector index of the mosaic inside a bounding box.

    Shows:
    - Grey background (mosaic outline)
    - Blue tile boundaries
    - Orange dashed page boundaries with page numbers
    """
    mw = mosaic.width
    mh = mosaic.height
    if mw <= 0 or mh <= 0:
        return

    scale_x = box_w / mw
    scale_y = box_h / mh
    idx_scale = min(scale_x, scale_y)
    draw_w = mw * idx_scale
    draw_h = mh * idx_scale

    # Centre the index inside the box
    off_x = box_x + (box_w - draw_w) / 2.0
    off_y = box_y + (box_h - draw_h) / 2.0

    c.saveState()

    # Mosaic background
    c.setFillColorRGB(0.93, 0.93, 0.93)
    c.setStrokeColorRGB(0.55, 0.55, 0.55)
    c.setLineWidth(0.5)
    c.rect(off_x, off_y, draw_w, draw_h, fill=1, stroke=1)

    # Tile boundaries (blue)
    c.setStrokeColorRGB(0.22, 0.47, 0.72)
    c.setLineWidth(0.5)
    for tile in mosaic.layout.tiles:
        tx = off_x + tile.x_off * idx_scale
        ty = off_y + draw_h - (tile.y_off + tile.height) * idx_scale
        tw = tile.width * idx_scale
        th = tile.height * idx_scale
        if tw > 0 and th > 0:
            c.rect(tx, ty, tw, th, fill=0, stroke=1)

    # Page boundaries (orange, dashed) and page numbers
    c.setStrokeColorRGB(0.82, 0.33, 0.05)
    c.setLineWidth(0.8)
    c.setDash([3, 2])
    label_size = max(4.0, min(8.0, draw_w / max(len(pages), 1) * 0.45))
    for page in pages:
        px = off_x + page.src_x * idx_scale
        py = off_y + draw_h - (page.src_y + page.src_h) * idx_scale
        pw = page.src_w * idx_scale
        ph = page.src_h * idx_scale
        if pw > 0 and ph > 0:
            c.rect(px, py, pw, ph, fill=0, stroke=1)

    c.setDash([])
    c.setFont("Helvetica-Bold", label_size)
    c.setFillColorRGB(0.82, 0.33, 0.05)
    for page in pages:
        cx = off_x + (page.src_x + page.src_w / 2.0) * idx_scale
        cy = off_y + draw_h - (page.src_y + page.src_h / 2.0) * idx_scale - label_size / 2.0
        c.drawCentredString(cx, cy, str(page.page_index + 1))

    c.restoreState()


# ---------------------------------------------------------------------------
# Cover page
# ---------------------------------------------------------------------------

def _render_cover_page(
    c: "rl_canvas.Canvas",
    mosaic: Mosaic,
    pages: List[PageInfo],
    page_w_pt: float,
    page_h_pt: float,
    margin_pt: float,
    cfg: PDFConfig,
    folder_name: str,
) -> None:
    """Render an atlas cover page with metadata and visual index."""
    pw = page_w_pt - 2 * margin_pt
    ph = page_h_pt - 2 * margin_pt
    lx = margin_pt      # left x
    by = margin_pt      # bottom y
    ty = by + ph        # top y

    c.saveState()

    # ---- Title block ----
    c.setFillColorRGB(0.12, 0.25, 0.45)
    c.rect(lx, ty - 60, pw, 60, fill=1, stroke=0)

    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 16)
    title = "ATLAS CARTOGRAPHIQUE — GEOIMAGE NOGDAL"
    c.drawCentredString(lx + pw / 2.0, ty - 22, title)
    c.setFont("Helvetica", 11)
    subtitle = f"Lot : {folder_name}" if folder_name else "Lot sans titre"
    c.drawCentredString(lx + pw / 2.0, ty - 42, subtitle)

    # ---- Metadata block ----
    n_tiles = len(mosaic.layout.tiles)
    n_pages = len(pages)
    psm = mosaic.pixel_size_m
    geo = mosaic.geo_extent
    ground_w_m = cfg.printable_w_mm / _MM_PER_METER * cfg.scale if cfg.scale > 0 else 0.0
    ground_h_m = cfg.image_h_mm / _MM_PER_METER * cfg.scale if cfg.scale > 0 else 0.0

    meta_lines = [
        f"Nombre de tuiles source : {n_tiles}",
        f"Nombre de feuilles A4 générées : {n_pages}",
        f"Échelle de sortie : 1 : {cfg.scale:,}" if cfg.scale > 0 else "Échelle : auto",
        (f"Emprise terrain par feuille : {ground_w_m:,.0f} m × {ground_h_m:,.0f} m"
         if ground_w_m > 0 else ""),
        f"Résolution source : {psm:.2f} m/pixel" if psm > 0 else "",
        f"Dimensions mosaïque : {mosaic.width:,} × {mosaic.height:,} px",
    ]
    if geo and geo.is_valid():
        meta_lines.append(
            f"Emprise L93 : X [{geo.min_x:,.0f} – {geo.max_x:,.0f}]"
            f"  Y [{geo.min_y:,.0f} – {geo.max_y:,.0f}]"
        )

    meta_lines = [ln for ln in meta_lines if ln]

    c.setFillColorRGB(0.15, 0.15, 0.15)
    c.setFont("Helvetica", 9)
    meta_y = ty - 75
    line_h = 14
    for ln in meta_lines:
        if meta_y < by + 180:
            break
        c.drawString(lx + 4, meta_y, ln)
        meta_y -= line_h

    # ---- Index title ----
    index_label_y = meta_y - 8
    c.setFont("Helvetica-Bold", 9)
    c.setFillColorRGB(0.12, 0.25, 0.45)
    c.drawString(lx, index_label_y, "INDEX GRAPHIQUE DES FEUILLES")

    # ---- Visual index ----
    legend_h = 16
    idx_top = index_label_y - 4
    idx_bottom = by + legend_h + 4
    idx_h = idx_top - idx_bottom
    if idx_h > 30:
        _draw_mosaic_index(c, mosaic, pages, lx, idx_bottom, pw, idx_h)

    # ---- Legend ----
    c.setFont("Helvetica", 7)
    c.setFillColorRGB(0.22, 0.47, 0.72)
    c.rect(lx, by + 4, 10, 6, fill=0, stroke=1)
    c.setFillColorRGB(0.15, 0.15, 0.15)
    c.drawString(lx + 13, by + 5, "Tuile source")
    c.setStrokeColorRGB(0.82, 0.33, 0.05)
    c.setDash([3, 2])
    c.rect(lx + 75, by + 4, 10, 6, fill=0, stroke=1)
    c.setDash([])
    c.setFillColorRGB(0.15, 0.15, 0.15)
    c.drawString(lx + 88, by + 5, "Feuille A4")

    c.restoreState()


# ---------------------------------------------------------------------------
# Overview page
# ---------------------------------------------------------------------------

def _render_overview_page(
    c: "rl_canvas.Canvas",
    mosaic: Mosaic,
    pages: List[PageInfo],
    page_w_pt: float,
    page_h_pt: float,
    margin_pt: float,
    cfg: PDFConfig,
    folder_name: str,
) -> None:
    """Render an atlas overview page (full-page mosaic thumbnail + overlays)."""
    pw = page_w_pt - 2 * margin_pt
    ph = page_h_pt - 2 * margin_pt
    lx = margin_pt
    by = margin_pt
    ty = by + ph

    c.saveState()

    # ---- Header ----
    hdr_h = 18.0
    c.setFillColorRGB(0.12, 0.25, 0.45)
    c.rect(lx, ty - hdr_h, pw, hdr_h, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 10)
    label = f"Vue d'ensemble — {folder_name}" if folder_name else "Vue d'ensemble"
    c.drawCentredString(lx + pw / 2.0, ty - hdr_h + 5, label)

    # ---- Mosaic thumbnail ----
    img_area_y = by
    img_area_h = ph - hdr_h - 2
    if PIL_AVAILABLE and mosaic.width > 0 and mosaic.height > 0:
        # Compute thumbnail pixel size proportional to the available area
        max_thumb_w = int(pw / PT_PER_INCH * 96)
        max_thumb_h = int(img_area_h / PT_PER_INCH * 96)
        max_thumb_w = max(_MIN_THUMB_PX, min(max_thumb_w, _MAX_THUMB_PX))
        max_thumb_h = max(_MIN_THUMB_PX, min(max_thumb_h, _MAX_THUMB_PX))
        thumb = mosaic.get_thumbnail(max_size=(max_thumb_w, max_thumb_h))
        thumb_bytes = _pil_to_bytes(thumb, fmt="JPEG", quality=80)
        thumb_reader = ImageReader(io.BytesIO(thumb_bytes))

        # Fit thumbnail proportionally
        sx = pw / max(thumb.width, 1)
        sy = img_area_h / max(thumb.height, 1)
        s = min(sx, sy)
        dw = thumb.width * s
        dh = thumb.height * s
        dx = lx + (pw - dw) / 2.0
        dy = img_area_y + (img_area_h - dh) / 2.0

        c.drawImage(thumb_reader, dx, dy, width=dw, height=dh, preserveAspectRatio=True)

        # Overlay: build a virtual mosaic bounding box for scaling
        # (thumb pixels correspond to mosaic pixels via thumb_scale)
        thumb_scale_x = thumb.width / max(mosaic.width, 1)
        thumb_scale_y = thumb.height / max(mosaic.height, 1)
        # We must use s (pt per thumb-pixel) for drawing vector overlays
        # pt_per_mosaic_px = s * thumb_scale (thumb_scale ≈ same in x and y)
        thumb_scale_avg = (thumb_scale_x + thumb_scale_y) / 2.0
        pt_per_mpx = s * thumb_scale_avg

        # Tile boundaries
        c.setStrokeColorRGB(0.22, 0.47, 0.72)
        c.setLineWidth(0.6)
        for tile in mosaic.layout.tiles:
            tx = dx + tile.x_off * pt_per_mpx
            ty2 = dy + dh - (tile.y_off + tile.height) * pt_per_mpx
            tw = tile.width * pt_per_mpx
            th = tile.height * pt_per_mpx
            if tw > 0 and th > 0:
                c.rect(tx, ty2, tw, th, fill=0, stroke=1)

        # Page boundaries and numbers
        c.setStrokeColorRGB(0.82, 0.33, 0.05)
        c.setLineWidth(1.0)
        c.setDash([4, 3])
        label_size = max(5.0, min(9.0, dw / max(len(pages), 1) * 0.5))
        for page in pages:
            px2 = dx + page.src_x * pt_per_mpx
            py2 = dy + dh - (page.src_y + page.src_h) * pt_per_mpx
            pw2 = page.src_w * pt_per_mpx
            ph2 = page.src_h * pt_per_mpx
            if pw2 > 0 and ph2 > 0:
                c.rect(px2, py2, pw2, ph2, fill=0, stroke=1)

        c.setDash([])
        c.setFont("Helvetica-Bold", label_size)
        c.setFillColorRGB(0.82, 0.33, 0.05)
        for page in pages:
            cx = dx + (page.src_x + page.src_w / 2.0) * pt_per_mpx
            cy2 = dy + dh - (page.src_y + page.src_h / 2.0) * pt_per_mpx - label_size / 2.0
            c.drawCentredString(cx, cy2, str(page.page_index + 1))
    else:
        # No PIL: just draw the vector index
        _draw_mosaic_index(c, mosaic, pages, lx, img_area_y, pw, img_area_h)

    c.restoreState()


# ---------------------------------------------------------------------------
# Content page renderer
# ---------------------------------------------------------------------------

def _render_page(
    c: "rl_canvas.Canvas",
    mosaic: Mosaic,
    page: "PageInfo",
    page_w_pt: float,
    page_h_pt: float,
    margin_pt: float,
    printable_w_pt: float,
    printable_h_pt: float,
    total_pages: int,
    folder_label: str = "",
) -> None:
    """
    Render one content page onto the ReportLab canvas (does not call showPage).

    When ``page.has_geo`` is True (atlas mode), the page is rendered with a
    dedicated header/footer zone:
    - top header: lot name | page number | col/row | scale
    - image area: map rendered at the configured scale
    - geo row: Lambert 93 extent
    - tile row: list of visible tiles
    - bottom footer: source | page number | "Impression à 100 %"

    When ``page.has_geo`` is False (legacy DPI-based mode), the image fills
    the printable area with a simple bottom label.
    """
    if page.has_geo:
        _render_atlas_content_page(
            c, mosaic, page, page_w_pt, page_h_pt,
            margin_pt, printable_w_pt, printable_h_pt,
            total_pages, folder_label,
        )
    else:
        _render_legacy_page(
            c, mosaic, page, page_w_pt, page_h_pt,
            margin_pt, printable_w_pt, printable_h_pt,
            total_pages, folder_label,
        )


def _render_legacy_page(
    c: "rl_canvas.Canvas",
    mosaic: Mosaic,
    page: "PageInfo",
    page_w_pt: float,
    page_h_pt: float,
    margin_pt: float,
    printable_w_pt: float,
    printable_h_pt: float,
    total_pages: int,
    folder_label: str,
) -> None:
    """Legacy rendering: image fills printable area with simple bottom label."""
    region = mosaic.get_region(page.src_x, page.src_y, page.src_w, page.src_h)
    img_bytes = _pil_to_bytes(region, fmt="JPEG", quality=92)
    img_reader = ImageReader(io.BytesIO(img_bytes))

    scale_x = printable_w_pt / max(page.src_w, 1)
    scale_y = printable_h_pt / max(page.src_h, 1)
    scale = min(scale_x, scale_y)
    draw_w = page.src_w * scale
    draw_h = page.src_h * scale

    x_pos = margin_pt + (printable_w_pt - draw_w) / 2
    y_pos = margin_pt + (printable_h_pt - draw_h) / 2

    c.drawImage(img_reader, x_pos, y_pos, width=draw_w, height=draw_h, preserveAspectRatio=True)

    c.setFont("Helvetica", 8)
    c.setFillColorRGB(0.5, 0.5, 0.5)
    prefix = f"{folder_label} — " if folder_label else ""
    label = (
        f"{prefix}Page {page.page_index + 1}/{total_pages}"
        f"  (col {page.col+1}, lig {page.row+1})"
    )
    c.drawString(margin_pt, margin_pt / 2, label)


def _render_atlas_content_page(
    c: "rl_canvas.Canvas",
    mosaic: Mosaic,
    page: "PageInfo",
    page_w_pt: float,
    page_h_pt: float,
    margin_pt: float,
    printable_w_pt: float,
    printable_h_pt: float,
    total_pages: int,
    folder_label: str,
) -> None:
    """Atlas-style content page with fixed scale, Lambert 93 annotations and rich footer."""
    lx = margin_pt
    by = margin_pt
    ty = by + printable_h_pt

    # ---- Vertical layout (bottom to top) ----
    footer_y = by
    footer_top = footer_y + _FOOTER_H_PT
    tile_row_y = footer_top
    tile_row_top = tile_row_y + _TILES_H_PT
    geo_row_y = tile_row_top
    geo_row_top = geo_row_y + _GEO_H_PT
    img_y = geo_row_top
    img_top = ty - _HEADER_H_PT
    img_h = img_top - img_y
    img_w = printable_w_pt

    # ---- Header bar ----
    c.saveState()
    c.setFillColorRGB(0.12, 0.25, 0.45)
    c.rect(lx, img_top, img_w, _HEADER_H_PT, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 8)
    col_label = f"C{page.col + 1} / L{page.row + 1}"
    hdr_parts = []
    if folder_label:
        hdr_parts.append(folder_label)
    hdr_parts.append(f"Feuille {page.page_index + 1} / {total_pages}")
    hdr_parts.append(col_label)
    hdr_text = "   |   ".join(hdr_parts)
    c.drawString(lx + 4, img_top + 6, hdr_text)
    c.restoreState()

    # ---- Map image ----
    if img_h > 0 and img_w > 0 and page.src_w > 0 and page.src_h > 0:
        region = mosaic.get_region(page.src_x, page.src_y, page.src_w, page.src_h)
        img_bytes = _pil_to_bytes(region, fmt="JPEG", quality=92)
        img_reader = ImageReader(io.BytesIO(img_bytes))
        c.drawImage(
            img_reader,
            lx,
            img_y,
            width=img_w,
            height=img_h,
            preserveAspectRatio=False,
        )

    # ---- Separator line above annotation rows ----
    c.saveState()
    c.setStrokeColorRGB(0.6, 0.6, 0.6)
    c.setLineWidth(0.3)
    c.line(lx, geo_row_top, lx + img_w, geo_row_top)
    c.line(lx, tile_row_top, lx + img_w, tile_row_top)
    c.line(lx, footer_top, lx + img_w, footer_top)

    # ---- Lambert 93 extent row ----
    c.setFont("Helvetica", 7.5)
    c.setFillColorRGB(0.1, 0.1, 0.1)
    if page.has_geo:
        geo_text = (
            f"Emprise L93 — "
            f"X : {page.geo_min_x:,.0f} → {page.geo_max_x:,.0f} m   "
            f"Y : {page.geo_min_y:,.0f} → {page.geo_max_y:,.0f} m"
        )
    else:
        geo_text = f"Col {page.col + 1} / Lig {page.row + 1}   (position pixel : {page.src_x},{page.src_y})"
    c.drawString(lx + 2, geo_row_y + 3, geo_text)

    # ---- Tile list row ----
    tile_text = "Tuiles : " + (", ".join(page.tile_names) if page.tile_names else "—")
    # Truncate if too long
    max_chars = int(img_w / _CHAR_WIDTH_APPROX_PT)
    if len(tile_text) > max_chars:
        tile_text = tile_text[:max_chars - 3] + "…"
    c.setFont("Helvetica", 7.5)
    c.drawString(lx + 2, tile_row_y + 3, tile_text)

    # ---- Footer bar ----
    c.setFillColorRGB(0.92, 0.92, 0.92)
    c.rect(lx, footer_y, img_w, _FOOTER_H_PT, fill=1, stroke=0)
    c.setFillColorRGB(0.2, 0.2, 0.2)
    c.setFont("Helvetica", 7)
    c.drawString(lx + 2, footer_y + 4, "Source : IGN")
    c.drawCentredString(lx + img_w / 2.0, footer_y + 4, f"Page {page.page_index + 1} / {total_pages}")
    c.setFont("Helvetica-Bold", 7)
    c.setFillColorRGB(0.65, 0.1, 0.1)
    c.drawRightString(lx + img_w - 2, footer_y + 4, "IMPRESSION A 100 %")

    c.restoreState()


def convert_to_pdf(
    mosaic: Mosaic,
    cfg: PDFConfig,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> Path:
    """
    Convert a Mosaic to a multi-page A4 PDF.

    Parameters
    ----------
    mosaic:
        The Mosaic object to render.
    cfg:
        PDF configuration (DPI, orientation, margins, overlap, output path).
    progress_callback:
        Optional callable(current_page, total_pages, message).

    Returns
    -------
    Path to the generated PDF file.
    """
    return convert_folders_to_pdf(
        [("", mosaic)],
        cfg,
        progress_callback=progress_callback,
    )


def convert_folders_to_pdf(
    folder_mosaics: List[Tuple[str, Mosaic]],
    cfg: PDFConfig,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> Path:
    """
    Convert one or more mosaics into a single multi-page A4 PDF atlas.

    When ``cfg.scale > 0`` and a mosaic carries georef data, each folder's
    section is rendered at fixed cartographic scale with:
      1. A cover page (metadata summary + visual index)
      2. An overview page (mosaic thumbnail with tile/page overlays)
      3. One content page per A4 window of the mosaic

    Otherwise the legacy DPI-based page layout is used.

    Parameters
    ----------
    folder_mosaics:
        List of (folder_name, Mosaic) pairs.
    cfg:
        PDF configuration.
    progress_callback:
        Optional callable(current_page, total_pages, message).

    Returns
    -------
    Path to the generated PDF file.
    """
    if not REPORTLAB_AVAILABLE:
        raise RuntimeError("reportlab is required. Install with: pip install reportlab")
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow is required. Install with: pip install Pillow")

    if not folder_mosaics:
        raise ValueError("Aucune mosaïque à convertir.")

    # ReportLab page size in points
    if cfg.orientation == Orientation.PORTRAIT:
        rl_page_size = A4
    else:
        rl_page_size = landscape(A4)

    page_w_pt = rl_page_size[0]
    page_h_pt = rl_page_size[1]
    margin_pt = cfg.margin_mm / MM_PER_INCH * PT_PER_INCH
    printable_w_pt = page_w_pt - 2 * margin_pt
    printable_h_pt = page_h_pt - 2 * margin_pt

    cfg.output_path.parent.mkdir(parents=True, exist_ok=True)
    c = rl_canvas.Canvas(str(cfg.output_path), pagesize=rl_page_size)
    c.setTitle("IGN SCAN25 Atlas Export")
    c.setAuthor("GEOIMAGE_NOGDAL")

    # Pre-compute per-folder page lists
    folder_data: list[tuple[str, Mosaic, List[PageInfo], bool]] = []
    for folder_name, mosaic in folder_mosaics:
        use_scale = (
            cfg.scale > 0
            and mosaic.pixel_size_m > 0
            and (mosaic.geo_extent is not None or mosaic.width > 0)
        )
        if use_scale:
            pages = compute_pages_at_scale(mosaic, cfg)
        else:
            pages = compute_pages(mosaic, cfg)
        folder_data.append((folder_name, mosaic, pages, use_scale))

    # Total rendered steps for progress reporting
    total_steps = sum(
        len(pages) + (2 if use_scale and cfg.atlas_pages else 0)
        for _, _, pages, use_scale in folder_data
    )
    if total_steps == 0:
        raise ValueError("Toutes les mosaïques ont une taille nulle — rien à convertir.")

    step = 0
    for folder_name, mosaic, pages, use_scale in folder_data:
        n_content = len(pages)

        if use_scale and cfg.atlas_pages:
            # ---- Cover page ----
            if progress_callback:
                progress_callback(step, total_steps, "Génération page de garde…")
            _render_cover_page(
                c, mosaic, pages, page_w_pt, page_h_pt, margin_pt, cfg, folder_name
            )
            c.showPage()
            step += 1

            # ---- Overview page ----
            if progress_callback:
                progress_callback(step, total_steps, "Génération vue d'ensemble…")
            _render_overview_page(
                c, mosaic, pages, page_w_pt, page_h_pt, margin_pt, cfg, folder_name
            )
            c.showPage()
            step += 1

        # ---- Content pages ----
        for i, page in enumerate(pages):
            if progress_callback:
                progress_callback(step, total_steps, f"Rendu feuille {i + 1}/{n_content}")

            _render_page(
                c,
                mosaic,
                page,
                page_w_pt=page_w_pt,
                page_h_pt=page_h_pt,
                margin_pt=margin_pt,
                printable_w_pt=printable_w_pt,
                printable_h_pt=printable_h_pt,
                total_pages=n_content,
                folder_label=folder_name,
            )
            c.showPage()
            step += 1

            if progress_callback:
                progress_callback(step, total_steps, f"Feuille {i + 1}/{n_content} terminée")

    c.save()
    return cfg.output_path
