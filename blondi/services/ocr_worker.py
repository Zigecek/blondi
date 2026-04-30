"""OCR Worker — background QThread zpracovávající `photos.ocr_status='pending'`.

Polluje DB s FOR UPDATE SKIP LOCKED → zpracuje OcrPipeline → INSERT plate_detections →
UPDATE photos.ocr_status='done'. Při startu aplikace vyčistí zombie řádky.

Emituje Qt signály `photo_processed(photo_id, počet detekcí)` a
`photo_failed(photo_id, str)` pro UI updatesi. Navíc `worker_disabled(str)`
pokud dojde k permanent failure (missing YOLO model, missing module)
— UI pak zobrazí status "OCR permanently disabled".
"""

from __future__ import annotations

import os
import threading
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, Signal

from blondi.constants import (
    OCR_POLL_INTERVAL_SEC,
    OCR_WORKER_ID_PREFIX,
    OCR_ZOMBIE_TIMEOUT_MIN,
)
from blondi.db.engine import Session
from blondi.db.repositories import detections_repo, photos_repo
from blondi.logging_config import get_logger
from blondi.ocr.pipeline import OcrPipeline

if TYPE_CHECKING:
    from blondi.ocr.dtos import Detection

_log = get_logger(__name__)

# Heartbeat interval během OCR process (s). Aktualizuje photos.ocr_locked_at,
# aby sweep_zombies nezresetoval fotku uprostřed pomalého OCR.
_HEARTBEAT_INTERVAL_S: float = 60.0
# Periodic sweep interval (s). Volá se mimo hot loop — v threading Timeru.
_PERIODIC_SWEEP_INTERVAL_S: float = 10 * 60.0


class PermanentOcrError(RuntimeError):
    """Neopravitelná chyba OCR workera (missing model file, missing module).

    Worker loop při této chybě emituje ``worker_disabled`` a skončí.
    """


def _generate_worker_id() -> str:
    return f"{OCR_WORKER_ID_PREFIX}-{os.getpid()}"


