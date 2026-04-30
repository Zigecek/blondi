"""SPZ tab — QTableView + PlatesModel (async paged) + filtry + add/edit/delete.

Filtr text_contains používá debounce (``CRUD_SEARCH_DEBOUNCE_MS``) aby každá
písmenka nevyvolala DB dotaz. Finální volání ``PlatesModel.set_filters`` je
no-op, pokud se filtry nezměnily.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from PySide6.QtCore import QModelIndex, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from blondi.config import AppConfig
from blondi.constants import CRUD_SEARCH_DEBOUNCE_MS
from blondi.db.engine import Session
from blondi.db.enums import PlateStatus
from blondi.db.repositories import plates_repo
from blondi.logging_config import get_logger
from blondi.ui.common.dialogs import confirm_dialog, error_dialog
from blondi.ui.common.table_models import PlatesModel, apply_default_sort_indicator

_log = get_logger(__name__)


class SpzTab(QWidget):
    """Tabulka SPZ + filtry + CRUD dialogy."""

    def __init__(
        self,
        config: Optional[AppConfig] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._config = config
        self._current_dlg = None

        # Debounce timer pro text search — jeden QTimer jako one-shot.
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(CRUD_SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._apply_filters)

        root = QVBoxLayout(self)

        filter_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Hledat v SPZ...")
        self._search.textChanged.connect(self._on_search_changed)
        filter_row.addWidget(self._search)

        self._status_combo = QComboBox()
        self._status_combo.addItem("Všechny statusy", None)
        for st in PlateStatus:
            self._status_combo.addItem(st.value, st)
        self._status_combo.currentIndexChanged.connect(self._apply_filters)
        filter_row.addWidget(self._status_combo)

        self._btn_add = QPushButton("+ Přidat")
        self._btn_add.clicked.connect(self._on_add)
        filter_row.addWidget(self._btn_add)

        self._btn_edit = QPushButton("Upravit")
        self._btn_edit.clicked.connect(self._on_edit)
        filter_row.addWidget(self._btn_edit)

        self._btn_delete = QPushButton("Smazat")
        self._btn_delete.clicked.connect(self._on_delete)
        filter_row.addWidget(self._btn_delete)

        filter_row.addStretch(1)
        self._status_label = QLabel("")
        filter_row.addWidget(self._status_label)
        root.addLayout(filter_row)

        self._model = PlatesModel(parent=self)
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

        self._apply_filters()

    # ---- Lifecycle ----

    def closeEvent(self, event) -> None:  # noqa: ANN001
        self._search_timer.stop()
        self._model.stop_all_workers()
        super().closeEvent(event)

    # ---- Filtry ----

    def _on_search_changed(self, _text: str) -> None:
        # Restart debounce timeru.
        self._search_timer.start()

    def _apply_filters(self) -> None:
        self._search_timer.stop()
        status = self._status_combo.currentData()
        text = self._search.text().strip()
        self._model.set_filters(status=status, text_contains=text or None)

    def _update_status(self, *_args) -> None:
        loaded = self._model.loaded()
        total = self._model.total()
        err = self._model.error()
        if err:
            self._status_label.setText(f"<span style='color:#c0392b;'>Chyba: {err}</span>")
        else:
            self._status_label.setText(f"{loaded} / {total}")

    # ---- Selection helpers ----

    def _selected_id(self) -> Optional[int]:
        idx = self._view.currentIndex()
        if not idx.isValid():
            return None
        row = self._model.row_at(idx.row())
        return row.id if row is not None else None

    # ---- CRUD akce ----

    def _on_dblclick(self, index: QModelIndex) -> None:
        from blondi.ui.crud.spz_detail_dialog import SpzDetailDialog

        if not index.isValid():
            return
        row = self._model.row_at(index.row())
        if row is None:
            return
        dlg = SpzDetailDialog(self._config, row.id, parent=self)
        self._current_dlg = dlg
        dlg.finished.connect(lambda _code: self._on_dlg_finished(dlg))
        dlg.show()

    def _on_dlg_finished(self, dlg) -> None:  # noqa: ANN001
        if self._current_dlg is dlg:
            self._current_dlg = None
        edit_requested = bool(getattr(dlg, "edit_requested", False))
        dlg.deleteLater()
        if edit_requested:
            self._on_edit()

    def _on_add(self) -> None:
        dlg = _SpzEditDialog(self)
        if dlg.exec() == QDialog.Accepted:
            data = dlg.data
            try:
                with Session() as s:
                    plates_repo.upsert(s, **data)
                    s.commit()
            except Exception as exc:
                error_dialog(self, "Chyba", str(exc))
            self._model.reset()

    def _on_edit(self) -> None:
        pid = self._selected_id()
        if pid is None:
            return
        with Session() as s:
            from blondi.db.models import LicensePlate

            row = s.get(LicensePlate, pid)
            if row is None:
                return
            prefill = {
                "plate_text": row.plate_text,
                "status": row.status,
                "valid_until": row.valid_until,
                "note": row.note or "",
            }
        dlg = _SpzEditDialog(self, prefill=prefill)
        if dlg.exec() == QDialog.Accepted:
            data = dlg.data
            try:
                with Session() as s:
                    plates_repo.upsert(s, **data)
                    s.commit()
            except Exception as exc:
                error_dialog(self, "Chyba", str(exc))
            self._model.reset()

    def _on_delete(self) -> None:
        pid = self._selected_id()
        if pid is None:
            return
        if not confirm_dialog(
            self, "Smazat SPZ?", "Opravdu smazat tento záznam?", destructive=True
        ):
            return
        try:
            with Session() as s:
                plates_repo.delete(s, pid)
                s.commit()
        except Exception as exc:
            error_dialog(self, "Chyba", str(exc))
        self._model.reset()


class _SpzEditDialog(QDialog):
    """Modal dialog pro přidat/upravit SPZ."""

    def __init__(self, parent=None, prefill: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("SPZ detail")
        prefill = prefill or {}

        form = QFormLayout(self)

        self._text_edit = QLineEdit(prefill.get("plate_text", ""))
        form.addRow("SPZ:", self._text_edit)

        self._status_combo = QComboBox()
        for st in PlateStatus:
            self._status_combo.addItem(st.value, st)
        if prefill.get("status"):
            idx = self._status_combo.findData(prefill["status"])
            if idx >= 0:
                self._status_combo.setCurrentIndex(idx)
        form.addRow("Status:", self._status_combo)

        self._valid_edit = QLineEdit(
            prefill["valid_until"].isoformat() if prefill.get("valid_until") else ""
        )
        self._valid_edit.setPlaceholderText("YYYY-MM-DD (volitelné)")
        form.addRow("Platí do:", self._valid_edit)

        self._note_edit = QPlainTextEdit(prefill.get("note", ""))
        self._note_edit.setFixedHeight(60)
        form.addRow("Poznámka:", self._note_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    @property
    def data(self) -> dict:
        valid_text = self._valid_edit.text().strip()
        valid_date = None
        if valid_text:
            try:
                valid_date = date.fromisoformat(valid_text)
            except ValueError:
                valid_date = None
        return {
            "plate_text": self._text_edit.text().strip(),
            "status": self._status_combo.currentData(),
            "valid_until": valid_date,
            "note": self._note_edit.toPlainText().strip() or None,
        }


__all__ = ["SpzTab"]
