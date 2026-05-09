"""
pdf_converter.py — Convert IGN raster mosaic to A4 PDF at exact cartographic scale.

Uses ReportLab for PDF generation.  Images are rendered tile-by-tile to
keep memory usage low.

A4 = 210 × 297 mm (portrait)  /  297 × 210 mm (landscape)

Atlas layout (per folder):
  1. Cover page   — metadata summary + visual index (tile grid + page grid)
  2. Content pages — one per A4 window of the mosaic at fixed scale
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from datetime import datetime
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
_HEADER_H_PT = 70.0   # Top header rounded rect (dataset + 3 info lines)
_GEO_H_PT    = 0.0    # Lambert 93 extent now embedded in the header
_TILES_H_PT  = 0.0    # Tile list now embedded in the header
_FOOTER_H_PT = 20.0   # Bottom footer bar (source | pagination | scale reminder)
# Total vertical overhead on content pages
_OVERHEAD_PT = _HEADER_H_PT + _GEO_H_PT + _TILES_H_PT + _FOOTER_H_PT

# ---------------------------------------------------------------------------
# Professional colour palette (RGB 0–1 floats)
# ---------------------------------------------------------------------------
_COL_ACCENT        = (0.12, 0.28, 0.50)   # dark-blue accent
_COL_WHITE         = (1.00, 1.00, 1.00)
_COL_BOX_BORDER    = (0.72, 0.79, 0.88)   # light blue-grey border
_COL_TEXT          = (0.12, 0.12, 0.15)   # near-black text
_COL_MUTED         = (0.45, 0.48, 0.54)   # secondary / muted text
_COL_NOTICE_FILL   = (0.95, 0.97, 1.00)   # light-blue notice background
_COL_NOTICE_STROKE = (0.67, 0.78, 0.92)   # notice border
_COL_TILE_FILL     = (0.83, 0.90, 0.98)   # tile legend swatch fill
_COL_TILE_STROKE   = (0.22, 0.47, 0.72)   # tile legend swatch stroke
_COL_PAGE_STROKE   = (0.82, 0.33, 0.05)   # page legend swatch stroke
_COL_BODY_FILL     = (0.96, 0.96, 0.97)   # image-area background

# Unit conversion helpers
_MM_PER_METER = 1000.0

# Overview page thumbnail bounds (pixels)
_MIN_THUMB_PX = 64
_MAX_THUMB_PX = 1024

# Approximate character width in points (used to truncate long tile lists)
_CHAR_WIDTH_APPROX_PT = 4.5

# Maximum number of tile names shown inline in the page header
_MAX_HEADER_TILES = 5

# Minimum height (points) of the overview map frame on the cover page
_MIN_OVERVIEW_H_PT = 20.0

# Space reserved above the overview map for its title (14 mm in points)
_OVERVIEW_TITLE_SPACE_PT = 14 * PT_PER_INCH / MM_PER_INCH

# Minimum drawing height (points) below which the mosaic index is skipped
_MIN_MAP_H_TO_DRAW_PT = 10.0

@dataclass
class PDFConfig:
    """Configuration for PDF export."""

    dpi: int = 300
    orientation: Orientation = Orientation.PORTRAIT
    margin_mm: float = 10.0
    overlap_mm: float = 5.0
    # If True, overlap_mm is treated as a minimum overlap and page starts are
    # distributed to fully cover the mosaic with no blank edge area.
    optimal_overlap: bool = False
    output_path: Path = Path("output.pdf")
    # Map scale denominator (e.g. 25000 for 1:25 000).
    # When > 0 and the mosaic carries georef data, atlas-style scale-based
    # page layout is used instead of the DPI-based pixel layout.
    scale: int = 25000
    # Whether to prepend cover + overview pages to each folder's section.
    atlas_pages: bool = True
    # Title displayed on the cover page header (user-customisable).
    atlas_title: str = "Atlas A4 en mosaïque continue"

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

    # Effective page region clamped to mosaic size (for mosaics smaller than 1 page)
    page_w = min(pw, mosaic.width)
    page_h = min(ph, mosaic.height)
    if cfg.optimal_overlap:
        starts_x = _compute_axis_starts_optimal(mosaic.width, page_w, overlap)
        starts_y = _compute_axis_starts_optimal(mosaic.height, page_h, overlap)
    else:
        # Stride is the number of new pixels introduced per step.
        stride_x = max(1, pw - overlap)
        stride_y = max(1, ph - overlap)
        cols = 1 if mosaic.width <= pw else 1 + math.ceil((mosaic.width - pw) / stride_x)
        rows = 1 if mosaic.height <= ph else 1 + math.ceil((mosaic.height - ph) / stride_y)
        starts_x = [col * stride_x for col in range(cols)]
        starts_y = [row * stride_y for row in range(rows)]

    pages: list[PageInfo] = []
    idx = 0
    for row, src_y in enumerate(starts_y):
        for col, src_x in enumerate(starts_x):

            # Shift edge pages back so we always render a full page_w × page_h region.
            # This prevents blank margins on the last column/row.
            if not cfg.optimal_overlap and src_x + page_w > mosaic.width:
                src_x = max(0, mosaic.width - page_w)
            if not cfg.optimal_overlap and src_y + page_h > mosaic.height:
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
    Adjacent pages overlap by ``cfg.overlap_mm`` (same semantics as
    ``compute_pages``): each page starts ``ground_w - overlap_ground`` metres
    after the previous one, ensuring a visible margin of repeated content.

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

    # Overlap between adjacent pages in metres (overlap_mm on paper at scale S)
    overlap_m = cfg.overlap_mm / _MM_PER_METER * cfg.scale
    # Convert to source pixels; clamp so stride stays at least 1
    overlap_src_px = max(0, min(int(round(overlap_m / psm)), min(src_w, src_h) - 1))
    if cfg.optimal_overlap:
        starts_x = _compute_axis_starts_optimal(mosaic.width, src_w, overlap_src_px)
        starts_y = _compute_axis_starts_optimal(mosaic.height, src_h, overlap_src_px)
    else:
        stride_w = max(1, src_w - overlap_src_px)
        stride_h = max(1, src_h - overlap_src_px)
        cols = max(1, math.ceil((mosaic.width - overlap_src_px) / stride_w))
        rows = max(1, math.ceil((mosaic.height - overlap_src_px) / stride_h))
        starts_x = [col * stride_w for col in range(cols)]
        starts_y = [row * stride_h for row in range(rows)]
    geo = mosaic.geo_extent

    pages: list[PageInfo] = []
    idx = 0
    for row, src_y in enumerate(starts_y):
        for col, src_x in enumerate(starts_x):
            # Shift edge pages back so we always render a full-size region.
            # This prevents blank margins on last column/row and ensures
            # border tiles are effectively cut on content pages.
            # Note: after this adjustment, (col,row) remains the logical grid
            # index while (src_x, src_y) is the effective source position.
            if not cfg.optimal_overlap and src_x + src_w > mosaic.width:
                src_x = max(0, mosaic.width - src_w)
            if not cfg.optimal_overlap and src_y + src_h > mosaic.height:
                src_y = max(0, mosaic.height - src_h)

            # Lambert 93 extents for this page
            has_geo = False
            page_min_x = page_min_y = page_max_x = page_max_y = 0.0
            if geo is not None and geo.is_valid():
                page_min_x = geo.min_x + src_x * psm
                page_max_x = page_min_x + ground_w
                page_max_y = geo.max_y - src_y * psm
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


def _compute_axis_starts_optimal(total_size: int, page_size: int, min_overlap: int) -> List[int]:
    """Compute page starts that fully cover the axis and keep overlap >= min_overlap."""
    if total_size <= 0:
        return [0]
    if page_size <= 0 or total_size <= page_size:
        return [0]

    # Upper-bound overlap to page_size - 1 so stride remains >= 1 px.
    min_ov = max(0, min(min_overlap, page_size - 1))
    stride_limit = max(1, page_size - min_ov)
    span = total_size - page_size
    steps = max(1, math.ceil(span / stride_limit))

    base_stride = span // steps
    remainder = span % steps

    starts = [0]
    cur = 0
    for i in range(steps):
        cur += base_stride + (1 if i < remainder else 0)
        starts.append(cur)
    return starts


def _pil_to_bytes(img: "Image.Image", fmt: str = "JPEG", quality: int = 90) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt, quality=quality)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _format_scale(scale_value: int) -> str:
    """Return a human-readable scale string such as '1 : 25 000' (using non-breaking spaces)."""
    return f"1\xa0:\xa0{scale_value:,}".replace(",", "\xa0")


def _format_distance_m(meters: float) -> str:
    """Format a ground distance in metres or kilometres."""
    if meters >= 1000.0:
        return f"{meters / 1000.0:.2f} km"
    return f"{meters:.0f} m"


def _shorten_text(text: str, max_chars: int) -> str:
    """Truncate *text* to *max_chars* characters, appending '…' if needed."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "\u2026"


