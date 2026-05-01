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
