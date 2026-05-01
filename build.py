"""
build.py — PyInstaller build script for GEOIMAGE NOGDAL.

Usage:
    python build.py

Produces: dist/GEOIMAGE_NOGDAL.exe (Windows) or dist/GEOIMAGE_NOGDAL (Linux/macOS)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent


def build() -> None:
    icon = ROOT / "assets" / "icon.ico"
    icon_flag = ["--icon", str(icon)] if icon.exists() else []

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",            # No console window on Windows
        "--name", "GEOIMAGE_NOGDAL",
        "--add-data", f"{ROOT / 'assets'}{os.pathsep}assets",
        "--hidden-import", "PIL._tkinter_finder",
        "--hidden-import", "glymur",
        "--hidden-import", "tifffile",
        "--hidden-import", "reportlab",
        "--hidden-import", "lxml",
        "--hidden-import", "lxml.etree",
        *icon_flag,
        str(ROOT / "main.py"),
    ]

    print("Running PyInstaller…")
    print(" ".join(cmd))
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        print("Build FAILED", file=sys.stderr)
        sys.exit(result.returncode)
    else:
        print("\nBuild successful!")
        dist = ROOT / "dist" / "GEOIMAGE_NOGDAL"
        if sys.platform == "win32":
            dist = dist.with_suffix(".exe")
        print(f"Executable: {dist}")


if __name__ == "__main__":
    build()