class OcrWorker(QThread):
    """Background OCR worker. Spusť `start()`, zastav `request_stop()` + `wait()`."""

    photo_processed = Signal(int, int)  # photo_id, počet detekcí
    photo_failed = Signal(int, str)  # photo_id, důvod
    # PR-06 FIND-106: signál pro UI, že OCR je permanentně disabled
    # (missing model, missing module, ...).
    worker_disabled = Signal(str)

    def __init__(self, pipeline: OcrPipeline, parent=None):
        super().__init__(parent)
        self._pipeline = pipeline
        self._worker_id = _generate_worker_id()
        # PR-06 FIND-117: threading.Event místo self._stop bool — umožňuje
        # rychle probudit worker z time.sleep backoff.
        self._stop_event = threading.Event()
        # Konsekutivní počet DB failů → exponential backoff, aby OCR worker
        # nespamoval log když je DB dočasně nedostupná (typicky když se
        # Windows připojí k Spot Wi-Fi a ztratí DNS pro externí DB host).
        self._db_fail_streak: int = 0
        self._last_db_error_key: str | None = None
        # Periodic sweep timer (spouští se v run()).
        self._sweep_timer: threading.Timer | None = None

    def request_stop(self) -> None:
        self._stop_event.set()

    @property
    def _stop(self) -> bool:
        return self._stop_event.is_set()

    def sweep_zombies_now(self) -> None:
        """Resetuje zaseknuté 'processing' řádky. Volá se při startu."""
        try:
            with Session() as s:
                count = photos_repo.sweep_zombies(
                    s, timeout_minutes=OCR_ZOMBIE_TIMEOUT_MIN
                )
                s.commit()
            if count:
                _log.info("OCR zombie sweep reset %d photo(s) to 'pending'", count)
        except Exception as exc:
            _log.warning("sweep_zombies failed: %s", exc)

    def _start_periodic_sweep(self) -> None:
        """Periodický sweep (každých X min) mimo hot loop (FIND-116)."""

        def _tick() -> None:
            if self._stop_event.is_set():
                return
            self.sweep_zombies_now()
            # Re-schedule.
            if not self._stop_event.is_set():
                self._sweep_timer = threading.Timer(
                    _PERIODIC_SWEEP_INTERVAL_S, _tick
                )
                self._sweep_timer.daemon = True
                self._sweep_timer.start()

        self._sweep_timer = threading.Timer(_PERIODIC_SWEEP_INTERVAL_S, _tick)
        self._sweep_timer.daemon = True
        self._sweep_timer.start()

    def run(self) -> None:  # noqa: D401 - Qt runner
        _log.info("OCR worker started (id=%s)", self._worker_id)
        try:
            try:
                self._pipeline.warmup()
            except (FileNotFoundError, ModuleNotFoundError) as exc:
                # PR-06 FIND-106: permanent failure — nedokážeme ani naloaderat
                # model. Emit worker_disabled a skonči.
                _log.exception("OCR pipeline warmup permanent failure: %s", exc)
                self.worker_disabled.emit(
                    f"OCR je vypnuté: {exc}. Zkontroluj OCR_YOLO_MODEL v .env."
                )
                return
            except Exception as exc:
                _log.exception("OCR pipeline warmup failed (transient?): %s", exc)
                # Pokračujeme — lazy load se pokusí znovu v process().

            self.sweep_zombies_now()
            self._start_periodic_sweep()

            while not self._stop_event.is_set():
                try:
                    processed = self._claim_and_process_one()
                    # Úspěch → resetuj streak.
                    self._db_fail_streak = 0
                    self._last_db_error_key = None
                    if not processed:
                        # Žádná pending fotka — počkej s možností rychlého ukončení.
                        if self._stop_event.wait(timeout=OCR_POLL_INTERVAL_SEC):
                            return
                except PermanentOcrError as exc:
                    _log.error("OCR worker permanent error: %s", exc)
                    self.worker_disabled.emit(str(exc))
                    return
                except Exception as exc:
                    self._handle_loop_error(exc)
        finally:
            # Stop periodic sweep timer.
            if self._sweep_timer is not None:
                try:
                    self._sweep_timer.cancel()
                except Exception:
                    pass
                self._sweep_timer = None
            # PR-07 FIND-015: remove thread-local session.
            try:
                from blondi.db.engine import thread_local_session_remove

                thread_local_session_remove()
            except Exception:
                pass
            _log.info("OCR worker stopped (id=%s)", self._worker_id)

    def _handle_loop_error(self, exc: Exception) -> None:
        """Exponential backoff + dedup logování pro opakující se DB chyby.

        Typická situace: Windows se připojil na Spot Wi-Fi a přišel o DNS pro
        externí DB host ("failed to resolve host 'kozohorsky.com'"). Bez
        backoff tenhle fail spamuje log desítkami stejných tracebacků za sekundu.
        """
        self._db_fail_streak += 1
        key = f"{exc.__class__.__name__}:{str(exc)[:120]}"
        is_new_error = key != self._last_db_error_key
        self._last_db_error_key = key

        if is_new_error or self._db_fail_streak == 1:
            # První výskyt → plný log s tracebackem.
            _log.exception(
                "OCR worker loop error (#%d): %s", self._db_fail_streak, exc
            )
        elif self._db_fail_streak % 30 == 0:
            # Každý 30. opakovaný fail → jen stručná zpráva (dedup).
            _log.warning(
                "OCR worker still failing (#%d, same error): %s",
                self._db_fail_streak,
                key,
            )
        # Exponential backoff: 2s, 4s, 8s, ... max 60s.
        # PR-06 FIND-117: Event.wait místo time.sleep → rychlé probuzení na stop.
        wait_s = min(2.0 * (2 ** min(self._db_fail_streak - 1, 5)), 60.0)
        self._stop_event.wait(timeout=wait_s)

    def _claim_and_process_one(self) -> bool:
        """Najde + claimne jednu fotku + zpracuje. Vrátí True/False.

        State machine:
        1. Session 1 (claim): SELECT FOR UPDATE SKIP LOCKED → mark processing +
           ocr_locked_by. Commit. Pokud None → return False.
        2. Mimo session: pipeline.process(bytes) → detekce.
           Během running běží heartbeat thread, který každých 60 s obnovuje
           ocr_locked_at (proti zombie sweep).
        3. Session 2: mark_done + insert_many detekce. Commit.
        4. Při exception v kroku 2 nebo 3 → Session 3: mark_failed. Commit.
        Zombie recovery: pokud sestup kroku 3 nebo 4 padne (DB outage),
        photo zůstává v 'processing' s ocr_locked_at. Periodic sweep po
        OCR_ZOMBIE_TIMEOUT_MIN min reset na 'pending'.
        """
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

        # PR-06 FIND-107: raise místo assert (v -O mode by skipl).
        if photo_id is None:
            raise RuntimeError("photo_id unexpectedly None after claim")

        _log.info(
            "OCR worker claimed photo=%s (%d bytes)", photo_id, len(image_bytes)
        )

        # Spusť heartbeat thread během pipeline.process (FIND-026).
        heartbeat_stop = threading.Event()
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(photo_id, heartbeat_stop),
            daemon=True,
            name=f"ocr-heartbeat-{photo_id}",
        )
        heartbeat_thread.start()

        try:
            try:
                detections = self._pipeline.process(image_bytes)
            except (FileNotFoundError, ModuleNotFoundError) as exc:
                # Permanent: model file nebo module chybí.
                raise PermanentOcrError(
                    f"OCR pipeline permanent failure: {exc}"
                ) from exc
            self._store_results(photo_id, detections)
            self.photo_processed.emit(photo_id, len(detections))
            _log.info("OCR worker done photo=%s detections=%d", photo_id, len(detections))
            return True
        except PermanentOcrError:
            # Propagate — worker loop to chytne a skončí.
            raise
        except Exception as exc:
            _log.exception("OCR failed on photo %s: %s", photo_id, exc)
            try:
                with Session() as s:
                    photos_repo.mark_failed(s, photo_id)
                    s.commit()
            except Exception as mark_exc:
                _log.warning(
                    "Nelze mark_failed pro photo %s: %s — zombie sweep ji uklidí.",
                    photo_id, mark_exc,
                )
            self.photo_failed.emit(photo_id, str(exc))
            return True
        finally:
            heartbeat_stop.set()
            # Krátký join — heartbeat má sleep max 60s, ale nejlépe pustit
            # garbage collectoru.
            heartbeat_thread.join(timeout=2.0)

    def _heartbeat_loop(self, photo_id: int, stop: threading.Event) -> None:
        """Každých HEARTBEAT_INTERVAL s obnoví ``ocr_locked_at`` pro photo.

        Zabrání sweep_zombies v resetu photo → double OCR na stejné fotce.
        """
        while not stop.wait(timeout=_HEARTBEAT_INTERVAL_S):
            try:
                with Session() as s:
                    photos_repo.record_heartbeat(s, photo_id, self._worker_id)
                    s.commit()
            except Exception as exc:
                _log.debug("Heartbeat failed for photo %s: %s", photo_id, exc)

    def _store_results(self, photo_id: int, detections: "list[Detection]") -> None:
        with Session() as s:
            engine_name = getattr(self._pipeline, "engine_name", "ocr")
            detections_repo.delete_for_photo_engine(s, photo_id, engine_name)
            if detections:
                rows = [d.to_db_row(photo_id) for d in detections]
                detections_repo.insert_many(s, rows)
            photos_repo.mark_done(s, photo_id)
            s.commit()


__all__ = ["OcrWorker", "PermanentOcrError"]
