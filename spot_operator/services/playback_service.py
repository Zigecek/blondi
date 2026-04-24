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

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import QObject, QThread, Signal

from spot_operator.constants import (
    PLAYBACK_AVOIDANCE_STRENGTH,
    PLAYBACK_NAV_TIMEOUT_SEC,
    PLAYBACK_RETURN_HOME_TIMEOUT_SEC,
    ROBOT_LOST_ERROR_MARKERS,
    TEMP_ROOT,
)
from spot_operator.db.engine import Session
from spot_operator.db.enums import RunStatus
from spot_operator.db.repositories import runs_repo
from spot_operator.logging_config import get_logger
from spot_operator.services.contracts import (
    CAPTURE_STATUS_FAILED,
    CAPTURE_STATUS_NOT_APPLICABLE,
    CAPTURE_STATUS_OK,
    CAPTURE_STATUS_PARTIAL,
    RETURN_HOME_STATUS_COMPLETED,
    RETURN_HOME_STATUS_FAILED,
    RETURN_HOME_STATUS_IN_PROGRESS,
    CaptureSummary,
    CheckpointResult,
    build_checkpoint_result,
    checkpoint_results_to_payload,
    parse_checkpoint_plan,
)
from spot_operator.services.map_storage import MapMetadata, load_map_to_temp
from spot_operator.services.photo_sink import save_photo_to_db

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CheckpointRef:
    """Zjednodušený odkaz na checkpoint (ze mapy v DB)."""

    name: str
    waypoint_id: str
    kind: str
    capture_sources: tuple[str, ...]


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
    # PR-05 FIND-097: drift warning pro UI display (nehalí playback).
    drift_detected = Signal(str, str, str)  # cp_name, actual_wp, target_wp
    # PR-05 FIND-104: avoidance setting failure — UI rozhodne pokračovat.
    avoidance_failed = Signal(str)
    # Obstacle pause (Fix 1): emit když navigate_to selže s STUCK/TIMEOUT/NO_ROUTE.
    # UI zobrazí dialog a volá resume_after_obstacle() / cancel_after_obstacle().
    obstacle_detected = Signal(str, str)  # (checkpoint_name, outcome_value)

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
        # `self._meta` persist pro přístup z `_navigate_with_retry` (strict
        # re-localize recovery potřebuje fiducial_id + start_waypoint_id).
        self._meta: Optional[MapMetadata] = None
        self._checkpoint_results: list[CheckpointResult] = []
        self._last_run_status: Optional[RunStatus] = None
        self._last_abort_reason: Optional[str] = None
        # Obstacle pause handshake (Fix 1): worker thread blokuje na Event,
        # UI thread emit obstacle_detected, operátor volí resume/cancel.
        self._obstacle_event = threading.Event()
        self._obstacle_resume_choice: bool = False

    @property
    def navigator(self) -> Any:
        return self._navigator

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
        # Pokud worker čeká na obstacle pause event, odblokuj ho jako "cancel".
        # Bez toho by zůstal viset v _wait_for_obstacle_decision (abort by se
        # aplikoval až po doručení navigate_to timeoutu — zbytečné čekání).
        self._obstacle_resume_choice = False
        self._obstacle_event.set()
        try:
            self._navigator.request_abort()
        except Exception as exc:
            _log.warning("navigator.request_abort failed: %s", exc)

    def resume_after_obstacle(self) -> None:
        """UI callback po dialogu: operátor zvolil 'Pokračovat'. Odblokuje
        worker thread v `_wait_for_obstacle_decision` → re-localize + retry."""
        self._obstacle_resume_choice = True
        self._obstacle_event.set()

    def cancel_after_obstacle(self) -> None:
        """UI callback po dialogu: operátor zvolil 'Zrušit jízdu'. Abort."""
        self._obstacle_resume_choice = False
        self._abort_requested = True
        self._obstacle_event.set()

    def _wait_for_obstacle_decision(self, cp_name: str, outcome_value: str) -> bool:
        """Emit obstacle_detected do UI a blokuj dokud operátor nevybere.

        Vrátí True pokud zvolil pokračovat (retry), False pokud cancel/abort.
        """
        self._obstacle_event.clear()
        self._obstacle_resume_choice = False
        _log.info(
            "Obstacle pause: emituji obstacle_detected(%s, %s) — čekám na volbu UI.",
            cp_name,
            outcome_value,
        )
        self.obstacle_detected.emit(cp_name, outcome_value)
        # Blokuj dokud UI nezavolá resume_after_obstacle / cancel_after_obstacle.
        # Bez timeoutu — operátor může chvilku potřebovat na uvolnění cesty.
        self._obstacle_event.wait()
        if self._abort_requested:
            return False
        return self._obstacle_resume_choice

    def request_return_home(self) -> None:
        """DEPRECATED: dřívější alias pro request_abort.

        PR-05 FIND-093: metoda neimplementovala skutečný return home,
        jen volala request_abort. UI (PlaybackRunPage._on_stop_return)
        teď explicit spouští _ReturnHomeThread. Zachováno jen pro
        backward compat — delegation zůstává na request_abort.
        """
        _log.warning(
            "request_return_home() je deprecated — volej request_abort + "
            "explicit return_home() v BG threadu."
        )
        self.request_abort()

    # ---- Hlavní orchestrace ----

    def upload_map_only(self, map_id: int) -> MapMetadata:
        """Extrahuje mapu z DB a uploadne ji do robota.

        **Parallel-safe** — netřeba být u fiducialu. Voláme už při vstupu na
        FiducialPage (operátor zatím dochází ke značce), aby `run` začínal
        skoro okamžitě.
        """
        self._emit_progress("Načítám mapu z databáze...")
        map_dir, meta = load_map_to_temp(map_id, TEMP_ROOT)
        self._map_temp_dir = map_dir

        self._emit_progress("Uploaduji mapu do robota...")
        self._navigator.upload_map(map_dir)
        self.map_uploaded.emit()
        return meta

    def localize_on_map(self, meta: MapMetadata) -> None:
        """Lokalizace robota na nahrané mapě — vyžaduje viditelný fiducial.

        Po úspěšném `set_localization` ověří, že waypoint_id je skutečně
        z aktuální mapy (belt-and-braces — GraphNav občas "approves" i falešně).
        """
        self._emit_progress("Lokalizuji robota podle fiducialu...")
        self._localize_with_fallback(meta)
        if not self._is_localized_on_current_graph():
            raise RuntimeError(
                "Lokalizace sice vrátila OK, ale robot není na aktuální mapě. "
                "Přistup blíž k fiducialu a zkus znovu."
            )
        self.localized.emit()

    def prepare_map(self, map_id: int) -> MapMetadata:
        """Backward-compat: upload + localize v sekvenci.

        Při spuštění PlaybackRunPage bez předchozího pre-uploadu (fallback
        cesta). Nová cesta je `upload_map_only` na FiducialPage →
        `localize_on_map` na PlaybackRunPage.
        """
        meta = self.upload_map_only(map_id)
        self.localize_on_map(meta)
        return meta

    def run_all_checkpoints(
        self, meta: MapMetadata, *, operator_label: str | None
    ) -> int:
        """Spustí autonomní průjezd checkpointů. Vrátí run_id.

        PR-05 FIND-086: run je vytvořen v DB **před** pre-flight checks,
        takže i neúspěšné pokusy jsou v audit trail (CRUD → Běhy).
        """
        self._abort_requested = False
        self._last_run_status = RunStatus.running
        self._last_abort_reason = None
        self._checkpoint_results = []

        # Extract checkpoints — může raise ValueError (viz FIND-103).
        try:
            checkpoints = self._extract_checkpoints(meta)
        except ValueError as exc:
            self._create_run_and_fail(
                meta=meta,
                operator_label=operator_label,
                total=0,
                reason=f"Mapa nelze načíst: {exc}",
            )
            raise RuntimeError(f"Mapa neobsahuje žádné checkpointy: {exc}") from exc

        # Create run v DB hned — i neúspěšný pre-flight zanechá audit trail.
        with Session() as s:
            run_code = runs_repo.generate_unique_run_code(s)
            run = runs_repo.create(
                s,
                run_code=run_code,
                map_id=meta.id,
                map_name_snapshot=meta.name,
                checkpoints_total=len(checkpoints),
                operator_label=operator_label,
                start_waypoint_id=meta.start_waypoint_id,
                checkpoint_results_json=[],
            )
            s.commit()
            self._run_id = run.id
        self.run_started.emit(self._run_id)
        _log.info("Run %s created (map=%s, checkpoints=%d)", run_code, meta.name, len(checkpoints))

        # --- Pre-flight checks (post-create). ---
        # Single localization state call (FIND-087): použij hodnoty napříč.
        localized_wp = self._current_localization_waypoint()
        expected_start = meta.start_waypoint_id or "(neznámý)"
        _log.info(
            "Playback pre-flight — localized waypoint: %s, expected start_waypoint_id: %s",
            localized_wp or "(neznámý)", expected_start,
        )
        if not self._is_localized_on_current_graph():
            self._finalize_failed_run(
                "Robot není lokalizován na aktuální mapě. "
                "Vrať se k fiducialu a zkus playback znovu."
            )
            raise RuntimeError(
                "Robot není lokalizován na aktuální mapě. Vrať se k fiducialu "
                "a zkus playback znovu."
            )
        if localized_wp and expected_start != "(neznámý)" and localized_wp != expected_start:
            msg = (
                f"Robot je lokalizovaný na jiném waypointu než start mapy "
                f"({localized_wp} != {expected_start}). Přibliž Spota k fiducialu a zkus znovu."
            )
            self._finalize_failed_run(msg)
            raise RuntimeError(msg)

        _log.info(
            "Checkpoint order (%d): %s",
            len(checkpoints),
            ", ".join(f"{c.name}->{c.waypoint_id[:12]}..." for c in checkpoints),
        )

        # Nastav globální obstacle avoidance strength pro playback.
        # GraphNav nepřijímá padding přes TravelParams, ale robot si pamatuje
        # mobility state → synchro_stand s požadovanou strength to nastaví.
        # PR-05 FIND-104: emit signal pro UI místo silent warning.
        try:
            from app.robot.mobility_state import set_global_avoidance

            set_global_avoidance(self._bundle.session, PLAYBACK_AVOIDANCE_STRENGTH)
            self._emit_progress(
                f"Obstacle avoidance nastaveno na strength={PLAYBACK_AVOIDANCE_STRENGTH}."
            )
        except Exception as exc:
            _log.warning(
                "Nepodařilo se nastavit global avoidance (strength=%d): %s",
                PLAYBACK_AVOIDANCE_STRENGTH,
                exc,
            )
            self.avoidance_failed.emit(
                f"Obstacle avoidance (strength={PLAYBACK_AVOIDANCE_STRENGTH}) nenastaveno: {exc}. "
                "Robot pojede s default padding — může být méně opatrný u překážek."
            )

        # Persist meta pro přístup z retry smyčky (strict re-localize recovery).
        self._meta = meta

        success = 0
        total = len(checkpoints)
        abort_reason: Optional[str] = None

        for idx, cp in enumerate(checkpoints, start=1):
            started_at = datetime.now(timezone.utc)
            if self._abort_requested:
                abort_reason = "Aborted by user"
                break
            try:
                self.checkpoint_reached.emit(idx, total, cp.name)
                self._emit_progress(f"Navigate to {cp.name} ({idx}/{total})")
                result = self._navigate_with_retry(cp)
                if not result.ok:
                    checkpoint_result = build_checkpoint_result(
                        name=cp.name,
                        waypoint_id=cp.waypoint_id,
                        nav_outcome=result.outcome.value,
                        capture_status=CAPTURE_STATUS_NOT_APPLICABLE,
                        expected_sources=cp.capture_sources if cp.kind == "checkpoint" else (),
                        saved_sources=(),
                        failed_sources=(),
                        error=result.message,
                        started_at=started_at,
                        finished_at=datetime.now(timezone.utc),
                    )
                    self._record_checkpoint_result(checkpoint_result, success)
                    abort_reason = f"navigate failed at {cp.name}: {result.message}"
                    _log.warning(abort_reason)

                    # TERMINÁLNÍ: RobotLostError = robot zcela ztratil
                    # GraphNav lokalizaci. Retry ani další CP nemůže uspět
                    # (bosdyn odmítá všechny navigate_to). Abort okamžitě.
                    if self._is_robot_lost_error(result):
                        _log.error(
                            "Robot je ztracený (RobotLostError) — abort run. "
                            "Fyzicky vrať Spota blíž k fiducialu a spusť "
                            "playback znovu. Pokud se to opakuje, přidej víc "
                            "fiducialů podél trasy."
                        )
                        self._emit_progress(
                            "⚠ Robot ztratil GraphNav lokalizaci. Run abortován."
                        )
                        break

                    # Fix 3: ROBOT_IMPAIRED (typicky po E-STOP) — všechny další
                    # navigate_to selžou, neztrácet čas dalšími pokusy.
                    from app.models import NavigationOutcome

                    if result.outcome == NavigationOutcome.ROBOT_IMPAIRED:
                        _log.error(
                            "Robot impaired (%s) — abort run. Pravděpodobně byl "
                            "stisknutý E-STOP nebo došlo k perception/behavior "
                            "fault. Uvolni E-STOP, power-on Spota a spusť znovu.",
                            result.message,
                        )
                        self._emit_progress(
                            "⚠ Spot je v chybovém stavu (motory vypnuty / E-STOP). "
                            "Run zrušen."
                        )
                        abort_reason = (
                            f"robot impaired at {cp.name}: {result.message}"
                        )
                        break

                    # Fix 1c: odstranili jsme tichý `continue` na další CP.
                    # Když _navigate_with_retry vrátí fail a operátor zvolil
                    # "Zrušit jízdu" v pause dialogu, nebo fail je jiný terminální
                    # stav, abortujeme run. Operátor tak nikdy neuvidí Spota
                    # potichu skákat po mapě po první chybě.
                    _log.warning(
                        "Navigate to %s selhalo (%s) — abort run.",
                        cp.name, result.outcome.value,
                    )
                    break

                # Po úspěšném navigate_to — kontrola drift před capture.
                self._warn_if_drift(cp)

                capture_summary = CaptureSummary(
                    status=CAPTURE_STATUS_NOT_APPLICABLE,
                    expected_sources=(),
                    saved_sources=(),
                    failed_sources=(),
                    error=None,
                )
                if cp.kind == "checkpoint" and cp.capture_sources:
                    capture_summary = self._capture_at_checkpoint(cp)

                checkpoint_result = build_checkpoint_result(
                    name=cp.name,
                    waypoint_id=cp.waypoint_id,
                    nav_outcome=result.outcome.value,
                    capture_status=capture_summary.status,
                    expected_sources=capture_summary.expected_sources,
                    saved_sources=capture_summary.saved_sources,
                    failed_sources=capture_summary.failed_sources,
                    error=capture_summary.error,
                    started_at=started_at,
                    finished_at=datetime.now(timezone.utc),
                )
                if checkpoint_result.is_complete:
                    success += 1
                self._record_checkpoint_result(checkpoint_result, success)
            except (KeyboardInterrupt, MemoryError, SystemExit):
                # Kritické exceptions propagujeme — v žádném případě
                # nepokračovat v playbacku.
                raise
            except Exception as exc:
                # Klasifikace: DB transient vs permanent.
                from sqlalchemy.exc import OperationalError, ProgrammingError

                if isinstance(exc, ProgrammingError):
                    # DB schema mismatch — další CP taky padne, abort.
                    _log.exception(
                        "DB schema error at %s — aborting run: %s", cp.name, exc
                    )
                    abort_reason = f"DB schema error at {cp.name}: {exc}"
                    break
                _log.exception("Checkpoint %s failed: %s", cp.name, exc)
                transient_db = isinstance(exc, OperationalError)
                checkpoint_result = build_checkpoint_result(
                    name=cp.name,
                    waypoint_id=cp.waypoint_id,
                    nav_outcome="error",
                    capture_status=CAPTURE_STATUS_NOT_APPLICABLE,
                    expected_sources=cp.capture_sources if cp.kind == "checkpoint" else (),
                    saved_sources=(),
                    failed_sources=(),
                    error=str(exc),
                    started_at=started_at,
                    finished_at=datetime.now(timezone.utc),
                )
                try:
                    self._record_checkpoint_result(checkpoint_result, success)
                except Exception as record_exc:
                    _log.warning("Failed to record error result: %s", record_exc)
                abort_reason = f"exception at {cp.name}: {exc}"
                if transient_db:
                    _log.info("Transient DB error — pokračuji na další CP")
                continue

        final_status = self._classify_final_status(success, total, abort_reason)
        self._last_run_status = final_status
        self._last_abort_reason = abort_reason
        with Session() as s:
            runs_repo.finish(
                s,
                self._run_id,
                status=final_status,
                checkpoints_reached=success,
                abort_reason=abort_reason,
                checkpoint_results_json=checkpoint_results_to_payload(
                    self._checkpoint_results
                ),
            )
            s.commit()

        if final_status == RunStatus.completed:
            self.run_completed.emit(success, total)
        else:
            self.run_failed.emit(abort_reason or final_status.value)

        return self._run_id

    def return_home(self, start_wp_id: str):
        """Volá return_home utilitu z autonomy.

        PR-05 FIND-100: pre-check že robot je lokalizován před voláním
        autonomy return_home — jinak by jel do náhodného místa.
        """
        from app.robot.return_home import return_home

        if not self._is_localized_on_current_graph():
            raise RuntimeError(
                "Robot není lokalizován v mapě — návrat domů nelze spustit. "
                "Zkus re-localize nebo fyzicky pohni Spotem k fiducialu."
            )
        self._emit_progress("Návrat domů...")
        if self._run_id is not None:
            with Session() as s:
                runs_repo.set_return_home(
                    s,
                    self._run_id,
                    status=RETURN_HOME_STATUS_IN_PROGRESS,
                    reason=None,
                )
                s.commit()
        try:
            result = return_home(
                self._navigator,
                start_wp_id,
                timeout_s=PLAYBACK_RETURN_HOME_TIMEOUT_SEC,
                progress=self._emit_progress,
            )
            if self._run_id is not None:
                with Session() as s:
                    runs_repo.set_return_home(
                        s,
                        self._run_id,
                        status=(
                            RETURN_HOME_STATUS_COMPLETED
                            if result.ok
                            else RETURN_HOME_STATUS_FAILED
                        ),
                        reason=None if result.ok else result.message,
                    )
                    s.commit()
            return result
        except Exception as exc:
            if self._run_id is not None:
                with Session() as s:
                    runs_repo.set_return_home(
                        s,
                        self._run_id,
                        status=RETURN_HOME_STATUS_FAILED,
                        reason=str(exc),
                    )
                    s.commit()
            _log.exception("return_home failed: %s", exc)
            raise

    def cleanup(self) -> None:
        """Smaže temp extrahovanou mapu s retry handling (PR-12 FIND-096)."""
        if self._map_temp_dir is not None:
            from spot_operator.services.map_storage import safe_rmtree

            safe_rmtree(self._map_temp_dir)
            self._map_temp_dir = None

    # ---- Internal helpers ----

    def _should_retry_outcome(self, result) -> bool:  # noqa: ANN001 — NavigationResult
        """Whitelist outcome-ů, kde re-localize + retry má smysl.

        PR-05 FIND-089: rozšířeno o STUCK (dočasná překážka) a NO_ROUTE
        (GraphNav má stale state) — obojí se po re-localize může uvolnit.
        Neznámé nové NavigationOutcome hodnoty logujeme jako warning
        (forward-compat; FIND-176).
        """
        from app.models import NavigationOutcome

        if result.is_localization_loss:
            return True
        retryable = {
            NavigationOutcome.TIMEOUT,
            NavigationOutcome.LOST,
            NavigationOutcome.NOT_LOCALIZED,
            NavigationOutcome.STUCK,
            NavigationOutcome.NO_ROUTE,
        }
        outcome = result.outcome
        known_outcomes = set(NavigationOutcome)
        if outcome not in known_outcomes:
            _log.warning(
                "Neznámá NavigationOutcome hodnota %r — autonomy může být "
                "novější než spot_operator; outcome nebude retryován.",
                outcome,
            )
            return False
        return outcome in retryable

    def _navigate_with_retry(self, cp: "CheckpointRef", max_retries: int = 2):
        """Navigate_to s obstacle pause (Fix 1) a omezeným re-localize retry.

        Flow:
          1. navigate_to → pokud ok, hotovo.
          2. Pokud non-retryable outcome (ROBOT_IMPAIRED, ERROR, …) → return.
          3. Pokud terminální `RobotLostError` → return (main loop to zpracuje).
          4. Jinak (STUCK/TIMEOUT/NO_ROUTE/LOST/NOT_LOCALIZED):
             emit obstacle_detected → blokuj na UI volbu operatora.
             - Resume: re-localize + navigate_to retry (až max_retries pokusů).
             - Cancel: abort, return current result.
        """
        attempt = 0
        last_result = None
        while attempt <= max_retries:
            if self._abort_requested:
                return last_result
            result = self._navigator.navigate_to(
                cp.waypoint_id, timeout=PLAYBACK_NAV_TIMEOUT_SEC
            )
            last_result = result
            post_wp = self._current_localization_waypoint()
            _log.info(
                "Navigate to %s (target=%s...) → outcome=%s, robot now at waypoint %s",
                cp.name, cp.waypoint_id[:12],
                result.outcome.value, post_wp[:12] if post_wp else "(neznámý)",
            )
            if result.ok:
                return result
            if not self._should_retry_outcome(result):
                return result
            # RobotLostError — terminální, hlavní smyčka to zpracuje.
            if self._is_robot_lost_error(result):
                return result

            attempt += 1
            if attempt > max_retries:
                break

            # Pause + UI dialog. Worker čeká, UI thread emituje resume/cancel.
            self._emit_progress(
                f"⏸ {cp.name}: {result.outcome.value} — "
                "zastaveno, čekám na rozhodnutí operátora"
            )
            resume = self._wait_for_obstacle_decision(cp.name, result.outcome.value)
            if not resume:
                _log.info("Operator cancelled after obstacle at %s", cp.name)
                self._emit_progress(f"{cp.name}: zrušeno operátorem.")
                return result

            _log.info(
                "Operator resume after obstacle at %s — re-localize + retry %d/%d",
                cp.name, attempt, max_retries,
            )
            self._emit_progress(
                f"{cp.name}: pokračuji — re-localize + retry {attempt}/{max_retries}"
            )
            # Preferuj STRICT re-localize (hint na start_waypoint + FIDUCIAL_SPECIFIC).
            # Pokud fiducial není vidět nebo strict selže, fallback na NEAREST.
            strict_ok = False
            if (
                self._meta is not None
                and self._meta.fiducial_id is not None
                and self._meta.start_waypoint_id
            ):
                try:
                    from spot_operator.robot.localize_strict import localize_at_start

                    localize_at_start(
                        self._bundle.session,
                        fiducial_id=self._meta.fiducial_id,
                        start_waypoint_id=self._meta.start_waypoint_id,
                    )
                    _log.info("Strict re-localize OK — retry navigate.")
                    strict_ok = True
                except Exception as exc:
                    _log.debug(
                        "Strict re-localize failed (%s); fallback to NEAREST.", exc
                    )
            if not strict_ok:
                try:
                    if not self._navigator.relocalize_nearest_fiducial():
                        _log.warning("Re-localize failed; retry will likely fail too.")
                except Exception as exc:
                    _log.warning("Re-localize raised: %s", exc)
        return last_result

    def _is_robot_lost_error(self, result) -> bool:  # noqa: ANN001 — NavigationResult
        """Detekce RobotLostError přes substring match v message.

        RobotLostError je TERMINÁLNÍ — bosdyn odmítá všechny navigate_to
        dokud robota nerelokalizujeme. Liší se od TIMEOUT (recoverable).
        """
        msg = (getattr(result, "message", "") or "").lower()
        return any(marker in msg for marker in ROBOT_LOST_ERROR_MARKERS)

    def _warn_if_drift(self, cp: "CheckpointRef") -> None:
        """Informativní warning pokud po úspěšném navigate_to skončil robot
        na jiném waypointu než bylo cílem. Neabortuje — jen signalizuje
        akumulující se odometry drift (prekurzor RobotLostError).

        PR-05 FIND-097: navíc emit ``drift_detected`` Qt signal pro UI,
        aby operátor viděl warning + mohl reagovat (např. re-localize).
        """
        post_wp = self._current_localization_waypoint()
        if post_wp and post_wp != cp.waypoint_id:
            _log.warning(
                "Localize drift at %s: bosdyn říká robot je na %s, cíl byl %s. "
                "Drift pokračuje — riziko RobotLostError v dalších CP.",
                cp.name, post_wp[:12], cp.waypoint_id[:12],
            )
            try:
                self.drift_detected.emit(cp.name, post_wp, cp.waypoint_id)
            except Exception:
                pass

    def _current_localization_waypoint(self) -> str:
        """Vrátí aktuální waypoint_id robota, nebo prázdný string při chybě."""
        client = getattr(self._bundle.session, "graph_nav_client", None)
        if client is None:
            return ""
        try:
            state = client.get_localization_state()
        except Exception as exc:
            _log.debug("get_localization_state failed: %s", exc)
            return ""
        return getattr(getattr(state, "localization", None), "waypoint_id", "") or ""

    def _is_localized_on_current_graph(self) -> bool:
        """Ověří přes `get_localization_state`, že robot je na nahraném grafu.

        Waypoint_ids z upload_map jsou cached v navigator. Pokud lokalizace
        ukazuje na jiný waypoint_id než jsou v grafu, robot je mis-localized
        (pravděpodobně stale odometrie z předchozího session).
        """
        client = getattr(self._bundle.session, "graph_nav_client", None)
        if client is None:
            _log.warning("verify_localization: graph_nav_client not available")
            return False
        try:
            state = client.get_localization_state()
        except Exception as exc:
            _log.warning("get_localization_state failed: %s", exc)
            return False
        wp = getattr(getattr(state, "localization", None), "waypoint_id", "")
        if not wp:
            _log.warning("verify_localization: no waypoint_id in state")
            return False
        try:
            known = set(self._navigator.get_waypoint_ids())
        except Exception as exc:
            _log.debug("verify_localization: get_waypoint_ids failed: %s", exc)
            known = set()
        if known and wp not in known:
            _log.warning(
                "verify_localization: robot localized at %s which is NOT in uploaded graph (%d waypoints)",
                wp,
                len(known),
            )
            return False
        _log.info("verify_localization: OK at waypoint %s", wp)
        return True

    def _localize_with_fallback(self, meta: MapMetadata) -> None:
        """Strictní lokalizace na startovní fiducial + hint na start_waypoint.

        **Proč ne autonomy.localize:** autonomy `SPECIFIC_FIDUCIAL` strategie
        nenastavuje `initial_guess.waypoint_id` — bosdyn pak vybere náhodnou
        observaci fiducialu v mapě (fiducial je obvykle zaznamenán z více
        waypointů). Výsledek: občas je robot lokalizován uprostřed mapy místo
        u startu → `navigate_to(CP_001)` plánuje cestu z mis-lokalizované
        pozice → robot jede na nejvzdálenější místo, větve ignoruje.

        Fix: vlastní wrapper v `spot_operator/robot/localize_strict.py` který
        volá bosdyn přímo s `initial_guess.waypoint_id = start_waypoint_id`
        + `FIDUCIAL_INIT_SPECIFIC`. Bosdyn tak preferuje observaci fiducialu
        blízko startu, ne random.

        Verifikuje localized_waypoint_id vůči `meta.start_waypoint_id` a
        pokud se liší, raise (abort místo tiché divné chůze).
        """
        from app.models import LocalizationStrategy
        from spot_operator.robot.localize_strict import localize_at_start

        if meta.fiducial_id is None:
            # Mapa nemá zapsaný fiducial_id (recording bug).
            _log.warning(
                "Map %s nemá meta.fiducial_id — používám FIDUCIAL_NEAREST jako last resort.",
                meta.name,
            )
            self._navigator.localize(strategy=LocalizationStrategy.FIDUCIAL_NEAREST)
            # PR-05 FIND-094: po fallback localize ověřit, že jsme skutečně
            # na očekávaném waypointu (nebo aspoň na mapě). Bez této kontroly
            # hrozí, že FIDUCIAL_NEAREST najde jinou observaci fiducialu
            # uprostřed mapy → playback bude mis-navigate.
            if not self._is_localized_on_current_graph():
                raise RuntimeError(
                    "FIDUCIAL_NEAREST localize skončil mimo aktuální mapu. "
                    "Přibliž Spota k fiducialu a zkus znovu."
                )
            localized_wp = self._current_localization_waypoint()
            if meta.start_waypoint_id and localized_wp and localized_wp != meta.start_waypoint_id:
                _log.warning(
                    "FIDUCIAL_NEAREST localize: robot je na waypointu %s, "
                    "očekáváno start=%s. Playback přesto zkusím, ale drift "
                    "je pravděpodobný.",
                    localized_wp[:12], meta.start_waypoint_id[:12],
                )
            return

        if not meta.start_waypoint_id:
            raise RuntimeError(
                f"Mapa '{meta.name}' nemá start_waypoint_id. Playback ji odmítl spustit."
            )

        # Hlavní cesta: strict localize s hintem na start_waypoint.
        try:
            localized_wp = localize_at_start(
                self._bundle.session,
                fiducial_id=meta.fiducial_id,
                start_waypoint_id=meta.start_waypoint_id,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Lokalizace na mapě '{meta.name}' selhala (fiducial_id="
                f"{meta.fiducial_id}, start={meta.start_waypoint_id}): {exc}. "
                f"Přibliž Spota k fiducialu a zkus znovu."
            ) from exc

        if localized_wp != meta.start_waypoint_id:
            # PR-05 FIND-095: původně tu byl druhý, nedosažitelný if
            # (po raise výše) — odstraněn. Raise je final.
            raise RuntimeError(
                "Lokalizace skončila na jiném waypointu než start mapy "
                f"({localized_wp} != {meta.start_waypoint_id})."
            )
        _log.info(
            "Localize exactly at start_waypoint %s — kalibrace OK.",
            localized_wp,
        )

    def _extract_checkpoints(self, meta: MapMetadata) -> list[CheckpointRef]:
        plan = parse_checkpoint_plan(
            meta.checkpoints_json,
            fallback_map_name=meta.name,
            fallback_start_waypoint_id=meta.start_waypoint_id,
            fallback_default_capture_sources=meta.default_capture_sources,
            fallback_fiducial_id=meta.fiducial_id,
        )
        if not plan.checkpoints:
            # Detailní příčina pro UI dialog (PR-05 FIND-103).
            if meta.checkpoints_json is None:
                raise ValueError(
                    "Mapa nemá uložené checkpoints_json (legacy nebo corrupted mapa)."
                )
            raise ValueError(
                "Mapa má prázdný seznam checkpointů (možná zrušené recording)."
            )
        # `waypoint_id` je už validated v parse_checkpoint_plan přes
        # _required_str (PR-05 FIND-102 — defensive filter odstraněn).
        return [
            CheckpointRef(
                name=cp.name,
                waypoint_id=cp.waypoint_id,
                kind=cp.kind,
                capture_sources=tuple(cp.capture_sources),
            )
            for cp in plan.checkpoints
        ]

    def _capture_at_checkpoint(self, cp: CheckpointRef) -> CaptureSummary:
        from spot_operator.robot.dual_side_capture import capture_sources
        from spot_operator.services.photo_sink import encode_bgr_to_jpeg

        expected_sources = tuple(cp.capture_sources)
        frames = capture_sources(self._poller, cp.capture_sources)
        saved_sources: list[str] = []
        failed_sources: list[str] = []
        for src in cp.capture_sources:
            bgr = frames.get(src)
            if bgr is None:
                failed_sources.append(src)
                continue
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
                saved_sources.append(src)
            except Exception as exc:
                _log.warning("save photo failed (cp=%s src=%s): %s", cp.name, src, exc)
                failed_sources.append(src)

        if not saved_sources:
            return CaptureSummary(
                status=CAPTURE_STATUS_FAILED,
                expected_sources=expected_sources,
                saved_sources=(),
                failed_sources=tuple(failed_sources or expected_sources),
                error="No photos were saved for this checkpoint.",
            )
        if failed_sources:
            return CaptureSummary(
                status=CAPTURE_STATUS_PARTIAL,
                expected_sources=expected_sources,
                saved_sources=tuple(saved_sources),
                failed_sources=tuple(failed_sources),
                error="Only part of the expected photo sources was saved.",
            )
        return CaptureSummary(
            status=CAPTURE_STATUS_OK,
            expected_sources=expected_sources,
            saved_sources=tuple(saved_sources),
            failed_sources=(),
            error=None,
        )

    def _classify_final_status(
        self, success: int, total: int, abort_reason: Optional[str]
    ) -> RunStatus:
        # PR-05 FIND-101: success == total beats abort — pokud všechny CP
        # prošly, je run completed i kdyby operátor kliknul Abort na
        # poslední moment.
        if success == total and total > 0:
            return RunStatus.completed
        if abort_reason:
            if abort_reason == "Aborted by user":
                return RunStatus.aborted
            if success == 0:
                return RunStatus.failed
            return RunStatus.partial
        return RunStatus.partial

    def _create_run_and_fail(
        self,
        *,
        meta: MapMetadata,
        operator_label: str | None,
        total: int,
        reason: str,
    ) -> None:
        """Vytvoří run (pro audit) a okamžitě ho označí failed.

        Volá se při pre-flight failure, kdy nelze vyexportovat checkpointy.
        """
        try:
            with Session() as s:
                run_code = runs_repo.generate_unique_run_code(s)
                run = runs_repo.create(
                    s,
                    run_code=run_code,
                    map_id=meta.id,
                    map_name_snapshot=meta.name,
                    checkpoints_total=total,
                    operator_label=operator_label,
                    start_waypoint_id=meta.start_waypoint_id,
                    checkpoint_results_json=[],
                )
                s.commit()
                self._run_id = run.id
            self._finalize_failed_run(reason)
        except Exception as exc:
            _log.exception("Failed to create audit run for pre-flight failure: %s", exc)

    def _finalize_failed_run(self, reason: str) -> None:
        """Update existing run record k failed state."""
        if self._run_id is None:
            return
        try:
            with Session() as s:
                runs_repo.finish(
                    s,
                    self._run_id,
                    status=RunStatus.failed,
                    checkpoints_reached=0,
                    abort_reason=reason,
                )
                s.commit()
        except Exception as exc:
            _log.warning("Failed to finalize failed run: %s", exc)
        self._last_run_status = RunStatus.failed
        self._last_abort_reason = reason
        self.run_failed.emit(reason)

    def _record_checkpoint_result(
        self, checkpoint_result: CheckpointResult, success_count: int
    ) -> None:
        self._checkpoint_results.append(checkpoint_result)
        with Session() as s:
            runs_repo.mark_progress(
                s,
                self._run_id,
                success_count,
                checkpoint_results_json=checkpoint_results_to_payload(
                    self._checkpoint_results
                ),
            )
            s.commit()

    def _emit_progress(self, text: str) -> None:
        _log.info(text)
        self.progress.emit(text)


__all__ = ["PlaybackService", "CheckpointRef"]
