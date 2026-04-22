"""Krok X: Kontrola fiducialu — buď (recording) jakýkoli, nebo (playback) specifický ID."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QWizardPage,
)

from spot_operator.config import AppConfig
from spot_operator.logging_config import get_logger
from spot_operator.ui.common.workers import FunctionWorker

_log = get_logger(__name__)


class FiducialPage(QWizardPage):
    """Kontrola, že Spot vidí fiducial v dostatečné blízkosti.

    Konfigurovatelné:
      - required_id: None pro recording (jakýkoli), int pro playback (konkrétní).
      - na validaci ukládá do wizardu property 'fiducial_id' (pro recording).
    """

    def __init__(
        self,
        config: AppConfig,
        *,
        required_id: Optional[int] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._config = config
        self._required_id = required_id
        self._detected_id: Optional[int] = None
        self._worker: Optional[FunctionWorker] = None

        title = "Kontrola fiducialu"
        if required_id is None:
            subtitle = (
                "Postav Spota 1–2 m před fiducial (obvykle u nabíječky na recepci)."
                " Pak klikni Zkontrolovat."
            )
        else:
            subtitle = (
                f"Tato mapa byla nahrána u fiducialu ID <b>{required_id}</b>."
                " Postav Spota před tento fiducial a klikni Zkontrolovat."
            )
        self.setTitle(title)
        self.setSubTitle(subtitle)

        root = QVBoxLayout(self)

        instructions = QLabel(
            "<p>Fiducial je černobílý čtvercový marker (AprilTag) nalepený "
            "u nabíječky. Spot potřebuje fiducial vidět, aby věděl, kde je.</p>"
        )
        instructions.setWordWrap(True)
        root.addWidget(instructions)

        action_row = QHBoxLayout()
        self._btn_check = QPushButton("Zkontrolovat fiducial")
        self._btn_check.clicked.connect(self._start_check)
        action_row.addWidget(self._btn_check)
        action_row.addStretch(1)
        root.addLayout(action_row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        self._status = QLabel("")
        self._status.setTextFormat(Qt.RichText)
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        root.addStretch(1)

    def set_required_id(self, required_id: Optional[int]) -> None:
        """Povoluje dynamicky měnit required_id (playback wizard ho čte po výběru mapy)."""
        self._required_id = required_id
        self._detected_id = None
        self._status.setText("")
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        if self._required_id is None:
            return self._detected_id is not None
        return self._detected_id == self._required_id

    def _start_check(self) -> None:
        bundle = self.wizard().bundle()  # type: ignore[attr-defined]
        if bundle is None or bundle.session is None:
            self._status.setText(
                "<span style='color:#c00;'>Spot není připojen.</span>"
            )
            return
        self._btn_check.setEnabled(False)
        self._progress.setVisible(True)
        self._status.setText("<i>Hledám fiducial...</i>")

        from app.robot.fiducial_check import visible_fiducials

        self._worker = FunctionWorker(
            visible_fiducials,
            bundle.session,
            required_id=self._required_id,
            max_distance_m=self._config.fiducial_distance_threshold_m,
        )
        self._worker.finished_ok.connect(self._on_check_done)
        self._worker.failed.connect(self._on_check_failed)
        self._worker.start()

    def _on_check_done(self, observations) -> None:  # noqa: ANN001
        self._btn_check.setEnabled(True)
        self._progress.setVisible(False)
        if not observations:
            if self._required_id is None:
                msg = "Nevidím žádný fiducial do 2 m. Posuň Spota blíž a zkus znovu."
            else:
                msg = (
                    f"Nevidím fiducial ID {self._required_id}. "
                    "Postav Spota před správný fiducial a zkus znovu."
                )
            self._status.setText(f"<span style='color:#c62828;'>✗ {msg}</span>")
            self._detected_id = None
            self.completeChanged.emit()
            return

        best = observations[0]
        self._detected_id = best.tag_id
        # Uložíme do wizardu pro pozdější použití (recording ukládá do mapy).
        self.wizard().setProperty("fiducial_id", best.tag_id)

        if self._required_id is not None and best.tag_id != self._required_id:
            self._status.setText(
                f"<span style='color:#c62828;'>✗ Vidím fiducial ID {best.tag_id} "
                f"ale mapa očekává {self._required_id}. Postav Spota na správné místo.</span>"
            )
            self.completeChanged.emit()
            return

        self._status.setText(
            f"<span style='color:#2e7d32;'>✓ Vidím fiducial ID {best.tag_id} "
            f"({best.distance_m:.2f} m).</span>"
        )
        self.completeChanged.emit()

    def _on_check_failed(self, reason: str) -> None:
        self._btn_check.setEnabled(True)
        self._progress.setVisible(False)
        self._status.setText(f"<span style='color:#c62828;'>Chyba: {reason}</span>")


__all__ = ["FiducialPage"]
