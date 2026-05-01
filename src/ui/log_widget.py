"""
log_widget.py — Simple scrollable log display for PyQt6.
"""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class LogWidget(QWidget):
    """Append-only log display with colour-coded levels."""

    cleared = pyqtSignal()

    _COLOURS = {
        "INFO":    QColor("#d4d4d4"),
        "SUCCESS": QColor("#6fcf97"),
        "WARNING": QColor("#f2c94c"),
        "ERROR":   QColor("#eb5757"),
        "DEBUG":   QColor("#828282"),
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self._text.setStyleSheet(
            "background:#1e1e1e; color:#d4d4d4; font-family:Consolas,monospace; font-size:11px;"
        )
        layout.addWidget(self._text)

        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 2, 0, 0)
        clear_btn = QPushButton("Effacer")
        clear_btn.setFixedWidth(80)
        clear_btn.clicked.connect(self.clear)
        btn_layout.addStretch()
        btn_layout.addWidget(clear_btn)
        layout.addLayout(btn_layout)

    def log(self, message: str, level: str = "INFO") -> None:
        """Append a log line. *level* is one of INFO, SUCCESS, WARNING, ERROR, DEBUG."""
        colour = self._COLOURS.get(level.upper(), self._COLOURS["INFO"])
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] [{level.upper():<7}] {message}"

        fmt = QTextCharFormat()
        fmt.setForeground(colour)

        cursor = self._text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(line + "\n", fmt)
        self._text.setTextCursor(cursor)
        self._text.ensureCursorVisible()

    def info(self, msg: str) -> None:
        self.log(msg, "INFO")

    def success(self, msg: str) -> None:
        self.log(msg, "SUCCESS")

    def warning(self, msg: str) -> None:
        self.log(msg, "WARNING")

    def error(self, msg: str) -> None:
        self.log(msg, "ERROR")

    def debug(self, msg: str) -> None:
        self.log(msg, "DEBUG")

    def clear(self) -> None:
        self._text.clear()
        self.cleared.emit()
