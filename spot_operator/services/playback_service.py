"""Playback service — autonomní projetí mapy s focením a zápisem do DB.

Emituje Qt signály pro UI:
  - run_started(run_id)
  - map_uploaded()
  - localized()
  - checkpoint_reached(index, total, name)
  - photo_taken(photo_id, source)
  - run_completed(success_count, total)
  - run_failed(reason)
  - progress(text)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import QObject, QThread, Signal

from spot_operator.constants import (
    PLAYBACK_NAV_TIMEOUT_SEC,
    PLAYBACK_RETURN_HOME_TIMEOUT_SEC,
    TEMP_ROOT,
)
from spot_operator.db.engine import Session
from spot_operator.db.enums import RunStatus
from spot_operator.db.repositories import runs_repo
from spot_operator.logging_config import get_logger
from spot_operator.services.map_storage import MapMetadata, load_map_to_temp
from spot_operator.services.photo_sink import save_photo_to_db

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CheckpointRef:
    """Zjednodušený odkaz na checkpoint (ze mapy v DB)."""

    name: str
    waypoint_id: str
    kind: str
    capture_sources: list[str]


class PlaybackService(QObject):
    """Orchestruje playback. UI spouští v QThread přes start()."""

    run_started = Signal(int)  # run_id
    map_uploaded = Signal()
    localized = Signal()
    checkpoint_reached = Signal(int, int, str)  # index, total, name
    photo_taken = Signal(int, str)  # photo_id, source
    run_completed = Signal(int, int)  # success, total
    run_failed = Signal(str)
    progress = Signal(str)

    def __init__(self, bundle: Any, parent: QObject | None = None):
        super().__init__(parent)
        self._bundle = bundle

        from app.robot.graphnav_navigation import GraphNavNavigator
        from app.robot.images import ImagePoller

        self._navigator = GraphNavNavigator(bundle.session)
        self._poller = ImagePoller(bundle.session)
        self._run_id: Optional[int] = None
        self._abort_requested = False
        self._map_temp_dir: Optional[Path] = None

    @property
    def navigator(self) -> Any:
        return self._navigator

    def request_abort(self) -> None:
        self._abort_requested = True
        try:
            self._navigator.request_abort()
        except Exception as exc:
            _log.warning("navigator.request_abort failed: %s", exc)

    def request_return_home(self) -> None:
        """Požádá o návrat domů — běží asynchronně přes RunReturnHomeThread."""
        # Reálně spouštíme v samostatném threadu, protože navigate_to blokuje.
        self.request_abort()

    # ---- Hlavní orchestrace ----

    def prepare_map(self, map_id: int) -> MapMetadata:
        """Extrahuje mapu z DB, uploadne ji do robota, localize fiducial-nearest."""
        self._emit_progress("Načítám mapu z databáze...")
        map_dir, meta = load_map_to_temp(map_id, TEMP_ROOT)
        self._map_temp_dir = map_dir

        self._emit_progress("Uploaduji mapu do robota...")
        self._navigator.upload_map(map_dir)
        self.map_uploaded.emit()

        self._emit_progress("Lokalizuji robota podle fiducialu...")
        self._localize_with_fallback(meta)
        self.localized.emit()
        return meta

    def run_all_checkpoints(
        self, meta: MapMetadata, *, operator_label: str | None
    ) -> int:
        """Spustí autonomní průjezd checkpointů. Vrátí run_id."""
        checkpoints = self._extract_checkpoints(meta)
        if not checkpoints:
            raise RuntimeError("Mapa neobsahuje žádné checkpointy.")

        run_code = runs_repo.generate_run_code()
        with Session() as s:
            run = runs_repo.create(
                s,
                run_code=run_code,
                map_id=meta.id,
                map_name_snapshot=meta.name,
                checkpoints_total=len(checkpoints),
                operator_label=operator_label,
                start_waypoint_id=meta.start_waypoint_id,
            )
            s.commit()
            self._run_id = run.id
        self.run_started.emit(self._run_id)
        _log.info("Run %s created (map=%s, checkpoints=%d)", run_code, meta.name, len(checkpoints))

        success = 0
        total = len(checkpoints)
        abort_reason: Optional[str] = None

        for idx, cp in enumerate(checkpoints, start=1):
            if self._abort_requested:
                abort_reason = "Aborted by user"
                break
            try:
                self.checkpoint_reached.emit(idx, total, cp.name)
                self._emit_progress(f"Navigate to {cp.name} ({idx}/{total})")
                result = self._navigator.navigate_to(
                    cp.waypoint_id, timeout=PLAYBACK_NAV_TIMEOUT_SEC
                )
                if not result.ok:
                    abort_reason = f"navigate failed at {cp.name}: {result.message}"
                    _log.warning(abort_reason)
                    continue

                if cp.kind == "checkpoint" and cp.capture_sources:
                    self._capture_at_checkpoint(cp)

                success += 1
                with Session() as s:
                    runs_repo.mark_progress(s, self._run_id, success)
                    s.commit()
            except Exception as exc:
                _log.exception("Checkpoint %s failed: %s", cp.name, exc)
                abort_reason = f"exception at {cp.name}: {exc}"
                continue

        final_status = self._classify_final_status(success, total, abort_reason)
        with Session() as s:
            runs_repo.finish(
                s,
                self._run_id,
                status=final_status,
                checkpoints_reached=success,
                abort_reason=abort_reason,
            )
            s.commit()

        if final_status in (RunStatus.aborted, RunStatus.failed):
            self.run_failed.emit(abort_reason or final_status.value)
        else:
            self.run_completed.emit(success, total)

        return self._run_id

    def return_home(self, start_wp_id: str) -> None:
        """Volá return_home utilitu z autonomy."""
        from app.robot.return_home import return_home

        self._emit_progress("Návrat domů...")
        try:
            return_home(
                self._navigator,
                start_wp_id,
                timeout_s=PLAYBACK_RETURN_HOME_TIMEOUT_SEC,
                progress=self._emit_progress,
            )
        except Exception as exc:
            _log.exception("return_home failed: %s", exc)

    def cleanup(self) -> None:
        """Smaže temp extrahovanou mapu."""
        if self._map_temp_dir is not None:
            import shutil

            shutil.rmtree(self._map_temp_dir, ignore_errors=True)
            self._map_temp_dir = None

    # ---- Internal helpers ----

    def _localize_with_fallback(self, meta: MapMetadata) -> None:
        from app.models import LocalizationStrategy

        if meta.fiducial_id is not None:
            try:
                self._navigator.localize(
                    strategy=LocalizationStrategy.SPECIFIC_FIDUCIAL,
                    fiducial_id=meta.fiducial_id,
                )
                return
            except Exception as exc:
                _log.warning(
                    "Localize to specific fiducial %s failed: %s. Falling back to nearest.",
                    meta.fiducial_id,
                    exc,
                )
        self._navigator.localize(strategy=LocalizationStrategy.FIDUCIAL_NEAREST)

    def _extract_checkpoints(self, meta: MapMetadata) -> list[CheckpointRef]:
        data = meta.checkpoints_json or {}
        items = data.get("checkpoints") or []
        default_sources = meta.default_capture_sources or []
        out: list[CheckpointRef] = []
        for item in items:
            kind = item.get("kind", "checkpoint")
            out.append(
                CheckpointRef(
                    name=item.get("name", "?"),
                    waypoint_id=item.get("waypoint_id", ""),
                    kind=kind,
                    capture_sources=list(item.get("capture_sources") or default_sources),
                )
            )
        return [c for c in out if c.waypoint_id]

    def _capture_at_checkpoint(self, cp: CheckpointRef) -> None:
        from spot_operator.robot.dual_side_capture import capture_sources
        from spot_operator.services.photo_sink import encode_bgr_to_jpeg

        frames = capture_sources(self._poller, cp.capture_sources)
        for src, bgr in frames.items():
            try:
                jpeg, w, h = encode_bgr_to_jpeg(bgr)
                photo_id = save_photo_to_db(
                    run_id=self._run_id,
                    checkpoint_name=cp.name,
                    camera_source=src,
                    image_bytes=jpeg,
                    width=w,
                    height=h,
                )
                self.photo_taken.emit(photo_id, src)
            except Exception as exc:
                _log.warning("save photo failed (cp=%s src=%s): %s", cp.name, src, exc)

    def _classify_final_status(
        self, success: int, total: int, abort_reason: Optional[str]
    ) -> RunStatus:
        if abort_reason:
            if success == 0:
                return RunStatus.failed
            return RunStatus.aborted if "Aborted" in abort_reason else RunStatus.partial
        if success == total:
            return RunStatus.completed
        return RunStatus.partial

    def _emit_progress(self, text: str) -> None:
        _log.info(text)
        self.progress.emit(text)


__all__ = ["PlaybackService", "CheckpointRef"]
