"""Běhy tab — QTableView + RunsModel (async paged) + tlačítka export ZIP.

Double-click otevře ``RunDetailDialog`` s tabulkou fotek v běhu.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QModelIndex
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from blondi.config import AppConfig
from blondi.logging_config import get_logger
from blondi.services.zip_exporter import build_run_zip
from blondi.ui.common.dialogs import error_dialog, info_dialog
from blondi.ui.common.table_models import RunsModel, apply_default_sort_indicator

_log = get_logger(__name__)


class RunsTab(QWidget):
    """Tabulka běhů + export ZIP + dvojklik = RunDetailDialog."""

    def __init__(
        self,
        config: Optional[AppConfig] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._config = config
        self._current_dlg = None

        root = QVBoxLayout(self)

        controls = QHBoxLayout()
        self._btn_refresh = QPushButton("Obnovit")
        self._btn_refresh.clicked.connect(self._reload)
        controls.addWidget(self._btn_refresh)

        self._btn_zip = QPushButton("Exportovat ZIP vybraného běhu")
        self._btn_zip.clicked.connect(self._export_zip)
        controls.addWidget(self._btn_zip)
        controls.addStretch(1)
        self._status_label = QLabel("")
        controls.addWidget(self._status_label)
        root.addLayout(controls)

        self._model = RunsModel(parent=self)
        self._view = QTableView()
        self._view.setModel(self._model)
        self._view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._view.setSelectionMode(QAbstractItemView.SingleSelection)
        self._view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._view.setAlternatingRowColors(True)
        self._view.verticalHeader().setVisible(False)
        self._view.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._view.doubleClicked.connect(self._on_dblclick)
        apply_default_sort_indicator(self._view, self._model)
        self._view.setSortingEnabled(True)
        self._model.modelReset.connect(self._update_status)
        self._model.rowsInserted.connect(self._update_status)
        root.addWidget(self._view)

        self._reload()

    # ---- Lifecycle ----

    def closeEvent(self, event) -> None:  # noqa: ANN001
        self._model.stop_all_workers()
        super().closeEvent(event)

    # ---- Actions ----

    def _reload(self) -> None:
        self._model.reset()

    def _update_status(self, *_args) -> None:
        loaded = self._model.loaded()
        total = self._model.total()
        err = self._model.error()
        if err:
            self._status_label.setText(f"<span style='color:#c0392b;'>Chyba: {err}</span>")
        else:
            self._status_label.setText(f"{loaded} / {total}")

    def _on_dblclick(self, index: QModelIndex) -> None:
        from blondi.ui.crud.run_detail_dialog import RunDetailDialog

        if not index.isValid():
            return
        row = self._model.row_at(index.row())
        if row is None:
            return
        dlg = RunDetailDialog(self._config, row.id, parent=self)
        self._current_dlg = dlg
        dlg.finished.connect(lambda _code: self._on_dlg_finished(dlg))
        dlg.show()

    def _on_dlg_finished(self, dlg) -> None:  # noqa: ANN001
        if self._current_dlg is dlg:
            self._current_dlg = None
        dlg.deleteLater()

    def _selected_id(self) -> Optional[int]:
        idx = self._view.currentIndex()
        if not idx.isValid():
            return None
        row = self._model.row_at(idx.row())
        return row.id if row is not None else None

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
