"""
scanner.py — Recursive file scanner for IGN raster data.

Supported formats: .jp2, .tif/.tiff, .ecw, .jpeg/.jpg, .png
Ignored extensions: .md5, .pdf (unless final output), .prj, .aux, etc.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

# Extensions that contain raster image data
RASTER_EXTENSIONS = {".jp2", ".tif", ".tiff", ".ecw", ".jpeg", ".jpg", ".png"}

# Extensions to ignore completely
IGNORED_EXTENSIONS = {
    ".md5", ".pdf", ".prj", ".ovr", ".aux", ".xml",
    ".dbf", ".shp", ".shx", ".cpg", ".atx",
}

# IGN SCAN25 tile name pattern: SC25_TOUR_XXXX_YYYY_L93_E100
_IGN_TILE_RE = re.compile(
    r"SC25[_\-].*?[_\-](\d{4})[_\-](\d{4})[_\-]",
    re.IGNORECASE,
)


@dataclass
class RasterFile:
    """Represents a single raster file discovered during scanning."""

    path: Path
    extension: str
    stem: str
    # Lambert 93 grid coords extracted from filename (may be None)
    grid_x: Optional[int] = None
    grid_y: Optional[int] = None
    # Associated metadata files
    tab_file: Optional[Path] = None
    vrt_file: Optional[Path] = None

    @property
    def size_bytes(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def __repr__(self) -> str:
        coords = f" [{self.grid_x},{self.grid_y}]" if self.grid_x is not None else ""
        return f"RasterFile({self.path.name}{coords})"


@dataclass
class ScanResult:
    """Result of a directory scan."""

    root_dir: Path
    raster_files: List[RasterFile] = field(default_factory=list)
    vrt_files: List[Path] = field(default_factory=list)
    tab_files: List[Path] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def total_files(self) -> int:
        return len(self.raster_files)

    @property
    def has_vrt(self) -> bool:
        return len(self.vrt_files) > 0

    def get_grid_bounds(self) -> Optional[Tuple[int, int, int, int]]:
        """Return (min_x, min_y, max_x, max_y) of grid coordinates, or None."""
        coords = [
            (f.grid_x, f.grid_y)
            for f in self.raster_files
            if f.grid_x is not None and f.grid_y is not None
        ]
        if not coords:
            return None
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        return min(xs), min(ys), max(xs), max(ys)


def _extract_ign_coords(filename: str) -> Tuple[Optional[int], Optional[int]]:
    """Extract (grid_x, grid_y) from an IGN SCAN25 filename, or (None, None)."""
    m = _IGN_TILE_RE.search(filename)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def scan_directory(root: str | Path, recursive: bool = True) -> ScanResult:
    """
    Scan *root* for raster files and associated metadata.

    Parameters
    ----------
    root:
        Directory to scan.
    recursive:
        If True (default) descend into sub-directories.

    Returns
    -------
    ScanResult
    """
    root = Path(root)
    result = ScanResult(root_dir=root)

    if not root.is_dir():
        result.errors.append(f"Not a directory: {root}")
        return result

    # Build quick lookup for .tab and .vrt files by stem
    tab_by_stem: dict[str, Path] = {}
    vrt_paths: list[Path] = []

    walker = os.walk(str(root)) if recursive else [(str(root), [], os.listdir(str(root)))]

    all_paths: list[Path] = []
    for dirpath, _dirnames, filenames in walker:
        for fname in filenames:
            all_paths.append(Path(dirpath) / fname)

    for p in all_paths:
        ext = p.suffix.lower()
        if ext == ".tab":
            tab_by_stem[p.stem.lower()] = p
            result.tab_files.append(p)
        elif ext == ".vrt":
            vrt_paths.append(p)
            result.vrt_files.append(p)

    for p in all_paths:
        ext = p.suffix.lower()
        if ext in IGNORED_EXTENSIONS:
            continue
        if ext not in RASTER_EXTENSIONS:
            continue

        gx, gy = _extract_ign_coords(p.name)
        tab = tab_by_stem.get(p.stem.lower())

        rf = RasterFile(
            path=p,
            extension=ext,
            stem=p.stem,
            grid_x=gx,
            grid_y=gy,
            tab_file=tab,
        )
        result.raster_files.append(rf)

    # Sort by grid coords (y desc = top row first, then x asc = left to right)
    result.raster_files.sort(
        key=lambda f: (
            -(f.grid_y or 0),
            f.grid_x or 0,
            str(f.path),
        )
    )

    return result
