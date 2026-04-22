"""OCR Worker — background QThread zpracovávající `photos.ocr_status='pending'`.

Polluje DB s FOR UPDATE SKIP LOCKED → zpracuje OcrPipeline → INSERT plate_detections →
UPDATE photos.ocr_status='done'. Při startu aplikace vyčistí zombie řádky.

Emituje Qt signály `photo_processed(photo_id, list[Detection])` a
`photo_failed(photo_id, str)` pro UI updatesi.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, Signal

from spot_operator.constants import (
    OCR_POLL_INTERVAL_SEC,
    OCR_WORKER_ID_PREFIX,
    OCR_ZOMBIE_TIMEOUT_MIN,
)
from spot_operator.db.engine import Session
from spot_operator.db.repositories import detections_repo, photos_repo
from spot_operator.logging_config import get_logger
from spot_operator.ocr.pipeline import OcrPipeline

if TYPE_CHECKING:
    from spot_operator.ocr.dtos import Detection

_log = get_logger(__name__)


def _generate_worker_id() -> str:
    return f"{OCR_WORKER_ID_PREFIX}-{os.getpid()}"


class OcrWorker(QThread):
    """Background OCR worker. Spusť `start()`, zastav `request_stop()` + `wait()`."""

    photo_processed = Signal(int, int)  # photo_id, počet detekcí
    photo_failed = Signal(int, str)  # photo_id, důvod

    def __init__(self, pipeline: OcrPipeline, parent=None):
        super().__init__(parent)
        self._pipeline = pipeline
        self._stop = False
        self._worker_id = _generate_worker_id()

    def request_stop(self) -> None:
        self._stop = True

    def sweep_zombies_now(self) -> None:
        """Resetuje zaseknuté 'processing' řádky. Volá se při startu."""
        with Session() as s:
            count = photos_repo.sweep_zombies(s, timeout_minutes=OCR_ZOMBIE_TIMEOUT_MIN)
            s.commit()
        if count:
            _log.info("OCR zombie sweep reset %d photo(s) to 'pending'", count)

    def run(self) -> None:  # noqa: D401 - Qt runner
        _log.info("OCR worker started (id=%s)", self._worker_id)
        try:
            self._pipeline.warmup()
        except Exception as exc:
            _log.exception("OCR pipeline warmup failed: %s", exc)
            # Pokračujeme — lazy load se pokusí znovu v process().

        self.sweep_zombies_now()

        while not self._stop:
            try:
                processed = self._claim_and_process_one()
                if not processed:
                    time.sleep(OCR_POLL_INTERVAL_SEC)
            except Exception as exc:
                _log.exception("OCR worker loop error: %s", exc)
                time.sleep(2.0)

        _log.info("OCR worker stopped (id=%s)", self._worker_id)

    def _claim_and_process_one(self) -> bool:
        """Najde + claimne jednu fotku + zpracuje. Vrátí True/False."""
        photo_id: int | None = None
        image_bytes: bytes = b""

        with Session() as s:
            photo = photos_repo.claim_next_pending(s, self._worker_id)
            if photo is None:
                s.rollback()
                return False
            photo_id = photo.id
            image_bytes = photo.image_bytes
            s.commit()

        assert photo_id is not None
        try:
            detections = self._pipeline.process(image_bytes)
            self._store_results(photo_id, detections)
            self.photo_processed.emit(photo_id, len(detections))
            _log.debug("OCR done photo=%s detections=%d", photo_id, len(detections))
            return True
        except Exception as exc:
            _log.exception("OCR failed on photo %s: %s", photo_id, exc)
            with Session() as s:
                photos_repo.mark_failed(s, photo_id)
                s.commit()
            self.photo_failed.emit(photo_id, str(exc))
            return True

    def _store_results(self, photo_id: int, detections: "list[Detection]") -> None:
        with Session() as s:
            if detections:
                rows = [d.to_db_row(photo_id) for d in detections]
                detections_repo.insert_many(s, rows)
            photos_repo.mark_done(s, photo_id)
            s.commit()


__all__ = ["OcrWorker"]
