"""Recording service — obaluje GraphNavRecorder z autonomy + waypoint namer + DB uložení.

Flow:
  1) start_recording() — spustí GraphNav recording.
  2) add_unnamed_waypoint() — WP_NNN bez fotky.
  3) capture_and_record_checkpoint(sources) — CP_NNN + capture fotek přes ImagePoller.
  4) stop_and_archive_to_db(name, ...) — stáhne mapu, zazipuje, uloží do DB, smaže temp.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from spot_operator.constants import TEMP_ROOT
from spot_operator.logging_config import get_logger
from spot_operator.services.contracts import (
    CAPTURE_STATUS_FAILED,
    CAPTURE_STATUS_NOT_APPLICABLE,
    CAPTURE_STATUS_OK,
    CAPTURE_STATUS_PARTIAL,
    CaptureFailedError,
    CaptureNote,
    build_checkpoint_plan_payload,
)
from spot_operator.services.map_storage import save_map_to_db

_log = get_logger(__name__)


@dataclass
class RecordedCheckpoint:
    """Checkpoint přidaný během nahrávání."""

    name: str
    waypoint_id: str
    kind: str  # 'waypoint' | 'checkpoint'
    capture_sources: list[str] = field(default_factory=list)
    photos: list[tuple[str, bytes, int, int]] = field(
        default_factory=list, repr=False
    )
    capture_status: str = CAPTURE_STATUS_NOT_APPLICABLE
    saved_sources: list[str] = field(default_factory=list)
    failed_sources: list[str] = field(default_factory=list)
    note: str = ""
    created_at: str = ""


@dataclass(frozen=True)
class RecordingSnapshot:
    """Immutable output z ``stop_and_export``.

    Drží referenci na temp adresář se staženou GraphNav mapou + metadata.
    Volající (SaveMapPage) pak volá ``save_snapshot_to_db(snapshot, ...)``
    idempotentně — pokud save failne, může se opakovat. Snapshot je retry-safe.

    Temp adresář se maže až po úspěšném save, nebo při explicit
    ``release_temp()``.
    """

    temp_dir: Path
    checkpoints: tuple[RecordedCheckpoint, ...]
    start_waypoint_id: str | None
    effective_fiducial_id: int | None
    default_capture_sources: tuple[str, ...]
    checkpoint_count: int

    def release_temp(self) -> None:
        """Smaže temp adresář. Volat po úspěšném save nebo explicit user cancel."""
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)


class RecordingService:
    """Drží stav jednoho nahrávacího sezení.

    Nepracuje přímo s DB za běhu — to až v `stop_and_archive_to_db()`. Důvod:
    dokud operátor běh nedokončí, nechceme v DB částečná data.
    """

    def __init__(self, bundle: Any):
        """
        Args:
            bundle: SpotBundle (session + managers) ze session_factory.connect().
        """
        self._bundle = bundle

        from app.robot.graphnav_recording import GraphNavRecorder
        from app.robot.waypoint_namer import WaypointNameGenerator

        self._recorder = GraphNavRecorder(bundle.session)
        self._namer = WaypointNameGenerator()
        self._checkpoints: list[RecordedCheckpoint] = []
        self._start_waypoint_id: Optional[str] = None
        self._fiducial_id: Optional[int] = None
        self._default_capture_sources: list[str] = []

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
        return self._recorder.is_recording

    def start(
        self,
        *,
        map_name_prefix: str,
        default_capture_sources: list[str],
        fiducial_id: Optional[int],
    ) -> None:
        """Spustí GraphNav recording. Musí se volat jednou.

        Start **vždy čistí předchozí stav** (``_checkpoints`` a
        ``_start_waypoint_id``) — pokud by byla instance re-used po aborcích,
        staré checkpointy by se jinak smíchaly s novými.

        Args:
            map_name_prefix: GraphNav prefix pro nahrávané waypointy.
            default_capture_sources: strany focení zvolené operátorem.
            fiducial_id: ID startovacího fiducialu (z fiducial_check).
        """
        if self._recorder.is_recording:
            raise RuntimeError("Recording already in progress.")
        # Reset state — v případě re-use instance po aborcích.
        self._checkpoints.clear()
        self._start_waypoint_id = None
        self._default_capture_sources = list(default_capture_sources)
        self._fiducial_id = fiducial_id
        self._recorder.start_recording(
            name_prefix=map_name_prefix,
            session_name=f"spot_operator_{map_name_prefix}",
        )

    def _ensure_start_waypoint(self, wp_id: str) -> None:
        """Nastaví ``_start_waypoint_id`` při prvním volání.

        Volá se z ``add_unnamed_waypoint`` i ``capture_and_record_checkpoint``.
        Pozor: pokud operátor zapomene kliknout 'Waypoint' (C) a rovnou
        fotí (V/N/B), ``start_waypoint_id`` se bude rovnat prvnímu
        checkpointu — což často způsobuje mis-localizaci playbacku.
        TeleopRecordPage tento scénář blokuje (disable photo tlačítek
        dokud neexistuje aspoň 1 waypoint).
        """
        if self._start_waypoint_id is None:
            self._start_waypoint_id = wp_id

    def add_unnamed_waypoint(self) -> RecordedCheckpoint:
        """Přidá waypoint bez fotky (operátor klikne 'Waypoint')."""
        name = self._namer.next_waypoint()
        wp_id = self._recorder.create_waypoint(name)
        self._ensure_start_waypoint(wp_id)
        cp = RecordedCheckpoint(
            name=name,
            waypoint_id=wp_id,
            kind="waypoint",
            capture_sources=[],
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._checkpoints.append(cp)
        _log.info("Waypoint added: %s id=%s", name, wp_id)
        return cp

    def capture_and_record_checkpoint(
        self,
        sources: list[str],
        *,
        image_poller: Any,
        jpeg_quality: int = 85,
    ) -> RecordedCheckpoint:
        """Přidá pojmenovaný checkpoint a pořídí fotky.

        **Při totálním selhání capture** (žádná fotka) raise
        :class:`CaptureFailedError` — volající (``TeleopRecordPage``)
        rozhodne retry / skip / abort dialogem. Recording service
        nebude silent-demotovat na waypoint (dřívější bug FIND-066/078,
        kde operátor nevěděl, že checkpoint je prázdný).

        Args:
            sources: které image sources fotit (['left_fisheye_image', ...]).
            image_poller: autonomy ImagePoller.
        """
        from spot_operator.robot.dual_side_capture import capture_sources as cap_sources
        from spot_operator.services.photo_sink import encode_bgr_to_jpeg

        name = self._namer.next_checkpoint()
        wp_id = self._recorder.create_waypoint(name)
        self._ensure_start_waypoint(wp_id)

        frames = cap_sources(image_poller, sources)
        photos: list[tuple[str, bytes, int, int]] = []
        saved_sources: list[str] = []
        failed_sources: list[str] = []
        for src in sources:
            bgr = frames.get(src)
            if bgr is None:
                failed_sources.append(src)
                continue
            try:
                jpeg, w, h = encode_bgr_to_jpeg(bgr, quality=jpeg_quality)
                photos.append((src, jpeg, w, h))
                saved_sources.append(src)
            except Exception as exc:
                _log.warning("Encode failed for source %s: %s", src, exc)
                failed_sources.append(src)

        if not photos:
            # Totální selhání — raise místo silent demotion na waypoint.
            # Waypoint již byl vytvořen v GraphNavu (create_waypoint výše),
            # takže mapa má validní bod, jen bez fotky — volající rozhodne
            # jestli přidat jako explicit waypoint (add_unnamed_waypoint),
            # retry, nebo abort.
            _log.warning(
                "Capture failure pro %s: žádný source neuspěl (sources=%s, failed=%s)",
                name, sources, failed_sources,
            )
            raise CaptureFailedError(
                name=name,
                saved_sources=[],
                failed_sources=failed_sources or list(sources),
            )

        capture_status = CAPTURE_STATUS_OK
        note: str = CaptureNote.OK.value
        if failed_sources:
            capture_status = CAPTURE_STATUS_PARTIAL
            note = CaptureNote.CAPTURE_PARTIAL.value

        cp = RecordedCheckpoint(
            name=name,
            waypoint_id=wp_id,
            kind="checkpoint",
            capture_sources=list(sources),
            photos=photos,
            capture_status=capture_status,
            saved_sources=saved_sources,
            failed_sources=failed_sources,
            note=note,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._checkpoints.append(cp)
        _log.info(
            "Checkpoint %s id=%s sources=%s photos=%d status=%s",
            name,
            wp_id,
            sources,
            len(photos),
            capture_status,
        )
        return cp

    def stop_and_export(
        self,
        *,
        end_fiducial_id: Optional[int] = None,
    ) -> RecordingSnapshot:
        """Phase 1 two-phase save: zastaví recording + stáhne mapu do temp.

        **Není idempotentní** — volat jen jednou po dokončení recordingu.
        Výsledný ``RecordingSnapshot`` je immutable a lze ho passnout do
        ``save_snapshot_to_db`` opakovaně (retry po DB chybě).

        Args:
            end_fiducial_id: ID fiducialu při ukončení (z UI re-check na SaveMapPage).

        Returns:
            RecordingSnapshot s temp_dir a metadaty.
        """
        if not self._recorder.is_recording:
            raise RuntimeError("No recording in progress.")

        TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        tmp_root = Path(tempfile.mkdtemp(prefix="rec_", dir=str(TEMP_ROOT)))
        ok = False
        try:
            self._recorder.stop_recording()
            self._recorder.download_map(tmp_root)

            # KRITICKÉ: Přečti skutečně viděné fiducial IDs z WaypointSnapshot
            # protobufů (stejně jako autonomy). Bez tohoto by fiducial_id v DB
            # byl jen "první co operátor viděl před startem", což může být
            # jiný AprilTag než robot skutečně kotvil — playback pak selhává
            # se SPECIFIC_FIDUCIAL a robot se mis-lokalizuje.
            observed_fiducial_id: Optional[int] = None
            observed_list: list[int] = []
            try:
                from app.robot.graphnav_recording import read_observed_fiducial_ids

                try:
                    observed_list = list(read_observed_fiducial_ids(tmp_root))
                except Exception as exc:
                    # PR-03 FIND-075: fiducial observation read je
                    # kritické pro playback localize. Silent warning +
                    # fallback by znamenalo nekorektní fiducial_id v DB.
                    # Raise s CZ zprávou — uživatel uvidí dialog v SaveMapPage.
                    raise RuntimeError(
                        "Nelze přečíst observované fiducialy z mapy "
                        f"(protobuf chyba: {exc}). Mapa by se uložila s potenciálně "
                        "nesprávným fiducial_id. Zkus recording znovu."
                    ) from exc
                # KRITICKÉ pořadí volby fiducial_id pro playback-lokalizaci:
                #   1) self._fiducial_id (UI fiducial_check PŘED start recording)
                #      POKUD je taky v `observed_list` — user fyzicky ověřil,
                #      že tento fiducial je vidět, a robot ho zaznamenal do mapy.
                #      Nejjistější volba pro SPECIFIC_FIDUCIAL při playbacku.
                #   2) end_fiducial_id (UI re-check v SaveMapPage) POKUD je
                #      v observed_list.
                #   3) První observed (sorted[0]) — obvykle nejnižší ID, nemusí
                #      být ten který user verifikoval, ale alespoň je v mapě.
                if self._fiducial_id is not None and self._fiducial_id in observed_list:
                    observed_fiducial_id = self._fiducial_id
                elif end_fiducial_id is not None and end_fiducial_id in observed_list:
                    observed_fiducial_id = end_fiducial_id
                elif observed_list:
                    observed_fiducial_id = observed_list[0]

                if observed_list:
                    _log.info(
                        "Observed fiducial IDs in recorded map: %s (using %s)",
                        observed_list, observed_fiducial_id,
                    )
                else:
                    _log.warning(
                        "No fiducials observed in waypoint snapshots — "
                        "falling back to UI fiducial_check value."
                    )
            except ImportError as exc:
                # Autonomy API není dostupné — loguj, nehas save úplně
                # (spot_operator v test módu může běžet bez full autonomy).
                _log.warning(
                    "read_observed_fiducial_ids import failed: %s — "
                    "fallback na UI fiducial hodnoty.",
                    exc,
                )

            # Priorita: 1) verified observed (z grafu + UI match),
            #           2) start fiducial (UI check only — pokud snapshots nic nenašly),
            #           3) end fiducial (UI check only).
            effective_fiducial_id = (
                observed_fiducial_id or self._fiducial_id or end_fiducial_id
            )

            snapshot = RecordingSnapshot(
                temp_dir=tmp_root,
                checkpoints=tuple(self._checkpoints),
                start_waypoint_id=self._start_waypoint_id,
                effective_fiducial_id=effective_fiducial_id,
                default_capture_sources=tuple(self._default_capture_sources),
                checkpoint_count=self.checkpoint_count,
            )
            ok = True
            return snapshot
        finally:
            if not ok:
                # Při selhání stop/download smaž temp (nelze retry — recording
                # je už stopnuté nebo v nekonzistentním stavu).
                shutil.rmtree(tmp_root, ignore_errors=True)

    def save_snapshot_to_db(
        self,
        snapshot: RecordingSnapshot,
        *,
        map_name: str,
        note: str,
        operator_label: str | None,
    ) -> int:
        """Phase 2 two-phase save: uloží snapshot do DB. **Idempotent retry-safe**.

        Pokud save selže (DB error, duplicate name, validation), volající může
        opakovat s jiným ``map_name`` na stejný snapshot. Temp je smazán
        **až po úspěchu** (``snapshot.release_temp()``) — operátor neztratí data.

        Args:
            snapshot: output ze ``stop_and_export``.
            map_name: jméno mapy (validace mimo).
            note: poznámka.
            operator_label: kdo nahrál (optional).

        Returns:
            map_id v DB.
        """
        checkpoints_json = build_checkpoint_plan_payload(
            map_name=map_name,
            start_waypoint_id=snapshot.start_waypoint_id,
            fiducial_id=snapshot.effective_fiducial_id,
            default_capture_sources=snapshot.default_capture_sources,
            checkpoints=snapshot.checkpoints,
        )
        map_id = save_map_to_db(
            name=map_name,
            source_dir=snapshot.temp_dir,
            fiducial_id=snapshot.effective_fiducial_id,
            start_waypoint_id=snapshot.start_waypoint_id,
            default_capture_sources=list(snapshot.default_capture_sources),
            checkpoints_json=checkpoints_json,
            checkpoints_count=snapshot.checkpoint_count,
            note=note or None,
            created_by_operator=operator_label or None,
        )
        # Úspěch — uvolni temp.
        snapshot.release_temp()
        return map_id

    def stop_and_archive_to_db(
        self,
        *,
        map_name: str,
        note: str,
        operator_label: str | None,
        end_fiducial_id: Optional[int],
    ) -> int:
        """Backward-compat wrapper: stop + save v jednom kroku.

        **Deprecated** — nové UI kódy používají dvoufázový flow
        (``stop_and_export`` + ``save_snapshot_to_db``), který je retry-safe.
        """
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
        """Zruší běžící recording bez uložení. Volá se při chybě nebo zavření wizardu.

        Pokud ``stop_recording`` selže, pokusí se jednou retry po 1 s delay —
        bosdyn může mít internal state "recording active", který nechce
        zůstat přes session boundary (další ``start_recording`` by padal
        s "session already active"). Viz PR-02 FIND-085.
        """
        import time

        if self._recorder.is_recording:
            try:
                self._recorder.stop_recording()
            except Exception as exc:
                _log.warning(
                    "stop_recording during abort failed: %s — zkouším retry po 1 s",
                    exc,
                )
                time.sleep(1.0)
                try:
                    # Pokud bosdyn vidí recording stále active, zkus ještě jednou.
                    if self._recorder.is_recording:
                        self._recorder.stop_recording()
                except Exception as exc2:
                    _log.error(
                        "stop_recording retry také selhal: %s — GraphNav internal "
                        "state může zůstat \"recording\". Možná nutný reconnect.",
                        exc2,
                    )
        self._checkpoints.clear()
        self._start_waypoint_id = None

    def _build_checkpoints_json(
        self,
        map_name: str,
        effective_fiducial_id: Optional[int] = None,
    ) -> dict:
        return build_checkpoint_plan_payload(
            map_name=map_name,
            start_waypoint_id=self._start_waypoint_id,
            fiducial_id=(
                effective_fiducial_id
                if effective_fiducial_id is not None
                else self._fiducial_id
            ),
            default_capture_sources=self._default_capture_sources,
            checkpoints=self._checkpoints,
        )

    # Umožníme wizardu dostat se k seznamu checkpointů (pro counter, seznam).
    @property
    def checkpoints(self) -> list[RecordedCheckpoint]:
        return list(self._checkpoints)


__all__ = ["RecordingService", "RecordedCheckpoint", "RecordingSnapshot"]
