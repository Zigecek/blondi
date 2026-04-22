"""Běhy tab — seznam spot_runs, klik = detail."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from spot_operator.db.engine import Session
from spot_operator.db.repositories import photos_repo, runs_repo
from spot_operator.logging_config import get_logger
from spot_operator.services.zip_exporter import build_run_zip
from spot_operator.ui.common.dialogs import error_dialog, info_dialog

_log = get_logger(__name__)


class RunsTab(QWidget):
    """Tabulka běhů + tlačítka export ZIP."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

        root = QVBoxLayout(self)

        controls = QHBoxLayout()
        self._btn_refresh = QPushButton("Obnovit")
        self._btn_refresh.clicked.connect(self._reload)
        controls.addWidget(self._btn_refresh)

        self._btn_zip = QPushButton("Exportovat ZIP vybraného běhu")
        self._btn_zip.clicked.connect(self._export_zip)
        controls.addWidget(self._btn_zip)
        controls.addStretch(1)
        root.addLayout(controls)

        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(
            ["ID", "Kód", "Mapa", "Start", "Konec", "Status", "Checkpointů"]
        )
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        root.addWidget(self._table)

        self._reload()

    def _reload(self) -> None:
        try:
            with Session() as s:
                rows = runs_repo.list_recent(s, limit=200)
                self._table.setRowCount(len(rows))
                for i, r in enumerate(rows):
                    self._set_row(i, r)
        except Exception as exc:
            _log.warning("Runs reload failed: %s", exc)

    def _set_row(self, row: int, r) -> None:  # noqa: ANN001
        cells = [
            str(r.id),
            r.run_code,
            r.map_name_snapshot or "",
            r.start_time.isoformat(timespec="seconds") if r.start_time else "",
            r.end_time.isoformat(timespec="seconds") if r.end_time else "",
            r.status.value,
            f"{r.checkpoints_reached}/{r.checkpoints_total}",
        ]
        for col, text in enumerate(cells):
            self._table.setItem(row, col, QTableWidgetItem(text))

    def _selected_id(self) -> Optional[int]:
        sel = self._table.currentRow()
        if sel < 0:
            return None
        item = self._table.item(sel, 0)
        if item is None:
            return None
        try:
            return int(item.text())
        except ValueError:
            return None

    def _export_zip(self) -> None:
        rid = self._selected_id()
        if rid is None:
            info_dialog(self, "Vyber běh", "Nejdřív vyber v tabulce běh.")
            return
        try:
            data, filename = build_run_zip(rid)
        except Exception as exc:
            error_dialog(self, "Chyba", f"Export selhal: {exc}")
            return
        target, _ = QFileDialog.getSaveFileName(
            self, "Uložit ZIP", filename, "ZIP archiv (*.zip)"
        )
        if not target:
            return
        try:
            Path(target).write_bytes(data)
        except Exception as exc:
            error_dialog(self, "Chyba", f"Nelze zapsat: {exc}")
            return
        info_dialog(self, "Hotovo", f"Uloženo: {target}")


__all__ = ["RunsTab"]
