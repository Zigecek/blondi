"""Fotky tab — QTableView + PhotosModel (async paged) + dvojklik na detail.

Oproti původní verzi:

- ``QAbstractTableModel`` místo ``QTableWidget`` → model lazy fetchuje stránku po
  stránce přes scroll, data plynou v BG threadu bez zamrzání UI.
- ``PhotosModel`` používá ``photos_repo.list_page_light`` které ``defer``
  ``image_bytes`` a ``selectinload`` detekce → žádné BYTEA v list SELECTech,
  žádné N+1.
- ``PhotoDetailDialog`` je držen jako atribut ``self._current_dlg`` dokud
  neemituje ``finished``, což zabraňuje GC během re-OCR workeru.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QModelIndex
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from spot_operator.config import AppConfig
from spot_operator.db.engine import Session
from spot_operator.db.repositories import photos_repo
from spot_operator.logging_config import get_logger
from spot_operator.ui.common.dialogs import confirm_dialog, error_dialog, info_dialog
from spot_operator.ui.common.table_models import PhotosModel, apply_default_sort_indicator
from spot_operator.ui.common.workers import FunctionWorker

_log = get_logger(__name__)


class PhotosTab(QWidget):
    """Tabulka všech fotek (všechny běhy) + dvojklik = PhotoDetailDialog."""

    def __init__(self, config: AppConfig, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._config = config
        self._current_dlg = None  # drží otevřený PhotoDetailDialog před GC
        self._reset_worker: FunctionWorker | None = None

        root = QVBoxLayout(self)

        controls = QHBoxLayout()
        self._btn_refresh = QPushButton("Obnovit")
        self._btn_refresh.clicked.connect(self._reload)
        controls.addWidget(self._btn_refresh)

        self._btn_reset_all = QPushButton("Reset všech fotek na OCR pending")
        self._btn_reset_all.setToolTip(
            "Všem fotkám ve stavu done/failed vrátí ocr_status = pending. "
            "Automatický OCR worker je potom znovu projde."
        )
        self._btn_reset_all.clicked.connect(self._on_reset_all_clicked)
        controls.addWidget(self._btn_reset_all)

        controls.addStretch(1)
        self._status_label = QLabel("")
        controls.addWidget(self._status_label)
        root.addLayout(controls)

        self._model = PhotosModel(parent=self)
        self._view = QTableView()
        self._view.setModel(self._model)
        self._view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._view.setSelectionMode(QAbstractItemView.SingleSelection)
        self._view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._view.setAlternatingRowColors(True)
        self._view.verticalHeader().setVisible(False)
        self._view.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._view.doubleClicked.connect(self._on_dblclick)
        # Nejprve indicator reflektující model defaults, pak enable — jinak Qt
        # zavolá model.sort(0, Asc) při enable a přebije nám defaulty.
        apply_default_sort_indicator(self._view, self._model)
        self._view.setSortingEnabled(True)
        self._model.modelReset.connect(self._update_status)
        self._model.rowsInserted.connect(self._update_status)
        root.addWidget(self._view)

        self._reload()

    # ---- Lifecycle ----

    def closeEvent(self, event) -> None:  # noqa: ANN001 — Qt API
        self._model.stop_all_workers()
        if self._reset_worker is not None:
            self._reset_worker.stop_and_wait()
            self._reset_worker = None
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
        from spot_operator.ui.crud.photo_detail_dialog import PhotoDetailDialog

        if not index.isValid():
            return
        row = self._model.row_at(index.row())
        if row is None:
            return
        dlg = PhotoDetailDialog(self._config, row.id, parent=self)
        self._current_dlg = dlg
        dlg.finished.connect(lambda _code: self._on_dlg_finished(dlg))
        dlg.show()

    def _on_dlg_finished(self, dlg) -> None:  # noqa: ANN001
        # Dialog skončil — uvolni referenci a refresh tabulku
        # (detekce mohly být změněny re-OCR).
        if self._current_dlg is dlg:
            self._current_dlg = None
        dlg.deleteLater()
        self._reload()

    # ---- Bulk reset ----

    def _on_reset_all_clicked(self) -> None:
        if self._reset_worker is not None and self._reset_worker.isRunning():
            return
        if not confirm_dialog(
            self,
            "Reset OCR stavu",
            "Všem fotkám ve stavu done/failed se vrátí ocr_status = pending "
            "a automatický OCR worker je znovu projde.\n\nPokračovat?",
        ):
            return
        self._btn_reset_all.setEnabled(False)
        worker = FunctionWorker(_reset_all_task, parent=self)
        worker.finished_ok.connect(self._on_reset_all_done)
        worker.failed.connect(self._on_reset_all_failed)
        worker.finished.connect(worker.deleteLater)
        self._reset_worker = worker
        worker.start()

    def _on_reset_all_done(self, count) -> None:  # noqa: ANN001
        self._reset_worker = None
        self._btn_reset_all.setEnabled(True)
        _log.info("Bulk reset of photos to pending: %d row(s)", int(count))
        info_dialog(
            self,
            "Reset hotový",
            f"Resetováno {int(count)} fotek zpět na pending. "
            "OCR worker je postupně projede.",
        )
        self._reload()

    def _on_reset_all_failed(self, reason: str) -> None:
        self._reset_worker = None
        self._btn_reset_all.setEnabled(True)
        _log.warning("Bulk reset failed: %s", reason)
        error_dialog(self, "Reset selhal", reason)


def _reset_all_task() -> int:
    """BG task — reset všech done/failed fotek na pending. Vrátí počet řádků."""
    with Session() as s:
        count = photos_repo.reset_all_to_pending(s)
        s.commit()
    return count


__all__ = ["PhotosTab"]
