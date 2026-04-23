"""Run detail dialog — souhrn běhu + tabulka fotek.

Metadata běhu se načítají asynchronně přes ``DbQueryWorker`` (jedno krátké
SELECT). Tabulka fotek používá ``PhotosModel(run_id=...)`` — stejný paged
model jako globální fotky tab, jen s filtrem na běh.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QModelIndex
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from spot_operator.config import AppConfig
from spot_operator.db.repositories import runs_repo
from spot_operator.logging_config import get_logger
from spot_operator.ui.common.table_models import PhotosModel, apply_default_sort_indicator
from spot_operator.ui.common.workers import DbQueryWorker

_log = get_logger(__name__)


class RunDetailDialog(QDialog):
    """Detail běhu: metadata (async) + seznam fotek (paged model)."""

    def __init__(
        self,
        config: AppConfig,
        run_id: int,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._config = config
        self._run_id = run_id
        self._current_dlg = None
        self._meta_worker: DbQueryWorker | None = None
        self.setWindowTitle(f"Běh #{run_id}")
        self.resize(860, 620)

        root = QVBoxLayout(self)

        form = QFormLayout()
        self._lbl_code = QLabel("<i>načítám…</i>")
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

        self._model = PhotosModel(parent=self, run_id=run_id)
        self._view = QTableView()
        self._view.setModel(self._model)
        self._view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._view.setSelectionMode(QAbstractItemView.SingleSelection)
        self._view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._view.setAlternatingRowColors(True)
        self._view.verticalHeader().setVisible(False)
        self._view.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._view.doubleClicked.connect(self._on_photo_dblclick)
        apply_default_sort_indicator(self._view, self._model)
        self._view.setSortingEnabled(True)
        root.addWidget(self._view, stretch=1)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        action_row.addWidget(buttons)
        root.addLayout(action_row)

        self._start_metadata_load()
        self._model.reset()

    # ---- Lifecycle ----

    def closeEvent(self, event) -> None:  # noqa: ANN001
        if self._meta_worker is not None:
            self._meta_worker.stop_and_wait()
            self._meta_worker = None
        self._model.stop_all_workers()
        super().closeEvent(event)

    # ---- Metadata (async) ----

    def _start_metadata_load(self) -> None:
        run_id = self._run_id
        worker = DbQueryWorker(
            lambda s: runs_repo.get_summary(s, run_id), parent=self,
        )
        worker.ok.connect(self._on_meta_ok)
        worker.failed.connect(self._on_meta_fail)
        worker.finished.connect(worker.deleteLater)
        self._meta_worker = worker
        worker.start()

    def _on_meta_ok(self, summary) -> None:  # noqa: ANN001 — DTO
        if not self.isVisible():
            return
        if summary is None:
            self._lbl_code.setText("<i>Běh nenalezen.</i>")
            return
        self._lbl_code.setText(summary.run_code)
        self._lbl_map.setText(summary.map_name_snapshot or "—")
        self._lbl_start.setText(
            summary.start_time.isoformat(timespec="seconds")
            if summary.start_time
            else "—"
        )
        self._lbl_end.setText(
            summary.end_time.isoformat(timespec="seconds") if summary.end_time else "—"
        )
        self._lbl_status.setText(summary.status)
        self._lbl_checkpoints.setText(
            f"{summary.checkpoints_reached}/{summary.checkpoints_total}"
        )

    def _on_meta_fail(self, err: str) -> None:
        if not self.isVisible():
            return
        _log.warning("Run metadata load failed: %s", err)
        self._lbl_code.setText(f"<span style='color:#c0392b;'>Chyba: {err}</span>")

    # ---- Photo double-click → Photo detail ----

    def _on_photo_dblclick(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        row = self._model.row_at(index.row())
        if row is None:
            return
        from spot_operator.ui.crud.photo_detail_dialog import PhotoDetailDialog

        dlg = PhotoDetailDialog(self._config, row.id, parent=self)
        self._current_dlg = dlg
        dlg.finished.connect(lambda _code: self._on_photo_dlg_finished(dlg))
        dlg.show()

    def _on_photo_dlg_finished(self, dlg) -> None:  # noqa: ANN001
        if self._current_dlg is dlg:
            self._current_dlg = None
        dlg.deleteLater()
        # Po případném re-OCR obnov seznam (detekce se mohly změnit).
        self._model.reset()


__all__ = ["RunDetailDialog"]
