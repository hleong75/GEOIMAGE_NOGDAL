"""
batch_processor.py — Multi-folder batch processing with thread pool.

Each BatchJob converts one folder of raster tiles to a PDF.
Jobs are queued and executed in a thread pool (one thread per job by default).
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, List, Optional

from .scanner import scan_directory, ScanResult
from .mosaic import Mosaic, build_mosaic_from_vrt, build_mosaic_from_filenames
from .pdf_converter import PDFConfig, convert_to_pdf, Orientation
from .license import LicenseManager


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass
class BatchJob:
    """A single conversion job."""

    input_dir: Path
    output_dir: Path
    dpi: int = 300
    orientation: Orientation = Orientation.PORTRAIT
    margin_mm: float = 10.0
    overlap_mm: float = 5.0
    scale: int = 25000

    status: JobStatus = JobStatus.PENDING
    progress: float = 0.0          # 0.0 – 1.0
    message: str = ""
    output_pdf: Optional[Path] = None
    error: str = ""

    # Callbacks (set by BatchProcessor)
    on_progress: Optional[Callable[["BatchJob"], None]] = field(default=None, repr=False)
    on_done: Optional[Callable[["BatchJob"], None]] = field(default=None, repr=False)


class BatchProcessor:
    """
    Manages a queue of BatchJob objects and executes them in a thread pool.
    """

    def __init__(
        self,
        max_workers: int = 2,
        license_manager: Optional[LicenseManager] = None,
    ) -> None:
        self.max_workers = self._normalize_workers(max_workers)
        self.license = license_manager or LicenseManager()
        self._jobs: List[BatchJob] = []
        self._lock = threading.Lock()
        self._semaphore = threading.Semaphore(self.max_workers)
        self._threads: list[threading.Thread] = []

    @staticmethod
    def available_workers() -> int:
        """Return the number of available CPU cores for batch workers."""
        return max(1, os.cpu_count() or 1)

    @staticmethod
    def _normalize_workers(max_workers: int) -> int:
        return max(1, int(max_workers))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_job(self, job: BatchJob) -> None:
        with self._lock:
            self._jobs.append(job)

    def clear_jobs(self) -> None:
        with self._lock:
            self._jobs = [j for j in self._jobs if j.status == JobStatus.RUNNING]

    def get_jobs(self) -> List[BatchJob]:
        with self._lock:
            return list(self._jobs)

    def set_max_workers(self, max_workers: int) -> None:
        """Update worker concurrency for future jobs.

        If active worker threads exist, this request is ignored and
        ``max_workers`` remains unchanged to avoid replacing synchronization
        primitives in-flight.
        """
        with self._lock:
            if any(t.is_alive() for t in self._threads):
                return
            self.max_workers = self._normalize_workers(max_workers)
            self._semaphore = threading.Semaphore(self.max_workers)

    def start(self) -> None:
        """Start processing all pending jobs in background threads."""
        with self._lock:
            pending = [j for j in self._jobs if j.status == JobStatus.PENDING]

        for job in pending:
            t = threading.Thread(target=self._run_job, args=(job,), daemon=True)
            self._threads.append(t)
            t.start()

    def wait(self) -> None:
        """Block until all threads finish."""
        for t in self._threads:
            t.join()
        self._threads.clear()

    def cancel_pending(self) -> None:
        with self._lock:
            for job in self._jobs:
                if job.status == JobStatus.PENDING:
                    job.status = JobStatus.SKIPPED
                    job.message = "Annulé"

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_job(self, job: BatchJob) -> None:
        self._semaphore.acquire()
        try:
            self._execute(job)
        finally:
            self._semaphore.release()

    def _execute(self, job: BatchJob) -> None:
        job.status = JobStatus.RUNNING
        job.message = "Démarrage…"
        self._notify(job)

        try:
            # License check
            if not self.license.can_export:
                job.status = JobStatus.SKIPPED
                job.message = "Mode démo épuisé — activez une licence"
                job.error = job.message
                self._notify(job)
                return

            # Scan
            job.message = "Scan du dossier…"
            self._notify(job)
            result: ScanResult = scan_directory(job.input_dir)

            if result.total_files == 0:
                raise ValueError(f"Aucun fichier raster trouvé dans {job.input_dir}")

            # Build mosaic
            job.message = "Reconstruction de la mosaïque…"
            job.progress = 0.1
            self._notify(job)

            mosaic: Optional[Mosaic] = None

            if result.has_vrt:
                vrt = result.vrt_files[0]
                job.message = f"Lecture VRT : {vrt.name}"
                self._notify(job)
                mosaic = Mosaic.from_vrt(vrt)

            if mosaic is None:
                tile_paths = [f.path for f in result.raster_files]
                mosaic = Mosaic.from_files(tile_paths)

            if mosaic.width == 0 or mosaic.height == 0:
                raise ValueError("Mosaïque vide — impossible de continuer.")

            # PDF config
            stem = job.input_dir.name or "export"
            out_path = job.output_dir / f"{stem}.pdf"
            cfg = PDFConfig(
                dpi=job.dpi,
                orientation=job.orientation,
                margin_mm=job.margin_mm,
                overlap_mm=job.overlap_mm,
                scale=job.scale,
                output_path=out_path,
            )

            from .pdf_converter import compute_pages, compute_pages_at_scale
            use_scale = cfg.scale > 0 and mosaic.pixel_size_m > 0
            if use_scale:
                pages = compute_pages_at_scale(mosaic, cfg)
            else:
                pages = compute_pages(mosaic, cfg)
            total_pages = len(pages)

            def page_cb(cur: int, total: int, msg: str) -> None:
                job.progress = 0.1 + 0.9 * cur / max(total, 1)
                job.message = msg
                self._notify(job)

            job.message = f"Génération PDF ({total_pages} pages)…"
            self._notify(job)

            output = convert_to_pdf(mosaic, cfg, progress_callback=page_cb)

            self.license.record_export()

            job.status = JobStatus.DONE
            job.progress = 1.0
            job.output_pdf = output
            job.message = f"PDF généré : {output.name}"
            self._notify(job)

        except Exception as exc:
            job.status = JobStatus.ERROR
            job.error = str(exc)
            job.message = f"Erreur : {exc}"
            self._notify(job)

    def _notify(self, job: BatchJob) -> None:
        if job.on_progress:
            try:
                job.on_progress(job)
            except Exception:
                pass
        if job.status in (JobStatus.DONE, JobStatus.ERROR, JobStatus.SKIPPED):
            if job.on_done:
                try:
                    job.on_done(job)
                except Exception:
                    pass
