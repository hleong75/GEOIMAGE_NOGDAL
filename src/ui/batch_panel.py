"""
batch_panel.py — Batch job queue UI.

Shows a table of BatchJob items with status, progress bar, and controls.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QThread, QObject
from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.batch_processor import BatchJob, BatchProcessor, JobStatus
from ..core.pdf_converter import Orientation, PDFConfig, convert_folders_to_pdf


# ---------------------------------------------------------------------------
# Worker for merged-PDF mode
# ---------------------------------------------------------------------------

class _MergeWorker(QObject):
    """Scans all job folders, builds mosaics, and generates a single merged PDF."""

    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        jobs: List[BatchJob],
        output_path: Path,
        cfg: PDFConfig,
        assembly_mode: bool = False,
    ) -> None:
        super().__init__()
        self._jobs = jobs
        self._output_path = output_path
        self._cfg = cfg
        self._assembly_mode = assembly_mode

    def run(self) -> None:
        from ..core.scanner import scan_directory
        from ..core.mosaic import Mosaic

        try:
            if self._assembly_mode:
                self._run_assembly(scan_directory, Mosaic)
            else:
                self._run_sectioned(scan_directory, Mosaic)
        except Exception as exc:
            self.error.emit(str(exc))

    def _run_sectioned(self, scan_directory, Mosaic) -> None:
        """Original mode: one PDF section per folder."""
        folder_mosaics: List[Tuple[str, Mosaic]] = []

        for i, job in enumerate(self._jobs):
            self.progress.emit(i, len(self._jobs), f"Scan : {job.input_dir.name}")
            result = scan_directory(job.input_dir)
            if result.total_files == 0:
                continue

            mosaic: Optional[Mosaic] = None
            if result.has_vrt:
                mosaic = Mosaic.from_vrt(result.vrt_files[0])
            if mosaic is None:
                mosaic = Mosaic.from_files([f.path for f in result.raster_files])

            if mosaic.width > 0 and mosaic.height > 0:
                folder_mosaics.append((job.input_dir.name, mosaic))

        if not folder_mosaics:
            self.error.emit("Aucun fichier raster trouvé dans les dossiers sélectionnés.")
            return

        total_pre = len(folder_mosaics)
        self.progress.emit(0, total_pre, f"Génération PDF fusionné ({total_pre} dossier(s))…")

        self._cfg.output_path = self._output_path

        def page_cb(cur: int, total: int, msg: str) -> None:
            self.progress.emit(cur, total, msg)

        output = convert_folders_to_pdf(folder_mosaics, self._cfg, progress_callback=page_cb)
        self.finished.emit(str(output))

    def _run_assembly(self, scan_directory, Mosaic) -> None:
        """Assembly mode: combine all images from all folders into one unified mosaic."""
        from ..core.pdf_converter import convert_to_pdf

        all_paths = []
        n_jobs = len(self._jobs)

        for i, job in enumerate(self._jobs):
            self.progress.emit(i, n_jobs, f"Scan : {job.input_dir.name}")
            result = scan_directory(job.input_dir)
            all_paths.extend(f.path for f in result.raster_files)

        if not all_paths:
            self.error.emit("Aucun fichier raster trouvé dans les dossiers sélectionnés.")
            return

        self.progress.emit(n_jobs, n_jobs, f"Construction de la mosaïque unifiée ({len(all_paths)} tuile(s))…")
        mosaic = Mosaic.from_files(all_paths)

        if mosaic.width == 0 or mosaic.height == 0:
            self.error.emit("La mosaïque assemblée est vide — vérifiez les données source.")
            return

        self._cfg.output_path = self._output_path

        def page_cb(cur: int, total: int, msg: str) -> None:
            self.progress.emit(cur, total, msg)

        output = convert_to_pdf(mosaic, self._cfg, progress_callback=page_cb)
        self.finished.emit(str(output))


class BatchPanel(QWidget):
    """UI panel for managing and running batch jobs."""

    log_message = pyqtSignal(str, str)  # (message, level)

    def __init__(self, processor: BatchProcessor, parent=None) -> None:
        super().__init__(parent)
        self._processor = processor
        self._merge_thread: Optional[QThread] = None
        self._merge_worker: Optional[_MergeWorker] = None
        self._build_ui()
        # Refresh display periodically
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._refresh_table)
        self._timer.start()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Toolbar
        tb = QHBoxLayout()
        add_btn = QPushButton("+ Ajouter dossier(s)")
        add_btn.clicked.connect(self._add_folders)
        clear_btn = QPushButton("Effacer liste")
        clear_btn.clicked.connect(self._clear)
        self._start_btn = QPushButton("▶ Lancer tout")
        self._start_btn.setStyleSheet(
            "QPushButton { background:#27ae60; color:white; font-weight:bold; border-radius:4px; }"
            "QPushButton:hover { background:#2ecc71; }"
        )
        self._start_btn.clicked.connect(self._start)
        cancel_btn = QPushButton("⏹ Annuler")
        cancel_btn.clicked.connect(self._cancel)

        for w in (add_btn, clear_btn, self._start_btn, cancel_btn):
            tb.addWidget(w)
        self._all_resources_check = QCheckBox("Moteur multitâche (toutes ressources)")
        self._all_resources_check.setToolTip(
            "Utilise automatiquement tous les cœurs CPU disponibles."
        )
        self._all_resources_check.toggled.connect(self._on_all_resources_toggled)
        tb.addWidget(self._all_resources_check)
        tb.addWidget(QLabel("Workers :"))
        self._workers_spin = QSpinBox()
        self._workers_spin.setRange(1, max(1, os.cpu_count() or 1))
        self._workers_spin.setValue(self._processor.max_workers)
        self._workers_spin.valueChanged.connect(self._on_workers_changed)
        tb.addWidget(self._workers_spin)
        tb.addStretch()
        layout.addLayout(tb)

        # Merge option row
        merge_row = QHBoxLayout()
        self._merge_check = QCheckBox("Fusionner en un seul PDF")
        self._merge_check.setToolTip(
            "Combine tous les dossiers en un seul fichier PDF (une section par dossier)"
        )
        self._merge_check.toggled.connect(self._on_merge_toggled)
        merge_row.addWidget(self._merge_check)

        self._merge_path_edit = QLineEdit()
        self._merge_path_edit.setPlaceholderText("Chemin du PDF fusionné…")
        self._merge_path_edit.setEnabled(False)
        merge_row.addWidget(self._merge_path_edit, stretch=1)

        self._merge_browse_btn = QPushButton("…")
        self._merge_browse_btn.setFixedWidth(32)
        self._merge_browse_btn.setEnabled(False)
        self._merge_browse_btn.clicked.connect(self._browse_merge_output)
        merge_row.addWidget(self._merge_browse_btn)
        layout.addLayout(merge_row)

        # Assembly option row (sub-option of merge)
        assemble_row = QHBoxLayout()
        assemble_row.addSpacing(20)
        self._assemble_check = QCheckBox("Assembler les images en une mosaïque unifiée")
        self._assemble_check.setToolTip(
            "Combine toutes les tuiles de tous les dossiers en une seule mosaïque géoréférencée\n"
            "avant de générer le PDF (ignore les sections par dossier)"
        )
        self._assemble_check.setEnabled(False)
        assemble_row.addWidget(self._assemble_check)
        assemble_row.addStretch()
        layout.addLayout(assemble_row)

        # Merge progress bar (hidden until merge mode active)
        self._merge_progress = QProgressBar()
        self._merge_progress.setRange(0, 100)
        self._merge_progress.setValue(0)
        self._merge_progress.setVisible(False)
        layout.addWidget(self._merge_progress)

        # Table
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["Dossier", "DPI", "Orientation", "Statut", "Progression"]
        )
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._table)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_merge_toggled(self, checked: bool) -> None:
        self._merge_path_edit.setEnabled(checked)
        self._merge_browse_btn.setEnabled(checked)
        self._assemble_check.setEnabled(checked)
        if not checked:
            self._assemble_check.setChecked(False)

    def _browse_merge_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Enregistrer le PDF fusionné", "", "PDF (*.pdf)"
        )
        if path:
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
            self._merge_path_edit.setText(path)

    def _add_folders(self) -> None:
        folders = QFileDialog.getExistingDirectory(
            self, "Ajouter un dossier source"
        )
        if not folders:
            return
        # QFileDialog returns a single folder; for multi-select we use native dialog workaround
        self._add_job_for_folder(Path(folders))

    def _add_job_for_folder(self, folder: Path) -> None:
        from ..core.pdf_converter import Orientation as Ori
        job = BatchJob(
            input_dir=folder,
            output_dir=folder,
        )
        job.on_progress = self._on_job_progress
        job.on_done = self._on_job_done
        self._processor.add_job(job)
        self._refresh_table()

    def add_folder(self, folder: Path) -> None:
        """Public API — add a folder from drag & drop."""
        self._add_job_for_folder(folder)

    def _clear(self) -> None:
        self._processor.clear_jobs()
        self._refresh_table()

    def _start(self) -> None:
        if self._merge_check.isChecked():
            self._start_merge()
        else:
            self._start_btn.setEnabled(False)
            self._processor.start()

    def _on_all_resources_toggled(self, enabled: bool) -> None:
        self._workers_spin.setEnabled(not enabled)
        if enabled:
            self._processor.set_max_workers(self._processor.available_workers())
        else:
            self._processor.set_max_workers(self._workers_spin.value())

    def _on_workers_changed(self, value: int) -> None:
        if not self._all_resources_check.isChecked():
            self._processor.set_max_workers(value)

    def _start_merge(self) -> None:
        jobs = self._processor.get_jobs()
        if not jobs:
            self.log_message.emit("Aucun dossier dans la liste.", "ERROR")
            return

        output_path_str = self._merge_path_edit.text().strip()
        if not output_path_str:
            self.log_message.emit(
                "Spécifiez le chemin du PDF fusionné (champ à droite de la case à cocher).",
                "ERROR",
            )
            return

        output_path = Path(output_path_str)
        if not output_path.suffix.lower() == ".pdf":
            output_path = output_path.with_suffix(".pdf")

        # Build a PDFConfig using the settings from the first job as defaults
        first_job = jobs[0]
        cfg = PDFConfig(
            dpi=first_job.dpi,
            orientation=first_job.orientation,
            margin_mm=first_job.margin_mm,
            overlap_mm=first_job.overlap_mm,
            output_path=output_path,
        )

        self._start_btn.setEnabled(False)
        self._merge_progress.setVisible(True)
        self._merge_progress.setValue(0)

        assembly_mode = self._assemble_check.isChecked()

        self._merge_thread = QThread()
        self._merge_worker = _MergeWorker(list(jobs), output_path, cfg, assembly_mode=assembly_mode)
        self._merge_worker.moveToThread(self._merge_thread)
        self._merge_thread.started.connect(self._merge_worker.run)
        self._merge_worker.progress.connect(self._on_merge_progress)
        self._merge_worker.finished.connect(self._on_merge_done)
        self._merge_worker.error.connect(self._on_merge_error)
        self._merge_worker.finished.connect(self._merge_thread.quit)
        self._merge_worker.error.connect(self._merge_thread.quit)
        self._merge_thread.finished.connect(self._on_merge_thread_done)
        self._merge_thread.start()

    def _on_merge_progress(self, cur: int, total: int, msg: str) -> None:
        pct = int(cur / max(total, 1) * 100)
        self._merge_progress.setValue(pct)
        self.log_message.emit(msg, "INFO")

    def _on_merge_done(self, output_path: str) -> None:
        self.log_message.emit(f"PDF fusionné généré : {output_path}", "SUCCESS")

    def _on_merge_error(self, msg: str) -> None:
        self.log_message.emit(f"Erreur fusion PDF : {msg}", "ERROR")

    def _on_merge_thread_done(self) -> None:
        self._merge_progress.setVisible(False)
        self._start_btn.setEnabled(True)

    def _cancel(self) -> None:
        self._processor.cancel_pending()
        self._start_btn.setEnabled(True)

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def _refresh_table(self) -> None:
        jobs = self._processor.get_jobs()
        self._table.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            self._table.setItem(row, 0, QTableWidgetItem(str(job.input_dir)))
            self._table.setItem(row, 1, QTableWidgetItem(str(job.dpi)))
            self._table.setItem(row, 2, QTableWidgetItem(job.orientation.value))
            self._table.setItem(row, 3, QTableWidgetItem(job.status.value))

            bar = self._table.cellWidget(row, 4)
            if not isinstance(bar, QProgressBar):
                bar = QProgressBar()
                bar.setRange(0, 100)
                self._table.setCellWidget(row, 4, bar)
            bar.setValue(int(job.progress * 100))

        # Re-enable start if all jobs done
        all_done = all(
            j.status in (JobStatus.DONE, JobStatus.ERROR, JobStatus.SKIPPED)
            for j in jobs
        )
        if jobs and all_done:
            self._start_btn.setEnabled(True)

    def _on_job_progress(self, job: BatchJob) -> None:
        self.log_message.emit(job.message, "INFO")

    def _on_job_done(self, job: BatchJob) -> None:
        level = "SUCCESS" if job.status == JobStatus.DONE else "ERROR"
        self.log_message.emit(job.message, level)
