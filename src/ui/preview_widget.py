"""
preview_widget.py — Mosaic thumbnail preview with pan/zoom support.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, QPoint, pyqtSignal, QRect
from PyQt6.QtGui import QColor, QPainter, QPixmap, QWheelEvent, QPen
from PyQt6.QtWidgets import QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget


class _ImageLabel(QLabel):
    """QLabel that supports zoom via mouse wheel."""
    selection_changed = pyqtSignal(object)  # Optional[tuple[int,int,int,int]]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._zoom = 1.0
        self._orig_pixmap: Optional[QPixmap] = None
        self._selection_rect: Optional[QRect] = None
        self._drag_start: Optional[QPoint] = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setStyleSheet("background:#2d2d2d;")
        self.setMouseTracking(True)

    def set_pixmap(self, pm: QPixmap) -> None:
        self._orig_pixmap = pm
        self._zoom = 1.0
        self._selection_rect = None
        self._update_display()
        self.selection_changed.emit(None)

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

    def clear_selection(self) -> None:
        self._selection_rect = None
        self.update()
        self.selection_changed.emit(None)

    def get_selected_region_original(self) -> Optional[tuple[int, int, int, int]]:
        if self._orig_pixmap is None or self._selection_rect is None:
            return None
        shown = self.pixmap()
        if shown is None or shown.width() <= 0 or shown.height() <= 0:
            return None

        normalized = self._selection_rect.normalized()
        bounds = QRect(0, 0, shown.width(), shown.height())
        rect = normalized.intersected(bounds)
        if rect.width() <= 0 or rect.height() <= 0:
            return None

        sx = self._orig_pixmap.width() / shown.width()
        sy = self._orig_pixmap.height() / shown.height()
        x = max(0, int(round(rect.x() * sx)))
        y = max(0, int(round(rect.y() * sy)))
        w = max(1, int(round(rect.width() * sx)))
        h = max(1, int(round(rect.height() * sy)))
        w = min(w, self._orig_pixmap.width() - x)
        h = min(h, self._orig_pixmap.height() - y)
        if w <= 0 or h <= 0:
            return None
        return x, y, w, h

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.pixmap() is not None:
            self._drag_start = event.position().toPoint()
            self._selection_rect = QRect(self._drag_start, self._drag_start)
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_start is not None:
            cur = event.position().toPoint()
            self._selection_rect = QRect(self._drag_start, cur).normalized()
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._drag_start is not None:
            self._drag_start = None
            region = self.get_selected_region_original()
            if region is None:
                self._selection_rect = None
            self.update()
            self.selection_changed.emit(region)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._selection_rect is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(255, 165, 0))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(QColor(255, 165, 0, 40))
        painter.drawRect(self._selection_rect.normalized())
        painter.end()


class PreviewWidget(QWidget):
    """
    Scrollable preview of the mosaic thumbnail.

    Usage::

        widget.set_pixmap(qpixmap)     # set from a QPixmap
        widget.set_pil_image(img)      # set from a PIL Image
        widget.clear()
    """

    selection_changed = pyqtSignal(object)  # Optional[tuple[int,int,int,int]]

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
        self._label.selection_changed.connect(self.selection_changed)
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
        self._label.clear_selection()
        self._scroll.hide()
        self._placeholder.show()

    def get_selected_region(self) -> Optional[tuple[int, int, int, int]]:
        return self._label.get_selected_region_original()
