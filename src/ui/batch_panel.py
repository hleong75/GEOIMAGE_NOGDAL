"""
batch_panel.py — Batch job queue UI.

Shows a table of BatchJob items with status, progress bar, and controls.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.batch_processor import BatchJob, BatchProcessor, JobStatus
from ..core.pdf_converter import Orientation


class BatchPanel(QWidget):
    """UI panel for managing and running batch jobs."""

    log_message = pyqtSignal(str, str)  # (message, level)

    def __init__(self, processor: BatchProcessor, parent=None) -> None:
        super().__init__(parent)
        self._processor = processor
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
        tb.addStretch()
        layout.addLayout(tb)

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
        self._start_btn.setEnabled(False)
        self._processor.start()

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
