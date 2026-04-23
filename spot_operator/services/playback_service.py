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
    PLAYBACK_AVOIDANCE_STRENGTH,
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
        """Spustí autonomní průjezd checkpointů. Vrátí run_id."""
        checkpoints = self._extract_checkpoints(meta)
        if not checkpoints:
            raise RuntimeError("Mapa neobsahuje žádné checkpointy.")

        # KRITICKÁ pojistka: pokud robot není lokalizován v aktuální mapě,
        # `navigate_to` by na základě stale odometrie jel zcela špatným směrem
        # → timeout → "běží na konec mapy". Abort raději teď, než to způsobí
        # fyzickou situaci.
        if not self._is_localized_on_current_graph():
            raise RuntimeError(
                "Robot není lokalizován na aktuální mapě. Vrať se k fiducialu "
                "a zkus playback znovu."
            )

        # Diagnostika před spuštěním: kde si robot myslí že je, a v jakém
        # pořadí budeme navštěvovat checkpointy. Když user hlásí "robot jede
        # na nejvzdálenější místo", tohle ukáže co skutečně GraphNav vidí.
        localized_wp = self._current_localization_waypoint()
        expected_start = meta.start_waypoint_id or "(neznámý)"
        _log.info(
            "Playback start — localized waypoint: %s, expected start_waypoint_id: %s",
            localized_wp or "(neznámý)", expected_start,
        )
        if localized_wp and expected_start != "(neznámý)" and localized_wp != expected_start:
            _log.warning(
                "Mis-localization suspicion: robot je lokalizovaný na %s, "
                "ale start mapy je %s. Pokud trasa jede divným směrem, "
                "přibliž Spota k fiducialu a zkus znovu.",
                localized_wp, expected_start,
            )
        _log.info(
            "Checkpoint order (%d): %s",
            len(checkpoints),
            ", ".join(f"{c.name}->{c.waypoint_id[:12]}..." for c in checkpoints),
        )

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

        # Nastav globální obstacle avoidance strength pro playback.
        # GraphNav nepřijímá padding přes TravelParams, ale robot si pamatuje
        # mobility state → synchro_stand s požadovanou strength to nastaví.
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

        success = 0
        total = len(checkpoints)
        abort_reason: Optional[str] = None
        consecutive_nav_fails = 0

        for idx, cp in enumerate(checkpoints, start=1):
            if self._abort_requested:
                abort_reason = "Aborted by user"
                break
            try:
                self.checkpoint_reached.emit(idx, total, cp.name)
                self._emit_progress(f"Navigate to {cp.name} ({idx}/{total})")
                result = self._navigate_with_retry(cp)
                if not result.ok:
                    consecutive_nav_fails += 1
                    abort_reason = f"navigate failed at {cp.name}: {result.message}"
                    _log.warning(abort_reason)
                    # Po 3 po sobě jdoucích selháních navigace ukonči run —
                    # robot je pravděpodobně mis-localized nebo má fyzickou
                    # překážku, další checkpointy budou taky fail.
                    if consecutive_nav_fails >= 3:
                        _log.error(
                            "Aborting run — %d po sobě jdoucích selhání navigace. "
                            "Robot je pravděpodobně mis-localized nebo má překážku.",
                            consecutive_nav_fails,
                        )
                        break
                    # Pokud byl retry vyčerpán i po re-localize (LOST/TIMEOUT),
                    # nemá smysl pokračovat — robot je v nefunkčním stavu.
                    if self._should_retry_outcome(result):
                        _log.error(
                            "Aborting run — re-localize nepomohl, robot nemůže "
                            "pokračovat (%s).", result.outcome.value,
                        )
                        break
                    continue

                if cp.kind == "checkpoint" and cp.capture_sources:
                    self._capture_at_checkpoint(cp)

                success += 1
                consecutive_nav_fails = 0
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

    def _should_retry_outcome(self, result) -> bool:  # noqa: ANN001 — NavigationResult
        """Rozšíření ``result.is_localization_loss`` o TIMEOUT.

        Autonomy definuje ``is_localization_loss`` jen pro LOST/NOT_LOCALIZED.
        Ale TIMEOUT typicky znamená, že robot je mis-localized (nehýbe se ke
        špatné cílové pozici a 30 s okno vyprší). Po re-localize má velkou
        šanci uspět, takže retry-ujeme stejně.
        """
        from app.models import NavigationOutcome

        if result.is_localization_loss:
            return True
        return result.outcome == NavigationOutcome.TIMEOUT

    def _navigate_with_retry(self, cp: "CheckpointRef", max_retries: int = 2):
        """Wrapper kolem `navigator.navigate_to` s re-localize při LOST/TIMEOUT.

        Autonomy má podobnou logiku v `_NavigationWorker._navigate_with_retry`.
        Bez ní při první ztrátě lokalizace v playbacku robot jen pokračuje
        dalším checkpointem a mis-navigates dál a dál.
        """
        attempt = 0
        last_result = None
        while attempt <= max_retries:
            result = self._navigator.navigate_to(
                cp.waypoint_id, timeout=PLAYBACK_NAV_TIMEOUT_SEC
            )
            last_result = result
            # Diagnostický log: kde robot skončil po navigate_to volání.
            # Při bugu "jede na nejvzdálenější místo" tohle ukáže, kam to
            # GraphNav reálně odvezlo.
            post_wp = self._current_localization_waypoint()
            _log.info(
                "Navigate to %s (target=%s...) → outcome=%s, robot now at waypoint %s",
                cp.name, cp.waypoint_id[:12],
                result.outcome.value, post_wp[:12] if post_wp else "(neznámý)",
            )
            if result.ok:
                return result
            if not self._should_retry_outcome(result):
                # Ne lokalizační / timeout problem — retry nemá smysl.
                return result
            attempt += 1
            if attempt > max_retries:
                break
            _log.warning(
                "Navigate to %s failed (%s). Re-localizing and retry %d/%d...",
                cp.name,
                result.message,
                attempt,
                max_retries,
            )
            self._emit_progress(
                f"{cp.name}: {result.outcome.value} — re-localize + retry "
                f"{attempt}/{max_retries}"
            )
            try:
                if not self._navigator.relocalize_nearest_fiducial():
                    _log.warning("Re-localize failed; retry will likely fail too.")
            except Exception as exc:
                _log.warning("Re-localize raised: %s", exc)
        return last_result

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
            return

        if not meta.start_waypoint_id:
            # Žádný hint — spadneme na autonomy SPECIFIC_FIDUCIAL bez initial_guess.
            _log.warning(
                "Map %s nemá start_waypoint_id — lokalizuji bez hintu (může být mis-lokalizováno).",
                meta.name,
            )
            try:
                self._navigator.localize(
                    strategy=LocalizationStrategy.SPECIFIC_FIDUCIAL,
                    fiducial_id=meta.fiducial_id,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Lokalizace na mapě '{meta.name}' selhala: "
                    f"SPECIFIC_FIDUCIAL s id={meta.fiducial_id} nenalezlo "
                    f"fiducial v kameře Spota ({exc}). Přibliž Spota k fiducialu."
                ) from exc
            return

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

        # Ověření: skončili jsme opravdu blízko startu?
        if localized_wp != meta.start_waypoint_id:
            # Není to nutně chyba — bosdyn může vrátit jiný blízký waypoint
            # pokud robot stojí mírně stranou od start_waypoint. Ale pokud
            # je rozdíl velký, bude to problém v navigaci. Logujem hlasitě.
            _log.warning(
                "Localize drift: bosdyn vrátil waypoint %s, očekávali jsme start=%s. "
                "Pokud robot jede zmatečně, přibliž ho blíž k fiducialu a zkus znovu.",
                localized_wp, meta.start_waypoint_id,
            )
        else:
            _log.info(
                "Localize exactly at start_waypoint %s — kalibrace OK.",
                localized_wp,
            )

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
