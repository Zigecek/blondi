"""Run detail dialog — souhrn běhu + tabulka fotek (klikatelná na detail)."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from spot_operator.config import AppConfig
from spot_operator.db.engine import Session
from spot_operator.db.repositories import detections_repo, photos_repo, runs_repo
from spot_operator.logging_config import get_logger

_log = get_logger(__name__)


class RunDetailDialog(QDialog):
    """Detail běhu: metadata + seznam fotek. Double-click fotky → Photo detail."""

    def __init__(
        self,
        config: AppConfig,
        run_id: int,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._config = config
        self._run_id = run_id
        self.setWindowTitle(f"Běh #{run_id}")
        self.resize(860, 620)

        root = QVBoxLayout(self)

        form = QFormLayout()
        self._lbl_code = QLabel("—")
        self._lbl_map = QLabel("—")
        self._lbl_start = QLabel("—")
        self._lbl_end = QLabel("—")
        self._lbl_status = QLabel("—")
        self._lbl_checkpoints = QLabel("—")
        form.addRow("Kód:", self._lbl_code)
        form.addRow("Mapa:", self._lbl_map)
        form.addRow("Start:", self._lbl_start)
        form.addRow("Konec:", self._lbl_end)
        form.addRow("Status:", self._lbl_status)
        form.addRow("Checkpointů:", self._lbl_checkpoints)
        root.addLayout(form)

        root.addSpacing(4)
        root.addWidget(QLabel("<b>Fotky v tomto běhu (dvojklik pro detail):</b>"))

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["ID", "Checkpoint", "Kamera", "OCR", "Přečteno", "Zachyceno"]
        )
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.doubleClicked.connect(self._on_photo_dblclick)
        root.addWidget(self._table, stretch=1)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        action_row.addWidget(buttons)
        root.addLayout(action_row)

        self._load()

    # ---- Load ----

    def _load(self) -> None:
        with Session() as s:
            run = runs_repo.get(s, self._run_id)
            if run is None:
                self._lbl_code.setText("<i>Běh nenalezen.</i>")
                return
            self._lbl_code.setText(run.run_code)
            self._lbl_map.setText(run.map_name_snapshot or "—")
            self._lbl_start.setText(
                run.start_time.isoformat(timespec="seconds") if run.start_time else "—"
            )
            self._lbl_end.setText(
                run.end_time.isoformat(timespec="seconds") if run.end_time else "—"
            )
            self._lbl_status.setText(run.status.value)
            self._lbl_checkpoints.setText(
                f"{run.checkpoints_reached}/{run.checkpoints_total}"
            )

            photos = photos_repo.list_for_run(s, self._run_id)
            self._table.setRowCount(len(photos))
            for i, p in enumerate(photos):
                dets = detections_repo.list_for_photo(s, p.id)
                plate = ", ".join(d.plate_text or "?" for d in dets) or "—"
                cells = [
                    str(p.id),
                    p.checkpoint_name or "",
                    p.camera_source,
                    p.ocr_status.value,
                    plate,
                    (
                        p.captured_at.isoformat(timespec="seconds")
                        if p.captured_at
                        else ""
                    ),
                ]
                for col, text in enumerate(cells):
                    item = QTableWidgetItem(text)
                    item.setData(Qt.UserRole, p.id)
                    self._table.setItem(i, col, item)

    # ---- Photo double-click → Photo detail ----

    def _on_photo_dblclick(self) -> None:
        row = self._table.currentRow()
        if row < 0:
            return
        item = self._table.item(row, 0)
        if item is None:
            return
        try:
            pid = int(item.text())
        except ValueError:
            return
        from spot_operator.ui.crud.photo_detail_dialog import PhotoDetailDialog

        dlg = PhotoDetailDialog(self._config, pid, parent=self)
        dlg.exec()


__all__ = ["RunDetailDialog"]
