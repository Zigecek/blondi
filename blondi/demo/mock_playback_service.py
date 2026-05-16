"""MockPlaybackService — demo náhrada za PlaybackService.

Stejné Qt signály, ale autonomní průjezd je simulovaný timerem. UI pak
postupně dostane events ``map_uploaded`` → ``localized`` → ``checkpoint_reached``
→ ``run_completed`` (nebo ``run_failed`` v demo error variantě).

Pro stabilní screenshoty stavů má **demo pause flag** — UI v PlaybackRunPage
aktivuje skryté tlačítko "Demo: další stav" které manuálně advancuje
state machine.
"""

from __future__ import annotations

import random
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from blondi.db.engine import Session
from blondi.db.enums import OcrStatus, RunStatus
from blondi.db.models import Photo, PlateDetection
from blondi.db.repositories import runs_repo
from blondi.logging_config import get_logger
from blondi.services.contracts import (
    CAPTURE_STATUS_OK,
    CheckpointResult,
    build_checkpoint_result,
    checkpoint_results_to_payload,
    parse_checkpoint_plan,
)
from blondi.services.map_storage import MapMetadata, read_map_metadata

_log = get_logger(__name__)

# Pool českých SPZ pro fake detekce v playback runu — sjednoceno se seed.py
# aby detection.plate_text matched license_plates.plate_text.
_DEMO_PLATE_POOL = [
    "1A1 2345", "2B2 3456", "3C3 4567", "4D4 5678", "5E5 6789",
    "6F6 7890", "7G7 8901", "8H8 9012", "9J9 0123", "1K1 1234",
    "3M3 3456", "5O5 5678", "7R7 7890", "1U1 0123", "5Z5 4567",
]


def _load_demo_jpeg(side: str) -> tuple[bytes, int, int]:
    """Načte JPEG bytes z ``demo/<side>.jpg`` (left / right / front)."""
    from PySide6.QtGui import QPixmap

    project_root = Path(__file__).resolve().parent.parent.parent
    asset = project_root / "demo" / f"{side}.jpg"
    if asset.is_file():
        raw = asset.read_bytes()
        pix = QPixmap(str(asset))
        if not pix.isNull() and raw:
            return raw, pix.width(), pix.height()
    return b"\xff\xd8\xff\xd9", 640, 480  # minimální empty JPEG fallback


def _persist_demo_photo(
    *,
    run_id: int,
    checkpoint_name: str,
    camera_source: str,
    rng: random.Random,
) -> None:
    """Vloží do DB Photo + 1-2 PlateDetection pro daný checkpoint.

    Používá ``demo/left.jpg`` nebo ``demo/right.jpg`` jako image_bytes podle
    camera_source. Detekce vybírá ze ``_DEMO_PLATE_POOL`` (různé SPZ).
    """
    side = "left" if "left" in camera_source.lower() else "right"
    raw, width, height = _load_demo_jpeg(side)
    captured = datetime.now(timezone.utc)
    with Session() as s:
        photo = Photo(
            run_id=run_id,
            checkpoint_name=checkpoint_name,
            camera_source=camera_source,
            image_bytes=raw,
            image_mime="image/jpeg",
            width=width,
            height=height,
            captured_at=captured,
            ocr_status=OcrStatus.done,
            ocr_processed_at=captured,
        )
        s.add(photo)
        s.flush()
        photo_id = photo.id

        # 1-2 unikátní SPZ.
        n_det = rng.choice([1, 1, 2])
        chosen = rng.sample(_DEMO_PLATE_POOL, n_det)
        for plate_text in chosen:
            text_conf = round(rng.uniform(0.65, 0.95), 3)
            det_conf = round(rng.uniform(0.55, 0.92), 3)
            bbox = {
                "x": rng.randint(50, 200),
                "y": rng.randint(80, 250),
                "w": rng.randint(180, 260),
                "h": rng.randint(50, 80),
            }
            s.add(
                PlateDetection(
                    photo_id=photo_id,
                    plate_text=plate_text,
                    text_confidence=text_conf,
                    detection_confidence=det_conf,
                    bbox=bbox,
                    engine_name="european-plates-mobile-vit-v2",
                    engine_version="1.0",
                )
            )
        s.commit()


