"""
main_window.py — Main application window.

Layout:
  ┌─────────────────────────────────────────────────────────────────┐
  │  Toolbar / Menu                                                  │
  ├────────────────────────┬────────────────────────────────────────┤
  │  Preview (drag&drop)   │  Settings panel                        │
  │                        │  (DPI, orientation, output dir, btn)   │
  ├────────────────────────┴────────────────────────────────────────┤
  │  Tab: Batch | Log                                                │
  └─────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, List

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QAction, QDragEnterEvent, QDropEvent, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.batch_processor import BatchJob, BatchProcessor
from ..core.license import LicenseManager
from ..core.mosaic import Mosaic
from ..core.scanner import scan_directory, ScanResult
from ..utils.helpers import map_region_between_sizes
from .batch_panel import BatchPanel
from .log_widget import LogWidget
from .preview_widget import PreviewWidget
from .settings_panel import SettingsPanel


# ---------------------------------------------------------------------------
# Worker thread for single-folder conversion
# ---------------------------------------------------------------------------

class _ConvertWorker(QObject):
    progress = pyqtSignal(int, int, str)   # current, total, message
    finished = pyqtSignal(str)             # output path
    error = pyqtSignal(str)

    def __init__(
        self,
        mosaic: Mosaic,
        output_name: str,
        default_output_dir: Path,
        settings_panel: SettingsPanel,
    ) -> None:
        super().__init__()
        self._mosaic = mosaic
        self._output_name = output_name
        self._default_output_dir = default_output_dir
        self._sp = settings_panel

    def run(self) -> None:
        from ..core.pdf_converter import PDFConfig, convert_to_pdf

        try:
            out_dir = self._sp.output_dir
            if not str(out_dir).strip() or str(out_dir) == ".":
                out_dir = self._default_output_dir
            out_path = out_dir / f"{self._output_name}.pdf"

            cfg = PDFConfig(
                dpi=self._sp.dpi,
                orientation=self._sp.orientation,
                margin_mm=self._sp.margin_mm,
                overlap_mm=self._sp.overlap_mm,
                optimal_overlap=self._sp.optimal_overlap,
                output_path=out_path,
                atlas_title=self._sp.atlas_title,
            )

            def cb(cur: int, total: int, msg: str) -> None:
                self.progress.emit(cur, total, msg)

            output = convert_to_pdf(self._mosaic, cfg, progress_callback=cb)
            self.finished.emit(str(output))

        except Exception as exc:
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._current_folder: Optional[Path] = None
        self._current_folders: list[Path] = []
        self._selected_region: Optional[tuple[int, int, int, int]] = None
        self._current_mosaic: Optional[Mosaic] = None
        self._license = LicenseManager()
        self._processor = BatchProcessor(max_workers=2, license_manager=self._license)
        self._worker: Optional[_ConvertWorker] = None
        self._thread: Optional[QThread] = None

        self._build_ui()
        self._build_menu()
        self.setAcceptDrops(True)
        self.setWindowTitle("GEOIMAGE NOGDAL — IGN SCAN25 → PDF")
        self.resize(1200, 800)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(8)

        # Top splitter: preview | settings
        top_splitter = QSplitter(Qt.Orientation.Horizontal)

        self._preview = PreviewWidget()
        self._preview.selection_changed.connect(self._on_preview_selection_changed)
        self._preview.setMinimumWidth(300)
        top_splitter.addWidget(self._preview)

        self._settings = SettingsPanel(self._license)
        self._settings.setFixedWidth(280)
        self._settings.convert_requested.connect(self._on_convert)
        self._settings.activate_license_requested.connect(self._on_activate_license)
        top_splitter.addWidget(self._settings)

        top_splitter.setStretchFactor(0, 3)
        top_splitter.setStretchFactor(1, 1)
        root_layout.addWidget(top_splitter, stretch=3)

        # Bottom tabs: batch | log
        self._tabs = QTabWidget()

        self._batch_panel = BatchPanel(self._processor)
        self._tabs.addTab(self._batch_panel, "Traitement en lot")

        self._log = LogWidget()
        self._tabs.addTab(self._log, "Journal")
        self._batch_panel.log_message.connect(self._log.log)

        root_layout.addWidget(self._tabs, stretch=2)

        # Status bar + progress
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._progress = QProgressBar()
        self._progress.setFixedWidth(200)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        self._status_bar.addPermanentWidget(self._progress)
        self._status_bar.showMessage("Prêt")

    def _build_menu(self) -> None:
        mb = self.menuBar()

        file_menu = mb.addMenu("Fichier")
        open_act = QAction("Ouvrir dossier…", self)
        open_act.setShortcut("Ctrl+O")
        open_act.triggered.connect(self._browse_folder)
        file_menu.addAction(open_act)
        file_menu.addSeparator()
        quit_act = QAction("Quitter", self)
        quit_act.setShortcut("Ctrl+Q")
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        help_menu = mb.addMenu("Aide")
        about_act = QAction("À propos…", self)
        about_act.triggered.connect(self._show_about)
        help_menu.addAction(about_act)

        lic_menu = mb.addMenu("Licence")
        act = QAction("Activer une licence…", self)
        act.triggered.connect(self._on_activate_license)
        lic_menu.addAction(act)
        info_act = QAction("Informations machine", self)
        info_act.triggered.connect(self._show_machine_id)
        lic_menu.addAction(info_act)

    # ------------------------------------------------------------------
    # Drag & Drop
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        folders = [Path(u.toLocalFile()) for u in urls if Path(u.toLocalFile()).is_dir()]
        files = [Path(u.toLocalFile()) for u in urls if Path(u.toLocalFile()).is_file()]

        if folders:
            self._load_folders(folders)
            for extra in folders[1:]:
                self._batch_panel.add_folder(extra)
        elif files:
            self._log.info(f"Fichier déposé : {files[0]} (déposez un dossier)")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Ouvrir dossier IGN")
        if folder:
            self._load_folder(Path(folder))

    def _load_folder(self, folder: Path) -> None:
        self._load_folders([folder])

    def _load_folders(self, folders: List[Path]) -> None:
        if not folders:
            return

        self._current_folders = folders
        self._current_folder = folders[0]
        self._selected_region = None
        if len(folders) == 1:
            self._status_bar.showMessage(f"Chargement : {folders[0]}")
            self._log.info(f"Ouverture du dossier : {folders[0]}")
        else:
            self._status_bar.showMessage(f"Chargement : {len(folders)} dossiers")
            self._log.info(f"Ouverture de {len(folders)} dossiers pour aperçu mosaïque unifié.")
        self._preview.clear()
        self._settings.set_convert_enabled(False)
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)
        self._progress.setValue(0)

        # Update default output dir
        # (don't override if user already set one)

        # Scan in background
        self._scan_thread = QThread()
        self._scan_worker = _ScanWorker(folders)
        self._scan_worker.moveToThread(self._scan_thread)
        self._scan_thread.started.connect(self._scan_worker.run)
        self._scan_worker.progress.connect(self._on_scan_progress)
        self._scan_worker.finished.connect(self._on_scan_done)
        self._scan_worker.error.connect(self._on_scan_error)
        self._scan_worker.finished.connect(self._scan_thread.quit)
        self._scan_worker.error.connect(self._scan_thread.quit)
        self._scan_thread.start()

    def _on_scan_progress(self, cur: int, total: int, msg: str) -> None:
        if total <= 0:
            self._progress.setRange(0, 0)
        else:
            self._progress.setRange(0, total)
            self._progress.setValue(cur)
        self._status_bar.showMessage(msg)

    def _on_scan_done(self, result) -> None:
        self._log.success(
            f"Scan terminé : {result.total_files} fichier(s) raster"
            + (f", {len(result.vrt_files)} VRT" if result.has_vrt else "")
        )
        if len(result.vrt_files) > 1:
            self._log.info("Plusieurs VRT détectés — aperçu construit depuis les tuiles raster fusionnées.")
        self._status_bar.showMessage(f"{result.total_files} fichier(s) trouvé(s)")
        self._settings.set_convert_enabled(result.total_files > 0)

        # Build mosaic & thumbnail in background
        self._progress.setRange(0, 0)
        self._progress.setValue(0)
        self._status_bar.showMessage("Construction de la mosaïque preview...")
        self._thumb_thread = QThread()
        self._thumb_worker = _ThumbnailWorker(result)
        self._thumb_worker.moveToThread(self._thumb_thread)
        self._thumb_thread.started.connect(self._thumb_worker.run)
        self._thumb_worker.progress.connect(self._on_thumb_progress)
        self._thumb_worker.finished.connect(self._on_thumb_done)
        self._thumb_worker.finished.connect(self._thumb_thread.quit)
        self._thumb_thread.start()

    def _on_scan_error(self, msg: str) -> None:
        self._log.error(f"Erreur scan : {msg}")
        self._status_bar.showMessage("Erreur")
        self._progress.setVisible(False)

    def _on_thumb_progress(self, cur: int, total: int, msg: str) -> None:
        if total <= 0:
            self._progress.setRange(0, 0)
        else:
            self._progress.setRange(0, total)
            self._progress.setValue(cur)
        self._status_bar.showMessage(msg)

    def _on_thumb_done(self, data) -> None:
        mosaic, thumb_img = data
        self._current_mosaic = mosaic
        if thumb_img is not None:
            self._preview.set_pil_image(thumb_img)
            self._log.info(
                f"Mosaïque : {mosaic.width}×{mosaic.height} px, "
                f"{len(mosaic.layout.tiles)} tuile(s)"
            )
            self._status_bar.showMessage("Aperçu prêt")
        else:
            self._status_bar.showMessage("Aperçu indisponible")
        self._progress.setVisible(False)

    def _on_convert(self) -> None:
        if not self._license.can_export:
            QMessageBox.warning(
                self, "Démo épuisée",
                "Le mode démo est épuisé.\nActivez une licence pour continuer.",
            )
            return

        if self._current_mosaic is None:
            QMessageBox.information(self, "Aucune mosaïque", "Ouvrez d'abord un dossier IGN.")
            return

        if self._thread and self._thread.isRunning():
            QMessageBox.information(self, "En cours", "Une conversion est déjà en cours.")
            return

        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._settings.set_convert_enabled(False)
        self._log.info("Démarrage de la conversion…")

        mosaic_to_convert = self._current_mosaic
        if self._selected_region is not None:
            try:
                x, y, w, h = self._selected_region
                mosaic_to_convert = self._current_mosaic.cropped(x, y, w, h)
                self._log.info(
                    f"Zone sélectionnée appliquée : x={x}, y={y}, largeur={w}, hauteur={h}"
                )
            except Exception as exc:
                self._progress.setVisible(False)
                self._settings.set_convert_enabled(True)
                QMessageBox.critical(self, "Zone invalide", str(exc))
                return

        if len(self._current_folders) == 1 and self._current_folder is not None:
            output_name = self._current_folder.name
            default_output_dir = self._current_folder
        elif self._current_folders:
            output_name = f"{self._current_folders[0].name}_merged"
            default_output_dir = self._current_folders[0]
        else:
            output_name = "export"
            default_output_dir = Path(".")

        self._thread = QThread()
        self._worker = _ConvertWorker(mosaic_to_convert, output_name, default_output_dir, self._settings)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_convert_progress)
        self._worker.finished.connect(self._on_convert_done)
        self._worker.error.connect(self._on_convert_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.finished.connect(self._on_thread_done)
        self._thread.start()

    def _on_convert_progress(self, cur: int, total: int, msg: str) -> None:
        pct = int(cur / max(total, 1) * 100)
        self._progress.setValue(pct)
        self._status_bar.showMessage(msg)
        self._log.debug(msg)

    def _on_convert_done(self, output_path: str) -> None:
        self._license.record_export()
        self._settings.refresh_license()
        self._log.success(f"PDF généré : {output_path}")
        self._status_bar.showMessage(f"PDF créé : {output_path}")
        QMessageBox.information(self, "Succès", f"PDF généré :\n{output_path}")

    def _on_convert_error(self, msg: str) -> None:
        self._log.error(f"Erreur : {msg}")
        self._status_bar.showMessage("Erreur de conversion")
        QMessageBox.critical(self, "Erreur", msg)

    def _on_thread_done(self) -> None:
        self._progress.setVisible(False)
        self._settings.set_convert_enabled(True)

    def _on_preview_selection_changed(self, region: Optional[tuple[int, int, int, int]]) -> None:
        mapped = region
        if (
            region is not None
            and self._current_mosaic is not None
            and self._current_mosaic.width > 0
            and self._current_mosaic.height > 0
        ):
            preview_size = self._preview.get_image_size()
            if preview_size is not None and preview_size[0] > 0 and preview_size[1] > 0:
                mapped = map_region_between_sizes(
                    region,
                    preview_size,
                    (self._current_mosaic.width, self._current_mosaic.height),
                )

        self._selected_region = mapped
        if region is None:
            self._status_bar.showMessage("Aucune zone sélectionnée (conversion complète)")
        else:
            x, y, w, h = self._selected_region
            self._status_bar.showMessage(f"Zone sélectionnée : x={x}, y={y}, {w}×{h} px")

    def _on_activate_license(self) -> None:
        key, ok = QInputDialog.getText(
            self, "Activer la licence",
            "Entrez votre clé de licence :",
        )
        if ok and key:
            if self._license.activate(key):
                self._settings.refresh_license()
                QMessageBox.information(self, "Succès", "Licence activée avec succès !")
            else:
                QMessageBox.warning(self, "Erreur", "Clé invalide pour cette machine.")

    def _show_machine_id(self) -> None:
        mid = self._license.machine_id
        QMessageBox.information(
            self,
            "Identifiant machine",
            f"Identifiant machine :\n\n{mid}\n\n"
            "Transmettez cet identifiant pour obtenir votre clé de licence.",
        )

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "À propos",
            "<b>GEOIMAGE NOGDAL</b> v1.0.0<br>"
            "Conversion raster IGN SCAN25 → PDF A4<br>"
            "Sans dépendance GDAL<br><br>"
            "Stack : Python · PyQt6 · Pillow · ReportLab · glymur · tifffile",
        )


# ---------------------------------------------------------------------------
# Scan worker
# ---------------------------------------------------------------------------

class _ScanWorker(QObject):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)
    progress = pyqtSignal(int, int, str)

    def __init__(self, folders: List[Path]) -> None:
        super().__init__()
        self._folders = folders

    def run(self) -> None:
        try:
            if not self._folders:
                self.error.emit("Aucun dossier à scanner.")
                return

            total = len(self._folders)
            all_results = []
            self.progress.emit(0, total, "Scan des dossiers en cours...")
            for i, folder in enumerate(self._folders, start=1):
                all_results.append(scan_directory(folder))
                self.progress.emit(i, total, f"Scan du dossier {i}/{total} : {folder.name}")

            merged = ScanResult(root_dir=self._folders[0])
            for res in all_results:
                merged.raster_files.extend(res.raster_files)
                merged.vrt_files.extend(res.vrt_files)
                merged.tab_files.extend(res.tab_files)
                merged.errors.extend(res.errors)
            # Keep deterministic map-like order: highest Lambert Y first (north→south),
            # then Lambert X (west→east).
            merged.raster_files.sort(
                key=lambda f: (
                    -(f.grid_y or 0),
                    f.grid_x or 0,
                    str(f.path),
                )
            )
            self.finished.emit(merged)
        except Exception as exc:
            self.error.emit(str(exc))


class _ThumbnailWorker(QObject):
    finished = pyqtSignal(object)  # (Mosaic, PIL.Image or None)
    progress = pyqtSignal(int, int, str)

    def __init__(self, scan_result) -> None:
        super().__init__()
        self._result = scan_result

    def run(self) -> None:
        try:
            mosaic: Optional[Mosaic] = None
            result = self._result

            self.progress.emit(0, 0, "Construction de la mosaïque preview...")
            if result.has_vrt and len(result.vrt_files) == 1:
                mosaic = Mosaic.from_vrt(result.vrt_files[0])

            if mosaic is None:
                tile_paths = [f.path for f in result.raster_files]
                if not tile_paths:
                    self.finished.emit((None, None))
                    return
                mosaic = Mosaic.from_files(tile_paths)

            try:
                def thumbnail_progress_callback(cur: int, total: int) -> None:
                    self.progress.emit(cur, total, f"Chargement aperçu carte : {cur}/{total}")

                thumb = mosaic.get_thumbnail((600, 600), progress_callback=thumbnail_progress_callback)
            except Exception:
                thumb = None

            self.finished.emit((mosaic, thumb))
        except Exception:
            self.finished.emit((None, None))
