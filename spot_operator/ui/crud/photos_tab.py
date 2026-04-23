"""Fotky tab — tabulka všech fotek + dvojklik na detail (klikatelné detekce)."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from spot_operator.config import AppConfig
from spot_operator.db.engine import Session
from spot_operator.db.repositories import detections_repo
from spot_operator.logging_config import get_logger

_log = get_logger(__name__)


class PhotosTab(QWidget):
    """Tabulka všech fotek napříč běhy + dvojklik = PhotoDetailDialog."""

    def __init__(self, config: AppConfig, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._config = config

        root = QVBoxLayout(self)
        controls = QHBoxLayout()
        self._btn_refresh = QPushButton("Obnovit")
        self._btn_refresh.clicked.connect(self._reload)
        controls.addWidget(self._btn_refresh)
        controls.addStretch(1)
        root.addLayout(controls)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["ID", "Run", "Checkpoint", "Kamera", "OCR", "Přečteno"]
        )
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.doubleClicked.connect(self._on_dblclick)
        root.addWidget(self._table)

        self._reload()

    def _reload(self) -> None:
        try:
            with Session() as s:
                from sqlalchemy import select

                from spot_operator.db.models import Photo

                rows = s.execute(
                    select(Photo).order_by(Photo.captured_at.desc()).limit(500)
                ).scalars().all()
                self._table.setRowCount(len(rows))
                for i, p in enumerate(rows):
                    dets = detections_repo.list_for_photo(s, p.id)
                    plate = ", ".join(d.plate_text or "?" for d in dets) or "—"
                    cells = [
                        str(p.id),
                        str(p.run_id),
                        p.checkpoint_name or "",
                        p.camera_source,
                        p.ocr_status.value,
                        plate,
                    ]
                    for col, text in enumerate(cells):
                        it = QTableWidgetItem(text)
                        it.setData(Qt.UserRole, p.id)
                        self._table.setItem(i, col, it)
        except Exception as exc:
            _log.warning("Photos reload failed: %s", exc)

    def _on_dblclick(self) -> None:
        from spot_operator.ui.crud.photo_detail_dialog import PhotoDetailDialog

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
        dlg = PhotoDetailDialog(self._config, pid, self)
        dlg.exec()
        self._reload()


__all__ = ["PhotosTab"]
