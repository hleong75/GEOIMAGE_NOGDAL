"""Tests for pdf_converter.py — layout computation (no rendering required)."""

import sys
import math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.pdf_converter import (
    PDFConfig, Orientation, compute_pages, compute_pages_at_scale,
    MM_PER_INCH, convert_folders_to_pdf,
    _HEADER_H_PT, _GEO_H_PT, _TILES_H_PT, _FOOTER_H_PT, PT_PER_INCH,
)
from src.core.mosaic import Mosaic, MosaicLayout, TileInfo
from src.core.georef import GeoInfo


def _make_mock_mosaic(width: int, height: int) -> Mosaic:
    """Create a Mosaic with a single placeholder tile (no real image)."""
    layout = MosaicLayout(
        tiles=[TileInfo(path=Path("dummy.tif"), x_off=0, y_off=0, width=width, height=height)],
        total_width=width,
        total_height=height,
    )
    return Mosaic(layout)


def _make_georef_mosaic(
    width_px: int,
    height_px: int,
    pixel_size_m: float,
    min_x: float = 700000.0,
    min_y: float = 6120000.0,
) -> Mosaic:
    """Create a Mosaic with georef data (Lambert 93) for scale-based tests."""
    max_x = min_x + width_px * pixel_size_m
    max_y = min_y + height_px * pixel_size_m
    geo = GeoInfo(
        min_x=min_x, min_y=min_y, max_x=max_x, max_y=max_y,
        pixel_size_x=pixel_size_m, pixel_size_y=pixel_size_m,
        width_px=width_px, height_px=height_px,
        source="test",
    )
    tile = TileInfo(
        path=Path("dummy.tif"), x_off=0, y_off=0,
        width=width_px, height=height_px, geo=geo,
    )
    layout = MosaicLayout(
        tiles=[tile],
        total_width=width_px,
        total_height=height_px,
        pixel_size_m=pixel_size_m,
        geo_extent=geo,
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


def test_config_overlap_px():
    cfg = PDFConfig(dpi=300, orientation=Orientation.PORTRAIT, margin_mm=10.0, overlap_mm=5.0)
    expected = int(5.0 / MM_PER_INCH * 300)
    assert cfg.overlap_px == expected


def test_config_image_h_mm():
    """image_h_mm must be strictly less than printable_h_mm (overhead subtracted)."""
    cfg = PDFConfig(dpi=300, orientation=Orientation.PORTRAIT, margin_mm=10.0, scale=25000)
    assert cfg.image_h_mm < cfg.printable_h_mm
    # Overhead is (_HEADER + _GEO + _TILES + _FOOTER) / 72 * 25.4 mm
    overhead_mm = (_HEADER_H_PT + _GEO_H_PT + _TILES_H_PT + _FOOTER_H_PT) / PT_PER_INCH * 25.4
    assert abs(cfg.image_h_mm - (cfg.printable_h_mm - overhead_mm)) < 0.5


def test_compute_pages_single_page():
    cfg = PDFConfig(dpi=300, orientation=Orientation.PORTRAIT, margin_mm=10.0, overlap_mm=0.0)
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
    cfg = PDFConfig(dpi=300, orientation=Orientation.PORTRAIT, margin_mm=10.0, overlap_mm=0.0)
    pw = cfg.printable_w_px
    ph = cfg.printable_h_px

    # 2 columns, 2 rows -- mosaic is exactly 2x2 pages, no partial edge
    mosaic = _make_mock_mosaic(pw * 2, ph * 2)
    pages = compute_pages(mosaic, cfg)
    assert len(pages) == 4

    # Check that all four cells are covered
    cells = {(p.col, p.row) for p in pages}
    assert cells == {(0, 0), (1, 0), (0, 1), (1, 1)}


def test_compute_pages_partial_last_col():
    cfg = PDFConfig(dpi=300, orientation=Orientation.PORTRAIT, margin_mm=10.0, overlap_mm=0.0)
    pw = cfg.printable_w_px
    ph = cfg.printable_h_px

    # Mosaic slightly wider than one page
    mosaic = _make_mock_mosaic(pw + 200, ph)
    pages = compute_pages(mosaic, cfg)
    assert len(pages) == 2

    # Edge page is shifted back so it always covers a full pw-wide region
    right_page = next(p for p in pages if p.col == 1)
    assert right_page.src_w == pw
    # src_x shifted back: mosaic.width - pw = 200
    assert right_page.src_x == 200


def test_compute_pages_overlap():
    cfg = PDFConfig(dpi=300, orientation=Orientation.PORTRAIT, margin_mm=10.0, overlap_mm=0.0)
    pw = cfg.printable_w_px
    ph = cfg.printable_h_px

    # With overlap, add a 10 mm overlap
    overlap_mm = 10.0
    cfg_ov = PDFConfig(dpi=300, orientation=Orientation.PORTRAIT, margin_mm=10.0, overlap_mm=overlap_mm)
    overlap_px = cfg_ov.overlap_px
    stride = pw - overlap_px

    # Mosaic exactly 2 page-widths wide, 1 page-height tall
    mosaic = _make_mock_mosaic(pw * 2, ph)
    pages = compute_pages(mosaic, cfg_ov)

    # More pages due to overlap
    assert len(pages) > 2

    # First page starts at 0
    assert pages[0].src_x == 0
    # Second page starts at stride = pw - overlap_px
    col1_pages = [p for p in pages if p.col == 1]
    assert col1_pages[0].src_x == stride


def test_compute_pages_optimal_overlap_no_blank_and_min_overlap():
    cfg = PDFConfig(
        dpi=300,
        orientation=Orientation.PORTRAIT,
        margin_mm=10.0,
        overlap_mm=10.0,
        optimal_overlap=True,
    )
    pw = cfg.printable_w_px
    ph = cfg.printable_h_px
    mosaic = _make_mock_mosaic(pw * 3 + 500, ph * 2 + 250)
    pages = compute_pages(mosaic, cfg)

    row0 = sorted([p for p in pages if p.row == 0], key=lambda p: p.col)
    col0 = sorted([p for p in pages if p.col == 0], key=lambda p: p.row)

    assert row0[0].src_x == 0
    assert row0[-1].src_x + row0[-1].src_w == mosaic.width
    assert col0[0].src_y == 0
    assert col0[-1].src_y + col0[-1].src_h == mosaic.height

    min_overlap_px = cfg.overlap_px
    strides_x = [row0[i + 1].src_x - row0[i].src_x for i in range(len(row0) - 1)]
    strides_y = [col0[i + 1].src_y - col0[i].src_y for i in range(len(col0) - 1)]
    overlaps_x = [row0[i].src_w - s for i, s in enumerate(strides_x)]
    overlaps_y = [col0[i].src_h - s for i, s in enumerate(strides_y)]
    assert all(ov >= min_overlap_px for ov in overlaps_x)
    assert all(ov >= min_overlap_px for ov in overlaps_y)


def test_compute_pages_sequence():
    cfg = PDFConfig(dpi=300, orientation=Orientation.PORTRAIT, margin_mm=0.0, overlap_mm=0.0)
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


# ---------------------------------------------------------------------------
# compute_pages_at_scale tests
# ---------------------------------------------------------------------------

def test_compute_pages_at_scale_fallback_no_pixel_size():
    """Without pixel_size_m, compute_pages_at_scale falls back to compute_pages."""
    cfg = PDFConfig(dpi=300, orientation=Orientation.PORTRAIT, margin_mm=10.0, scale=25000)
    # Mosaic without georef (pixel_size_m = 0)
    mosaic = _make_mock_mosaic(100, 100)
    assert mosaic.pixel_size_m == 0.0

    pages_scale = compute_pages_at_scale(mosaic, cfg)
    pages_legacy = compute_pages(mosaic, cfg)
    assert len(pages_scale) == len(pages_legacy)


def test_compute_pages_at_scale_fallback_no_scale():
    """With scale=0, compute_pages_at_scale falls back to compute_pages."""
    cfg = PDFConfig(dpi=300, orientation=Orientation.PORTRAIT, margin_mm=10.0, scale=0)
    mosaic = _make_georef_mosaic(10000, 10000, pixel_size_m=2.5)

    pages_scale = compute_pages_at_scale(mosaic, cfg)
    pages_legacy = compute_pages(mosaic, cfg)
    assert len(pages_scale) == len(pages_legacy)


def test_compute_pages_at_scale_single_page():
    """A tiny mosaic (smaller than one page at 1:25000) yields exactly one page."""
    cfg = PDFConfig(dpi=300, orientation=Orientation.PORTRAIT, margin_mm=10.0, scale=25000)
    # At 1:25000, portrait, 10 mm margin:
    #   ground_w = 190/1000 * 25000 = 4750 m
    #   image_h  < 277 mm → image_h_mm ≈ 259 mm → ground_h ≈ 6475 m
    # A small mosaic of 100×100 pixels at 2.5 m/px = 250 m × 250 m → 1 page
    mosaic = _make_georef_mosaic(100, 100, pixel_size_m=2.5)
    pages = compute_pages_at_scale(mosaic, cfg)
    assert len(pages) == 1
    assert pages[0].page_index == 0
    assert pages[0].col == 0
    assert pages[0].row == 0


def test_compute_pages_at_scale_geo_extent_populated():
    """Pages computed at scale carry Lambert 93 extents."""
    cfg = PDFConfig(dpi=300, orientation=Orientation.PORTRAIT, margin_mm=10.0, scale=25000)
    mosaic = _make_georef_mosaic(10000, 10000, pixel_size_m=2.5,
                                  min_x=700000.0, min_y=6120000.0)
    pages = compute_pages_at_scale(mosaic, cfg)
    assert len(pages) >= 1
    p0 = pages[0]
    assert p0.has_geo
    assert abs(p0.geo_min_x - 700000.0) < 1.0
    assert abs(p0.geo_max_y - (6120000.0 + 10000 * 2.5)) < 1.0


def test_compute_pages_at_scale_tile_names():
    """Each page lists the stems of tiles it overlaps."""
    cfg = PDFConfig(dpi=300, orientation=Orientation.PORTRAIT, margin_mm=10.0, scale=25000)
    mosaic = _make_georef_mosaic(100, 100, pixel_size_m=2.5)
    pages = compute_pages_at_scale(mosaic, cfg)
    assert len(pages) >= 1
    # The single tile "dummy" should appear in page 0
    assert "dummy" in pages[0].tile_names


def test_compute_pages_at_scale_multi_page():
    """A large mosaic produces multiple pages covering the full extent."""
    cfg = PDFConfig(dpi=300, orientation=Orientation.PORTRAIT, margin_mm=10.0, scale=25000)
    pixel_size_m = 2.5
    # ground_w ≈ 4750 m at 1:25000, 190mm printable width
    # Make a mosaic that covers ~2×2 pages: ~9500 m × ~2×image_h_mm/1000*25000 m
    ground_w = cfg.printable_w_mm / 1000.0 * cfg.scale   # ~4750 m
    ground_h = cfg.image_h_mm / 1000.0 * cfg.scale
    # Mosaic a bit more than 2 pages wide and 2 pages tall
    w_px = int((ground_w * 2 + 100) / pixel_size_m)
    h_px = int((ground_h * 2 + 100) / pixel_size_m)
    mosaic = _make_georef_mosaic(w_px, h_px, pixel_size_m=pixel_size_m)
    pages = compute_pages_at_scale(mosaic, cfg)
    assert len(pages) >= 4   # at least 2×2 pages
    cols = {p.col for p in pages}
    rows = {p.row for p in pages}
    assert len(cols) >= 2
    assert len(rows) >= 2


def test_compute_pages_at_scale_consistent_src_size():
    """All pages in a scale-based layout share the same src_w and src_h."""
    cfg = PDFConfig(dpi=300, orientation=Orientation.PORTRAIT, margin_mm=10.0, scale=25000)
    pixel_size_m = 2.5
    ground_w = cfg.printable_w_mm / 1000.0 * cfg.scale
    ground_h = cfg.image_h_mm / 1000.0 * cfg.scale
    w_px = int((ground_w * 3 + 50) / pixel_size_m)
    h_px = int((ground_h * 2 + 50) / pixel_size_m)
    mosaic = _make_georef_mosaic(w_px, h_px, pixel_size_m=pixel_size_m)
    pages = compute_pages_at_scale(mosaic, cfg)
    assert len(pages) > 1
    src_w_set = {p.src_w for p in pages}
    src_h_set = {p.src_h for p in pages}
    assert len(src_w_set) == 1, "All pages should have the same src_w"
    assert len(src_h_set) == 1, "All pages should have the same src_h"


def test_compute_pages_at_scale_landscape():
    """Scale-based layout works correctly in landscape orientation."""
    cfg = PDFConfig(dpi=300, orientation=Orientation.LANDSCAPE, margin_mm=10.0, scale=25000)
    assert cfg.printable_w_mm > cfg.printable_h_mm   # landscape: wider than tall
    mosaic = _make_georef_mosaic(10000, 5000, pixel_size_m=2.5)
    pages = compute_pages_at_scale(mosaic, cfg)
    assert len(pages) >= 1
    # All pages should have landscape aspect ratio (src_w >= src_h)
    for p in pages:
        assert p.src_w >= p.src_h


def test_compute_pages_at_scale_overlap_applied():
    """Overlap is applied in scale-based mode: adjacent pages share source pixels."""
    pixel_size_m = 2.5
    overlap_mm = 10.0
    cfg_ov = PDFConfig(dpi=300, orientation=Orientation.PORTRAIT, margin_mm=10.0,
                       scale=25000, overlap_mm=overlap_mm)
    cfg_no = PDFConfig(dpi=300, orientation=Orientation.PORTRAIT, margin_mm=10.0,
                       scale=25000, overlap_mm=0.0)

    ground_w = cfg_ov.printable_w_mm / 1000.0 * cfg_ov.scale
    ground_h = cfg_ov.image_h_mm / 1000.0 * cfg_ov.scale

    # Mosaic wide and tall enough to need at least 2 columns and 2 rows
    w_px = int((ground_w * 2 + 100) / pixel_size_m)
    h_px = int((ground_h * 2 + 100) / pixel_size_m)
    mosaic = _make_georef_mosaic(w_px, h_px, pixel_size_m=pixel_size_m)

    pages_ov = compute_pages_at_scale(mosaic, cfg_ov)
    pages_no = compute_pages_at_scale(mosaic, cfg_no)

    # With overlap, there should be at least as many pages as without
    assert len(pages_ov) >= len(pages_no)

    # All pages share the same src_w / src_h regardless of overlap
    assert len({p.src_w for p in pages_ov}) == 1
    assert len({p.src_h for p in pages_ov}) == 1

    # Adjacent columns must overlap in source pixels
    col0 = next(p for p in pages_ov if p.col == 0 and p.row == 0)
    col1 = next(p for p in pages_ov if p.col == 1 and p.row == 0)
    assert col0.src_x + col0.src_w > col1.src_x, "Adjacent pages should share source pixels"

    # Expected stride: src_w - round(overlap_m / psm)
    overlap_m = overlap_mm / 1000.0 * cfg_ov.scale
    expected_stride = col0.src_w - int(round(overlap_m / pixel_size_m))
    assert col1.src_x == expected_stride

    # Adjacent rows must also overlap in source pixels
    row0 = next(p for p in pages_ov if p.col == 0 and p.row == 0)
    row1 = next(p for p in pages_ov if p.col == 0 and p.row == 1)
    assert row0.src_y + row0.src_h > row1.src_y, "Adjacent rows should share source pixels"

    # Without overlap, pages must be exactly adjacent (no shared pixels)
    col0_no = next(p for p in pages_no if p.col == 0 and p.row == 0)
    col1_no = next(p for p in pages_no if p.col == 1 and p.row == 0)
    assert col0_no.src_x + col0_no.src_w == col1_no.src_x, "No-overlap pages must be exactly adjacent"


def test_compute_pages_at_scale_overlap_geo_extents():
    """With overlap, Lambert 93 extents per page are shifted by the stride (not full ground_w)."""
    pixel_size_m = 2.5
    overlap_mm = 10.0
    cfg = PDFConfig(dpi=300, orientation=Orientation.PORTRAIT, margin_mm=10.0,
                    scale=25000, overlap_mm=overlap_mm)

    ground_w = cfg.printable_w_mm / 1000.0 * cfg.scale
    ground_h = cfg.image_h_mm / 1000.0 * cfg.scale
    overlap_m = overlap_mm / 1000.0 * cfg.scale
    stride_m = ground_w - overlap_m

    w_px = int((ground_w * 2 + 100) / pixel_size_m)
    h_px = int(ground_h / pixel_size_m)
    mosaic = _make_georef_mosaic(w_px, h_px, pixel_size_m=pixel_size_m, min_x=700000.0)

    pages = compute_pages_at_scale(mosaic, cfg)

    col0 = next(p for p in pages if p.col == 0 and p.row == 0)
    col1 = next(p for p in pages if p.col == 1 and p.row == 0)

    assert col0.has_geo
    assert col1.has_geo
    # col1 geo_min_x should be col0.geo_min_x + stride_m (not + ground_w)
    assert abs(col1.geo_min_x - (col0.geo_min_x + stride_m)) < 1.0
    # Each page covers exactly ground_w metres
    assert abs(col0.geo_max_x - col0.geo_min_x - ground_w) < 1.0
    assert abs(col1.geo_max_x - col1.geo_min_x - ground_w) < 1.0


def test_compute_pages_at_scale_optimal_overlap_no_blank_and_min_overlap():
    pixel_size_m = 2.5
    overlap_mm = 10.0
    cfg = PDFConfig(
        dpi=300,
        orientation=Orientation.PORTRAIT,
        margin_mm=10.0,
        scale=25000,
        overlap_mm=overlap_mm,
        optimal_overlap=True,
    )

    ground_w = cfg.printable_w_mm / 1000.0 * cfg.scale
    ground_h = cfg.image_h_mm / 1000.0 * cfg.scale
    w_px = int((ground_w * 3 + 200) / pixel_size_m)
    h_px = int((ground_h * 2 + 200) / pixel_size_m)
    mosaic = _make_georef_mosaic(w_px, h_px, pixel_size_m=pixel_size_m)
    pages = compute_pages_at_scale(mosaic, cfg)

    row0 = sorted([p for p in pages if p.row == 0], key=lambda p: p.col)
    col0 = sorted([p for p in pages if p.col == 0], key=lambda p: p.row)

    assert row0[0].src_x == 0
    assert row0[-1].src_x + row0[-1].src_w == mosaic.width
    assert col0[0].src_y == 0
    assert col0[-1].src_y + col0[-1].src_h == mosaic.height

    min_overlap_src = int(round(overlap_mm / 1000.0 * cfg.scale / pixel_size_m))
    overlaps_x = [
        row0[i].src_w - (row0[i + 1].src_x - row0[i].src_x)
        for i in range(len(row0) - 1)
    ]
    overlaps_y = [
        col0[i].src_h - (col0[i + 1].src_y - col0[i].src_y)
        for i in range(len(col0) - 1)
    ]
    assert all(ov >= min_overlap_src for ov in overlaps_x)
    assert all(ov >= min_overlap_src for ov in overlaps_y)
