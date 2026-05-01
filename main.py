"""
main.py — Application entry point.

Usage:
    python main.py                     # GUI mode
    python main.py --cli --input DIR   # CLI mode
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def run_gui() -> None:
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QPalette, QColor
    from PyQt6.QtCore import Qt

    app = QApplication(sys.argv)
    app.setApplicationName("GEOIMAGE NOGDAL")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("GEOIMAGE")

    # Dark theme
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(45, 45, 45))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(212, 212, 212))
    palette.setColor(QPalette.ColorRole.Base, QColor(30, 30, 30))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 220))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(0, 0, 0))
    palette.setColor(QPalette.ColorRole.Text, QColor(212, 212, 212))
    palette.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(212, 212, 212))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))
    palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    app.setPalette(palette)

    from src.ui.main_window import MainWindow
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


def run_cli(args: argparse.Namespace) -> int:
    """CLI mode — convert a folder to PDF without GUI."""
    from src.core.scanner import scan_directory
    from src.core.mosaic import Mosaic
    from src.core.pdf_converter import PDFConfig, Orientation, convert_to_pdf
    from src.core.license import LicenseManager

    lic = LicenseManager()
    if not lic.can_export:
        print("ERROR: Mode démo épuisé. Activez une licence.", file=sys.stderr)
        return 1

    input_dir = Path(args.input)
    output_dir = Path(args.output) if args.output else input_dir

    print(f"Scan : {input_dir}")
    result = scan_directory(input_dir)
    if result.total_files == 0:
        print("ERROR: Aucun fichier raster trouvé.", file=sys.stderr)
        return 1

    print(f"{result.total_files} fichier(s) trouvé(s)")

    mosaic = None
    if result.has_vrt:
        mosaic = Mosaic.from_vrt(result.vrt_files[0])
        print(f"VRT chargé : {result.vrt_files[0].name}")

    if mosaic is None:
        mosaic = Mosaic.from_files([f.path for f in result.raster_files])
        print("Mosaïque reconstruite depuis les noms de fichiers")

    print(f"Mosaïque : {mosaic.width}×{mosaic.height} px")

    orientation = Orientation.LANDSCAPE if args.landscape else Orientation.PORTRAIT
    out_path = output_dir / f"{input_dir.name}.pdf"

    cfg = PDFConfig(
        dpi=args.dpi,
        orientation=orientation,
        margin_mm=args.margin,
        output_path=out_path,
    )

    def cb(cur: int, total: int, msg: str) -> None:
        print(f"  [{cur}/{total}] {msg}")

    print(f"Génération PDF → {out_path}")
    output = convert_to_pdf(mosaic, cfg, progress_callback=cb)
    lic.record_export()
    print(f"PDF créé : {output}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="GEOIMAGE NOGDAL — IGN SCAN25 to PDF converter"
    )
    parser.add_argument("--cli", action="store_true", help="Run in CLI mode (no GUI)")
    parser.add_argument("--input", "-i", help="Input directory (CLI mode)")
    parser.add_argument("--output", "-o", help="Output directory (CLI mode)")
    parser.add_argument("--dpi", type=int, default=300, help="DPI (default: 300)")
    parser.add_argument("--landscape", action="store_true", help="Landscape orientation")
    parser.add_argument("--margin", type=float, default=10.0, help="Margin in mm")

    args = parser.parse_args()

    if args.cli:
        if not args.input:
            parser.error("--input is required in CLI mode")
        sys.exit(run_cli(args))
    else:
        run_gui()


if __name__ == "__main__":
    main()
