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
    Divide the mosaic into A4 pages.

    Returns a list of PageInfo objects, one per page.
    """
    pw = cfg.printable_w_px
    ph = cfg.printable_h_px

    cols = math.ceil(mosaic.width / pw)
    rows = math.ceil(mosaic.height / ph)

    pages: list[PageInfo] = []
    idx = 0
    for row in range(rows):
        for col in range(cols):
            src_x = col * pw
            src_y = row * ph
            src_w = min(pw, mosaic.width - src_x)
            src_h = min(ph, mosaic.height - src_y)
            pages.append(
                PageInfo(
                    page_index=idx,
                    col=col,
                    row=row,
                    src_x=src_x,
                    src_y=src_y,
                    src_w=src_w,
                    src_h=src_h,
                )
            )
            idx += 1

    return pages


def _pil_to_bytes(img: "Image.Image", fmt: str = "JPEG", quality: int = 90) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt, quality=quality)
    return buf.getvalue()


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
        PDF configuration (DPI, orientation, margins, output path).
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

    pages = compute_pages(mosaic, cfg)
    total = len(pages)

    if total == 0:
        raise ValueError("Mosaic has zero size — nothing to convert.")

    # ReportLab page size in points
    if cfg.orientation == Orientation.PORTRAIT:
        rl_page_size = A4  # (595.27, 841.89) pt
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

    for i, page in enumerate(pages):
        if progress_callback:
            progress_callback(i, total, f"Rendering page {i+1}/{total}")

        # Render the mosaic region for this page
        region = mosaic.get_region(page.src_x, page.src_y, page.src_w, page.src_h)

        # Convert to JPEG bytes
        img_bytes = _pil_to_bytes(region, fmt="JPEG", quality=92)
        img_reader = ImageReader(io.BytesIO(img_bytes))

        # Scale the image to fill the printable area proportionally (no destructive resize)
        scale_x = printable_w_pt / max(page.src_w, 1)
        scale_y = printable_h_pt / max(page.src_h, 1)
        # Use the smaller scale to keep aspect ratio
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
        label = f"Page {page.page_index + 1}/{total}  (col {page.col+1}, row {page.row+1})"
        c.drawString(margin_pt, margin_pt / 2, label)

        c.showPage()

        if progress_callback:
            progress_callback(i + 1, total, f"Page {i+1}/{total} done")

    c.save()
    return cfg.output_path
