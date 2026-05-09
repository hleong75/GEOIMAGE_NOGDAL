"""
settings_panel.py — Right-side settings panel (DPI, orientation, output folder).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt

from ..core.pdf_converter import Orientation
from ..core.license import LicenseManager


class SettingsPanel(QWidget):
    """Collects PDF export settings from the user."""

    convert_requested = pyqtSignal()
    activate_license_requested = pyqtSignal()

    def __init__(self, license_manager: LicenseManager, parent=None) -> None:
        super().__init__(parent)
        self._license = license_manager
        self._build_ui()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def dpi(self) -> int:
        return self._dpi_spin.value()

    @property
    def orientation(self) -> Orientation:
        return (
            Orientation.PORTRAIT
            if self._orient_combo.currentIndex() == 0
            else Orientation.LANDSCAPE
        )

    @property
    def margin_mm(self) -> float:
        return self._margin_spin.value()

    @property
    def overlap_mm(self) -> float:
        return self._overlap_spin.value()

    @property
    def optimal_overlap(self) -> bool:
        return self._optimal_overlap_check.isChecked()

    @property
    def atlas_title(self) -> str:
        return self._title_edit.text().strip() or "Atlas A4 en mosaïque continue"

    @property
    def output_dir(self) -> Path:
        return Path(self._output_edit.text() or ".")

    # ------------------------------------------------------------------
    # UI builder
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)

        # ---- PDF Settings ----
        pdf_group = QGroupBox("Paramètres PDF")
        form = QFormLayout(pdf_group)
        form.setSpacing(8)

        self._dpi_spin = QSpinBox()
        self._dpi_spin.setRange(72, 1200)
        self._dpi_spin.setSingleStep(50)
        self._dpi_spin.setValue(300)
        self._dpi_spin.setSuffix(" dpi")
        form.addRow("Résolution :", self._dpi_spin)

        self._orient_combo = QComboBox()
        self._orient_combo.addItems(["Portrait (A4)", "Paysage (A4)"])
        form.addRow("Orientation :", self._orient_combo)

        self._margin_spin = QSpinBox()
        self._margin_spin.setRange(0, 50)
        self._margin_spin.setValue(10)
        self._margin_spin.setSuffix(" mm")
        form.addRow("Marges :", self._margin_spin)

        self._overlap_spin = QSpinBox()
        self._overlap_spin.setRange(0, 50)
        self._overlap_spin.setValue(5)
        self._overlap_spin.setSuffix(" mm")
        self._overlap_spin.setToolTip(
            "Chevauchement entre pages adjacentes"
        )
        form.addRow("Chevauchement min. :", self._overlap_spin)

        self._optimal_overlap_check = QCheckBox("Chevauchement optimal (sans blancs)")
        self._optimal_overlap_check.setToolTip(
            "Ajuste automatiquement les pas entre pages pour supprimer les blancs en bordure "
            "tout en respectant le chevauchement minimum."
        )
        form.addRow("", self._optimal_overlap_check)

        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText("Atlas A4 en mosaïque continue")
        self._title_edit.setToolTip(
            "Titre affiché en grand sur la page de garde du PDF"
        )
        form.addRow("Titre de l'atlas :", self._title_edit)

        main_layout.addWidget(pdf_group)

        # ---- Output folder ----
        out_group = QGroupBox("Dossier de sortie")
        out_layout = QHBoxLayout(out_group)
        self._output_edit = QLineEdit()
        self._output_edit.setPlaceholderText("Même dossier que la source")
        browse_btn = QPushButton("…")
        browse_btn.setFixedWidth(32)
        browse_btn.clicked.connect(self._browse_output)
        out_layout.addWidget(self._output_edit)
        out_layout.addWidget(browse_btn)
        main_layout.addWidget(out_group)

        # ---- Convert button ----
        self._convert_btn = QPushButton("🖨  Convertir en PDF")
        self._convert_btn.setFixedHeight(44)
        self._convert_btn.setStyleSheet(
            "QPushButton { background:#0e7afe; color:white; font-size:14px; border-radius:6px; }"
            "QPushButton:hover { background:#1a8eff; }"
            "QPushButton:disabled { background:#555; color:#999; }"
        )
        self._convert_btn.clicked.connect(self.convert_requested)
        main_layout.addWidget(self._convert_btn)

        # ---- License status ----
        lic_group = QGroupBox("Licence")
        lic_layout = QVBoxLayout(lic_group)
        self._lic_label = QLabel(self._license.status_text())
        self._lic_label.setWordWrap(True)
        lic_layout.addWidget(self._lic_label)

        self._activate_btn = QPushButton("Activer une licence")
        self._activate_btn.clicked.connect(self.activate_license_requested)
        lic_layout.addWidget(self._activate_btn)
        main_layout.addWidget(lic_group)

        main_layout.addStretch()

    def refresh_license(self) -> None:
        self._lic_label.setText(self._license.status_text())

    def set_convert_enabled(self, enabled: bool) -> None:
        self._convert_btn.setEnabled(enabled)

    def _browse_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choisir le dossier de sortie")
        if folder:
            self._output_edit.setText(folder)
