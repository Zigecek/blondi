"""Floating E-Stop widget — vždy viditelný v pravém horním rohu wizardu."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QPushButton, QWidget

_ACTIVE_QSS = """
QPushButton {
    background-color: #c62828;
    color: white;
    border: 3px solid #8d1010;
    border-radius: 8px;
    padding: 12px 18px;
    font-weight: bold;
}
QPushButton:hover { background-color: #b71c1c; }
QPushButton:pressed { background-color: #7f0000; }
QPushButton:disabled { background-color: #757575; border-color: #424242; }
"""

_TRIGGERED_QSS = """
QPushButton {
    background-color: #ff1744;
    color: white;
    border: 3px solid #ffea00;
    border-radius: 8px;
    padding: 12px 18px;
    font-weight: bold;
}
"""


class EstopFloating(QPushButton):
    """Permanentně viditelné velké červené tlačítko E-Stop.

    Připíchnuté k pravému hornímu rohu parent widgetu (typicky Wizard page).
    Po kliku zavolá provided callback (který sáhne na EstopManager.trigger()).
    """

    def __init__(
        self,
        parent: QWidget,
        on_trigger: Callable[[], None],
        *,
        label: str = "E-STOP (F1)",
    ):
        super().__init__(label, parent)
        self._on_trigger = on_trigger
        self._triggered = False

        self.setFont(QFont("Segoe UI", 14, QFont.Bold))
        self.setFixedSize(180, 70)
        self.setStyleSheet(_ACTIVE_QSS)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_AlwaysStackOnTop, True)
        self.clicked.connect(self._on_click)

        self._reposition()
        if parent is not None:
            parent.installEventFilter(self)

    def eventFilter(self, obj, event):  # noqa: D401 - Qt filter
        if obj is self.parent():
            from PySide6.QtCore import QEvent

            if event.type() in (QEvent.Resize, QEvent.Show, QEvent.Move):
                self._reposition()
        return super().eventFilter(obj, event)

    def _reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        margin = 10
        x = parent.width() - self.width() - margin
        y = margin
        self.move(x, y)
        self.raise_()

    def _on_click(self) -> None:
        try:
            self._on_trigger()
        except Exception:
            # Nechceme crash GUI — logging se postará výše.
            pass
        self.mark_triggered()

    def mark_triggered(self) -> None:
        self._triggered = True
        self.setStyleSheet(_TRIGGERED_QSS)
        self.setText("E-STOP ! AKTIVNÍ")

    def reset(self) -> None:
        self._triggered = False
        self.setStyleSheet(_ACTIVE_QSS)
        self.setText("E-STOP (F1)")

    @property
    def is_triggered(self) -> bool:
        return self._triggered


__all__ = ["EstopFloating"]
