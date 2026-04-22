"""Hlavní okno aplikace — launcher s třemi velkými tlačítky."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
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
from spot_operator.ui.common.dialogs import error_dialog
from spot_operator.ui.wizards.playback_wizard import PlaybackWizard
from spot_operator.ui.wizards.recording_wizard import RecordingWizard

_log = get_logger(__name__)


class MainWindow(QMainWindow):
    """Launcher se 3 akcemi: jízda, nahrávání, CRUD (pokud nainstalovaný)."""

    def __init__(self, config: AppConfig, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._config = config
        self._recording_wizard: Optional[RecordingWizard] = None
        self._playback_wizard: Optional[PlaybackWizard] = None
        self._crud_window = None

        self.setWindowTitle(f"{__app_name__} {__version__}")
        self.resize(960, 640)
        self.setStatusBar(QStatusBar(self))

        central = QWidget(self)
        self.setCentralWidget(central)

        root = QVBoxLayout(central)

        # Top bar: DB status
        top = QHBoxLayout()
        self._db_status = QLabel("DB: ?")
        self._db_status.setStyleSheet("padding:4px 8px; font-weight:bold;")
        top.addWidget(self._db_status)
        top.addStretch(1)
        top.addWidget(QLabel(f"{__app_name__} v{__version__}"))
        root.addLayout(top)

        # Title
        title = QLabel(__app_name__)
        title_font = QFont("Segoe UI", 28, QFont.Bold)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("padding: 24px 0;")
        root.addWidget(title)

        # Buttons grid
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

        self._btn_crud = self._make_big_button(
            "🛠  Správa SPZ a běhů",
            "CRUD nástroj pro development (volitelný).",
            self._open_crud,
            color="#455a64",
        )
        grid.addWidget(self._btn_crud, 1, 1)

        root.addLayout(grid)
        root.addStretch(1)

        self._register_crud_if_available()
        self._start_db_ping_timer()

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
        try:
            wiz = RecordingWizard(self._config, parent=self)
            wiz.show()
            self._recording_wizard = wiz
        except Exception as exc:
            _log.exception("Failed to open RecordingWizard: %s", exc)
            error_dialog(self, "Chyba", str(exc))

    def _start_playback(self) -> None:
        try:
            wiz = PlaybackWizard(self._config, parent=self)
            wiz.show()
            self._playback_wizard = wiz
        except Exception as exc:
            _log.exception("Failed to open PlaybackWizard: %s", exc)
            error_dialog(self, "Chyba", str(exc))

    # ---- DB status polling ----

    def _start_db_ping_timer(self) -> None:
        self._db_timer = QTimer(self)
        self._db_timer.setInterval(5000)
        self._db_timer.timeout.connect(self._update_db_status)
        self._db_timer.start()
        self._update_db_status()

    def _update_db_status(self) -> None:
        ok = db_ping()
        if ok:
            self._db_status.setText("● DB OK")
            self._db_status.setStyleSheet(
                "padding:4px 8px; font-weight:bold; color:#2e7d32;"
            )
        else:
            self._db_status.setText("● DB DOWN")
            self._db_status.setStyleSheet(
                "padding:4px 8px; font-weight:bold; color:#c62828;"
            )

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