class MockPlaybackService(QObject):
    """Demo PlaybackService — emituje stejné signály jako reálná verze."""

    run_started = Signal(int)
    map_uploaded = Signal()
    localized = Signal()
    checkpoint_reached = Signal(int, int, str)
    photo_taken = Signal(int, str)
    run_completed = Signal(int, int)
    run_failed = Signal(str)
    progress = Signal(str)
    drift_detected = Signal(str, str, str)
    avoidance_failed = Signal(str)
    obstacle_detected = Signal(str, str)

    def __init__(self, bundle: Any, parent: QObject | None = None):
        super().__init__(parent)
        self._bundle = bundle
        self._run_id: Optional[int] = None
        self._abort_requested = False
        self._meta: Optional[MapMetadata] = None
        self._last_run_status: Optional[RunStatus] = None
        self._last_abort_reason: Optional[str] = None
        # Pro demo run_failed forced flag — UI ho přepne pro screenshot 09b.
        self.demo_force_fail: bool = False

    # --- Public API kompatibilní s PlaybackService ---

    @property
    def navigator(self) -> Any:
        return None

    @property
    def run_id(self) -> int | None:
        return self._run_id

    @property
    def last_run_status(self) -> RunStatus | None:
        return self._last_run_status

    @property
    def last_abort_reason(self) -> str | None:
        return self._last_abort_reason

    def request_abort(self) -> None:
        self._abort_requested = True
        _log.info("MockPlaybackService.request_abort")

    def resume_after_obstacle(self) -> None:
        pass

    def cancel_after_obstacle(self) -> None:
        self._abort_requested = True

    def request_return_home(self) -> None:
        self.request_abort()

    def upload_map_only(self, map_id: int) -> MapMetadata:
        self._emit_progress("Načítám mapu z databáze...")
        time.sleep(0.4)
        meta = read_map_metadata(map_id)
        if meta is None:
            raise RuntimeError(f"Mapa id={map_id} nenalezena v DB.")
        self._emit_progress("Uploaduji mapu do robota...")
        time.sleep(0.6)
        self.map_uploaded.emit()
        return meta

    def localize_on_map(self, meta: MapMetadata) -> None:
        self._emit_progress("Lokalizuji robota podle fiducialu...")
        time.sleep(0.4)
        self.localized.emit()

    def prepare_map(self, map_id: int) -> MapMetadata:
        meta = self.upload_map_only(map_id)
        self.localize_on_map(meta)
        return meta

    def run_all_checkpoints(
        self, meta: MapMetadata, *, operator_label: str | None
    ) -> int:
        """Simulovaný průjezd checkpointů. Vytvoří skutečný run row v DB
        aby playback result page mělo co zobrazit.
        """
        self._abort_requested = False
        self._last_run_status = RunStatus.running
        self._last_abort_reason = None
        self._meta = meta
        checkpoint_results: list[CheckpointResult] = []

        # Vyextrahuj checkpointy z mapy.
        try:
            plan = parse_checkpoint_plan(
                meta.checkpoints_json,
                fallback_map_name=meta.name,
                fallback_start_waypoint_id=meta.start_waypoint_id,
                fallback_default_capture_sources=meta.default_capture_sources,
                fallback_fiducial_id=meta.fiducial_id,
            )
            checkpoints = plan.checkpoints
        except Exception as exc:
            _log.warning("MockPlayback: nelze parsovat checkpointy: %s", exc)
            checkpoints = ()

        total = len(checkpoints) or (meta.checkpoints_count or 5)

        # Vytvoř run row v DB.
        with Session() as s:
            run_code = runs_repo.generate_unique_run_code(s)
            run = runs_repo.create(
                s,
                run_code=run_code,
                map_id=meta.id,
                map_name_snapshot=meta.name,
                checkpoints_total=total,
                operator_label=operator_label or "demo",
                start_waypoint_id=meta.start_waypoint_id,
                checkpoint_results_json=[],
            )
            s.commit()
            self._run_id = run.id

        self.run_started.emit(self._run_id)
        self._emit_progress(f"▶ Run id={self._run_id}")

        success = 0
        abort_reason: Optional[str] = None
        rng = random.Random(self._run_id or 0)
        # Default capture sources pro fallback (když cp.capture_sources je prázdný).
        default_sources = list(meta.default_capture_sources) or [
            "left_fisheye_image",
            "right_fisheye_image",
        ]

        for idx, cp in enumerate(checkpoints, start=1):
            if self._abort_requested:
                abort_reason = "Aborted by user"
                break
            if self.demo_force_fail and idx >= max(1, total // 2):
                abort_reason = (
                    "RobotLostError: ztratil GraphNav lokalizaci v demo simulaci"
                )
                self._emit_progress(f"⚠ {abort_reason}")
                break
            self.checkpoint_reached.emit(idx, total, cp.name)
            self._emit_progress(f"→ {idx}/{total}: {cp.name}")
            time.sleep(0.6)
            # Skutečně ulož foto + detekce do DB.
            sources = list(cp.capture_sources) or default_sources
            for src in sources:
                try:
                    _persist_demo_photo(
                        run_id=self._run_id,
                        checkpoint_name=cp.name,
                        camera_source=src,
                        rng=rng,
                    )
                    self.photo_taken.emit(self._run_id, src)
                except Exception as exc:
                    _log.warning(
                        "MockPlayback: ulozeni demo fotky pro %s/%s selhalo: %s",
                        cp.name, src, exc,
                    )
                time.sleep(0.05)
            started = datetime.now(timezone.utc)
            checkpoint_results.append(
                build_checkpoint_result(
                    name=cp.name,
                    waypoint_id=cp.waypoint_id,
                    nav_outcome="reached",
                    capture_status=CAPTURE_STATUS_OK,
                    expected_sources=cp.capture_sources or tuple(default_sources),
                    saved_sources=cp.capture_sources or tuple(default_sources),
                    failed_sources=(),
                    error=None,
                    started_at=started,
                    finished_at=datetime.now(timezone.utc),
                )
            )
            success += 1

        # Pokud žádné checkpointy v mapě, jen předstírej úspěch + ulož 1 foto na CP.
        if not checkpoints:
            for idx in range(1, total + 1):
                if self._abort_requested:
                    break
                cp_name = f"CP_{idx:03d}"
                self.checkpoint_reached.emit(idx, total, cp_name)
                self._emit_progress(f"→ {idx}/{total}: {cp_name}")
                for src in default_sources:
                    try:
                        _persist_demo_photo(
                            run_id=self._run_id,
                            checkpoint_name=cp_name,
                            camera_source=src,
                            rng=rng,
                        )
                        self.photo_taken.emit(self._run_id, src)
                    except Exception as exc:
                        _log.warning(
                            "MockPlayback fallback: ulozeni demo fotky selhalo: %s",
                            exc,
                        )
                time.sleep(0.5)
                success += 1

        if abort_reason:
            final_status = (
                RunStatus.aborted if abort_reason == "Aborted by user" else RunStatus.failed
            )
        elif success >= total:
            final_status = RunStatus.completed
        else:
            final_status = RunStatus.partial

        self._last_run_status = final_status
        self._last_abort_reason = abort_reason

        with Session() as s:
            runs_repo.finish(
                s,
                self._run_id,
                status=final_status,
                checkpoints_reached=success,
                abort_reason=abort_reason,
                checkpoint_results_json=checkpoint_results_to_payload(checkpoint_results),
            )
            s.commit()

        if final_status == RunStatus.completed:
            self.run_completed.emit(success, total)
        else:
            self.run_failed.emit(abort_reason or final_status.value)

        return self._run_id

    def return_home(self, start_wp_id: str):
        self._emit_progress("Návrat domů (demo)...")
        time.sleep(1.5)

        class _FakeResult:
            ok = True
            outcome = type("_", (), {"value": "reached"})()
            message = "demo OK"

        return _FakeResult()

    def cleanup(self) -> None:
        pass

    def _emit_progress(self, text: str) -> None:
        _log.info(text)
        self.progress.emit(text)


__all__ = ["MockPlaybackService"]
