"""Krok 4 recordingu: WASD teleop + fotky + waypointy + live view."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QWizardPage,
)

from spot_operator.constants import (
    CAMERA_FRONT_COMPOSITE,
    CAMERA_LEFT,
    CAMERA_RIGHT,
    PREFERRED_LEFT_CANDIDATES,
    PREFERRED_RIGHT_CANDIDATES,
    TELEOP_DEFAULT_SPEED_PROFILE,
    TELEOP_SPEED_LABELS,
    TELEOP_SPEED_PROFILES,
    UI_PHOTO_OVERLAY_MIN_WIDTH,
    UI_SIDE_PANEL_WIDTH,
    WASD_AVOIDANCE_STRENGTH,
    pick_side_source,
)
from spot_operator.logging_config import get_logger
from spot_operator.services.contracts import CaptureFailedError, validate_sources_known
from spot_operator.services.recording_service import RecordingService
from spot_operator.ui.common.dialogs import confirm_dialog, error_dialog, info_dialog
from spot_operator.ui.common.estop_floating import EstopFloating
from spot_operator.ui.common.photo_confirm_overlay import PhotoConfirmOverlay

_log = get_logger(__name__)


class TeleopRecordPage(QWizardPage):
    """Jádro recording wizardu. Auto-start nahrávání + WASD + tlačítka fotek."""

    recording_finished = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setTitle("4. Průjezd trasy + focení")
        self.setSubTitle(
            "Nahrávání běží. Ovládej Spota klávesami WASD, fotit klávesami V N B,"
            " waypoint bez fotky klávesou C. U každého auta si vyber stranu focení."
        )

        self._service: Optional[RecordingService] = None
        self._estop_widget: Optional[EstopFloating] = None
        self._image_pipeline = None
        self._live_view = None
        self._keys_pressed: set[int] = set()
        # Photo confirm overlay — aktivní jen během náhledu před uložením.
        # None mimo okamžik preview. Tlačítka Foto jsou disabled, když overlay běží.
        self._current_overlay: Optional[PhotoConfirmOverlay] = None
        # Konkrétní jména image sources — resolved v initializePage přes
        # pick_side_source proti wizard.property("available_sources").
        # Default je hardcoded CAMERA_LEFT/RIGHT jako fallback.
        self._camera_left: str = CAMERA_LEFT
        self._camera_right: str = CAMERA_RIGHT

        # Velocity keep-alive timer: Spot SDK velocity commands mají default
        # end_time ~0.5 s (VELOCITY_CMD_DURATION v autonomy). Autonomy tick
        # je 100 ms = 10 Hz. Při slabší Wi-Fi (latence 150-300 ms) pomalejší
        # tick nestíhá a command expiruje než dorazí → ExpiredError + robot
        # na chvíli stojí → seká se WASD i kamera.
        self._velocity_timer = QTimer(self)
        self._velocity_timer.setInterval(100)
        self._velocity_timer.timeout.connect(self._on_velocity_tick)

        root = QHBoxLayout(self)

        # --- Centr: live view ---
        self._live_view_container = QFrame()
        self._live_view_container.setFrameShape(QFrame.Box)
        self._live_view_container.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )
        live_layout = QVBoxLayout(self._live_view_container)
        live_layout.setContentsMargins(0, 0, 0, 0)
        self._live_placeholder = QLabel("(live view)")
        self._live_placeholder.setAlignment(Qt.AlignCenter)
        self._live_placeholder.setStyleSheet("background:#222; color:#888;")
        live_layout.addWidget(self._live_placeholder)
        root.addWidget(self._live_view_container, stretch=1)

        # --- Pravý panel: tlačítka + čítače ---
        side = QFrame()
        side.setFixedWidth(UI_SIDE_PANEL_WIDTH)
        side_layout = QVBoxLayout(side)

        self._status_rec = QLabel("● NAHRÁVÁM")
        self._status_rec.setStyleSheet(
            "color:#c62828; font-weight:bold; font-size:18px;"
        )
        side_layout.addWidget(self._status_rec)

        self._counter = QLabel("Waypointů: 0 · Fotek: 0")
        self._counter.setStyleSheet("font-size:13px;")
        side_layout.addWidget(self._counter)

        self._battery = QLabel("Baterie: --")
        self._battery.setStyleSheet("font-size:13px;")
        side_layout.addWidget(self._battery)

        side_layout.addSpacing(12)

        # Foto tlačítka (V/N/B) jsou **disabled** dokud není 1. Waypoint (C).
        # Důvod: pokud by operátor rovnou fotil bez Waypointu, start_waypoint_id
        # by se svázal na první CP (mis-match s fiducial observací
        # v mapě → playback se mis-localizuje → robot jede náhodně). Viz
        # PR-02 FIND-072 (root cause hlášeného bugu).
        self._btn_photo_left = QPushButton("📷 Foto vlevo         (V)")
        self._btn_photo_left.clicked.connect(self._capture_left)
        self._btn_photo_left.setEnabled(False)
        side_layout.addWidget(self._btn_photo_left)

        self._btn_photo_right = QPushButton("📷 Foto vpravo        (N)")
        self._btn_photo_right.clicked.connect(self._capture_right)
        self._btn_photo_right.setEnabled(False)
        side_layout.addWidget(self._btn_photo_right)

        self._btn_photo_both = QPushButton("📷 Foto z obou stran  (B)")
        self._btn_photo_both.clicked.connect(self._capture_both)
        self._btn_photo_both.setEnabled(False)
        side_layout.addWidget(self._btn_photo_both)

        self._btn_waypoint = QPushButton("📍 Waypoint           (C)")
        self._btn_waypoint.clicked.connect(self._add_waypoint)
        side_layout.addWidget(self._btn_waypoint)

        self._start_hint = QLabel(
            "<b>1) Stiskni 'Waypoint' (C) u startu (fiducial)</b><br>"
            "<span style='color:#666;'>Foto tlačítka se odemknou po prvním "
            "waypointu.</span>"
        )
        self._start_hint.setTextFormat(Qt.RichText)
        self._start_hint.setWordWrap(True)
        self._start_hint.setStyleSheet(
            "background:#fff3e0; padding:6px; border:1px solid #ffb74d;"
        )
        side_layout.addWidget(self._start_hint)

        side_layout.addSpacing(12)

        # Rychlost pohybu (Slow/Normal/Fast) — mapuje se na m/s + rad/s dle profilu.
        speed_row = QHBoxLayout()
        speed_row.addWidget(QLabel("Rychlost:"))
        self._speed_combo = QComboBox()
        for key, label in TELEOP_SPEED_LABELS.items():
            lin, ang = TELEOP_SPEED_PROFILES[key]
            self._speed_combo.addItem(f"{label} ({lin:.2f} m/s)", key)
        default_idx = self._speed_combo.findData(TELEOP_DEFAULT_SPEED_PROFILE)
        if default_idx >= 0:
            self._speed_combo.setCurrentIndex(default_idx)
        speed_row.addWidget(self._speed_combo, stretch=1)
        side_layout.addLayout(speed_row)

        self._hint = QLabel(
            "<small><b>WASD</b> = pohyb, <b>QE</b> = rotace, "
            "<b>Mezerník</b> = stop, <b>F1</b> = E-STOP<br>"
            "<b>V</b> = foto vlevo, <b>N</b> = foto vpravo, "
            "<b>B</b> = obě strany, <b>C</b> = waypoint</small>"
        )
        self._hint.setTextFormat(Qt.RichText)
        side_layout.addWidget(self._hint)

        side_layout.addStretch(1)

        self._btn_finish = QPushButton("Dokončit nahrávání")
        self._btn_finish.setStyleSheet(
            "QPushButton { background:#f9a825; font-weight:bold; padding:10px; }"
        )
        self._btn_finish.clicked.connect(self._on_finish_clicked)
        side_layout.addWidget(self._btn_finish)

        root.addWidget(side)

        # Timer pro poll baterie
        self._battery_timer = QTimer(self)
        self._battery_timer.setInterval(5000)
        self._battery_timer.timeout.connect(self._poll_battery)

        self.setFocusPolicy(Qt.StrongFocus)

    def initializePage(self) -> None:
        wizard = self.wizard()
        bundle = wizard.bundle()  # type: ignore[attr-defined]
        if bundle is None:
            error_dialog(self, "Chyba", "Není navázané spojení se Spotem.")
            return
        state = wizard.recording_state()  # type: ignore[attr-defined]

        # Auto-resolve konkrétní jména image sources podle toho, co Spot
        # advertisuje (některé firmwary: `left_fisheye_image`, jiné
        # `frontleft_fisheye_image`). available_sources ukládá LoginPage do
        # wizard property po úspěšném connect.
        available = list(state.available_sources)
        if available:
            left = pick_side_source(available, PREFERRED_LEFT_CANDIDATES)
            right = pick_side_source(available, PREFERRED_RIGHT_CANDIDATES)
            if left is None or right is None:
                raise RuntimeError(
                    "Spot neadvertizuje očekávané levé/pravé kamery; "
                    f"dostupné zdroje: {', '.join(available)}"
                )
            self._camera_left = left
            self._camera_right = right
            _log.info(
                "Teleop capture sources: left=%s right=%s",
                self._camera_left,
                self._camera_right,
            )
        else:
            _log.warning(
                "No available_sources from LoginPage; using hardcoded defaults %s, %s",
                self._camera_left,
                self._camera_right,
            )

        self._service = RecordingService(bundle)
        fiducial_id = state.fiducial_id
        # `default_capture_sources` v DB mapě je "obě strany, které tenhle robot
        # umí" — operátor u jednotlivých checkpointů volí individuálně přes
        # tlačítka [ ] P.
        default_sources = [self._camera_left, self._camera_right]

        try:
            self._service.start(
                map_name_prefix="rec",
                default_capture_sources=default_sources,
                fiducial_id=int(fiducial_id) if fiducial_id is not None else None,
            )
        except Exception as exc:
            _log.exception("Failed to start recording: %s", exc)
            error_dialog(self, "Chyba", f"Nelze spustit nahrávání: {exc}")
            return

        # Sdílíme service s následující SaveMapPage přes wizard property
        # (místo křehkého iterování přes pageIds()).
        state.recording_service = self._service

        self._ensure_image_pipeline(bundle)
        self._ensure_estop_widget(wizard, bundle)
        self._battery_timer.start()
        # Velocity keep-alive — Spot už stojí z FiducialPage, takže timer
        # může běžet rovnou. Tick pošle velocity jen když jsou klávesy drženy.
        self._velocity_timer.start()
        self.setFocus()

    def cleanupPage(self) -> None:
        """Qt volá při návratu zpět. Zde bychom měli být v safe state — neumožníme to."""
        # RecordingWizard disable backButton, takže tohle by se nemělo spustit
        # uprostřed recordingu. Pokud se sem dostaneme (typicky zavření okna →
        # safe_abort → naše explicitní volání), provedeme plný teardown.
        self._teardown()

    def _teardown(self) -> None:
        """Bezpečně uklidí všechny thready a widgets.

        Volat jde opakovaně (idempotentní) — `cleanupPage` + `closeEvent` by
        mohly obě trigger nakonec stejnou cestu.
        """
        # 0) Zavři případný photo confirm overlay.
        if self._current_overlay is not None:
            try:
                self._current_overlay.teardown()
                self._current_overlay.close()
                self._current_overlay.deleteLater()
            except Exception:
                pass
            self._current_overlay = None

        # 1) Zastav poll baterie a velocity keep-alive.
        try:
            self._battery_timer.stop()
        except Exception:
            pass
        try:
            self._velocity_timer.stop()
        except Exception:
            pass

        # 2) Soft stop robot (autonomy dispatcher .stop() pošle velocity 0).
        bundle = self.wizard().bundle() if self.wizard() is not None else None
        if bundle is not None and getattr(bundle, "move_dispatcher", None) is not None:
            try:
                bundle.move_dispatcher.stop()
            except Exception as exc:
                _log.debug("move_dispatcher stop failed during teardown: %s", exc)

        # 3) Zastav image pipeline (QThread).
        if self._image_pipeline is not None:
            try:
                self._image_pipeline.stop() if hasattr(self._image_pipeline, "stop") else None
                self._image_pipeline.quit()
                self._image_pipeline.wait(2000)
            except Exception as exc:
                _log.warning("ImagePipeline teardown failed: %s", exc)
            self._image_pipeline = None

        # 4) Skryj floating E-Stop widget (zůstává v paměti, ale není visible).
        if self._estop_widget is not None:
            try:
                self._estop_widget.hide()
                self._estop_widget.deleteLater()
            except Exception:
                pass
            self._estop_widget = None

        # 5) Pokud recording stále běží (např. nečekané zavření), abortuj bez uložení.
        if self._service is not None and self._service.is_recording:
            try:
                self._service.abort()
            except Exception as exc:
                _log.warning("RecordingService abort failed: %s", exc)
        wizard = self.wizard()
        state_getter = getattr(wizard, "recording_state", None)
        if callable(state_getter):
            state = state_getter()
            state.recording_service = None

        _log.info("TeleopRecordPage teardown complete")

    # ---- Keyboard teleop ----

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: D401
        if event.isAutoRepeat():
            return
        key = event.key()
        self._keys_pressed.add(key)

        if key == Qt.Key_V:
            self._capture_left()
        elif key == Qt.Key_N:
            self._capture_right()
        elif key == Qt.Key_B:
            self._capture_both()
        elif key == Qt.Key_C:
            self._add_waypoint()
        elif key == Qt.Key_Space:
            self._send_velocity(0, 0, 0)
        else:
            self._update_velocity_from_keys()

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: D401
        if event.isAutoRepeat():
            return
        self._keys_pressed.discard(event.key())
        self._update_velocity_from_keys()

    def _current_speed(self) -> tuple[float, float]:
        """Aktuální (linear, angular) speed podle vybraného profile."""
        key = self._speed_combo.currentData() or TELEOP_DEFAULT_SPEED_PROFILE
        return TELEOP_SPEED_PROFILES.get(key, TELEOP_SPEED_PROFILES[TELEOP_DEFAULT_SPEED_PROFILE])

    def _update_velocity_from_keys(self) -> None:
        linear, angular = self._current_speed()
        vx = vy = vyaw = 0.0
        if Qt.Key_W in self._keys_pressed:
            vx += linear
        if Qt.Key_S in self._keys_pressed:
            vx -= linear
        if Qt.Key_A in self._keys_pressed:
            vy += linear
        if Qt.Key_D in self._keys_pressed:
            vy -= linear
        if Qt.Key_Q in self._keys_pressed:
            vyaw += angular
        if Qt.Key_E in self._keys_pressed:
            vyaw -= angular
        self._send_velocity(vx, vy, vyaw)

    def _send_velocity(self, vx: float, vy: float, vyaw: float) -> None:
        bundle = self.wizard().bundle()  # type: ignore[attr-defined]
        if bundle is None or bundle.move_dispatcher is None:
            return
        try:
            # autonomy `MoveCommandDispatcher.send_velocity(vx, vy, vyaw, ...)` —
            # nevoláme `send()`, ta metoda neexistuje.
            bundle.move_dispatcher.send_velocity(
                vx, vy, vyaw, avoidance_strength=WASD_AVOIDANCE_STRENGTH
            )
        except Exception as exc:
            _log.warning("move send_velocity failed: %s", exc)

    def _on_velocity_tick(self) -> None:
        """Periodický 5 Hz tick — re-publish aktuální velocity pokud klávesy drženy.

        Bez tohoto Spot zastaví po ~0.6 s (default SDK velocity end_time).
        """
        if not self._keys_pressed:
            return
        self._update_velocity_from_keys()

    # ---- E-Stop release ----

    def _handle_estop_release(self) -> None:
        """Volá `EstopManager.release()` + abortuje rozpracované nahrávání.

        Po release je Spot vypnutý — recording data jsou neplatná. Operátor
        je informován a musí wizard zavřít a začít znovu od FiducialPage.
        """
        bundle = self.wizard().bundle() if self.wizard() else None
        if bundle is None or bundle.estop is None:
            return
        try:
            bundle.estop.release()
            _log.info("E-Stop released during teleop recording")
        except Exception as exc:
            _log.exception("E-Stop release failed: %s", exc)
            raise

        # Recording je ztracené — abort + vyčisti state
        if self._service is not None and self._service.is_recording:
            try:
                self._service.abort()
            except Exception as exc:
                _log.warning("RecordingService abort after estop release: %s", exc)

        self._keys_pressed.clear()
        try:
            self._velocity_timer.stop()
        except Exception:
            pass

        error_dialog(
            self,
            "E-Stop uvolněn",
            "Motory byly vypnuty, nahrávání bylo zrušeno.\n\n"
            "Zavři wizard a začni znovu od kontroly fiducialu.",
        )

    # ---- Capture / waypoint ----

    def _capture_left(self) -> None:
        """Slot: tlačítko "Foto vlevo" nebo klávesa V. Otevře preview overlay."""
        self._show_photo_overlay([self._camera_left])

    def _capture_right(self) -> None:
        """Slot: tlačítko "Foto vpravo" nebo klávesa N. Otevře preview overlay."""
        self._show_photo_overlay([self._camera_right])

    def _capture_both(self) -> None:
        """Slot: tlačítko "Foto z obou stran" nebo klávesa B. Otevře preview overlay."""
        self._show_photo_overlay([self._camera_left, self._camera_right])

    # ---- Photo confirm overlay lifecycle ----

    def _show_photo_overlay(self, sources: list[str]) -> None:
        """Otevře non-modal overlay s live preview. Po potvrzení uloží fresh snímek."""
        if self._current_overlay is not None:
            return  # už běží preview, ignoruj double-click
        bundle = self.wizard().bundle()  # type: ignore[attr-defined]
        if bundle is None:
            return

        overlay = PhotoConfirmOverlay(bundle, sources, parent=self)
        overlay.confirmed.connect(self._on_photo_confirmed)
        overlay.cancelled.connect(self._on_photo_cancelled)
        # Fullscreen-ish overlay: využij většinu wizard okna.
        # Maximalizované okno má typicky 1800+ px — overlay 80% šířky, 85% výšky.
        overlay.setMinimumWidth(UI_PHOTO_OVERLAY_MIN_WIDTH)
        target_w = max(int(self.width() * 0.8), UI_PHOTO_OVERLAY_MIN_WIDTH)
        target_w = min(target_w, max(self.width() - 40, UI_PHOTO_OVERLAY_MIN_WIDTH))
        target_h = max(int(self.height() * 0.85), 500)
        target_h = min(target_h, max(self.height() - 40, 500))
        overlay.resize(target_w, target_h)
        overlay.move(
            (self.width() - overlay.width()) // 2,
            (self.height() - overlay.height()) // 2,
        )
        overlay.show()
        overlay.raise_()
        self._current_overlay = overlay
        self._set_photo_buttons_enabled(False)

    def _on_photo_confirmed(self, sources: list) -> None:
        """Operátor klikl "Vyfotit a uložit" — fresh capture přes service."""
        self._capture(list(sources))
        self._close_overlay()

    def _on_photo_cancelled(self) -> None:
        """Operátor klikl "Zrušit" — žádné uložení, žádný waypoint."""
        self._close_overlay()

    def _close_overlay(self) -> None:
        """Uklidí overlay (stop pipelines, hide, deleteLater) a re-enable tlačítka."""
        if self._current_overlay is not None:
            try:
                self._current_overlay.teardown()
                self._current_overlay.close()
                self._current_overlay.deleteLater()
            except Exception as exc:
                _log.debug("Overlay close failed: %s", exc)
            self._current_overlay = None
        self._set_photo_buttons_enabled(True)

    def _set_photo_buttons_enabled(self, enabled: bool) -> None:
        for btn in (self._btn_photo_left, self._btn_photo_right, self._btn_photo_both):
            btn.setEnabled(enabled)

    def _capture(self, sources: list[str]) -> None:
        if self._service is None:
            return
        bundle = self.wizard().bundle()  # type: ignore[attr-defined]
        if bundle is None:
            return
        from app.robot.images import ImagePoller

        try:
            state = self.wizard().recording_state()  # type: ignore[attr-defined]
            valid_sources = validate_sources_known(
                sources,
                state.available_sources,
                context="Recording checkpoint",
            )
            poller = getattr(self, "_poller", None)
            if poller is None:
                poller = ImagePoller(bundle.session)
                self._poller = poller
            try:
                result = self._service.capture_and_record_checkpoint(
                    valid_sources, image_poller=poller
                )
            except CaptureFailedError as exc:
                # Total capture failure — nabídnout retry / skip / cancel
                # místo silent demotion na waypoint (PR-02 FIND-066/078).
                self._handle_capture_failure(list(valid_sources), exc)
                return
            self._update_counter()
            if result.capture_status == "partial":
                info_dialog(
                    self,
                    "Dílčí focení",
                    "Uložila se jen část zdrojů. Checkpoint je zapsán jako partial.",
                )
        except Exception as exc:
            _log.exception("Capture failed: %s", exc)
            error_dialog(self, "Chyba při focení", str(exc))

    def _handle_capture_failure(
        self, sources: list[str], exc: CaptureFailedError
    ) -> None:
        """Dialog retry / skip / cancel při totálním selhání capture.

        Volba:
        - Retry: zkusit znovu fotit stejnými sources.
        - Skip: přidat explicit Waypoint (bez fotky) — bod zůstane v mapě.
        - Cancel: nedělat nic (operátor se sám vrátí nebo projde dál).
        """
        from PySide6.QtWidgets import QMessageBox

        box = QMessageBox(self)
        box.setWindowTitle("Focení selhalo")
        box.setIcon(QMessageBox.Warning)
        box.setText(
            f"U checkpointu {exc.name} se nepodařilo uložit žádný snímek "
            f"({len(exc.failed_sources)}/{len(sources)} selhaly)."
        )
        box.setInformativeText(
            "Co teď?\n\n"
            "• Zkusit znovu — další pokus o capture stejných stran.\n"
            "• Přeskočit — přidá explicit Waypoint (bez fotky).\n"
            "• Zrušit — nedělá nic."
        )
        retry_btn = box.addButton("Zkusit znovu", QMessageBox.AcceptRole)
        skip_btn = box.addButton("Přeskočit (Waypoint)", QMessageBox.DestructiveRole)
        box.addButton("Zrušit", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is retry_btn:
            self._capture(sources)
        elif clicked is skip_btn:
            try:
                self._service.add_unnamed_waypoint()
                self._update_counter()
            except Exception as exc2:
                _log.exception("Skip-to-waypoint failed: %s", exc2)
                error_dialog(self, "Chyba", str(exc2))
        # Cancel → no-op

    def _add_waypoint(self) -> None:
        if self._service is None:
            return
        try:
            self._service.add_unnamed_waypoint()
            self._update_counter()
            # Po prvním waypointu odemkni foto tlačítka + skryj hint.
            if self._service.waypoint_count >= 1:
                self._set_photo_buttons_enabled(True)
                self._start_hint.setVisible(False)
        except Exception as exc:
            _log.exception("Add waypoint failed: %s", exc)
            error_dialog(self, "Chyba při přidávání waypointu", str(exc))

    def _update_counter(self) -> None:
        if self._service is None:
            return
        self._counter.setText(
            f"Waypointů: {self._service.waypoint_count} · Fotek: {self._service.photo_count}"
        )

    # ---- Finish ----

    def _on_finish_clicked(self) -> None:
        if self._service is None or not self._service.is_recording:
            return
        # 0 waypointů = tvrdý blok (mapa by neměla start_waypoint_id,
        # save_map_to_db by padl). Viz PR-02 FIND-144.
        if self._service.waypoint_count == 0:
            error_dialog(
                self,
                "Žádné waypointy",
                "Mapa nemá žádný waypoint — nelze ji uložit. Stiskni Waypoint "
                "(C) u startu u fiducialu, pak projdi trasu a přidej další body.",
            )
            return
        if self._service.waypoint_count < 2:
            if not confirm_dialog(
                self,
                "Málo waypointů",
                "Zatím jsi nepřidal dost waypointů. Opravdu ukončit?",
                destructive=True,
            ):
                return
        self._send_velocity(0, 0, 0)
        # Signalizuje wizardu, že je čas přejít na save page.
        self.recording_finished.emit()
        # Next tlačítko kliknutí vyvoláme programaticky:
        self.wizard().next()

    # ---- Helpers ----

    def _ensure_image_pipeline(self, bundle) -> None:
        if self._image_pipeline is not None:
            return
        try:
            from app.image_pipeline import ImagePipeline
            from app.robot.images import ImagePoller
            from app.ui.live_view_widget import LiveViewWidget
        except Exception as exc:
            _log.warning("ImagePipeline unavailable: %s", exc)
            return
        # Swap placeholder for real widget
        layout = self._live_view_container.layout()
        self._live_placeholder.setParent(None)
        self._live_view = LiveViewWidget(self._live_view_container)
        layout.addWidget(self._live_view)

        # POZOR: ImagePipeline v konstruktoru chce `ImagePoller` instanci
        # (ne session). Sám poller nevlastní — vytvoří si ho z session.
        poller = ImagePoller(bundle.session)
        self._image_pipeline = ImagePipeline(poller)
        # Default source = front_composite (stitched přední obraz) — stejně
        # jako autonomy. Operátor vidí široký záběr pro WASD teleop. Tlačítka
        # Foto vlevo/vpravo/obě používají konkrétní single-side source
        # nezávisle na live view.
        self._image_pipeline.set_source(CAMERA_FRONT_COMPOSITE)
        self._image_pipeline.set_recording(True)
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
        # Registrujeme oba callbacky — trigger + release (toggle chování).
        self._estop_widget = EstopFloating(
            self,
            on_trigger=bundle.estop.trigger,
            on_release=self._handle_estop_release,
        )
        wizard.set_estop_callback(bundle.estop.trigger, self._handle_estop_release)
        self._estop_widget.show()

    def _poll_battery(self) -> None:
        bundle = self.wizard().bundle()  # type: ignore[attr-defined]
        if bundle is None:
            return
        try:
            from app.robot.health import HealthMonitor

            hm = HealthMonitor(bundle.session)
            pct = hm.get_battery_percentage()
            self._battery.setText(f"Baterie: {pct:.0f} %")
            if pct < 15:
                self._battery.setStyleSheet("color:#c62828; font-weight:bold;")
        except Exception as exc:
            _log.debug("battery poll failed: %s", exc)

    @property
    def recording_service(self) -> Optional[RecordingService]:
        return self._service


__all__ = ["TeleopRecordPage"]
