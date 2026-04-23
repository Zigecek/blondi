"""Kontrola fiducialu — s live view + WASD teleopem + power-on tlačítkem.

Od 1.2.0 tato stránka umožňuje operátorovi **fyzicky dovézt** Spota k fiducialu
(kdekoli se zrovna nachází). Postup:

1. Připojit se ke Spotovi (krok 2 LoginPage) — bundle je aktivní.
2. Na této stránce kliknout "Zapnout a postavit Spota" (~20 s power_on + stand).
3. WASD / QE klávesami nebo ovladačem fyzicky dovézt robota 1-2 m před fiducial.
4. Kliknout "Zkontrolovat fiducial" → fiducial_check.visible_fiducials.

V rámci stránky je viditelný:
 - live view (front_composite — stitched frontleft + frontright)
 - floating E-Stop widget v pravém horním rohu (F1 shortcut)
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QWizardPage,
)

from spot_operator.config import AppConfig
from spot_operator.constants import (
    CAMERA_FRONT_COMPOSITE,
    TELEOP_DEFAULT_SPEED_PROFILE,
    TELEOP_SPEED_LABELS,
    TELEOP_SPEED_PROFILES,
    UI_SIDE_PANEL_WIDTH,
    WASD_AVOIDANCE_STRENGTH,
)
from spot_operator.logging_config import get_logger
from spot_operator.ui.common.dialogs import error_dialog
from spot_operator.ui.common.estop_floating import EstopFloating
from spot_operator.ui.common.workers import FunctionWorker, cleanup_worker

_log = get_logger(__name__)


def _power_on_and_stand(bundle) -> None:  # noqa: ANN001
    """Helper pro FunctionWorker — blokující power_on + stand."""
    if bundle is None or bundle.power is None:
        raise RuntimeError("PowerManager není dostupný (Spot možná není připojený).")
    bundle.power.power_on()
    bundle.power.stand()


class FiducialPage(QWizardPage):
    """Kontrola, že Spot vidí fiducial + možnost WASD ho dovézt k němu.

    Konfigurovatelné:
      - required_id: None pro recording (jakýkoli), int pro playback (konkrétní).
      - Na validaci ukládá do wizardu property 'fiducial_id' (pro recording).
    """

    def __init__(
        self,
        config: AppConfig,
        *,
        required_id: Optional[int] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._config = config
        self._required_id = required_id
        self._detected_id: Optional[int] = None
        self._worker: Optional[FunctionWorker] = None
        self._power_worker: Optional[FunctionWorker] = None

        # Teleop / live view / E-Stop lifecycle
        self._image_pipeline = None
        self._live_view = None
        self._estop_widget: Optional[EstopFloating] = None
        self._spot_powered_on: bool = False
        self._keys_pressed: set[int] = set()

        # Velocity keep-alive timer: Spot SDK velocity commands mají default
        # end_time ~0.5 s (VELOCITY_CMD_DURATION v autonomy). Autonomy tick
        # je 100 ms = 10 Hz. Používáme stejný interval — při slabší Wi-Fi
        # (latence 150-300 ms) jinak command expiruje než dorazí k robotu
        # → ExpiredError + robot na chvíli stojí → seká se WASD i kamera.
        self._velocity_timer = QTimer(self)
        self._velocity_timer.setInterval(100)
        self._velocity_timer.timeout.connect(self._on_velocity_tick)

        self.setTitle("Kontrola fiducialu")
        self._update_subtitle()

        root = QHBoxLayout(self)

        # --- Centr: live view ---
        self._live_view_container = QFrame()
        self._live_view_container.setFrameShape(QFrame.Box)
        self._live_view_container.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )
        live_layout = QVBoxLayout(self._live_view_container)
        live_layout.setContentsMargins(0, 0, 0, 0)
        self._live_placeholder = QLabel("(live view — přední kompozit)")
        self._live_placeholder.setAlignment(Qt.AlignCenter)
        self._live_placeholder.setStyleSheet("background:#111; color:#888;")
        live_layout.addWidget(self._live_placeholder)
        root.addWidget(self._live_view_container, stretch=1)

        # --- Pravý panel: power-on + WASD hint + check fiducial + status ---
        side = QFrame()
        side.setFixedWidth(UI_SIDE_PANEL_WIDTH)
        side_layout = QVBoxLayout(side)

        step_1 = QLabel("<b>1. Zapnout a postavit Spota</b>")
        step_1.setTextFormat(Qt.RichText)
        side_layout.addWidget(step_1)
        self._btn_power = QPushButton("▶ Zapnout a postavit Spota")
        self._btn_power.setStyleSheet(
            "QPushButton { background:#1565c0; color:white; font-weight:bold; padding:10px; }"
        )
        self._btn_power.clicked.connect(self._on_power_clicked)
        side_layout.addWidget(self._btn_power)
        self._power_progress = QProgressBar()
        self._power_progress.setRange(0, 0)
        self._power_progress.setVisible(False)
        side_layout.addWidget(self._power_progress)
        # Dva labely:
        #  - `_power_state_label`: trvalý stav Spota (stojí / vypnutý). Operátor
        #    podle něj pozná, zda má smysl stisknout WASD.
        #  - `_power_status`: průběžné zprávy (zapínám…, chyba…). Dočasné.
        self._power_state_label = QLabel("<span style='color:#888;'>● Spot vypnutý</span>")
        self._power_state_label.setTextFormat(Qt.RichText)
        self._power_state_label.setWordWrap(True)
        side_layout.addWidget(self._power_state_label)
        self._power_status = QLabel("")
        self._power_status.setTextFormat(Qt.RichText)
        self._power_status.setWordWrap(True)
        side_layout.addWidget(self._power_status)

        side_layout.addSpacing(14)

        step_2 = QLabel(
            "<b>2. Dovést Spota k fiducialu</b><br>"
            "<small><b>WASD</b> = pohyb, <b>QE</b> = rotace,<br>"
            "<b>Space</b> = stop, <b>F1</b> = E-STOP</small>"
        )
        step_2.setTextFormat(Qt.RichText)
        side_layout.addWidget(step_2)

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

        self._teleop_hint = QLabel("<i>(aktivuje se po zapnutí Spota)</i>")
        self._teleop_hint.setStyleSheet("color:#888;")
        self._teleop_hint.setTextFormat(Qt.RichText)
        side_layout.addWidget(self._teleop_hint)

        side_layout.addSpacing(14)

        step_3 = QLabel("<b>3. Zkontrolovat fiducial</b>")
        step_3.setTextFormat(Qt.RichText)
        side_layout.addWidget(step_3)

        instructions = QLabel(
            "<p>Fiducial je černobílý čtvercový marker (AprilTag) nalepený "
            "u nabíječky. Postav Spota 1–2 m před něj.</p>"
        )
        instructions.setWordWrap(True)
        instructions.setTextFormat(Qt.RichText)
        side_layout.addWidget(instructions)

        self._btn_check = QPushButton("Zkontrolovat fiducial")
        self._btn_check.clicked.connect(self._start_check)
        side_layout.addWidget(self._btn_check)

        self._check_progress = QProgressBar()
        self._check_progress.setRange(0, 0)
        self._check_progress.setVisible(False)
        side_layout.addWidget(self._check_progress)

        self._status = QLabel("")
        self._status.setTextFormat(Qt.RichText)
        self._status.setWordWrap(True)
        side_layout.addWidget(self._status)

        side_layout.addStretch(1)
        root.addWidget(side)

        self.setFocusPolicy(Qt.StrongFocus)

    def _update_subtitle(self) -> None:
        if self._required_id is None:
            subtitle = (
                "Zapni Spota, dovez ho k fiducialu (obvykle u nabíječky na recepci),"
                " pak klikni Zkontrolovat."
            )
        else:
            subtitle = (
                f"Tato mapa byla nahrána u fiducialu ID <b>{self._required_id}</b>."
                " Zapni Spota, dovez ho před tento fiducial, klikni Zkontrolovat."
            )
        self.setSubTitle(subtitle)

    def set_required_id(self, required_id: Optional[int]) -> None:
        """Povoluje dynamicky měnit required_id (playback wizard ho čte po výběru mapy)."""
        self._required_id = required_id
        self._detected_id = None
        self._status.setText("")
        self._update_subtitle()
        self.completeChanged.emit()


    def isComplete(self) -> bool:
        if self._required_id is None:
            return self._detected_id is not None
        return self._detected_id == self._required_id

    # ---- Lifecycle ----

    def initializePage(self) -> None:
        bundle = self.wizard().bundle()  # type: ignore[attr-defined]
        if bundle is None:
            error_dialog(self, "Chyba", "Spot není připojen.")
            return
        self._ensure_image_pipeline(bundle)
        self._ensure_estop_widget(bundle)
        # Detekce: pokud jsou motory už zapnuté (Spot už stál od minula /
        # sdílený bundle z MainWindow), skipni "Zapnout a postavit" krok.
        self._detect_and_mark_already_on(bundle)
        self.setFocus()

    def _detect_and_mark_already_on(self, bundle) -> None:
        """Jestli je robot už powered-on, nastav UI do stavu "hotovo" bez
        čekání na click tlačítka. Stand command je idempotentní — pokud
        user přesto chce "znovu postavit", tlačítko zůstává klikatelné.
        """
        from spot_operator.robot.power_state import is_motors_powered

        if not is_motors_powered(bundle):
            return
        _log.info("Spot motors already powered on — skipping power-on click.")
        self._spot_powered_on = True
        self._btn_power.setText("▶ Znovu postavit Spota")
        self._power_state_label.setText(
            "<span style='color:#2e7d32;'>● Spot už stojí — WASD je aktivní.</span>"
        )
        self._power_status.setText(
            "<i>(Motory byly zapnuté od předchozí relace.)</i>"
        )
        self._teleop_hint.setText(
            "<i>Stiskni WASD / QE pro pohyb, Space pro stop.</i>"
        )
        self._teleop_hint.setStyleSheet("color:#2e7d32;")
        if not self._velocity_timer.isActive():
            self._velocity_timer.start()

    def cleanupPage(self) -> None:
        self._teardown()

    def _teardown(self) -> None:
        """Bezpečný úklid threadů a widgets. Idempotentní — dá se volat opakovaně."""
        # 0) Stop velocity keep-alive timer.
        try:
            self._velocity_timer.stop()
        except Exception:
            pass

        cleanup_worker(self._worker)
        self._worker = None
        cleanup_worker(self._power_worker)
        self._power_worker = None

        # 1) Soft stop velocity (i když není power_on, nepoškodí).
        bundle = self.wizard().bundle() if self.wizard() is not None else None
        if bundle is not None and getattr(bundle, "move_dispatcher", None) is not None:
            try:
                bundle.move_dispatcher.stop()
            except Exception as exc:
                _log.debug("move_dispatcher.stop during fiducial teardown: %s", exc)

        # 2) Stop image pipeline.
        if self._image_pipeline is not None:
            try:
                if hasattr(self._image_pipeline, "stop"):
                    self._image_pipeline.stop()
                self._image_pipeline.quit()
                self._image_pipeline.wait(2000)
            except Exception as exc:
                _log.warning("ImagePipeline teardown failed: %s", exc)
            self._image_pipeline = None

        # 3) Skryj E-Stop widget.
        if self._estop_widget is not None:
            try:
                self._estop_widget.hide()
                self._estop_widget.deleteLater()
            except Exception:
                pass
            self._estop_widget = None

        # Vyčisti stisknuté klávesy, aby při opětovném vstupu neběžely "ulpělé".
        self._keys_pressed.clear()

        _log.info("FiducialPage teardown complete")

    # ---- Power-on ----

    def _on_power_clicked(self) -> None:
        bundle = self.wizard().bundle()  # type: ignore[attr-defined]
        if bundle is None or bundle.power is None:
            error_dialog(self, "Chyba", "Spot není připojen nebo chybí PowerManager.")
            return

        self._btn_power.setEnabled(False)
        self._power_progress.setVisible(True)
        self._power_status.setText(
            "<i>Motory se zapínají (~20 s)…</i>"
        )

        self._power_worker = FunctionWorker(_power_on_and_stand, bundle)
        self._power_worker.finished_ok.connect(self._on_power_ok)
        self._power_worker.failed.connect(self._on_power_failed)
        self._power_worker.start()

    def _on_power_ok(self, _result) -> None:  # noqa: ANN001
        self._power_progress.setVisible(False)
        self._spot_powered_on = True
        # Tlačítko zůstává ENABLED (idempotentní click — Spot SDK `power_on`
        # na running robot vrací rychle bez efektu). Text se nemění; stav
        # "stojí" jde do samostatného _power_state_label.
        self._btn_power.setEnabled(True)
        self._power_state_label.setText(
            "<span style='color:#2e7d32;'>● Spot stojí a přijímá příkazy.</span>"
        )
        self._power_status.setText("")  # vymaž průběžnou hlášku
        self._teleop_hint.setText(
            "<i>Stiskni WASD / QE pro pohyb, Space pro stop.</i>"
        )
        self._teleop_hint.setStyleSheet("color:#2e7d32;")
        # Spusť velocity keep-alive timer (5 Hz) pokud už neběží.
        if not self._velocity_timer.isActive():
            self._velocity_timer.start()
        self.setFocus()

    def _on_power_failed(self, reason: str) -> None:
        self._power_progress.setVisible(False)
        self._btn_power.setEnabled(True)
        self._power_state_label.setText(
            "<span style='color:#c62828;'>● Spot vypnutý</span>"
        )
        self._power_status.setText(
            f"<span style='color:#c62828;'>✗ Zapnutí selhalo: {reason}</span>"
        )
        error_dialog(
            self,
            "Zapnutí motorů selhalo",
            f"Spota nelze zapnout: {reason}\n\n"
            "Typické příčiny: Spot je vybitý, lease nebyl získán, robot je v E-Stop stavu.",
        )

    # ---- E-Stop release + power state reset ----

    def _handle_estop_release(self) -> None:
        """Volá `EstopManager.release()` + resetuje stav stránky.

        Po release je Spot fyzicky vypnutý (motory cut). Operátor musí znovu
        kliknout "Zapnout a postavit Spota" aby mohl pokračovat.
        """
        bundle = self.wizard().bundle() if self.wizard() else None
        if bundle is None or bundle.estop is None:
            _log.warning("E-Stop release: bundle or estop manager missing")
            return
        try:
            bundle.estop.release()
            _log.info("E-Stop released")
        except Exception as exc:
            _log.exception("E-Stop release failed: %s", exc)
            raise
        self._mark_spot_off("E-Stop uvolněn. Pro další pohyb zapni motory znovu.")

    def _mark_spot_off(self, reason: str = "") -> None:
        """Vizuálně nastaví UI do stavu 'Spot není powered'.

        Tlačítko "Zapnout a postavit Spota" zůstává enabled — operátor ho může
        znovu kliknout. Velocity timer se zastaví (žádné drží-klávesu posílání).
        """
        self._spot_powered_on = False
        self._keys_pressed.clear()
        try:
            self._velocity_timer.stop()
        except Exception:
            pass
        self._btn_power.setEnabled(True)
        self._power_state_label.setText(
            "<span style='color:#c62828;'>● Spot vypnutý — klikni Zapnout pro pokračování.</span>"
        )
        if reason:
            self._power_status.setText(f"<i>{reason}</i>")
        self._teleop_hint.setText("<i>(aktivuje se po zapnutí Spota)</i>")
        self._teleop_hint.setStyleSheet("color:#888;")

    # ---- Fiducial check ----

    def _start_check(self) -> None:
        bundle = self.wizard().bundle()  # type: ignore[attr-defined]
        if bundle is None or bundle.session is None:
            self._status.setText(
                "<span style='color:#c00;'>Spot není připojen.</span>"
            )
            return
        self._btn_check.setEnabled(False)
        self._check_progress.setVisible(True)
        self._status.setText("<i>Hledám fiducial...</i>")

        from app.robot.fiducial_check import visible_fiducials

        self._worker = FunctionWorker(
            visible_fiducials,
            bundle.session,
            required_id=self._required_id,
            max_distance_m=self._config.fiducial_distance_threshold_m,
        )
        self._worker.finished_ok.connect(self._on_check_done)
        self._worker.failed.connect(self._on_check_failed)
        self._worker.start()

    def _on_check_done(self, observations) -> None:  # noqa: ANN001
        self._btn_check.setEnabled(True)
        self._check_progress.setVisible(False)
        if not observations:
            if self._required_id is None:
                msg = "Nevidím žádný fiducial do 2 m. Posuň Spota blíž a zkus znovu."
            else:
                msg = (
                    f"Nevidím fiducial ID {self._required_id}. "
                    "Postav Spota před správný fiducial a zkus znovu."
                )
            self._status.setText(f"<span style='color:#c62828;'>✗ {msg}</span>")
            self._detected_id = None
            self.completeChanged.emit()
            return

        best = observations[0]
        self._detected_id = best.tag_id
        self._store_detected_fiducial(best.tag_id)

        if self._required_id is not None and best.tag_id != self._required_id:
            self._status.setText(
                f"<span style='color:#c62828;'>✗ Vidím fiducial ID {best.tag_id} "
                f"ale mapa očekává {self._required_id}. Postav Spota na správné místo.</span>"
            )
            self.completeChanged.emit()
            return

        self._status.setText(
            f"<span style='color:#2e7d32;'>✓ Vidím fiducial ID {best.tag_id} "
            f"({best.distance_m:.2f} m).</span>"
        )
        self.completeChanged.emit()

    def _on_check_failed(self, reason: str) -> None:
        self._btn_check.setEnabled(True)
        self._check_progress.setVisible(False)
        self._status.setText(f"<span style='color:#c62828;'>Chyba: {reason}</span>")

    def _store_detected_fiducial(self, fiducial_id: int) -> None:
        wizard = self.wizard()
        state_getter = getattr(wizard, "flow_state", None)
        state = state_getter() if callable(state_getter) else None
        if state is not None and hasattr(state, "fiducial_id"):
            state.fiducial_id = fiducial_id
            return
        wizard.setProperty("fiducial_id", fiducial_id)

    # ---- Keyboard teleop (aktivní jen po power_on) ----

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: D401
        if event.isAutoRepeat() or not self._spot_powered_on:
            super().keyPressEvent(event)
            return
        key = event.key()
        self._keys_pressed.add(key)
        if key == Qt.Key_Space:
            self._send_velocity(0.0, 0.0, 0.0)
        else:
            self._update_velocity_from_keys()

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: D401
        if event.isAutoRepeat() or not self._spot_powered_on:
            super().keyReleaseEvent(event)
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
            bundle.move_dispatcher.send_velocity(
                vx, vy, vyaw, avoidance_strength=WASD_AVOIDANCE_STRENGTH
            )
        except Exception as exc:
            _log.warning("move send_velocity failed: %s", exc)

    def _on_velocity_tick(self) -> None:
        """Periodický 5 Hz tick — re-publish aktuální velocity pokud jsou
        klávesy drženy.

        Spot SDK velocity commands mají end_time ~0.6 s. Autonomy
        `_CommandDispatcher` neopakuje last command sám. Bez tohoto tick by
        Spot zastavil po 10 cm i když operátor klávesu pořád drží.
        """
        if not self._spot_powered_on:
            return
        if not self._keys_pressed:
            return  # nic drženo → neposílej nic (Spot sám po timeout zastaví)
        self._update_velocity_from_keys()

    # ---- Widget helpers ----

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
        layout = self._live_view_container.layout()
        self._live_placeholder.setParent(None)
        self._live_view = LiveViewWidget(self._live_view_container)
        layout.addWidget(self._live_view)

        # POZOR: ImagePipeline v konstruktoru chce `ImagePoller` instanci
        # (ne session). Dříve jsme předávali `bundle.session` což tiše
        # selhávalo v poller.capture() → frame_ready nikdy nepřišel.
        poller = ImagePoller(bundle.session)
        self._image_pipeline = ImagePipeline(poller)
        # Default source = front_composite (stitched frontleft + frontright),
        # stejně jako autonomy. Pokud Spot pravou přední kameru nemá,
        # poller.capture_front_composite() fallne na samotnou levou
        # (ověřeno v autonomy/tests/test_image_poller.py).
        self._image_pipeline.set_source(CAMERA_FRONT_COMPOSITE)
        self._image_pipeline.frame_ready.connect(self._live_view.update_frame)
        try:
            self._image_pipeline.start()
        except Exception as exc:
            _log.warning("ImagePipeline start failed: %s", exc)

    def _ensure_estop_widget(self, bundle) -> None:
        if self._estop_widget is not None:
            return
        if bundle.estop is None:
            return
        wizard = self.wizard()
        # Registrujeme oba callbacky: trigger (klik když není triggered) i
        # release (klik když je triggered). Release pipe přes
        # _handle_estop_release, která navíc resetuje power state přes
        # _mark_spot_off.
        self._estop_widget = EstopFloating(
            self,
            on_trigger=bundle.estop.trigger,
            on_release=self._handle_estop_release,
        )
        # F1 shortcut v base wizardu také toggluje (přes trigger_from_shortcut).
        if wizard is not None:
            wizard.set_estop_callback(  # type: ignore[attr-defined]
                bundle.estop.trigger,
                self._handle_estop_release,
            )
        self._estop_widget.show()


__all__ = ["FiducialPage"]
