"""SPZ detail dialog — zobrazí SPZ info + náhled poslední fotky s touto SPZ.

Načítá SPZ i náhled asynchronně přes ``DbQueryWorker``. Worker lifecycle
spravován parent-ship + ``stop_and_wait`` v ``closeEvent``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
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
from spot_operator.db.models import LicensePlate
from spot_operator.db.repositories import photos_repo
from spot_operator.logging_config import get_logger
from spot_operator.ui.common.workers import DbQueryWorker

_log = get_logger(__name__)


@dataclass(frozen=True)
class _PlateSummary:
    plate_text: str
    status: str
    valid_until: date | None
    note: str | None
    created_at: datetime | None


@dataclass(frozen=True)
class _LastPhoto:
    image_bytes: bytes
    run_id: int
    captured_at: datetime | None


def _load_plate_summary(session, plate_id: int) -> _PlateSummary | None:
    plate = session.get(LicensePlate, plate_id)
    if plate is None:
        return None
    return _PlateSummary(
        plate_text=plate.plate_text,
        status=plate.status.value,
        valid_until=plate.valid_until,
        note=plate.note,
        created_at=plate.created_at,
    )


def _load_last_photo(session, plate_text: str) -> _LastPhoto | None:
    result = photos_repo.fetch_last_image_bytes_for_plate(session, plate_text)
    if result is None:
        return None
    img_bytes, run_id, captured_at = result
    return _LastPhoto(image_bytes=img_bytes, run_id=run_id, captured_at=captured_at)


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
        self._workers: list[DbQueryWorker] = []
        self._original_pixmap: QPixmap | None = None
        self.setWindowTitle("Detail SPZ")
        self.resize(680, 560)

        root = QVBoxLayout(self)

        form = QFormLayout()
        self._lbl_text = QLabel("<i>načítám…</i>")
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

        self._preview = QLabel("<i>načítám…</i>")
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

        self._start_load()

    @property
    def edit_requested(self) -> bool:
        """True pokud uživatel klikl "Upravit" — volající po close otevře edit."""
        return self._edit_requested

    # ---- Lifecycle ----

    def closeEvent(self, event) -> None:  # noqa: ANN001
        for w in list(self._workers):
            w.stop_and_wait()
        self._workers.clear()
        super().closeEvent(event)

    # ---- Async load ----

    def _start_load(self) -> None:
        plate_id = self._plate_id
        worker = DbQueryWorker(
            lambda s: _load_plate_summary(s, plate_id), parent=self,
        )
        worker.ok.connect(self._on_plate_ok)
        worker.failed.connect(self._on_plate_fail)
        worker.finished.connect(worker.deleteLater)
        self._workers.append(worker)
        worker.start()

    def _on_plate_ok(self, summary: _PlateSummary | None) -> None:
        if not self.isVisible():
            return
        if summary is None:
            self._lbl_text.setText("<i>SPZ nenalezena.</i>")
            self._preview.setText("<i>—</i>")
            return
        self._lbl_text.setText(summary.plate_text)
        self._lbl_status.setText(summary.status)
        self._lbl_valid.setText(
            summary.valid_until.isoformat() if summary.valid_until else "—"
        )
        self._lbl_note.setText(summary.note or "—")
        self._lbl_created.setText(
            summary.created_at.isoformat(timespec="seconds")
            if summary.created_at
            else "—"
        )
        # Teď spusť async fetch poslední fotky.
        self._start_photo_load(summary.plate_text)

    def _on_plate_fail(self, err: str) -> None:
        if not self.isVisible():
            return
        _log.warning("Plate load failed: %s", err)
        self._lbl_text.setText(f"<span style='color:#c0392b;'>Chyba: {err}</span>")

    def _start_photo_load(self, plate_text: str) -> None:
        worker = DbQueryWorker(
            lambda s: _load_last_photo(s, plate_text), parent=self,
        )
        worker.ok.connect(self._on_photo_ok)
        worker.failed.connect(self._on_photo_fail)
        worker.finished.connect(worker.deleteLater)
        self._workers.append(worker)
        worker.start()

    def _on_photo_ok(self, last: _LastPhoto | None) -> None:
        if not self.isVisible():
            return
        if last is None:
            self._preview.setText("<i>Žádná fotka s touto SPZ v DB.</i>")
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(last.image_bytes):
            self._preview.setText("<i>Fotku nelze dekódovat.</i>")
            return
        self._original_pixmap = pixmap
        self._render_pixmap()
        ts = (
            last.captured_at.isoformat(timespec="seconds")
            if last.captured_at
            else "—"
        )
        self._lbl_photo_title.setText(
            f"<b>Poslední fotka</b> (run #{last.run_id}, {ts}):"
        )

    def _on_photo_fail(self, err: str) -> None:
        if not self.isVisible():
            return
        _log.warning("SpzDetail photo load failed: %s", err)
        self._preview.setText(f"<span style='color:#c0392b;'>Chyba: {err}</span>")

    def _render_pixmap(self) -> None:
        pixmap = self._original_pixmap
        if pixmap is None or pixmap.isNull():
            return
        w = max(self._preview.width(), 620)
        h = max(self._preview.height(), 320)
        self._preview.setPixmap(
            pixmap.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        self._render_pixmap()

    def _on_edit(self) -> None:
        self._edit_requested = True
        self.accept()


__all__ = ["SpzDetailDialog"]
