"""Floating E-Stop widget — v pravém dolním rohu parent widgetu.

Od 1.2.1:
  - Pozice: pravý **dolní** roh (dříve horní — překrývalo side panel s tlačítky).
  - Toggle chování: klik v aktivním stavu = trigger, klik v triggered stavu = release.
  - Přijímá druhý callback `on_release` pro `EstopManager.release()`.

Od 1.4 (PR-01 safety): ``on_release`` je **povinný** parametr. Dříve byl Optional a
pokud chyběl, widget se jen vizuálně resetl do "klidného" stavu, ale robot byl
fyzicky stále v E-Stop. Safety-critical lie — nyní konstruktor bez on_release
hodí TypeError.

Když operátor klikne E-Stop, motory Spota se okamžitě cut a widget se přepne
do "AKTIVNÍ — klik uvolnit" stavu. Další klik zavolá release callback, widget
se resetuje do výchozího červeného stavu.
"""

from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QPushButton, QWidget

_log = logging.getLogger(__name__)

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
QPushButton:hover { background-color: #d50000; }
"""

_DEFAULT_ACTIVE_LABEL = "E-STOP (F1)"
_DEFAULT_TRIGGERED_LABEL = "⚠ AKTIVNÍ — klik uvolnit"


class EstopFloating(QPushButton):
    """Permanentně viditelné velké červené tlačítko E-Stop s toggle chováním.

    - Aktivní stav (default): klik → `on_trigger()` → přepne do triggered stavu.
    - Triggered stav: klik → `on_release()` → reset do aktivního stavu.

    Parent widget dostává eventFilter — při každé Resize/Show/Move se widget
    přepozicuje do pravého dolního rohu. Tak je vždy vidět.
    """

    def __init__(
        self,
        parent: QWidget,
        on_trigger: Callable[[], None],
        on_release: Callable[[], None],
    ):
        if on_trigger is None:
            raise TypeError("EstopFloating: on_trigger je povinný callback.")
        if on_release is None:
            raise TypeError(
                "EstopFloating: on_release je povinný callback. "
                "Bez něj by klik v triggered stavu jen vizuálně resetoval widget, "
                "ale robot by zůstal v E-Stop."
            )
        super().__init__(_DEFAULT_ACTIVE_LABEL, parent)
        self._on_trigger = on_trigger
        self._on_release = on_release
        self._triggered = False

        self.setFont(QFont("Segoe UI", 14, QFont.Bold))
        self.setFixedSize(220, 70)
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
        """Pozicovat do pravého dolního rohu parent widgetu.

        Dříve (1.0 — 1.2.0) byl widget v pravém HORNÍM rohu, což překrývalo
        side panel stránky (tlačítka "Zapnout Spota", "Foto ..."). Od 1.2.1
        je v pravém dolním rohu — prázdné místo (všechny pages mají
        `addStretch(1)` na konci side layout).
        """
        parent = self.parentWidget()
        if parent is None:
            return
        margin = 10
        x = parent.width() - self.width() - margin
        y = parent.height() - self.height() - margin
        self.move(x, y)
        self.raise_()

    # ---- Click handling ----

    def _on_click(self) -> None:
        if self._triggered:
            self._do_release()
        else:
            self._do_trigger()

    def _do_trigger(self) -> None:
        try:
            self._on_trigger()
        except Exception as exc:
            _log.exception("E-Stop trigger callback failed: %s", exc)
        self.mark_triggered()

    def _do_release(self) -> None:
        """Uvolní E-Stop endpoint + resetuje widget.

        ``on_release`` je od PR-01 povinný parametr konstruktoru — tady
        můžeme předpokládat, že není None.
        """
        try:
            self._on_release()
        except Exception as exc:
            _log.exception("E-Stop release callback failed: %s", exc)
            # Nezapínáme reset — endpoint je pořád triggered.
            return
        self.reset()

    # ---- Public API ----

    def mark_triggered(self) -> None:
        self._triggered = True
        self.setStyleSheet(_TRIGGERED_QSS)
        self.setText(_DEFAULT_TRIGGERED_LABEL)

    def reset(self) -> None:
        self._triggered = False
        self.setStyleSheet(_ACTIVE_QSS)
        self.setText(_DEFAULT_ACTIVE_LABEL)

    def trigger_from_shortcut(self) -> None:
        """F1 shortcut → stejné chování jako klik.

        V triggered stavu je to release; jinak trigger.
        """
        self._on_click()

    @property
    def is_triggered(self) -> bool:
        return self._triggered

    def closeEvent(self, event) -> None:  # noqa: D401, ANN001 - Qt event
        """Při close widget odpoj eventFilter z parenta, aby nezůstal visící."""
        parent = self.parentWidget()
        if parent is not None:
            try:
                parent.removeEventFilter(self)
            except Exception:
                pass
        super().closeEvent(event)


__all__ = ["EstopFloating"]
