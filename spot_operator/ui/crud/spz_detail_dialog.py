"""SPZ detail dialog — zobrazí SPZ info + náhled poslední fotky s touto SPZ."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from spot_operator.config import AppConfig
from spot_operator.db.engine import Session
from spot_operator.db.models import LicensePlate
from spot_operator.db.repositories import photos_repo
from spot_operator.logging_config import get_logger

_log = get_logger(__name__)


class SpzDetailDialog(QDialog):
    """Detail SPZ: údaje z registru + náhled poslední fotky (pokud existuje)."""

    def __init__(
        self,
        config: AppConfig,
        plate_id: int,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._config = config
        self._plate_id = plate_id
        self._edit_requested = False
        self.setWindowTitle("Detail SPZ")
        self.resize(680, 560)

        root = QVBoxLayout(self)

        form = QFormLayout()
        self._lbl_text = QLabel("—")
        self._lbl_text.setStyleSheet("font-size:16px; font-weight:bold;")
        self._lbl_status = QLabel("—")
        self._lbl_valid = QLabel("—")
        self._lbl_note = QLabel("—")
        self._lbl_note.setWordWrap(True)
        self._lbl_created = QLabel("—")
        form.addRow("SPZ:", self._lbl_text)
        form.addRow("Status:", self._lbl_status)
        form.addRow("Platí do:", self._lbl_valid)
        form.addRow("Poznámka:", self._lbl_note)
        form.addRow("Vytvořeno:", self._lbl_created)
        root.addLayout(form)

        root.addSpacing(6)
        self._lbl_photo_title = QLabel("<b>Poslední fotka:</b>")
        self._lbl_photo_title.setTextFormat(Qt.RichText)
        root.addWidget(self._lbl_photo_title)

        self._preview = QLabel("<i>načítám...</i>")
        self._preview.setTextFormat(Qt.RichText)
        self._preview.setAlignment(Qt.AlignCenter)
        self._preview.setStyleSheet("background:#111; color:#888;")
        self._preview.setMinimumHeight(300)
        root.addWidget(self._preview, stretch=1)

        action_row = QHBoxLayout()
        self._btn_edit = QPushButton("Upravit záznam")
        self._btn_edit.clicked.connect(self._on_edit)
        action_row.addWidget(self._btn_edit)
        action_row.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        action_row.addWidget(buttons)
        root.addLayout(action_row)

        self._load()

    @property
    def edit_requested(self) -> bool:
        """True pokud uživatel klikl "Upravit" — volající může po close
        otevřít edit dialog."""
        return self._edit_requested

    def _load(self) -> None:
        with Session() as s:
            plate: LicensePlate | None = s.get(LicensePlate, self._plate_id)
            if plate is None:
                self._lbl_text.setText("<i>SPZ nenalezena.</i>")
                self._preview.setText("<i>—</i>")
                return
            self._lbl_text.setText(plate.plate_text)
            self._lbl_status.setText(plate.status.value)
            self._lbl_valid.setText(
                plate.valid_until.isoformat() if plate.valid_until else "—"
            )
            self._lbl_note.setText(plate.note or "—")
            self._lbl_created.setText(
                plate.created_at.isoformat(timespec="seconds")
                if plate.created_at
                else "—"
            )
            last_photo = photos_repo.get_last_photo_for_plate(s, plate.plate_text)
            if last_photo is None:
                self._preview.setText("<i>Žádná fotka s touto SPZ v DB.</i>")
                return
            pixmap = QPixmap()
            pixmap.loadFromData(last_photo.image_bytes)
            if pixmap.isNull():
                self._preview.setText("<i>(Fotku nelze dekódovat.)</i>")
                return
            self._preview.setPixmap(
                pixmap.scaled(
                    self._preview.width() or 620,
                    self._preview.height() or 320,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )
            # Aktualizuj titulek s datem focení.
            ts = (
                last_photo.captured_at.isoformat(timespec="seconds")
                if last_photo.captured_at
                else "—"
            )
            self._lbl_photo_title.setText(
                f"<b>Poslední fotka</b> (run #{last_photo.run_id}, {ts}):"
            )

    def _on_edit(self) -> None:
        self._edit_requested = True
        self.accept()


__all__ = ["SpzDetailDialog"]
