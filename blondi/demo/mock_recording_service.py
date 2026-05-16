"""MockRecordingService — demo náhrada za RecordingService.

Stejné API jako ``blondi.services.recording_service.RecordingService``, ale
neinteraguje se Spot SDK ani neukládá GraphNav data — vše je fake. Při
``save_snapshot_to_db`` skutečně vloží Map row do DB s dummy ZIP archivem,
aby playback wizard pak měl co vybrat.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from blondi.constants import TEMP_ROOT
from blondi.logging_config import get_logger
from blondi.services.contracts import (
    CAPTURE_STATUS_OK,
    CaptureNote,
    build_checkpoint_plan_payload,
)
from blondi.services.recording_service import (
    RecordedCheckpoint,
    RecordingSnapshot,
)

_log = get_logger(__name__)


class MockRecordingService:
    """Drop-in náhrada za RecordingService pro demo režim.

    Atributy / metody zachovávají signaturu reálné třídy:
    ``is_recording``, ``checkpoint_count``, ``waypoint_count``, ``photo_count``,
    ``start_waypoint_id``, ``start()``, ``add_unnamed_waypoint()``,
    ``capture_and_record_checkpoint()``, ``stop_and_export()``,
    ``save_snapshot_to_db()``, ``stop_and_archive_to_db()``, ``abort()``.
    """

    def __init__(self, bundle: Any):
        self._bundle = bundle
        self._is_recording = False
        self._checkpoints: list[RecordedCheckpoint] = []
        self._start_waypoint_id: Optional[str] = None
        self._fiducial_id: Optional[int] = None
        self._default_capture_sources: list[str] = []
        self._waypoint_counter = 0
        self._checkpoint_counter = 0

    @property
    def checkpoint_count(self) -> int:
        return sum(1 for c in self._checkpoints if c.kind == "checkpoint")

    @property
    def waypoint_count(self) -> int:
        return len(self._checkpoints)

    @property
    def photo_count(self) -> int:
        return sum(len(c.photos) for c in self._checkpoints)

    @property
    def start_waypoint_id(self) -> Optional[str]:
        return self._start_waypoint_id

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    def start(
        self,
        *,
        map_name_prefix: str,
        default_capture_sources: list[str],
        fiducial_id: Optional[int],
    ) -> None:
        if self._is_recording:
            raise RuntimeError("Recording already in progress (mock).")
        self._checkpoints.clear()
        self._start_waypoint_id = None
        self._waypoint_counter = 0
        self._checkpoint_counter = 0
        self._default_capture_sources = list(default_capture_sources)
        self._fiducial_id = fiducial_id
        self._is_recording = True
        _log.info(
            "MockRecordingService.start prefix=%s sources=%s fiducial=%s",
            map_name_prefix,
            default_capture_sources,
            fiducial_id,
        )

    def add_unnamed_waypoint(self) -> RecordedCheckpoint:
        self._waypoint_counter += 1
        name = f"WP_{self._waypoint_counter:03d}"
        wp_id = f"demo_wp_{self._waypoint_counter:03d}"
        if self._start_waypoint_id is None:
            self._start_waypoint_id = wp_id
        cp = RecordedCheckpoint(
            name=name,
            waypoint_id=wp_id,
            kind="waypoint",
            capture_sources=[],
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._checkpoints.append(cp)
        return cp

    def capture_and_record_checkpoint(
        self,
        sources: list[str],
        *,
        image_poller: Any = None,
        jpeg_quality: int = 85,
    ) -> RecordedCheckpoint:
        """Fake checkpoint — neukládá reálné fotky, jen metadata.

        Photos slot necháme prázdný (demo seed pak doplní vlastní fake fotky
        při save).
        """
        from blondi.demo.live_view_stub import compose_single

        self._checkpoint_counter += 1
        name = f"CP_{self._checkpoint_counter:03d}"
        wp_id = f"demo_cp_{self._checkpoint_counter:03d}"
        if self._start_waypoint_id is None:
            self._start_waypoint_id = wp_id

        # Vyrob jeden fake JPEG pro každý source z left/right placeholder.
        photos: list[tuple[str, bytes, int, int]] = []
        for src in sources:
            pix = compose_single("left" if "left" in src.lower() else "right")
            jpeg_bytes = _pixmap_to_jpeg(pix)
            photos.append((src, jpeg_bytes, pix.width(), pix.height()))

        cp = RecordedCheckpoint(
            name=name,
            waypoint_id=wp_id,
            kind="checkpoint",
            capture_sources=list(sources),
            photos=photos,
            capture_status=CAPTURE_STATUS_OK,
            saved_sources=list(sources),
            failed_sources=[],
            note=CaptureNote.OK.value,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._checkpoints.append(cp)
        return cp

    def stop_and_export(
        self,
        *,
        end_fiducial_id: Optional[int] = None,
    ) -> RecordingSnapshot:
        if not self._is_recording:
            raise RuntimeError("No recording in progress (mock).")
        # Krátká prodleva pro UI feedback "Stahuji mapu...".
        time.sleep(1.0)
        TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        tmp_root = Path(tempfile.mkdtemp(prefix="rec_demo_", dir=str(TEMP_ROOT)))
        # Vytvoř minimální fake GraphNav layout — prázdné soubory aby
        # validate_map_dir prošel (alespoň pro demo).
        _create_fake_graphnav_layout(tmp_root, self._checkpoints)

        self._is_recording = False
        effective_fiducial_id = self._fiducial_id or end_fiducial_id

        snapshot = RecordingSnapshot(
            temp_dir=tmp_root,
            checkpoints=tuple(self._checkpoints),
            start_waypoint_id=self._start_waypoint_id,
            effective_fiducial_id=effective_fiducial_id,
            default_capture_sources=tuple(self._default_capture_sources),
            checkpoint_count=self.checkpoint_count,
        )
        _log.info(
            "MockRecordingService.stop_and_export: %d checkpoints, fiducial=%s",
            self.checkpoint_count,
            effective_fiducial_id,
        )
        return snapshot

    def save_snapshot_to_db(
        self,
        snapshot: RecordingSnapshot,
        *,
        map_name: str,
        note: str,
        operator_label: str | None,
    ) -> int:
        """Skutečně uloží mapu do DB — UI pak v MapSelectPage uvidí novou položku.

        Bypassuje ``save_map_to_db`` z map_storage (ten validuje skutečnou
        GraphNav strukturu); místo toho si sami vytvoříme dummy ZIP a vložíme
        do ``maps_repo.create``.
        """
        import hashlib

        from blondi.db.engine import Session
        from blondi.db.repositories import maps_repo
        from blondi.services.map_archiver import zip_map_dir

        checkpoints_json = build_checkpoint_plan_payload(
            map_name=map_name,
            start_waypoint_id=snapshot.start_waypoint_id,
            fiducial_id=snapshot.effective_fiducial_id,
            default_capture_sources=snapshot.default_capture_sources,
            checkpoints=snapshot.checkpoints,
        )
        try:
            archive, sha = zip_map_dir(snapshot.temp_dir)
        except Exception as exc:
            _log.warning("zip_map_dir failed v demo: %s — vyrobím dummy ZIP", exc)
            archive = b"PK\x05\x06" + b"\x00" * 18
            sha = hashlib.sha256(archive).hexdigest()

        with Session() as s:
            if maps_repo.exists_by_name(s, map_name):
                raise RuntimeError(f"Mapa s názvem {map_name!r} už v DB existuje.")
            m = maps_repo.create(
                s,
                name=map_name,
                archive_bytes=archive,
                archive_sha256=sha,
                archive_size_bytes=len(archive),
                fiducial_id=snapshot.effective_fiducial_id,
                start_waypoint_id=snapshot.start_waypoint_id,
                default_capture_sources=list(snapshot.default_capture_sources),
                checkpoints_json=checkpoints_json,
                metadata_version=2,
                archive_is_valid=True,
                archive_validation_error=None,
                waypoints_count=len(snapshot.checkpoints),
                checkpoints_count=snapshot.checkpoint_count,
                note=note or "(demo)",
                created_by_operator=operator_label or "demo",
            )
            s.commit()
            map_id = m.id

        snapshot.release_temp()
        _log.info("MockRecordingService.save_snapshot_to_db: map_id=%d", map_id)
        return map_id

    def stop_and_archive_to_db(
        self,
        *,
        map_name: str,
        note: str,
        operator_label: str | None,
        end_fiducial_id: Optional[int],
    ) -> int:
        snapshot = self.stop_and_export(end_fiducial_id=end_fiducial_id)
        try:
            return self.save_snapshot_to_db(
                snapshot,
                map_name=map_name,
                note=note,
                operator_label=operator_label,
            )
        except Exception:
            snapshot.release_temp()
            raise

    def abort(self) -> None:
        if self._is_recording:
            self._is_recording = False
        self._checkpoints.clear()
        self._start_waypoint_id = None

    @property
    def checkpoints(self) -> list[RecordedCheckpoint]:
        return list(self._checkpoints)


def _pixmap_to_jpeg(pix) -> bytes:
    """Konvertuje QPixmap na JPEG bytes (pro demo fotky)."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    pix.save(buf, "JPEG", 85)
    buf.close()
    return bytes(ba.data())


def _create_fake_graphnav_layout(target: Path, checkpoints: list[RecordedCheckpoint]) -> None:
    """Vytvoří minimální fake GraphNav adresářovou strukturu v ``target``.

    Skutečná GraphNav obsahuje ``graph`` proto + ``waypoint_snapshots/`` adresář.
    Pro demo postačí dummy soubory — validate_map_dir může selhat, proto je
    save_snapshot_to_db v MockRecordingService bypass.
    """
    target.mkdir(parents=True, exist_ok=True)
    (target / "graph").write_bytes(b"\x00")
    snapshots_dir = target / "waypoint_snapshots"
    snapshots_dir.mkdir(exist_ok=True)
    for cp in checkpoints:
        (snapshots_dir / f"{cp.waypoint_id}.snapshot").write_bytes(b"\x00")


__all__ = ["MockRecordingService"]
