"""Photo detail dialog — náhled fotky, detekce (klikatelné), re-OCR.

Refactor oproti původní verzi:

- Metadata i BYTEA se načítají **asynchronně** přes ``DbQueryWorker``.
  ``_load_metadata`` nahraje jen metadata + detekce (bez ``image_bytes``),
  ``_load_image_bytes`` v paralelním dotazu stáhne samotný JPEG.
- Všechny workery mají ``parent=self`` (Qt je udrží při životě s dialogem) a
  jsou evidované v ``self._workers``. V ``closeEvent`` se všechny
  odpojí a ``stop_and_wait()``.
- Sloty z workerů začínají ``if not self.isVisible(): return`` jako pojistka
  proti signálu přicházejícímu po zavření (mezi disconnect a skutečným exit).
- Re-OCR worker načítá ``image_bytes`` uvnitř BG threadu, ne v UI threadu —
  eliminuje dřívější synchronní ``photos_repo.get(...).image_bytes``.
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
from spot_operator.constants import OCR_ENGINE_FAST_PLATE, OCR_ENGINE_NOMEROFF
from spot_operator.db.engine import Session
from spot_operator.db.enums import PlateStatus
from spot_operator.db.repositories import detections_repo, photos_repo, plates_repo
from spot_operator.db.repositories.photos_repo import PhotoMetadata
from spot_operator.logging_config import get_logger
from spot_operator.ocr.fallback import reprocess_bytes
from spot_operator.ocr.pipeline import create_default_pipeline
from spot_operator.ui.common.dialogs import confirm_dialog, error_dialog, info_dialog
from spot_operator.ui.common.workers import DbQueryWorker, FunctionWorker

_log = get_logger(__name__)


class PhotoDetailDialog(QDialog):
    """Náhled fotky + klikatelné detekce + re-OCR tlačítko."""

    def __init__(
        self,
        config: AppConfig,
        photo_id: int,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._config = config
        self._photo_id = photo_id
        self._workers: list = []
        self._reocr_worker: FunctionWorker | None = None
        self.setWindowTitle(f"Foto #{photo_id}")
        self.resize(760, 600)

        root = QVBoxLayout(self)

        self._preview = QLabel("<i>Načítám fotku…</i>")
        self._preview.setAlignment(Qt.AlignCenter)
        self._preview.setTextFormat(Qt.RichText)
        self._preview.setStyleSheet("background:#111; color:#888;")
        root.addWidget(self._preview, stretch=1)

        self._detail_text = QLabel("<i>Načítám metadata…</i>")
        self._detail_text.setTextFormat(Qt.RichText)
        self._detail_text.setWordWrap(True)
        self._detail_text.setTextInteractionFlags(
            Qt.TextBrowserInteraction | Qt.LinksAccessibleByMouse
        )
        self._detail_text.linkActivated.connect(self._on_detection_clicked)
        root.addWidget(self._detail_text)

        action_row = QHBoxLayout()
        self._btn_reocr_fast_plate = QPushButton("Re-OCR: fast-plate (hlavní)")
        self._btn_reocr_fast_plate.setToolTip(
            "Spustí hlavní OCR pipeline (YOLO + fast-plate-ocr) nad touto fotkou."
        )
        self._btn_reocr_fast_plate.clicked.connect(self._run_reocr_fast_plate)
        action_row.addWidget(self._btn_reocr_fast_plate)

        self._btn_reocr_nomeroff = QPushButton("Re-OCR: Nomeroff")
        self._btn_reocr_nomeroff.setToolTip(
            "Spustí Nomeroff fallback (subprocess s torch/nomeroff_net) nad touto fotkou."
        )
        self._btn_reocr_nomeroff.clicked.connect(self._run_reocr_nomeroff)
        action_row.addWidget(self._btn_reocr_nomeroff)

        action_row.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        action_row.addWidget(buttons)
        root.addLayout(action_row)

        self._start_load()

    # ---- Lifecycle ----

    def closeEvent(self, event) -> None:  # noqa: ANN001
        for w in list(self._workers):
            w.stop_and_wait()
        self._workers.clear()
        if self._reocr_worker is not None:
            self._reocr_worker.stop_and_wait()
            self._reocr_worker = None
        super().closeEvent(event)

    # ---- Async load ----

    def _start_load(self) -> None:
        self._load_metadata()
        self._load_image_bytes()

    def _load_metadata(self) -> None:
        photo_id = self._photo_id
        worker = DbQueryWorker(
            lambda s: photos_repo.get_photo_metadata(s, photo_id), parent=self,
        )
        worker.ok.connect(self._on_meta_ok)
        worker.failed.connect(self._on_meta_fail)
        worker.finished.connect(worker.deleteLater)
        self._workers.append(worker)
        worker.start()

    def _load_image_bytes(self) -> None:
        photo_id = self._photo_id
        worker = DbQueryWorker(
            lambda s: photos_repo.fetch_image_bytes(s, photo_id), parent=self,
        )
        worker.ok.connect(self._on_image_ok)
        worker.failed.connect(self._on_image_fail)
        worker.finished.connect(worker.deleteLater)
        self._workers.append(worker)
        worker.start()

    def _on_meta_ok(self, meta: PhotoMetadata | None) -> None:
        if not self.isVisible():
            return
        if meta is None:
            self._detail_text.setText("<i>Foto nenalezeno.</i>")
            return
        self._detail_text.setText(_format_detail(meta))

    def _on_meta_fail(self, err: str) -> None:
        if not self.isVisible():
            return
        _log.warning("Photo metadata load failed: %s", err)
        self._detail_text.setText(f"<span style='color:#c0392b;'>Chyba: {err}</span>")

    def _on_image_ok(self, image_bytes: bytes | None) -> None:
        if not self.isVisible():
            return
        if not image_bytes:
            self._preview.setText("<i>Fotka není dostupná.</i>")
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(image_bytes):
            self._preview.setText("<i>Fotku nelze dekódovat.</i>")
            return
        # Cache originál pro případné resize (nyní scaleme na aktuální velikost).
        self._original_pixmap = pixmap
        self._render_pixmap()

    def _on_image_fail(self, err: str) -> None:
        if not self.isVisible():
            return
        _log.warning("Photo image load failed: %s", err)
        self._preview.setText(f"<span style='color:#c0392b;'>Chyba: {err}</span>")

    def _render_pixmap(self) -> None:
        pixmap = getattr(self, "_original_pixmap", None)
        if pixmap is None or pixmap.isNull():
            return
        w = max(self._preview.width(), 640)
        h = max(self._preview.height(), 420)
        self._preview.setPixmap(
            pixmap.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        self._render_pixmap()

    # ---- Detection click → SPZ detail nebo "Přidat do registru" prompt ----

    def _on_detection_clicked(self, href: str) -> None:
        if not href.startswith("plate:"):
            return
        plate_text = href[len("plate:"):].strip().upper()
        if not plate_text:
            return

        from spot_operator.ui.crud.spz_detail_dialog import SpzDetailDialog

        with Session() as s:
            plate = plates_repo.get_by_text(s, plate_text)

        if plate is not None:
            dlg = SpzDetailDialog(self._config, plate.id, parent=self)
            dlg.exec()
            return

        if not confirm_dialog(
            self,
            "SPZ není v registru",
            f"SPZ '{plate_text}' není v registru license_plates. "
            f"Chceš ji přidat?",
        ):
            return

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

    def _run_reocr_nomeroff(self) -> None:
        if self._reocr_worker is not None and self._reocr_worker.isRunning():
            return
        self._set_reocr_buttons_enabled(False)
        photo_id = self._photo_id
        yolo_path = self._config.ocr_yolo_model_path

        worker = FunctionWorker(
            _reocr_nomeroff_task, photo_id, yolo_path, parent=self,
        )
        worker.finished_ok.connect(
            lambda detections: self._on_reocr_done(detections, OCR_ENGINE_NOMEROFF)
        )
        worker.failed.connect(self._on_reocr_failed)
        worker.finished.connect(worker.deleteLater)
        self._reocr_worker = worker
        worker.start()

    def _run_reocr_fast_plate(self) -> None:
        if self._reocr_worker is not None and self._reocr_worker.isRunning():
            return
        self._set_reocr_buttons_enabled(False)
        photo_id = self._photo_id
        config = self._config

        worker = FunctionWorker(
            _reocr_fast_plate_task, photo_id, config, parent=self,
        )
        worker.finished_ok.connect(
            lambda detections: self._on_reocr_done(detections, OCR_ENGINE_FAST_PLATE)
        )
        worker.failed.connect(self._on_reocr_failed)
        worker.finished.connect(worker.deleteLater)
        self._reocr_worker = worker
        worker.start()

    def _set_reocr_buttons_enabled(self, enabled: bool) -> None:
        self._btn_reocr_fast_plate.setEnabled(enabled)
        self._btn_reocr_nomeroff.setEnabled(enabled)

    def _on_reocr_done(self, detections, engine_name: str) -> None:  # noqa: ANN001
        self._reocr_worker = None
        if not self.isVisible():
            return
        try:
            with Session() as s:
                detections_repo.delete_for_photo_engine(s, self._photo_id, engine_name)
                if detections:
                    rows = [d.to_db_row(self._photo_id) for d in detections]
                    detections_repo.insert_many(s, rows)
                s.commit()
            info_dialog(
                self,
                "Re-OCR hotové",
                f"Engine: {engine_name}\nDetekcí: {len(detections)}",
            )
        except Exception as exc:
            error_dialog(self, "Chyba", str(exc))
        self._set_reocr_buttons_enabled(True)
        # Přenačti metadata (detekce se změnily) — nikoli obrázek.
        self._load_metadata()

    def _on_reocr_failed(self, reason: str) -> None:
        self._reocr_worker = None
        if not self.isVisible():
            return
        self._set_reocr_buttons_enabled(True)
        error_dialog(self, "Re-OCR selhalo", reason)


# ---- Helpery ----

def _reocr_nomeroff_task(photo_id: int, yolo_model_path):  # noqa: ANN001 — běží v BG threadu
    """Re-OCR work: stáhne bytes a projede Nomeroff subprocess. BG thread."""
    with Session() as s:
        image_bytes = photos_repo.fetch_image_bytes(s, photo_id)
    if not image_bytes:
        raise RuntimeError("Foto nenalezeno nebo prázdné.")
    return reprocess_bytes(image_bytes, yolo_model_path=yolo_model_path)


def _reocr_fast_plate_task(photo_id: int, config: AppConfig):
    """Re-OCR work: stáhne bytes a projede hlavní pipeline (YOLO + fast-plate-ocr).

    Pipeline se vytváří per-call (ne sdílená s automatickým workerem) — lazy load
    YOLO+ONNX trvá pár sekund na první spuštění, ale běží v BG threadu a pro
    manuální test to je přijatelné.
    """
    with Session() as s:
        image_bytes = photos_repo.fetch_image_bytes(s, photo_id)
    if not image_bytes:
        raise RuntimeError("Foto nenalezeno nebo prázdné.")
    pipeline = create_default_pipeline(config)
    return pipeline.process(image_bytes)


def _format_detail(meta: PhotoMetadata) -> str:
    lines = [
        f"<b>Run:</b> {meta.run_id} &nbsp; "
        f"<b>Checkpoint:</b> {meta.checkpoint_name or '—'} &nbsp; "
        f"<b>Kamera:</b> {meta.camera_source} &nbsp; "
        f"<b>OCR:</b> {meta.ocr_status}"
    ]
    if meta.detections:
        lines.append("<br><b>Detekce</b> (klikni pro SPZ detail):")
        for d in meta.detections:
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
    return "<br>".join(lines)


def _pct(v) -> str:  # noqa: ANN001
    if v is None:
        return "—"
    return f"{v * 100:.0f} %"


__all__ = ["PhotoDetailDialog"]
