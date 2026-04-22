"""Fotky tab — tabulka + detail + re-OCR lepším enginem."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
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
from spot_operator.db.repositories import detections_repo, photos_repo
from spot_operator.logging_config import get_logger
from spot_operator.ocr.fallback import reprocess_bytes
from spot_operator.ui.common.dialogs import error_dialog, info_dialog
from spot_operator.ui.common.workers import FunctionWorker

_log = get_logger(__name__)


class PhotosTab(QWidget):
    """Tabulka všech fotek napříč běhy + dvojklik = detail."""

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
        dlg = _PhotoDetailDialog(self._config, pid, self)
        dlg.exec()
        self._reload()


class _PhotoDetailDialog(QDialog):
    """Náhled fotky + detekce + re-OCR tlačítko."""

    def __init__(self, config: AppConfig, photo_id: int, parent=None):
        super().__init__(parent)
        self._config = config
        self._photo_id = photo_id
        self.setWindowTitle(f"Foto #{photo_id}")
        self.resize(700, 540)

        root = QVBoxLayout(self)

        self._preview = QLabel()
        self._preview.setAlignment(Qt.AlignCenter)
        self._preview.setStyleSheet("background:#111;")
        root.addWidget(self._preview, stretch=1)

        self._detail_text = QLabel()
        self._detail_text.setTextFormat(Qt.RichText)
        self._detail_text.setWordWrap(True)
        root.addWidget(self._detail_text)

        action_row = QHBoxLayout()
        self._btn_reocr = QPushButton("Spustit OCR znovu (Nomeroff fallback)")
        self._btn_reocr.clicked.connect(self._run_reocr)
        action_row.addWidget(self._btn_reocr)
        action_row.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        action_row.addWidget(buttons)
        root.addLayout(action_row)

        self._load()

    def _load(self) -> None:
        with Session() as s:
            photo = photos_repo.get(s, self._photo_id)
            if photo is None:
                self._detail_text.setText("<i>Foto nenalezeno.</i>")
                return
            pixmap = QPixmap()
            pixmap.loadFromData(photo.image_bytes)
            if not pixmap.isNull():
                self._preview.setPixmap(
                    pixmap.scaled(
                        self._preview.width() or 600,
                        self._preview.height() or 400,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )
                )
            detections = detections_repo.list_for_photo(s, self._photo_id)
            lines = [
                f"<b>Run:</b> {photo.run_id} &nbsp; "
                f"<b>Checkpoint:</b> {photo.checkpoint_name or '—'} &nbsp; "
                f"<b>Kamera:</b> {photo.camera_source} &nbsp; "
                f"<b>OCR:</b> {photo.ocr_status.value}"
            ]
            if detections:
                lines.append("<br><b>Detekce:</b>")
                for d in detections:
                    lines.append(
                        f"&nbsp;&nbsp;- <b>{d.plate_text}</b> "
                        f"(text conf {_pct(d.text_confidence)}, "
                        f"det conf {_pct(d.detection_confidence)}) "
                        f"[{d.engine_name}]"
                    )
            else:
                lines.append("<br><i>Žádné detekce.</i>")
            self._detail_text.setText("<br>".join(lines))

    def _run_reocr(self) -> None:
        self._btn_reocr.setEnabled(False)
        with Session() as s:
            photo = photos_repo.get(s, self._photo_id)
            if photo is None:
                error_dialog(self, "Chyba", "Foto nenalezeno.")
                return
            image_bytes = photo.image_bytes

        worker = FunctionWorker(
            reprocess_bytes,
            image_bytes,
            yolo_model_path=self._config.ocr_yolo_model_path,
        )
        worker.finished_ok.connect(self._on_reocr_done)
        worker.failed.connect(self._on_reocr_failed)
        worker.start()
        self._worker = worker

    def _on_reocr_done(self, detections) -> None:  # noqa: ANN001
        try:
            with Session() as s:
                engine_name = (
                    detections[0].engine_name if detections else "yolo_v1m+nomeroff"
                )
                detections_repo.delete_for_photo_engine(s, self._photo_id, engine_name)
                if detections:
                    rows = [d.to_db_row(self._photo_id) for d in detections]
                    detections_repo.insert_many(s, rows)
                s.commit()
            info_dialog(self, "Re-OCR hotové", f"Detekcí: {len(detections)}")
        except Exception as exc:
            error_dialog(self, "Chyba", str(exc))
        self._btn_reocr.setEnabled(True)
        self._load()

    def _on_reocr_failed(self, reason: str) -> None:
        self._btn_reocr.setEnabled(True)
        error_dialog(self, "Re-OCR selhalo", reason)


def _pct(v) -> str:  # noqa: ANN001
    if v is None:
        return "—"
    return f"{v * 100:.0f} %"


__all__ = ["PhotosTab"]
