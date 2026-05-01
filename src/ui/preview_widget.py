"""
preview_widget.py — Mosaic thumbnail preview with pan/zoom support.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, QPoint, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPixmap, QWheelEvent
from PyQt6.QtWidgets import QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget


class _ImageLabel(QLabel):
    """QLabel that supports zoom via mouse wheel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._zoom = 1.0
        self._orig_pixmap: Optional[QPixmap] = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setStyleSheet("background:#2d2d2d;")

    def set_pixmap(self, pm: QPixmap) -> None:
        self._orig_pixmap = pm
        self._zoom = 1.0
        self._update_display()

    def _update_display(self) -> None:
        if self._orig_pixmap is None:
            return
        w = int(self._orig_pixmap.width() * self._zoom)
        h = int(self._orig_pixmap.height() * self._zoom)
        scaled = self._orig_pixmap.scaled(
            w, h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        super().setPixmap(scaled)
        self.resize(scaled.size())

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        factor = 1.15 if delta > 0 else (1 / 1.15)
        self._zoom = max(0.05, min(self._zoom * factor, 20.0))
        self._update_display()
        event.accept()


class PreviewWidget(QWidget):
    """
    Scrollable preview of the mosaic thumbnail.

    Usage::

        widget.set_pixmap(qpixmap)     # set from a QPixmap
        widget.set_pil_image(img)      # set from a PIL Image
        widget.clear()
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll.setStyleSheet("background:#2d2d2d; border:none;")

        self._label = _ImageLabel()
        self._scroll.setWidget(self._label)

        layout.addWidget(self._scroll)

        # Placeholder
        self._placeholder = QLabel("Glissez un dossier ici pour prévisualiser")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color:#555; font-size:13px;")
        layout.addWidget(self._placeholder)
        self._scroll.hide()

    def set_pixmap(self, pm: QPixmap) -> None:
        self._label.set_pixmap(pm)
        self._placeholder.hide()
        self._scroll.show()

    def set_pil_image(self, img) -> None:
        """Accept a PIL Image and display it."""
        import io
        from PyQt6.QtGui import QPixmap, QImage
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        qimg = QImage()
        qimg.loadFromData(buf.read())
        self.set_pixmap(QPixmap.fromImage(qimg))

    def clear(self) -> None:
        self._scroll.hide()
        self._placeholder.show()