def _draw_wrapped_text(
    c: "rl_canvas.Canvas",
    text: str,
    x: float,
    y: float,
    max_w: float,
    font: str = "Helvetica",
    font_size: float = 8.0,
    line_h: float = 10.0,
) -> None:
    """Draw *text* inside *max_w* points, wrapping at word boundaries."""
    c.setFont(font, font_size)
    words = text.split()
    line = ""
    cur_y = y
    for word in words:
        candidate = f"{line} {word}".strip()
        if c.stringWidth(candidate, font, font_size) <= max_w:
            line = candidate
        else:
            if line:
                c.drawString(x, cur_y, line)
                cur_y -= line_h
            line = word
    if line:
        c.drawString(x, cur_y, line)



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
    generation_time: Optional[datetime] = None,
) -> None:
    """Render a professional atlas cover page with metadata and visual index."""
    lx = margin_pt                        # printable left x
    pw = page_w_pt - 2 * margin_pt        # printable width
    by = margin_pt                        # printable bottom y
    top_y = page_h_pt - margin_pt         # printable top y (uniform margin on all sides)

    dataset_name = folder_name or "Lot sans titre"

    c.saveState()

    # ── Accent header box (uniform margin on all four sides, 48 mm tall) ─────
    header_h = 48 * mm
    header_bottom = top_y - header_h
    c.setFillColorRGB(*_COL_ACCENT)
    c.roundRect(lx, header_bottom, pw, header_h, 6, fill=1, stroke=0)

    # generation_time is normally provided by convert_folders_to_pdf (captured once
    # before any page is rendered).  The fallback to datetime.now() is intentional
    # for callers that invoke _render_cover_page directly without a timestamp.
    ts = generation_time if generation_time is not None else datetime.now()
    header_text_pad = 5 * mm                           # inner left padding inside header box
    c.setFillColorRGB(*_COL_WHITE)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(lx + header_text_pad, top_y - 17 * mm, cfg.atlas_title.upper())
    c.setFont("Helvetica", 12)
    c.drawString(lx + header_text_pad, top_y - 27 * mm, dataset_name)
    c.setFont("Helvetica", 10)
    c.drawString(
        lx + header_text_pad,
        top_y - 36 * mm,
        f"Généré le {ts.strftime('%d/%m/%Y à %H:%M')}",
    )

    # ── Summary box ──────────────────────────────────────────────────────────
    summary_x = lx
    summary_y = header_bottom - 42 * mm   # 8 mm gap below header + 34 mm box height
    summary_w = pw
    summary_h = 34 * mm
    c.setFillColorRGB(*_COL_WHITE)
    c.setStrokeColorRGB(*_COL_BOX_BORDER)
    c.setLineWidth(0.8)
    c.roundRect(summary_x, summary_y, summary_w, summary_h, 8, fill=1, stroke=1)

    geo = mosaic.geo_extent
    ground_w_m = cfg.printable_w_mm / _MM_PER_METER * cfg.scale if cfg.scale > 0 else 0.0
    ground_h_m = cfg.image_h_mm / _MM_PER_METER * cfg.scale if cfg.scale > 0 else 0.0
    mosaic_w_m = (geo.max_x - geo.min_x) if (geo and geo.is_valid()) else 0.0
    mosaic_h_m = (geo.max_y - geo.min_y) if (geo and geo.is_valid()) else 0.0

    info_lines = [
        f"Échelle fixe : {_format_scale(cfg.scale)}" if cfg.scale > 0 else "Échelle : auto",
    ]
    tiles_pages_base = f"Tuiles source : {len(mosaic.layout.tiles)}    •    Pages atlas : {len(pages)}"
    info_lines.append(
        tiles_pages_base + " (+ couverture + vue d'ensemble)" if cfg.atlas_pages else tiles_pages_base
    )
    if mosaic_w_m > 0 and mosaic_h_m > 0:
        info_lines.append(
            f"Mosaïque assemblée : {_format_distance_m(mosaic_w_m)} × {_format_distance_m(mosaic_h_m)}"
        )
    if ground_w_m > 0 and ground_h_m > 0:
        info_lines.append(
            f"Couverture utile par page : {_format_distance_m(ground_w_m)} × {_format_distance_m(ground_h_m)}"
        )

    c.setFillColorRGB(*_COL_TEXT)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(summary_x + 5 * mm, summary_y + summary_h - 7 * mm, "RÉSUMÉ DE PRODUCTION")
    c.setFont("Helvetica", 9)
    line_y = summary_y + summary_h - 13 * mm
    for line in info_lines:
        c.drawString(summary_x + 5 * mm, line_y, line)
        line_y -= 5 * mm

    # Source path at bottom of summary
    psm = mosaic.pixel_size_m
    psm_txt = f"  —  {psm:.2f} m/px" if psm > 0 else ""
    c.setFillColorRGB(*_COL_MUTED)
    c.setFont("Helvetica", 8)
    c.drawString(summary_x + 5 * mm, summary_y + 3.5 * mm, f"Mosaïque : {mosaic.width:,} × {mosaic.height:,} px{psm_txt}")

    # ── Notice box ───────────────────────────────────────────────────────────
    notice_x = lx
    notice_y = summary_y - 15 * mm   # below summary
    notice_w = pw
    notice_h = 10 * mm
    c.setFillColorRGB(*_COL_NOTICE_FILL)
    c.setStrokeColorRGB(*_COL_NOTICE_STROKE)
    c.setLineWidth(0.6)
    c.roundRect(notice_x, notice_y, notice_w, notice_h, 5, fill=1, stroke=1)
    c.setFillColorRGB(*_COL_TEXT)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(notice_x + 4 * mm, notice_y + 6.1 * mm, "Principe atlas :")
    c.setFont("Helvetica", 8)
    c.drawString(
        notice_x + 28 * mm,
        notice_y + 6.1 * mm,
        "toutes les tuiles sont assemblées en mosaïque continue, puis découpées en pages A4"
        " à échelle constante.",
    )
    c.setFont("Helvetica", 8)
    c.drawString(
        notice_x + 28 * mm,
        notice_y + 2.1 * mm,
        "Imprimer à 100 % (sans mise à l'échelle) pour conserver le rapport cartographique.",
    )

    # ── Legend ───────────────────────────────────────────────────────────────
    legend_y = notice_y - 6 * mm
    c.setFillColorRGB(*_COL_TILE_FILL)
    c.setStrokeColorRGB(*_COL_TILE_STROKE)
    c.setLineWidth(0.7)
    c.rect(lx, legend_y, 8 * mm, 4 * mm, fill=1, stroke=1)
    c.setFillColorRGB(*_COL_TEXT)
    c.setFont("Helvetica", 8)
    c.drawString(lx + 10 * mm, legend_y + 1.3 * mm, "Tuiles source assemblées")

    c.setFillColorRGB(*_COL_WHITE)
    c.setStrokeColorRGB(*_COL_PAGE_STROKE)
    c.setLineWidth(0.7)
    c.setDash([3, 2])
    c.rect(lx + 65 * mm, legend_y, 8 * mm, 4 * mm, fill=1, stroke=1)
    c.setDash([])
    c.setFillColorRGB(*_COL_TEXT)
    c.drawString(lx + 75 * mm, legend_y + 1.3 * mm, "Découpage des pages A4")

    # ── Overview map frame (fills remaining space) ────────────────────────────
    overview_x = lx
    overview_y = by                                                     # uniform bottom margin
    overview_w = pw
    overview_h = max(_MIN_OVERVIEW_H_PT, legend_y - 4 * mm - overview_y)
    c.setFillColorRGB(*_COL_WHITE)
    c.setStrokeColorRGB(*_COL_BOX_BORDER)
    c.setLineWidth(0.8)
    c.roundRect(overview_x, overview_y, overview_w, overview_h, 8, fill=1, stroke=1)

    c.setFillColorRGB(*_COL_TEXT)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(
        overview_x + 5 * mm,
        overview_y + overview_h - 8 * mm,
        "VUE D'ENSEMBLE DE LA MOSAÏQUE ET PAGINATION",
    )

    inner_margin = 5 * mm
    map_x = overview_x + inner_margin
    map_y = overview_y + inner_margin
    map_w = overview_w - 2 * inner_margin
    map_h = overview_h - _OVERVIEW_TITLE_SPACE_PT
    if map_h > _MIN_MAP_H_TO_DRAW_PT:
        _draw_mosaic_index(c, mosaic, pages, map_x, map_y, map_w, map_h)

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
    hdr_h = 14 * mm
    c.setFillColorRGB(*_COL_ACCENT)
    c.rect(0, page_h_pt - hdr_h, page_w_pt, hdr_h, fill=1, stroke=0)
    c.setFillColorRGB(*_COL_WHITE)
    c.setFont("Helvetica-Bold", 11)
    label = f"Vue d'ensemble — {folder_name}" if folder_name else "Vue d'ensemble"
    c.drawCentredString(page_w_pt / 2.0, page_h_pt - hdr_h + 4 * mm, label)
    c.setFont("Helvetica", 8)
    c.drawCentredString(
        page_w_pt / 2.0,
        page_h_pt - hdr_h + 1.5 * mm,
        f"{len(mosaic.layout.tiles)} tuile(s) source   •   {len(pages)} page(s) atlas",
    )

    # ---- Mosaic thumbnail ----
    img_area_y = by
    img_area_h = ph - hdr_h - 2 * mm  # 2 mm gap below accent header
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
    """Professional atlas content page: accent header, image frame, footer."""
    lx = margin_pt
    by = margin_pt
    ty = by + printable_h_pt
    pw = printable_w_pt

    # ── Vertical layout ────────────────────────────────────────────────────
    # (All values in points; _OVERHEAD_PT = _HEADER_H_PT + _FOOTER_H_PT)
    footer_y   = by
    footer_top = footer_y + _FOOTER_H_PT
    img_y      = footer_top
    img_top    = ty - _HEADER_H_PT
    img_h      = img_top - img_y          # equals cfg.image_h_mm * mm
    img_w      = pw
    header_y   = img_top

    c.saveState()

    # ── Accent header (rounded rect) ────────────────────────────────────────
    c.setFillColorRGB(*_COL_ACCENT)
    c.setStrokeColorRGB(*_COL_ACCENT)
    c.roundRect(lx, header_y, pw, _HEADER_H_PT, 8, fill=1, stroke=0)

    tile_labels = ", ".join(page.tile_names[:_MAX_HEADER_TILES])
    # remaining_tiles is negative when fewer than _MAX_HEADER_TILES tiles are present;
    # the `> 0` guard ensures the suffix is only appended when tiles were truncated.
    remaining_tiles = len(page.tile_names) - _MAX_HEADER_TILES
    if remaining_tiles > 0:
        tile_labels += f" … (+{remaining_tiles})"

    c.setFillColorRGB(*_COL_WHITE)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(lx + 5 * mm, header_y + _HEADER_H_PT - 6 * mm, folder_label or "Atlas")
    c.setFont("Helvetica", 8.6)
    c.drawString(
        lx + 5 * mm,
        header_y + _HEADER_H_PT - 11.5 * mm,
        f"Page {page.page_index + 1}/{total_pages}"
        f"   \u2022   L{page.row + 1} C{page.col + 1}",
    )
    if page.has_geo:
        c.drawString(
            lx + 5 * mm,
            header_y + _HEADER_H_PT - 16.8 * mm,
            f"Emprise : X\u202f{page.geo_min_x:,.0f}\u202f\u2192\u202f{page.geo_max_x:,.0f} m"
            f"  |  Y\u202f{page.geo_min_y:,.0f}\u202f\u2192\u202f{page.geo_max_y:,.0f} m",
        )
    c.drawString(
        lx + 5 * mm,
        header_y + _HEADER_H_PT - 22.1 * mm,
        f"Tuiles : {_shorten_text(tile_labels or '—', 100)}",
    )

    # ── Image frame (decorative border around map) ──────────────────────────
    c.setFillColorRGB(*_COL_WHITE)
    c.setStrokeColorRGB(*_COL_BOX_BORDER)
    c.setLineWidth(0.6)
    c.roundRect(lx, img_y, img_w, img_h, 6, fill=1, stroke=1)

    # ── Map image ──────────────────────────────────────────────────────────
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

    # ── Footer ────────────────────────────────────────────────────────────
    c.setStrokeColorRGB(*_COL_BOX_BORDER)
    c.setLineWidth(0.5)
    c.line(lx, footer_top, lx + pw, footer_top)

    c.setFillColorRGB(*_COL_MUTED)
    c.setFont("Helvetica", 7.6)
    first_tile_name = page.tile_names[0] if page.tile_names else ""
    c.drawString(lx, footer_y + 2.7 * mm, _shorten_text(first_tile_name, 60))
    c.setFillColorRGB(*_COL_TEXT)
    c.drawCentredString(
        lx + pw / 2.0,
        footer_y + 2.7 * mm,
        f"Imprimer à 100 % pour respecter l'échelle",
    )
    c.drawRightString(
        lx + pw,
        footer_y + 2.7 * mm,
        f"PDF {page.page_index + 1}/{total_pages}",
    )

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
      2. One content page per A4 window of the mosaic

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
        len(pages) + (1 if use_scale and cfg.atlas_pages else 0)
        for _, _, pages, use_scale in folder_data
    )
    if total_steps == 0:
        raise ValueError("Toutes les mosaïques ont une taille nulle — rien à convertir.")

    # Capture generation timestamp once for all cover pages in this PDF
    generation_time = datetime.now()

    step = 0
    for folder_name, mosaic, pages, use_scale in folder_data:
        n_content = len(pages)

        if use_scale and cfg.atlas_pages:
            # ---- Cover page ----
            if progress_callback:
                progress_callback(step, total_steps, "Génération page de garde…")
            _render_cover_page(
                c, mosaic, pages, page_w_pt, page_h_pt, margin_pt, cfg, folder_name,
                generation_time=generation_time,
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
