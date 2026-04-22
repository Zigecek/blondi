"""Krok 1: Wi-Fi připojení k Spotovi. Info + kontrola dostupnosti IP."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
    QWizardPage,
)

from spot_operator.config import AppConfig
from spot_operator.services import spot_wifi
from spot_operator.ui.common.workers import FunctionWorker


class WifiPage(QWizardPage):
    """Info + tlačítko 'Zkontrolovat' → ping + TCP test."""

    def __init__(self, config: AppConfig, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._config = config
        self._check_result: Optional[spot_wifi.WifiCheckResult] = None
        self._worker: Optional[FunctionWorker] = None

        self.setTitle("1. Wi-Fi připojení k Spotovi")
        self.setSubTitle(
            "Na Windows otevři Wi-Fi menu a připoj se k síti Spota. "
            "Pak klikni Zkontrolovat."
        )

        root = QVBoxLayout(self)

        info_frame = QFrame()
        info_frame.setFrameShape(QFrame.StyledPanel)
        info_form = QFormLayout(info_frame)
        info_form.addRow(
            "<b>Jak se připojit:</b>",
            QLabel(
                "Klikni na ikonu Wi-Fi v pravém dolním rohu Windows a vyber síť "
                "Spota (typicky <code>spot-BD-XXXXXXXX</code>). Heslo je na QR "
                "štítku Spota nebo v dokumentaci."
            ),
        )
        self._ip_edit = QLineEdit(self._config.spot_default_ip)
        self._ip_edit.setPlaceholderText("např. 192.168.80.3")
        info_form.addRow("IP adresa Spota:", self._ip_edit)
        root.addWidget(info_frame)

        action_row = QHBoxLayout()
        self._btn_check = QPushButton("Zkontrolovat připojení")
        self._btn_check.clicked.connect(self._start_check)
        action_row.addWidget(self._btn_check)

        self._btn_open_wifi = QPushButton("Otevřít Wi-Fi menu Windows")
        self._btn_open_wifi.clicked.connect(spot_wifi.open_windows_wifi_menu)
        action_row.addWidget(self._btn_open_wifi)

        action_row.addStretch(1)
        root.addLayout(action_row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminate
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        self._status = QLabel("")
        self._status.setTextFormat(Qt.RichText)
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        root.addItem(QSpacerItem(1, 1, QSizePolicy.Minimum, QSizePolicy.Expanding))

    # ---- QWizardPage API ----

    def isComplete(self) -> bool:
        return self._check_result is not None and self._check_result.ok

    def _start_check(self) -> None:
        ip = self._ip_edit.text().strip()
        if not ip:
            self._status.setText("<span style='color:#c00;'>Zadej IP adresu.</span>")
            return
        self._btn_check.setEnabled(False)
        self._progress.setVisible(True)
        self._status.setText("<i>Testuji připojení...</i>")

        self._worker = FunctionWorker(spot_wifi.check_connection, ip)
        self._worker.finished_ok.connect(self._on_check_done)
        self._worker.failed.connect(self._on_check_failed)
        self._worker.start()

    def _on_check_done(self, result: spot_wifi.WifiCheckResult) -> None:
        self._check_result = result
        self._btn_check.setEnabled(True)
        self._progress.setVisible(False)
        # Prefill do wizardu — další stránky si IP přečtou.
        self.wizard().setProperty("spot_ip", result.ip)

        if result.ok:
            self._status.setText(
                f"<span style='color:#2e7d32;'>✓ Spojení OK ({result.detail})</span>"
            )
        else:
            self._status.setText(
                f"<span style='color:#c62828;'>✗ Spojení NEFUNGUJE ({result.detail})."
                " Zkontroluj Wi-Fi a IP.</span>"
            )
        self.completeChanged.emit()

    def _on_check_failed(self, reason: str) -> None:
        self._btn_check.setEnabled(True)
        self._progress.setVisible(False)
        self._status.setText(f"<span style='color:#c62828;'>Chyba: {reason}</span>")


__all__ = ["WifiPage"]
