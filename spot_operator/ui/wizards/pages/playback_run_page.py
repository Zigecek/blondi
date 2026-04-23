"""Krok 5 playbacku: START → autonomní průjezd + live view + STOP."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QWizardPage,
)

from spot_operator.config import AppConfig
from spot_operator.constants import CAMERA_FRONT_COMPOSITE
from spot_operator.logging_config import get_logger
from spot_operator.services.map_storage import MapMetadata
from spot_operator.services.playback_service import PlaybackService
from spot_operator.ui.common.dialogs import confirm_dialog, error_dialog
from spot_operator.ui.common.estop_floating import EstopFloating

_log = get_logger(__name__)


class _RunThread(QThread):
    """QThread wrapper pro playback_service.run_all_checkpoints."""

    finished_ok = Signal(int)
    failed = Signal(str)

    def __init__(self, service: PlaybackService, meta: MapMetadata, operator: Optional[str]):
        super().__init__()
        self._service = service
        self._meta = meta
        self._operator = operator

    def run(self) -> None:  # noqa: D401
        try:
            run_id = self._service.run_all_checkpoints(self._meta, operator_label=self._operator)
            self.finished_ok.emit(run_id)
        except Exception as exc:
            self.failed.emit(str(exc))


class _ReturnHomeThread(QThread):
    """Thread pro asynchronní return_home."""

    done = Signal()

    def __init__(self, service: PlaybackService, start_wp_id: str):
        super().__init__()
        self._service = service
        self._start_wp_id = start_wp_id

    def run(self) -> None:  # noqa: D401
        try:
            self._service.return_home(self._start_wp_id)
        finally:
            self.done.emit()


class PlaybackRunPage(QWizardPage):
    """Autonomní run page: START, live view, progress, STOP s návratem, E-STOP."""

    def __init__(
        self,
        config: AppConfig,
        *,
        ocr_worker=None,  # noqa: ANN001 — OcrWorker; lazy typing
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._config = config
        self._ocr_worker = ocr_worker
        self._service: Optional[PlaybackService] = None
        self._meta: Optional[MapMetadata] = None
        self._run_thread: Optional[_RunThread] = None
        self._return_home_thread: Optional[_ReturnHomeThread] = None
        self._estop_widget: Optional[EstopFloating] = None
        self._image_pipeline = None
        self._live_view = None
        self._run_id: Optional[int] = None
        self._run_finished = False
        # Udržuje stav zda jsme připojili OCR signály (abychom je nepropojili opakovaně).
        self._ocr_signals_connected = False

        self.setTitle("5. Spuštění autonomní jízdy")
        self.setSubTitle("Klikni START. Jakmile průjezd skončí, přejdi na výsledek.")

        root = QHBoxLayout(self)

        # Live view centrum
        self._live_container = QFrame()
        self._live_container.setFrameShape(QFrame.Box)
        self._live_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        live_layout = QVBoxLayout(self._live_container)
        live_layout.setContentsMargins(0, 0, 0, 0)
        self._live_placeholder = QLabel("(live view)")
        self._live_placeholder.setAlignment(Qt.AlignCenter)
        self._live_placeholder.setStyleSheet("background:#111; color:#888;")
        live_layout.addWidget(self._live_placeholder)
        root.addWidget(self._live_container, stretch=1)

        # Pravý panel
        side = QFrame()
        side.setFixedWidth(320)
        side_layout = QVBoxLayout(side)

        self._btn_start = QPushButton("▶ START")
        self._btn_start.setStyleSheet(
            "QPushButton { background:#2e7d32; color:white; font-size:18px;"
            " font-weight:bold; padding:14px; border-radius:6px; }"
        )
        self._btn_start.clicked.connect(self._on_start)
        side_layout.addWidget(self._btn_start)

        self._progress = QProgressBar()
        self._progress.setRange(0, 1)
        self._progress.setVisible(False)
        side_layout.addWidget(self._progress)

        self._status_label = QLabel("Nahrávám mapu do Spota...")
        self._status_label.setWordWrap(True)
        side_layout.addWidget(self._status_label)

        side_layout.addSpacing(8)

        self._log_list = QListWidget()
        self._log_list.setMaximumHeight(240)
        side_layout.addWidget(self._log_list, stretch=1)

        self._btn_stop_return = QPushButton("■ STOP s návratem domů")
        self._btn_stop_return.setStyleSheet(
            "QPushButton { background:#f9a825; font-weight:bold; padding:8px; }"
        )
        self._btn_stop_return.clicked.connect(self._on_stop_return)
        self._btn_stop_return.setVisible(False)
        side_layout.addWidget(self._btn_stop_return)

        self._btn_next = QPushButton("Pokračovat k výsledku ▶")
        self._btn_next.setEnabled(False)
        self._btn_next.clicked.connect(self._go_next)
        side_layout.addWidget(self._btn_next)

        root.addWidget(side)

    def initializePage(self) -> None:
        wizard = self.wizard()
        bundle = wizard.bundle()  # type: ignore[attr-defined]
        if bundle is None:
            error_dialog(self, "Chyba", "Spot není připojen.")
            return

        self._service = PlaybackService(bundle)
        self._wire_service_signals()

        # Uplay mapu + lokalizace v pozadí
        from spot_operator.ui.common.workers import FunctionWorker

        map_id = wizard.property("selected_map_id")
        if map_id is None:
            error_dialog(self, "Chyba", "Není vybraná mapa.")
            return

        self._progress.setVisible(True)
        self._progress.setRange(0, 0)
        self._btn_start.setEnabled(False)

        worker = FunctionWorker(self._service.prepare_map, int(map_id))
        worker.finished_ok.connect(self._on_prepare_ok)
        worker.failed.connect(self._on_prepare_failed)
        worker.start()
        self._prepare_worker = worker

        self._ensure_live_view(bundle)
        self._ensure_estop_widget(wizard, bundle)

    def isComplete(self) -> bool:
        return self._run_finished

    def validatePage(self) -> bool:
        return self._run_finished

    def cleanupPage(self) -> None:
        """Návrat zpět (nebo safe_abort z wizardu) — uklidit všechny thready."""
        self._teardown()

    def _teardown(self) -> None:
        """Bezpečně uklidí run thread, return home thread, image pipeline a
        floating E-Stop widget. Idempotentní."""
        # 1) Pokud běží autonomní run, požádej o přerušení a počkej.
        if self._run_thread is not None and self._run_thread.isRunning():
            try:
                if self._service is not None:
                    self._service.request_abort()
            except Exception as exc:
                _log.warning("request_abort during teardown failed: %s", exc)
            try:
                self._run_thread.wait(5000)
            except Exception:
                pass

        # 2) Pokud běží return home, počkej delší chvíli (až 10 s).
        if self._return_home_thread is not None and self._return_home_thread.isRunning():
            try:
                self._return_home_thread.wait(10000)
            except Exception:
                pass

        # 3) Odpoj OCR signály aby po zavření stránky neběžely queued call-y.
        if self._ocr_worker is not None and self._ocr_signals_connected:
            try:
                self._ocr_worker.photo_processed.disconnect(self._on_ocr_done)
                self._ocr_worker.photo_failed.disconnect(self._on_ocr_failed)
            except Exception:
                pass
            self._ocr_signals_connected = False

        # 4) Zastav image pipeline.
        if self._image_pipeline is not None:
            try:
                if hasattr(self._image_pipeline, "stop"):
                    self._image_pipeline.stop()
                self._image_pipeline.quit()
                self._image_pipeline.wait(2000)
            except Exception as exc:
                _log.warning("ImagePipeline teardown failed: %s", exc)
            self._image_pipeline = None

        # 5) Skryj E-Stop widget.
        if self._estop_widget is not None:
            try:
                self._estop_widget.hide()
                self._estop_widget.deleteLater()
            except Exception:
                pass
            self._estop_widget = None

        # 6) Smaž temp extrahovanou mapu.
        if self._service is not None:
            try:
                self._service.cleanup()
            except Exception as exc:
                _log.warning("PlaybackService.cleanup failed: %s", exc)

        _log.info("PlaybackRunPage teardown complete")

    # ---- Slots ----

    def _on_prepare_ok(self, meta: MapMetadata) -> None:
        self._meta = meta
        self._progress.setVisible(False)
        self._btn_start.setEnabled(True)
        self._status_label.setText("Mapa nahraná a lokalizováno. Klikni START.")

    def _on_prepare_failed(self, reason: str) -> None:
        self._progress.setVisible(False)
        self._btn_start.setEnabled(False)
        self._status_label.setText(f"❌ Příprava selhala: {reason}")
        error_dialog(self, "Chyba", f"Nelze připravit mapu: {reason}")

    def _on_start(self) -> None:
        if self._service is None or self._meta is None:
            return
        self._btn_start.setVisible(False)
        self._btn_stop_return.setVisible(True)
        self._progress.setVisible(True)
        self._progress.setRange(0, self._meta.checkpoints_count or 0)
        self._progress.setValue(0)

        operator = self._config.operator_label or None
        self._run_thread = _RunThread(self._service, self._meta, operator)
        self._run_thread.finished_ok.connect(self._on_run_finished)
        self._run_thread.failed.connect(self._on_run_failed)
        self._run_thread.start()

    def _on_stop_return(self) -> None:
        if self._service is None or self._meta is None:
            return
        if not confirm_dialog(
            self,
            "Opravdu zastavit?",
            "Autonomní jízda se zastaví a Spot se vrátí k fiducialu.",
            destructive=True,
        ):
            return
        start_wp = self._meta.start_waypoint_id
        if not start_wp:
            error_dialog(self, "Chyba", "Nemáme start_waypoint_id, návrat domů nelze spustit.")
            return
        self._service.request_abort()
        self._return_home_thread = _ReturnHomeThread(self._service, start_wp)
        self._return_home_thread.done.connect(self._on_return_home_done)
        self._return_home_thread.start()
        self._btn_stop_return.setEnabled(False)
        self._append_log("Přerušuji běh, návrat domů...")

    def _on_return_home_done(self) -> None:
        self._append_log("Návrat domů dokončen.")
        self._btn_stop_return.setEnabled(True)

    def _on_run_finished(self, run_id: int) -> None:
        self._run_id = run_id
        self._run_finished = True
        self.wizard().setProperty("completed_run_id", run_id)
        self._status_label.setText("✓ Jízda dokončena.")
        self._btn_next.setEnabled(True)
        self._btn_stop_return.setVisible(False)
        self.completeChanged.emit()

    def _on_run_failed(self, reason: str) -> None:
        self._run_finished = True
        self.wizard().setProperty(
            "completed_run_id", self._run_id if self._run_id is not None else -1
        )
        self._status_label.setText(f"⚠ Jízda skončila s chybou: {reason}")
        self._btn_next.setEnabled(True)
        self._btn_stop_return.setVisible(False)
        self.completeChanged.emit()

    def _go_next(self) -> None:
        self.wizard().next()

    def _append_log(self, text: str) -> None:
        self._log_list.addItem(text)
        self._log_list.scrollToBottom()

    def _wire_service_signals(self) -> None:
        if self._service is None:
            return
        self._service.progress.connect(self._append_log)
        self._service.run_started.connect(lambda rid: self._append_log(f"▶ Run id={rid}"))
        self._service.checkpoint_reached.connect(
            lambda idx, total, name: self._on_progress(idx, total, name)
        )
        self._service.photo_taken.connect(
            lambda pid, src: self._append_log(f"  📷 foto id={pid} ({src})")
        )
        self._service.run_completed.connect(
            lambda s, t: self._append_log(f"✓ Dokončeno: {s}/{t}")
        )
        self._service.run_failed.connect(
            lambda reason: self._append_log(f"⚠ Chyba: {reason}")
        )

        # Propojení OCR worker signálů — live feedback v log listu.
        if self._ocr_worker is not None and not self._ocr_signals_connected:
            self._ocr_worker.photo_processed.connect(self._on_ocr_done)
            self._ocr_worker.photo_failed.connect(self._on_ocr_failed)
            self._ocr_signals_connected = True

    def _on_progress(self, idx: int, total: int, name: str) -> None:
        self._progress.setRange(0, total)
        self._progress.setValue(idx)
        self._append_log(f"→ {idx}/{total}: {name}")

    def _on_ocr_done(self, photo_id: int, count: int) -> None:
        """OCR worker dokončil zpracování fotky. Zobraz přečtené SPZ.

        POZN: signál přichází z worker threadu, Qt ho doručí queued do UI threadu.
        Pokud stránka už není viditelná (wizard se zavřel), ignoruj.
        """
        if not self.isVisible():
            return
        if count == 0:
            self._append_log(f"  (foto {photo_id}: SPZ nenalezena)")
            return
        # Načti přečtené SPZ texty z DB.
        try:
            from spot_operator.db.engine import Session
            from spot_operator.db.repositories import detections_repo

            with Session() as s:
                detections = detections_repo.list_for_photo(s, photo_id)
            plates = ", ".join(d.plate_text or "?" for d in detections) or "?"
            self._append_log(f"  🔤 SPZ: {plates}")
        except Exception as exc:
            _log.warning("Could not load detections for photo %s: %s", photo_id, exc)
            self._append_log(f"  🔤 SPZ: {count} detekce (načtení selhalo)")

    def _on_ocr_failed(self, photo_id: int, reason: str) -> None:
        if not self.isVisible():
            return
        self._append_log(f"  ⚠ OCR {photo_id} selhal: {reason}")

    def _ensure_live_view(self, bundle) -> None:
        if self._image_pipeline is not None:
            return
        try:
            from app.image_pipeline import ImagePipeline
            from app.robot.images import ImagePoller
            from app.ui.live_view_widget import LiveViewWidget
        except Exception as exc:
            _log.warning("ImagePipeline unavailable: %s", exc)
            return
        layout = self._live_container.layout()
        self._live_placeholder.setParent(None)
        self._live_view = LiveViewWidget(self._live_container)
        layout.addWidget(self._live_view)

        # POZOR: ImagePipeline v konstruktoru chce `ImagePoller` instanci
        # (ne session). Jinak poller.capture() tiše selhá a frame_ready
        # nikdy nepřijde.
        poller = ImagePoller(bundle.session)
        self._image_pipeline = ImagePipeline(poller)
        # Default source = front_composite (stitched přední obraz).
        self._image_pipeline.set_source(CAMERA_FRONT_COMPOSITE)
        self._image_pipeline.set_autonomous(True)
        self._image_pipeline.frame_ready.connect(self._live_view.update_frame)
        try:
            self._image_pipeline.start()
        except Exception as exc:
            _log.warning("ImagePipeline start failed: %s", exc)

    def _ensure_estop_widget(self, wizard, bundle) -> None:
        if self._estop_widget is not None:
            return
        if bundle.estop is None:
            return
        self._estop_widget = EstopFloating(
            self,
            on_trigger=bundle.estop.trigger,
            on_release=self._handle_estop_release,
        )
        wizard.set_estop_callback(bundle.estop.trigger, self._handle_estop_release)
        self._estop_widget.show()

    def _handle_estop_release(self) -> None:
        """Uvolni E-Stop a abortuj autonomní běh.

        V playbacku je E-Stop = konec jízdy. Service request_abort + run_thread
        by dokončí. Pak operátor přejde na result page s `run_failed` stavem.
        """
        bundle = self.wizard().bundle() if self.wizard() else None
        if bundle is None or bundle.estop is None:
            return
        try:
            bundle.estop.release()
            _log.info("E-Stop released during playback")
        except Exception as exc:
            _log.exception("E-Stop release failed: %s", exc)
            raise
        if self._service is not None:
            try:
                self._service.request_abort()
            except Exception as exc:
                _log.warning("playback request_abort after estop release: %s", exc)


__all__ = ["PlaybackRunPage"]
