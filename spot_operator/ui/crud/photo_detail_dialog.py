"""Photo detail dialog — náhled fotky, detekce (klikatelné), re-OCR.

Přesunuto z `photos_tab.py` aby šel reused z `run_detail_dialog.py` bez
circular importů. Rozšířeno o klikatelné detekce: klik na SPZ text →
pokud je v registru, otevře SPZ detail; jinak nabídne přidání do registru.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from spot_operator.config import AppConfig
from spot_operator.db.engine import Session
from spot_operator.db.enums import PlateStatus
from spot_operator.db.repositories import detections_repo, photos_repo, plates_repo
from spot_operator.logging_config import get_logger
from spot_operator.ocr.fallback import reprocess_bytes
from spot_operator.ui.common.dialogs import confirm_dialog, error_dialog, info_dialog
from spot_operator.ui.common.workers import FunctionWorker

_log = get_logger(__name__)


class PhotoDetailDialog(QDialog):
    """Náhled fotky + (klikatelné) detekce + re-OCR tlačítko."""

    def __init__(
        self,
        config: AppConfig,
        photo_id: int,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._config = config
        self._photo_id = photo_id
        self.setWindowTitle(f"Foto #{photo_id}")
        self.resize(760, 600)

        root = QVBoxLayout(self)

        self._preview = QLabel()
        self._preview.setAlignment(Qt.AlignCenter)
        self._preview.setStyleSheet("background:#111;")
        root.addWidget(self._preview, stretch=1)

        self._detail_text = QLabel()
        self._detail_text.setTextFormat(Qt.RichText)
        self._detail_text.setWordWrap(True)
        # Klikatelné hyperlinky pro SPZ detekce (href="plate:TEXT").
        self._detail_text.setTextInteractionFlags(
            Qt.TextBrowserInteraction | Qt.LinksAccessibleByMouse
        )
        self._detail_text.linkActivated.connect(self._on_detection_clicked)
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

    # ---- Load ----

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
                        self._preview.width() or 640,
                        self._preview.height() or 420,
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
                lines.append("<br><b>Detekce</b> (klikni pro SPZ detail):")
                for d in detections:
                    text = d.plate_text
                    if text:
                        linked = (
                            f"<a href='plate:{text}'"
                            f" style='color:#1565c0; text-decoration:underline;'>"
                            f"<b>{text}</b></a>"
                        )
                    else:
                        linked = "<b>(bez textu)</b>"
                    lines.append(
                        f"&nbsp;&nbsp;- {linked} "
                        f"(text conf {_pct(d.text_confidence)}, "
                        f"det conf {_pct(d.detection_confidence)}) "
                        f"[{d.engine_name}]"
                    )
            else:
                lines.append("<br><i>Žádné detekce.</i>")
            self._detail_text.setText("<br>".join(lines))

    # ---- Detection click → SPZ detail nebo "Přidat do registru" prompt ----

    def _on_detection_clicked(self, href: str) -> None:
        if not href.startswith("plate:"):
            return
        plate_text = href[len("plate:"):].strip().upper()
        if not plate_text:
            return

        # Hledáme v registru. Pokud existuje → otevři SPZ detail.
        from spot_operator.ui.crud.spz_detail_dialog import SpzDetailDialog

        with Session() as s:
            plate = plates_repo.get_by_text(s, plate_text)

        if plate is not None:
            dlg = SpzDetailDialog(self._config, plate.id, parent=self)
            dlg.exec()
            return

        # Není v registru → nabídnout "Přidat do registru?"
        if not confirm_dialog(
            self,
            "SPZ není v registru",
            f"SPZ '{plate_text}' není v registru license_plates. "
            f"Chceš ji přidat?",
        ):
            return

        # Otevři edit dialog prefilled s plate_text.
        from spot_operator.ui.crud.spz_tab import _SpzEditDialog

        prefill = {
            "plate_text": plate_text,
            "status": PlateStatus.unknown,
            "valid_until": None,
            "note": "",
        }
        edit_dlg = _SpzEditDialog(self, prefill=prefill)
        if edit_dlg.exec() == QDialog.Accepted:
            try:
                with Session() as s:
                    plates_repo.upsert(s, **edit_dlg.data)
                    s.commit()
                info_dialog(self, "Přidáno", f"SPZ {plate_text} byla přidána do registru.")
            except Exception as exc:
                _log.warning("Add plate from detection failed: %s", exc)
                error_dialog(self, "Chyba", str(exc))

    # ---- Re-OCR ----

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


__all__ = ["PhotoDetailDialog"]
