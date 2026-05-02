"""
pdf_converter.py — Convert IGN raster mosaic to A4 PDF at exact scale.

Uses ReportLab for PDF generation.  Images are rendered tile-by-tile to
keep memory usage low.

A4 = 210 × 297 mm (portrait)  /  297 × 210 mm (landscape)
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
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


@dataclass
class PDFConfig:
    """Configuration for PDF export."""

    dpi: int = 300
    orientation: Orientation = Orientation.PORTRAIT
    margin_mm: float = 10.0
    overlap_mm: float = 5.0
    output_path: Path = Path("output.pdf")

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


def _pil_to_bytes(img: "Image.Image", fmt: str = "JPEG", quality: int = 90) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt, quality=quality)
    return buf.getvalue()


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
    """Render one mosaic page onto the ReportLab canvas (does not call showPage)."""
    region = mosaic.get_region(page.src_x, page.src_y, page.src_w, page.src_h)

    img_bytes = _pil_to_bytes(region, fmt="JPEG", quality=92)
    img_reader = ImageReader(io.BytesIO(img_bytes))

    # Scale to fill the printable area proportionally.
    # Because all pages have the same src_w × src_h (full-size windows),
    # the scale is uniform and no blank margins appear.
    scale_x = printable_w_pt / max(page.src_w, 1)
    scale_y = printable_h_pt / max(page.src_h, 1)
    scale = min(scale_x, scale_y)
    draw_w = page.src_w * scale
    draw_h = page.src_h * scale

    # Center within printable area
    x_pos = margin_pt + (printable_w_pt - draw_w) / 2
    y_pos = margin_pt + (printable_h_pt - draw_h) / 2

    c.drawImage(
        img_reader,
        x_pos,
        y_pos,
        width=draw_w,
        height=draw_h,
        preserveAspectRatio=True,
    )

    # Page label
    c.setFont("Helvetica", 8)
    c.setFillColorRGB(0.5, 0.5, 0.5)
    prefix = f"{folder_label} — " if folder_label else ""
    label = (
        f"{prefix}Page {page.page_index + 1}/{total_pages}"
        f"  (col {page.col+1}, row {page.row+1})"
    )
    c.drawString(margin_pt, margin_pt / 2, label)


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
    Convert one or more mosaics into a single multi-page A4 PDF.

    Parameters
    ----------
    folder_mosaics:
        List of (folder_name, Mosaic) pairs.  Each mosaic produces a set of
        pages; all pages are concatenated in order into the output PDF.
    cfg:
        PDF configuration (DPI, orientation, margins, overlap, output path).
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

    # Pre-compute all pages across all folders
    all_pages: list[tuple[str, Mosaic, PageInfo]] = []
    for folder_name, mosaic in folder_mosaics:
        pages = compute_pages(mosaic, cfg)
        for page in pages:
            all_pages.append((folder_name, mosaic, page))

    total = len(all_pages)
    if total == 0:
        raise ValueError("Toutes les mosaïques ont une taille nulle — rien à convertir.")

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
    c.setTitle("IGN SCAN25 Export")
    c.setAuthor("GEOIMAGE_NOGDAL")

    for i, (folder_name, mosaic, page) in enumerate(all_pages):
        if progress_callback:
            progress_callback(i, total, f"Rendu page {i+1}/{total}")

        _render_page(
            c,
            mosaic,
            page,
            page_w_pt=page_w_pt,
            page_h_pt=page_h_pt,
            margin_pt=margin_pt,
            printable_w_pt=printable_w_pt,
            printable_h_pt=printable_h_pt,
            total_pages=total,
            folder_label=folder_name,
        )

        c.showPage()

        if progress_callback:
            progress_callback(i + 1, total, f"Page {i+1}/{total} terminée")

    c.save()
    return cfg.output_path
