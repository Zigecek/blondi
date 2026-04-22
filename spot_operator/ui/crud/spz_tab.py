"""SPZ tab — tabulka registru + filtry + add/edit/delete."""

from __future__ import annotations

from datetime import date
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from spot_operator.db.engine import Session
from spot_operator.db.enums import PlateStatus
from spot_operator.db.repositories import plates_repo
from spot_operator.logging_config import get_logger
from spot_operator.ui.common.dialogs import confirm_dialog, error_dialog

_log = get_logger(__name__)


class SpzTab(QWidget):
    """Tabulka SPZ + filtry + CRUD dialogy."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        root = QVBoxLayout(self)

        filter_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Hledat v SPZ...")
        self._search.textChanged.connect(self._reload)
        filter_row.addWidget(self._search)

        self._status_combo = QComboBox()
        self._status_combo.addItem("Všechny statusy", None)
        for st in PlateStatus:
            self._status_combo.addItem(st.value, st)
        self._status_combo.currentIndexChanged.connect(self._reload)
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
        root.addLayout(filter_row)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["ID", "SPZ", "Status", "Platí do", "Poznámka"]
        )
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        root.addWidget(self._table)

        self._reload()

    def _reload(self) -> None:
        status = self._status_combo.currentData()
        text = self._search.text().strip()
        try:
            with Session() as s:
                rows = plates_repo.list_all(
                    s, status=status, text_contains=text or None
                )
                self._table.setRowCount(len(rows))
                for i, r in enumerate(rows):
                    self._set_row(i, r.id, r.plate_text, r.status.value,
                                  r.valid_until, r.note or "")
        except Exception as exc:
            _log.warning("SPZ reload failed: %s", exc)

    def _set_row(self, row: int, id_: int, text: str, status: str,
                 valid_until: Optional[date], note: str) -> None:
        items = [
            QTableWidgetItem(str(id_)),
            QTableWidgetItem(text),
            QTableWidgetItem(status),
            QTableWidgetItem(valid_until.isoformat() if valid_until else ""),
            QTableWidgetItem(note),
        ]
        for col, it in enumerate(items):
            it.setData(Qt.UserRole, id_)
            self._table.setItem(row, col, it)

    def _selected_id(self) -> Optional[int]:
        sel = self._table.currentRow()
        if sel < 0:
            return None
        item = self._table.item(sel, 0)
        if item is None:
            return None
        try:
            return int(item.text())
        except Exception:
            return None

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
            self._reload()

    def _on_edit(self) -> None:
        pid = self._selected_id()
        if pid is None:
            return
        with Session() as s:
            from spot_operator.db.models import LicensePlate

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
            self._reload()

    def _on_delete(self) -> None:
        pid = self._selected_id()
        if pid is None:
            return
        if not confirm_dialog(
            self, "Smazat SPZ?", "Opravdu smazat tento záznam?", destructive=True
        ):
            return
        with Session() as s:
            plates_repo.delete(s, pid)
            s.commit()
        self._reload()


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
