"""Krok 5 recordingu: WASD teleop + fotky + waypointy + live view."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
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

from spot_operator.constants import CAMERA_LEFT, CAMERA_RIGHT
from spot_operator.logging_config import get_logger
from spot_operator.services.recording_service import RecordingService
from spot_operator.ui.common.dialogs import confirm_dialog, error_dialog
from spot_operator.ui.common.estop_floating import EstopFloating

_log = get_logger(__name__)


_WALK_VELOCITY = 0.5  # m/s
_YAW_VELOCITY = 0.6  # rad/s


class TeleopRecordPage(QWizardPage):
    """Jádro recording wizardu. Auto-start nahrávání + WASD + tlačítka fotek."""

    recording_finished = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setTitle("5. Průjezd trasy + focení")
        self.setSubTitle(
            "Nahrávání běží. Ovládej Spota klávesami WASD, fotit klávesami [ ] P,"
            " waypoint bez fotky klávesou C."
        )

        self._service: Optional[RecordingService] = None
        self._estop_widget: Optional[EstopFloating] = None
        self._image_pipeline = None
        self._live_view = None
        self._keys_pressed: set[int] = set()

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
        side.setFixedWidth(280)
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

        self._btn_photo_left = QPushButton("📷 Foto [levá]   ( [ )")
        self._btn_photo_left.clicked.connect(lambda: self._capture([CAMERA_LEFT]))
        side_layout.addWidget(self._btn_photo_left)

        self._btn_photo_right = QPushButton("📷 Foto [pravá]  ( ] )")
        self._btn_photo_right.clicked.connect(lambda: self._capture([CAMERA_RIGHT]))
        side_layout.addWidget(self._btn_photo_right)

        self._btn_photo_both = QPushButton("📷 Foto [obě]    ( P )")
        self._btn_photo_both.clicked.connect(
            lambda: self._capture([CAMERA_LEFT, CAMERA_RIGHT])
        )
        side_layout.addWidget(self._btn_photo_both)

        self._btn_waypoint = QPushButton("📍 Waypoint        ( C )")
        self._btn_waypoint.clicked.connect(self._add_waypoint)
        side_layout.addWidget(self._btn_waypoint)

        side_layout.addSpacing(12)

        self._hint = QLabel(
            "<small><b>WASD</b> = pohyb, <b>QE</b> = rotace,<br>"
            "<b>Mezerník</b> = stop, <b>F1</b> = E-STOP</small>"
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

        self._service = RecordingService(bundle)
        sources = wizard.property("capture_sources") or [CAMERA_LEFT]
        fiducial_id = wizard.property("fiducial_id")

        try:
            self._service.start(
                map_name_prefix="rec",
                default_capture_sources=list(sources),
                fiducial_id=int(fiducial_id) if fiducial_id is not None else None,
            )
        except Exception as exc:
            _log.exception("Failed to start recording: %s", exc)
            error_dialog(self, "Chyba", f"Nelze spustit nahrávání: {exc}")
            return

        # Sdílíme service s následující SaveMapPage přes wizard property
        # (místo křehkého iterování přes pageIds()).
        wizard.setProperty("recording_service", self._service)

        self._ensure_image_pipeline(bundle)
        self._ensure_estop_widget(wizard, bundle)
        self._battery_timer.start()
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
        # 1) Zastav poll baterie.
        try:
            self._battery_timer.stop()
        except Exception:
            pass

        # 2) Soft stop robot (velocity → 0). Při E-Stopu to neškodí.
        bundle = self.wizard().bundle() if self.wizard() is not None else None
        if bundle is not None and getattr(bundle, "move_dispatcher", None) is not None:
            try:
                bundle.move_dispatcher.send(0.0, 0.0, 0.0)
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

        _log.info("TeleopRecordPage teardown complete")

    # ---- Keyboard teleop ----

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: D401
        if event.isAutoRepeat():
            return
        key = event.key()
        self._keys_pressed.add(key)

        if key == Qt.Key_BracketLeft:
            self._capture([CAMERA_LEFT])
        elif key == Qt.Key_BracketRight:
            self._capture([CAMERA_RIGHT])
        elif key == Qt.Key_P:
            self._capture([CAMERA_LEFT, CAMERA_RIGHT])
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

    def _update_velocity_from_keys(self) -> None:
        vx = vy = vyaw = 0.0
        if Qt.Key_W in self._keys_pressed:
            vx += _WALK_VELOCITY
        if Qt.Key_S in self._keys_pressed:
            vx -= _WALK_VELOCITY
        if Qt.Key_A in self._keys_pressed:
            vy += _WALK_VELOCITY
        if Qt.Key_D in self._keys_pressed:
            vy -= _WALK_VELOCITY
        if Qt.Key_Q in self._keys_pressed:
            vyaw += _YAW_VELOCITY
        if Qt.Key_E in self._keys_pressed:
            vyaw -= _YAW_VELOCITY
        self._send_velocity(vx, vy, vyaw)

    def _send_velocity(self, vx: float, vy: float, vyaw: float) -> None:
        bundle = self.wizard().bundle()  # type: ignore[attr-defined]
        if bundle is None or bundle.move_dispatcher is None:
            return
        try:
            bundle.move_dispatcher.send(vx, vy, vyaw)
        except Exception as exc:
            _log.warning("move send failed: %s", exc)

    # ---- Capture / waypoint ----

    def _capture(self, sources: list[str]) -> None:
        if self._service is None:
            return
        bundle = self.wizard().bundle()  # type: ignore[attr-defined]
        if bundle is None:
            return
        from app.robot.images import ImagePoller

        try:
            poller = getattr(self, "_poller", None)
            if poller is None:
                poller = ImagePoller(bundle.session)
                self._poller = poller
            self._service.capture_and_record_checkpoint(sources, image_poller=poller)
            self._update_counter()
        except Exception as exc:
            _log.exception("Capture failed: %s", exc)
            error_dialog(self, "Chyba při focení", str(exc))

    def _add_waypoint(self) -> None:
        if self._service is None:
            return
        try:
            self._service.add_unnamed_waypoint()
            self._update_counter()
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
            from app.ui.live_view_widget import LiveViewWidget
        except Exception as exc:
            _log.warning("ImagePipeline unavailable: %s", exc)
            return
        # Swap placeholder for real widget
        layout = self._live_view_container.layout()
        self._live_placeholder.setParent(None)
        self._live_view = LiveViewWidget(self._live_view_container)
        layout.addWidget(self._live_view)

        self._image_pipeline = ImagePipeline(bundle.session)
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
        self._estop_widget = EstopFloating(self, bundle.estop.trigger)
        wizard.set_estop_callback(bundle.estop.trigger)
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
