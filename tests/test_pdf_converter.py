"""Tests for pdf_converter.py — layout computation (no rendering required)."""

import sys
import math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.pdf_converter import PDFConfig, Orientation, compute_pages, MM_PER_INCH
from src.core.mosaic import Mosaic, MosaicLayout, TileInfo


def _make_mock_mosaic(width: int, height: int) -> Mosaic:
    """Create a Mosaic with a single placeholder tile (no real image)."""
    layout = MosaicLayout(
        tiles=[TileInfo(path=Path("dummy.tif"), x_off=0, y_off=0, width=width, height=height)],
        total_width=width,
        total_height=height,
    )
    return Mosaic(layout)


def test_config_printable_size_portrait():
    cfg = PDFConfig(dpi=300, orientation=Orientation.PORTRAIT, margin_mm=10.0)
    # A4 portrait: 210×297 mm, 10 mm margin each side → 190×277 mm printable
    assert abs(cfg.printable_w_mm - 190.0) < 0.1
    assert abs(cfg.printable_h_mm - 277.0) < 0.1

    expected_px_w = int(190.0 / MM_PER_INCH * 300)
    expected_px_h = int(277.0 / MM_PER_INCH * 300)
    assert cfg.printable_w_px == expected_px_w
    assert cfg.printable_h_px == expected_px_h


def test_config_printable_size_landscape():
    cfg = PDFConfig(dpi=300, orientation=Orientation.LANDSCAPE, margin_mm=10.0)
    # A4 landscape: 297×210 mm → printable 277×190
    assert abs(cfg.printable_w_mm - 277.0) < 0.1
    assert abs(cfg.printable_h_mm - 190.0) < 0.1


def test_compute_pages_single_page():
    cfg = PDFConfig(dpi=300, orientation=Orientation.PORTRAIT, margin_mm=10.0)
    # A small mosaic that fits on one page
    mosaic = _make_mock_mosaic(100, 100)
    pages = compute_pages(mosaic, cfg)
    assert len(pages) == 1
    assert pages[0].page_index == 0
    assert pages[0].col == 0
    assert pages[0].row == 0
    assert pages[0].src_x == 0
    assert pages[0].src_y == 0
    assert pages[0].src_w == 100
    assert pages[0].src_h == 100


def test_compute_pages_multiple():
    cfg = PDFConfig(dpi=300, orientation=Orientation.PORTRAIT, margin_mm=10.0)
    pw = cfg.printable_w_px
    ph = cfg.printable_h_px

    # 2 columns, 2 rows
    mosaic = _make_mock_mosaic(pw * 2, ph * 2)
    pages = compute_pages(mosaic, cfg)
    assert len(pages) == 4

    # Check that all four cells are covered
    cells = {(p.col, p.row) for p in pages}
    assert cells == {(0, 0), (1, 0), (0, 1), (1, 1)}


def test_compute_pages_partial_last_col():
    cfg = PDFConfig(dpi=300, orientation=Orientation.PORTRAIT, margin_mm=10.0)
    pw = cfg.printable_w_px
    ph = cfg.printable_h_px

    # Mosaic slightly wider than one page
    mosaic = _make_mock_mosaic(pw + 200, ph)
    pages = compute_pages(mosaic, cfg)
    assert len(pages) == 2

    # Second page should have reduced width
    right_page = next(p for p in pages if p.col == 1)
    assert right_page.src_w == 200


def test_compute_pages_sequence():
    cfg = PDFConfig(dpi=300, orientation=Orientation.PORTRAIT, margin_mm=0.0)
    pw = cfg.printable_w_px
    ph = cfg.printable_h_px

    mosaic = _make_mock_mosaic(pw * 3, ph * 2)
    pages = compute_pages(mosaic, cfg)
    assert len(pages) == 6

    # Pages ordered row by row, col by col
    assert pages[0].col == 0 and pages[0].row == 0
    assert pages[1].col == 1 and pages[1].row == 0
    assert pages[2].col == 2 and pages[2].row == 0
    assert pages[3].col == 0 and pages[3].row == 1



