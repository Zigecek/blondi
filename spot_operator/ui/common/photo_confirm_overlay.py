"""Photo confirm overlay — non-modal live preview z 1-2 Spot kamer.

Od 1.3.0. Operátor klikne v TeleopRecordPage "Foto vlevo/vpravo/obě" → tento
overlay se objeví nad live view, ukazuje live video z dotyčné kamery (kamer),
operátor vizuálně ověří, že SPZ je vidět, a teprve pak potvrdí uložení.

Non-modal: WASD klávesy propadají do parent widgetu (TeleopRecordPage). Overlay
zachycuje jen myš (tlačítka). Důvod: operátor může upravit pozici Spota a vidět
okamžitou změnu v preview, aniž by musel dialog zavřít.

Při potvrzení emituje `confirmed(list[str])` signál s použitými sources. Při
zrušení `cancelled()`. Vlastník (TeleopRecordPage) je odpovědný za zavolání
`teardown()` a `close()` po přijetí signálu.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from spot_operator.robot.session_factory import SpotBundle

_log = logging.getLogger(__name__)


class PhotoConfirmOverlay(QWidget):
    """Non-modal overlay s live videem z 1-2 Spot kamer + potvrzovacími tlačítky.

    Emituje signály:
      - `confirmed(list[str])` — operátor klikl "✓ Vyfotit a uložit".
      - `cancelled()` — operátor klikl "✗ Zrušit".

    Vlastník musí po přijetí signálu volat `teardown()` + `close()`.
    """

    confirmed = Signal(list)
    cancelled = Signal()

    def __init__(self, bundle: object, sources: list[str], parent: QWidget):
        super().__init__(parent)
        self._sources = list(sources)
        self._pipelines: list = []
        self._live_views: list = []

        # Non-modal: klávesy propadnou do parent widgetu (TeleopRecordPage).
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            "QWidget { background-color: rgba(20,20,20,230); }"
            "QWidget#photoConfirmOverlay { border: 2px solid #1565c0; }"
        )
        self.setObjectName("photoConfirmOverlay")

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        title = QLabel("Náhled — ověř, že SPZ je vidět, pak potvrď.")
        title.setStyleSheet("color:white; font-weight:bold; font-size:14px; padding:4px;")
        title.setTextFormat(Qt.PlainText)
        root.addWidget(title)

        # Import autonomy tříd lazy (až po inject_paths) — používáme jednu
        # sdílenou ImagePoller instanci, každá pipeline má jiný source.
        from app.image_pipeline import ImagePipeline
        from app.robot.images import ImagePoller
        from app.ui.live_view_widget import LiveViewWidget

        poller = ImagePoller(bundle.session)  # type: ignore[attr-defined]
        for src in self._sources:
            lbl = QLabel(f"<b>Kamera:</b> {src}")
            lbl.setStyleSheet("color:white; padding:2px 4px;")
            lbl.setTextFormat(Qt.RichText)
            root.addWidget(lbl)

            live = LiveViewWidget(self)
            live.setMinimumHeight(220)
            root.addWidget(live)

            pipeline = ImagePipeline(poller)
            pipeline.set_source(src)
            pipeline.frame_ready.connect(live.update_frame)
            try:
                pipeline.start()
            except Exception as exc:
                _log.warning("Overlay pipeline start failed for %s: %s", src, exc)

            self._pipelines.append(pipeline)
            self._live_views.append(live)

        # Dvě velká tlačítka dole.
        btn_row = QHBoxLayout()
        self._btn_confirm = QPushButton("✓ Vyfotit a uložit")
        self._btn_confirm.setStyleSheet(
            "QPushButton { background:#2e7d32; color:white; font-weight:bold; "
            "padding:10px; font-size:14px; border:none; border-radius:4px; }"
            "QPushButton:hover { background:#1b5e20; }"
        )
        self._btn_confirm.setFocusPolicy(Qt.NoFocus)
        self._btn_confirm.clicked.connect(self._on_confirm)
        btn_row.addWidget(self._btn_confirm)

        self._btn_cancel = QPushButton("✗ Zrušit")
        self._btn_cancel.setStyleSheet(
            "QPushButton { background:#616161; color:white; font-weight:bold; "
            "padding:10px; font-size:14px; border:none; border-radius:4px; }"
            "QPushButton:hover { background:#424242; }"
        )
        self._btn_cancel.setFocusPolicy(Qt.NoFocus)
        self._btn_cancel.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._btn_cancel)

        root.addLayout(btn_row)

    # ---- Slots ----

    def _on_confirm(self) -> None:
        self.confirmed.emit(list(self._sources))

    def _on_cancel(self) -> None:
        self.cancelled.emit()

    # ---- Lifecycle ----

    def teardown(self) -> None:
        """Bezpečně zastaví všechny ImagePipeline QThready. Idempotentní."""
        for pipeline in self._pipelines:
            try:
                if hasattr(pipeline, "stop"):
                    pipeline.stop()
                pipeline.quit()
                pipeline.wait(2000)
            except Exception as exc:
                _log.debug("Overlay pipeline teardown failed: %s", exc)
        self._pipelines.clear()

    def closeEvent(self, event):  # noqa: D401 - Qt hook
        self.teardown()
        super().closeEvent(event)


__all__ = ["PhotoConfirmOverlay"]
