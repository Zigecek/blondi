"""Krok 6 playbacku: Shrnutí běhu + seznam SPZ + stažení ZIP."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QWizardPage,
)

from spot_operator.db.engine import Session
from spot_operator.db.repositories import detections_repo, photos_repo, runs_repo
from spot_operator.logging_config import get_logger
from spot_operator.services.zip_exporter import build_run_zip
from spot_operator.ui.common.dialogs import error_dialog, info_dialog

_log = get_logger(__name__)


class PlaybackResultPage(QWizardPage):
    """Shrnutí + tabulka přečtených SPZ + stažení ZIP."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._run_id: Optional[int] = None

        self.setTitle("6. Výsledek jízdy")
        self.setSubTitle("Stáhni ZIP s fotkami a metadaty nebo ukonči wizard.")

        root = QVBoxLayout(self)

        self._summary = QLabel("—")
        self._summary.setTextFormat(Qt.RichText)
        self._summary.setWordWrap(True)
        root.addWidget(self._summary)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["Checkpoint", "Kamera", "Stav OCR", "SPZ", "Conf (text/det)"]
        )
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        root.addWidget(self._table, stretch=1)

        action_row = QHBoxLayout()
        self._btn_export = QPushButton("Stáhnout ZIP")
        self._btn_export.clicked.connect(self._on_export)
        action_row.addWidget(self._btn_export)
        action_row.addStretch(1)
        root.addLayout(action_row)

    def initializePage(self) -> None:
        rid = self.wizard().property("completed_run_id")
        try:
            self._run_id = int(rid) if rid is not None else None
        except Exception:
            self._run_id = None
        if self._run_id is None or self._run_id < 0:
            self._summary.setText("<span style='color:#c00;'>Run id nebyl zapsán.</span>")
            return
        self._populate()

    def _populate(self) -> None:
        assert self._run_id is not None
        with Session() as s:
            run = runs_repo.get(s, self._run_id)
            if run is None:
                self._summary.setText(
                    f"<span style='color:#c00;'>Run id {self._run_id} není v DB.</span>"
                )
                return
            photos = photos_repo.list_for_run(s, self._run_id)
            self._summary.setText(
                f"<b>Run:</b> {run.run_code}<br>"
                f"<b>Mapa:</b> {run.map_name_snapshot or '—'}<br>"
                f"<b>Stav:</b> {run.status.value}<br>"
                f"<b>Checkpointů:</b> {run.checkpoints_reached}/{run.checkpoints_total}<br>"
                f"<b>Fotek:</b> {len(photos)}"
            )

            self._table.setRowCount(len(photos))
            for row, photo in enumerate(photos):
                detections = detections_repo.list_for_photo(s, photo.id)
                plate_text = ", ".join(d.plate_text or "?" for d in detections) or "—"
                if detections:
                    best = detections[0]
                    conf_text = (
                        f"{(best.text_confidence or 0) * 100:.0f} % / "
                        f"{(best.detection_confidence or 0) * 100:.0f} %"
                    )
                else:
                    conf_text = "—"
                self._table.setItem(row, 0, QTableWidgetItem(photo.checkpoint_name or ""))
                self._table.setItem(row, 1, QTableWidgetItem(photo.camera_source))
                self._table.setItem(row, 2, QTableWidgetItem(photo.ocr_status.value))
                self._table.setItem(row, 3, QTableWidgetItem(plate_text))
                self._table.setItem(row, 4, QTableWidgetItem(conf_text))

    def _on_export(self) -> None:
        if self._run_id is None:
            return
        try:
            data, filename = build_run_zip(self._run_id)
        except Exception as exc:
            _log.exception("ZIP export failed: %s", exc)
            error_dialog(self, "Chyba", f"Export selhal: {exc}")
            return

        target, _ = QFileDialog.getSaveFileName(
            self,
            "Uložit ZIP",
            filename,
            "ZIP archiv (*.zip)",
        )
        if not target:
            return
        try:
            Path(target).write_bytes(data)
        except Exception as exc:
            error_dialog(self, "Chyba", f"Nelze zapsat soubor: {exc}")
            return
        info_dialog(self, "Hotovo", f"Soubor uložen: {target}")


__all__ = ["PlaybackResultPage"]
