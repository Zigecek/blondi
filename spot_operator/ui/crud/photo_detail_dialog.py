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
from PySide6.QtGui import QKeyEvent, QPixmap
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
        *,
        photo_ids: list[int] | None = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._config = config
        self._photo_id = photo_id
        # Navigace šipkami (problém 6) — list IDs v aktuálním pořadí tabulky.
        self._photo_ids: list[int] = list(photo_ids) if photo_ids else [photo_id]
        try:
            self._current_index = self._photo_ids.index(photo_id)
        except ValueError:
            self._photo_ids = [photo_id]
            self._current_index = 0
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

        # Navigace šipkami mezi fotkami (problém 6).
        self._btn_prev = QPushButton("◀ Předchozí")
        self._btn_prev.setToolTip("Předchozí fotka (←, PageUp)")
        self._btn_prev.clicked.connect(self._go_prev)
        action_row.addWidget(self._btn_prev)

        self._btn_next = QPushButton("Další ▶")
        self._btn_next.setToolTip("Další fotka (→, PageDown)")
        self._btn_next.clicked.connect(self._go_next)
        action_row.addWidget(self._btn_next)

        self._nav_label = QLabel("")
        self._nav_label.setStyleSheet("color:#666; padding: 0 8px;")
        action_row.addWidget(self._nav_label)

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

        # Non-modální status pro re-OCR výsledek — nahrazuje dřívější info_dialog,
        # který blokoval UI a komplikoval cleanup při rychlém zavření (problém 7).
        self._reocr_status = QLabel("")
        self._reocr_status.setTextFormat(Qt.RichText)
        self._reocr_status.setWordWrap(True)
        self._reocr_status.setVisible(False)
        root.addWidget(self._reocr_status)

        self._update_nav_ui()
        self._start_load()

    # ---- Lifecycle ----

    def closeEvent(self, event) -> None:  # noqa: ANN001
        # PR-12 (problém 7): re-OCR worker nejdřív explicit disconnect signálů,
        # pak stop_and_wait. Subprocess Nomeroff může běžet ~30 s — bez disconnectu
        # by finished_ok emitoval do právě zavíraného widgetu a crashnul.
        if self._reocr_worker is not None:
            try:
                self._reocr_worker.finished_ok.disconnect()
                self._reocr_worker.failed.disconnect()
            except (TypeError, RuntimeError):
                pass
            self._reocr_worker.stop_and_wait()
            self._reocr_worker = None
        for w in list(self._workers):
            w.stop_and_wait()
        self._workers.clear()
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
        self._reocr_status.setText(
            "<i>Spouštím Nomeroff subprocess (může trvat až 30 s)…</i>"
        )
        self._reocr_status.setVisible(True)
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
        self._reocr_status.setText(
            "<i>Spouštím fast-plate pipeline (YOLO + OCR)…</i>"
        )
        self._reocr_status.setVisible(True)
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
            # Non-modální status místo info_dialog — blokující dialog dřív komplikoval
            # cleanup při rychlém zavření hlavního dialogu (problém 7 crash).
            self._reocr_status.setText(
                f"<span style='color:#2e7d32;'>✓ Re-OCR hotové — engine "
                f"<b>{engine_name}</b>, detekcí: <b>{len(detections)}</b></span>"
            )
            self._reocr_status.setVisible(True)
        except Exception as exc:
            _log.warning("Re-OCR DB write failed: %s", exc)
            self._reocr_status.setText(
                f"<span style='color:#c0392b;'>✗ Chyba zápisu do DB: {exc}</span>"
            )
            self._reocr_status.setVisible(True)
        self._set_reocr_buttons_enabled(True)
        # Přenačti metadata (detekce se změnily) — nikoli obrázek. Pojistka:
        # pokud dialog mezitím přestal být visible, nezačíná další worker.
        if self.isVisible():
            self._load_metadata()

    def _on_reocr_failed(self, reason: str) -> None:
        self._reocr_worker = None
        if not self.isVisible():
            return
        self._set_reocr_buttons_enabled(True)
        # Non-modální status místo error_dialog (problém 7).
        self._reocr_status.setText(
            f"<span style='color:#c0392b;'>✗ Re-OCR selhalo: {reason}</span>"
        )
        self._reocr_status.setVisible(True)

    # ---- Navigace šipkami (problém 6) ----

    def _go_prev(self) -> None:
        if self._current_index > 0:
            self._current_index -= 1
            self._switch_photo(self._photo_ids[self._current_index])

    def _go_next(self) -> None:
        if self._current_index < len(self._photo_ids) - 1:
            self._current_index += 1
            self._switch_photo(self._photo_ids[self._current_index])

    def _switch_photo(self, photo_id: int) -> None:
        """Přepne dialog na jinou fotku — zastaví staré workery, vyčistí UI,
        načte nová data. Re-OCR běh blokuje přepnutí (viz _update_nav_ui)."""
        self._photo_id = photo_id
        self.setWindowTitle(f"Foto #{photo_id}")
        self._preview.setText("<i>Načítám fotku…</i>")
        self._preview.setPixmap(QPixmap())  # vyprázdní staré zobrazení
        self._detail_text.setText("<i>Načítám metadata…</i>")
        self._reocr_status.setVisible(False)
        if hasattr(self, "_original_pixmap"):
            del self._original_pixmap
        # Stop staré workery (metadata + bytes z předchozí fotky).
        for w in list(self._workers):
            try:
                w.stop_and_wait()
            except Exception:
                pass
        self._workers.clear()
        self._update_nav_ui()
        self._start_load()

    def _update_nav_ui(self) -> None:
        total = len(self._photo_ids)
        at_first = self._current_index <= 0
        at_last = self._current_index >= total - 1
        # Během re-OCR zamknout navigaci, aby přepnutí fotky nezpůsobilo
        # race s worker callbackem (_on_reocr_done pak zapisuje do jiné fotky).
        reocr_running = (
            self._reocr_worker is not None and self._reocr_worker.isRunning()
        )
        self._btn_prev.setEnabled(not at_first and not reocr_running)
        self._btn_next.setEnabled(not at_last and not reocr_running)
        if total > 1:
            self._nav_label.setText(f"{self._current_index + 1} / {total}")
        else:
            self._nav_label.setText("")

    def _set_reocr_buttons_enabled(self, enabled: bool) -> None:  # noqa: F811
        # Override — zohledni i navigační tlačítka během re-OCR.
        self._btn_reocr_fast_plate.setEnabled(enabled)
        self._btn_reocr_nomeroff.setEnabled(enabled)
        self._update_nav_ui()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: D401
        key = event.key()
        if key in (Qt.Key_Left, Qt.Key_PageUp):
            self._go_prev()
            return
        if key in (Qt.Key_Right, Qt.Key_PageDown):
            self._go_next()
            return
        super().keyPressEvent(event)


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
