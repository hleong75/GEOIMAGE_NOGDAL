"""
helpers.py — General-purpose utility functions.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def resource_path(relative_path: str) -> Path:
    """
    Get absolute path to a resource, works for both development and PyInstaller bundles.
    """
    if hasattr(sys, "_MEIPASS"):
        # PyInstaller bundle
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).parent.parent.parent  # project root

    return base / relative_path


def human_bytes(n: int) -> str:
    """Format byte count as human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def format_duration(seconds: float) -> str:
    """Format a duration in seconds to a human-readable string."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s"


def map_region_between_sizes(
    region: tuple[int, int, int, int],
    src_size: tuple[int, int],
    dst_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    """Map a rectangular region from source-space pixels to destination-space pixels."""
    sx, sy, sw, sh = region
    src_w, src_h = src_size
    dst_w, dst_h = dst_size

    if src_w <= 0 or src_h <= 0 or dst_w <= 0 or dst_h <= 0:
        raise ValueError("Dimensions invalides pour conversion de zone.")

    scale_x = dst_w / src_w
    scale_y = dst_h / src_h
    x = max(0, min(dst_w - 1, int(round(sx * scale_x))))
    y = max(0, min(dst_h - 1, int(round(sy * scale_y))))
    w = max(1, int(round(sw * scale_x)))
    h = max(1, int(round(sh * scale_y)))
    w = min(w, dst_w - x)
    h = min(h, dst_h - y)
    return x, y, w, h
