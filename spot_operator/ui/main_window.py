"""Hlavní okno aplikace — launcher se stavem DB + Spot spojení a tlačítky."""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from spot_operator import __app_name__, __version__
from spot_operator.config import AppConfig
from spot_operator.db import ping as db_ping
from spot_operator.logging_config import get_logger
from spot_operator.services.map_storage import cleanup_temp_root
from spot_operator.ui.common.dialogs import confirm_dialog, error_dialog
from spot_operator.ui.wizards.playback_wizard import PlaybackWizard
from spot_operator.ui.wizards.recording_wizard import RecordingWizard
from spot_operator.ui.wizards.walk_wizard import WalkWizard

_TEMP_CLEANUP_INTERVAL_MS = 30 * 60 * 1000  # 30 minut

_log = get_logger(__name__)


class MainWindow(QMainWindow):
    """Launcher s perzistentním Spot bundle (sdílený napříč wizardy) + DB status.

    Bundle je vytvořen tlačítkem "Připojit se ke Spotovi" nebo auto-trigguje
    při prvním pokusu spustit wizard. Jakmile je bundle aktivní, oba wizardy
    dostanou ho v konstruktoru a skipnou Connect krok.
    """

    def __init__(
        self,
        config: AppConfig,
        *,
        ocr_worker=None,  # noqa: ANN001 — OcrWorker, lazy import kvůli cycle
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._config = config
        self._ocr_worker = ocr_worker
        self._recording_wizard: Optional[RecordingWizard] = None
        self._playback_wizard: Optional[PlaybackWizard] = None
        self._walk_wizard: Optional[WalkWizard] = None
        self._crud_window = None
        self._bundle: Any | None = None
        # Cleanup se musí zavolat jak při closeEvent, tak přes aboutToQuit
        # (kill signál / OS shutdown). Idempotentní flag zabrání double-run.
        self._cleanup_done: bool = False
        # PR-11 FIND-161: dedup DB ping log (při DB down se jinak spamuje
        # warning každých 5 s).
        self._db_ping_last_state: bool | None = None
        self._db_ping_fail_streak: int = 0

        self.setWindowTitle(f"{__app_name__} {__version__}")
        self.resize(960, 640)
        self.setStatusBar(QStatusBar(self))

        central = QWidget(self)
        self.setCentralWidget(central)

        root = QVBoxLayout(central)

        # ---- Top bar: DB status + Spot status + Connect/Disconnect ----
        top = QHBoxLayout()

        self._db_status = QLabel("DB: ?")
        self._db_status.setStyleSheet("padding:4px 8px; font-weight:bold;")
        top.addWidget(self._db_status)

        self._spot_status = QLabel("Spot: 🔴 Nepřipojen")
        self._spot_status.setStyleSheet("padding:4px 8px; font-weight:bold;")
        top.addWidget(self._spot_status)

        self._btn_connect_spot = QPushButton("Připojit se ke Spotovi")
        self._btn_connect_spot.clicked.connect(self._toggle_spot_connection)
        top.addWidget(self._btn_connect_spot)

        top.addStretch(1)
        top.addWidget(QLabel(f"{__app_name__} v{__version__}"))
        root.addLayout(top)

        # ---- Title ----
        title = QLabel(__app_name__)
        title_font = QFont("Segoe UI", 28, QFont.Bold)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("padding: 24px 0;")
        root.addWidget(title)

        # ---- Buttons grid ----
        grid = QGridLayout()
        grid.setSpacing(16)

        self._btn_play = self._make_big_button(
            "▶  Spustit jízdu podle mapy",
            "Vyber existující mapu a nech Spota autonomně projet checkpointy.",
            self._start_playback,
            color="#2e7d32",
        )
        grid.addWidget(self._btn_play, 0, 0, 1, 2)

        self._btn_rec = self._make_big_button(
            "●  Nahrát novou mapu",
            "Ovládej Spota klávesnicí a nahraj nové parkoviště.",
            self._start_recording,
            color="#1565c0",
        )
        grid.addWidget(self._btn_rec, 1, 0)

        self._btn_walk = self._make_big_button(
            "🐕  Chůze se Spotem",
            "Ovládej Spota klávesnicí bez nahrávání. Vyzkoušej fiducial + teleop.",
            self._start_walk,
            color="#6a1b9a",
        )
        grid.addWidget(self._btn_walk, 1, 1)

        self._btn_crud = self._make_big_button(
            "🛠  Správa SPZ a běhů",
            "CRUD nástroj pro development (volitelný).",
            self._open_crud,
            color="#455a64",
        )
        grid.addWidget(self._btn_crud, 2, 0, 1, 2)

        root.addLayout(grid)
        root.addStretch(1)

        self._register_crud_if_available()
        self._start_db_ping_timer()
        self._start_temp_cleanup_timer()
        self._update_spot_status()
        self._update_robot_controls()

        # Registrovat emergency cleanup pro případ, že closeEvent nedorazí
        # (OS kill signal, crash, app.quit volaný odjinud). Idempotent.
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._emergency_cleanup)

    # ---- Spot connection lifecycle ----

    @property
    def spot_bundle(self) -> Any | None:
        """Aktuálně aktivní Spot bundle (nebo None)."""
        return self._bundle

    def _toggle_spot_connection(self) -> None:
        if self._bundle is None:
            self._connect_spot()
        else:
            self._disconnect_spot()

    def _connect_spot(self) -> bool:
        """Otevře modal ConnectDialog. Vrátí True při úspěšném připojení.

        Používá lazy import aby main_window šlo importovat bez autonomy
        v test režimu.
        """
        from spot_operator.ui.common.connect_dialog import ConnectDialog

        dlg = ConnectDialog(self._config, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return False
        if dlg.bundle is None:
            return False
        self._bundle = dlg.bundle
        _log.info("Spot bundle acquired and held by MainWindow.")
        self._update_spot_status()
        self._update_robot_controls()
        return True

    def _disconnect_spot(self) -> None:
        if self._bundle is None:
            return
        if (
            self._recording_wizard is not None
            or self._playback_wizard is not None
            or self._walk_wizard is not None
        ):
            error_dialog(
                self,
                "Wizard běží",
                "Nejdřív zavři probíhající wizard — až poté lze Spota odpojit.",
            )
            return
        if not confirm_dialog(
            self,
            "Odpojit Spota?",
            "Ukončí se aktivní session, lease se uvolní, E-Stop se odregistruje.",
        ):
            return
        try:
            self._bundle.disconnect()
        except Exception as exc:
            _log.exception("bundle.disconnect failed: %s", exc)
        self._bundle = None
        _log.info("Spot disconnected (by user).")
        self._update_spot_status()
        self._update_robot_controls()

    def _update_spot_status(self) -> None:
        if self._bundle is None:
            self._spot_status.setText("Spot: 🔴 Nepřipojen")
            self._spot_status.setStyleSheet(
                "padding:4px 8px; font-weight:bold; color:#c62828;"
            )
            self._btn_connect_spot.setText("Připojit se ke Spotovi")
            return
        ip = getattr(self._bundle.session, "hostname", None) or getattr(
            self._bundle.session, "_hostname", None
        )
        ip_text = f" ({ip})" if ip else ""
        self._spot_status.setText(f"Spot: 🟢 Připojen{ip_text}")
        self._spot_status.setStyleSheet(
            "padding:4px 8px; font-weight:bold; color:#2e7d32;"
        )
        self._btn_connect_spot.setText("Odpojit Spota")
        self._btn_connect_spot.setEnabled(not self._has_active_robot_wizard())

    def _ensure_bundle(self) -> bool:
        """Wizard helper: pokud není bundle, vynutí ConnectDialog. Vrátí True
        pokud je bundle nakonec k dispozici."""
        if self._bundle is not None:
            return True
        return self._connect_spot()

    # ---- CRUD optional loader ----

    def _register_crud_if_available(self) -> None:
        try:
            from spot_operator.ui.crud import crud_window  # noqa: F401
        except ImportError:
            _log.info("CRUD module not installed; hiding CRUD button.")
            self._btn_crud.setVisible(False)

    def _open_crud(self) -> None:
        try:
            from spot_operator.ui.crud.crud_window import CrudWindow
        except ImportError:
            _log.info("CRUD module not installed.")
            return
        if self._crud_window is None:
            self._crud_window = CrudWindow(self._config, parent=self)
        self._crud_window.show()
        self._crud_window.raise_()
        self._crud_window.activateWindow()

    # ---- Wizard launchers ----

    def _start_recording(self) -> None:
        if not self._ensure_robot_wizard_slot():
            return
        if not self._ensure_bundle():
            return
        try:
            wiz = RecordingWizard(
                self._config, parent=self, bundle=self._bundle
            )
            wiz.finished.connect(self._on_wizard_closed)
            wiz.showMaximized()
            self._recording_wizard = wiz
            self._update_robot_controls()
        except Exception as exc:
            _log.exception("Failed to open RecordingWizard: %s", exc)
            error_dialog(self, "Chyba", str(exc))

    def _start_playback(self) -> None:
        if not self._ensure_robot_wizard_slot():
            return
        if not self._ensure_bundle():
            return
        try:
            wiz = PlaybackWizard(
                self._config,
                ocr_worker=self._ocr_worker,
                parent=self,
                bundle=self._bundle,
            )
            wiz.finished.connect(self._on_wizard_closed)
            wiz.showMaximized()
            self._playback_wizard = wiz
            self._update_robot_controls()
        except Exception as exc:
            _log.exception("Failed to open PlaybackWizard: %s", exc)
            error_dialog(self, "Chyba", str(exc))

    def _start_walk(self) -> None:
        if not self._ensure_robot_wizard_slot():
            return
        if not self._ensure_bundle():
            return
        try:
            wiz = WalkWizard(self._config, parent=self, bundle=self._bundle)
            wiz.finished.connect(self._on_wizard_closed)
            wiz.showMaximized()
            self._walk_wizard = wiz
            self._update_robot_controls()
        except Exception as exc:
            _log.exception("Failed to open WalkWizard: %s", exc)
            error_dialog(self, "Chyba", str(exc))

    def _on_wizard_closed(self, _result: int) -> None:
        """Po zavření wizardu vynuluj referenci. Bundle zůstává v MainWindow."""
        sender = self.sender()
        if sender is self._recording_wizard:
            self._recording_wizard = None
        elif sender is self._playback_wizard:
            self._playback_wizard = None
        elif sender is self._walk_wizard:
            self._walk_wizard = None
        # Refresh status — pro případ že by wizard nakonec bundle zničil
        # (by shouldn't, ale bezpečnost > performance).
        if self._bundle is not None:
            try:
                # Lightweight check — pokud session není alive, vynuluj.
                sess = self._bundle.session
                if sess is None or getattr(sess, "robot", None) is None:
                    _log.warning(
                        "Post-wizard: bundle session is dead — discarding."
                    )
                    self._bundle = None
            except Exception:
                pass
        self._update_spot_status()
        self._update_robot_controls()

    # ---- DB status polling ----

    def _start_db_ping_timer(self) -> None:
        self._db_timer = QTimer(self)
        self._db_timer.setInterval(5000)
        self._db_timer.timeout.connect(self._update_db_status)
        self._db_timer.start()
        self._update_db_status()

    # ---- Temp cleanup ----

    def _start_temp_cleanup_timer(self) -> None:
        self._temp_cleanup_timer = QTimer(self)
        self._temp_cleanup_timer.setInterval(_TEMP_CLEANUP_INTERVAL_MS)
        self._temp_cleanup_timer.timeout.connect(self._periodic_temp_cleanup)
        self._temp_cleanup_timer.start()

    def _periodic_temp_cleanup(self) -> None:
        """Pravidelný úklid temp/ — jen pokud není aktivní wizard.

        Během recordingu / playbacku čteme/píšeme do temp/, takže cleanup by
        rozbil běžící operaci. Když ale žádný wizard neběží, bezpečně smažeme
        pozůstatky po předchozích pádech.
        """
        if (
            self._recording_wizard is not None
            or self._playback_wizard is not None
            or self._walk_wizard is not None
        ):
            return
        try:
            cleanup_temp_root(self._config.temp_root)
        except Exception as exc:
            _log.warning("Periodic temp cleanup failed: %s", exc)

    def _update_db_status(self) -> None:
        ok = db_ping()
        # PR-11 FIND-161: dedup log — první fail plný, pak každý 30. tick.
        if ok:
            if self._db_ping_last_state is False:
                _log.info("DB ping: recovered after %d failures", self._db_ping_fail_streak)
            self._db_ping_fail_streak = 0
            self._db_status.setText("● DB OK")
            self._db_status.setStyleSheet(
                "padding:4px 8px; font-weight:bold; color:#2e7d32;"
            )
        else:
            self._db_ping_fail_streak += 1
            if self._db_ping_last_state is not False or self._db_ping_fail_streak % 30 == 0:
                _log.warning(
                    "DB ping failed (streak=%d) — zkontroluj DATABASE_URL / síť",
                    self._db_ping_fail_streak,
                )
            self._db_status.setText("● DB DOWN")
            self._db_status.setStyleSheet(
                "padding:4px 8px; font-weight:bold; color:#c62828;"
            )
        self._db_ping_last_state = ok

    # ---- Close cleanup ----

    def closeEvent(self, event):  # noqa: D401 - Qt hook
        """Při zavření hlavního okna disconnectujeme bundle (pokud existuje).

        Volá `_emergency_cleanup`, který je idempotentní — stejná funkce
        běží i z `aboutToQuit` slotu pro případ kill signálu / crash.
        """
        self._emergency_cleanup()
        super().closeEvent(event)

    def _emergency_cleanup(self) -> None:
        """Idempotentní teardown volaný z closeEvent i aboutToQuit.

        Pořadí: zastav timery (aby po částečném teardownu nevyhazovaly
        signály), pak disconnect bundle.
        """
        if self._cleanup_done:
            return
        self._cleanup_done = True

        # Timery musí být stoppnuté PŘED disconnect bundle, aby timeouts
        # nenakreslily něco do zničeného UI.
        for timer_attr in ("_db_timer", "_temp_cleanup_timer"):
            timer = getattr(self, timer_attr, None)
            if timer is not None:
                try:
                    timer.stop()
                except Exception as exc:
                    _log.warning("Timer %s stop failed: %s", timer_attr, exc)

        if self._bundle is not None:
            try:
                self._bundle.disconnect()
            except Exception as exc:
                _log.warning("MainWindow cleanup: bundle.disconnect failed: %s", exc)
            self._bundle = None

    def _active_robot_wizard(self) -> QWidget | None:
        return (
            self._recording_wizard
            or self._playback_wizard
            or self._walk_wizard
        )

    def _has_active_robot_wizard(self) -> bool:
        return self._active_robot_wizard() is not None

    def _ensure_robot_wizard_slot(self) -> bool:
        if not self._has_active_robot_wizard():
            return True
        error_dialog(
            self,
            "Wizard už běží",
            "Spot bundle už používá jiný wizard. Nejdřív ho dokonči nebo zavři.",
        )
        return False

    def _update_robot_controls(self) -> None:
        wizard_active = self._has_active_robot_wizard()
        for btn in (self._btn_play, self._btn_rec, self._btn_walk):
            btn.setEnabled(not wizard_active)
        if self._bundle is None:
            self._btn_connect_spot.setEnabled(not wizard_active)
        else:
            self._btn_connect_spot.setEnabled(not wizard_active)

    # ---- Utils ----

    def _make_big_button(
        self, label: str, description: str, slot, *, color: str
    ) -> QPushButton:
        btn = QPushButton(f"{label}\n\n{description}")
        btn.setMinimumHeight(140)
        btn.setFont(QFont("Segoe UI", 14))
        btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {color};
                color: white;
                border: none;
                border-radius: 12px;
                padding: 24px;
                text-align: left;
            }}
            QPushButton:hover {{ opacity: 0.9; }}
            QPushButton:disabled {{ background:#9e9e9e; }}
            """
        )
        btn.clicked.connect(slot)
        return btn


__all__ = ["MainWindow"]
