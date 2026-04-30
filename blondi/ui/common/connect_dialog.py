"""Dialog pro připojení ke Spotovi z MainWindow.

Obaluje stejný flow jako `ConnectPage` (Wi-Fi check → session connect), ale
jako samostatný dialog, který vrací bundle volajícímu (MainWindow drží
`SpotBundle` napříč wizardy).
"""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from blondi.config import AppConfig
from blondi.logging_config import get_logger
from blondi.services import credentials_service, spot_wifi
from blondi.ui.common.workers import FunctionWorker, cleanup_worker
from blondi.ui.wizards.pages.connect_page import _connect_with_wifi_check

_log = get_logger(__name__)


class ConnectDialog(QDialog):
    """Modal dialog: Wi-Fi check + Spot login. Vrací bundle přes `.bundle`."""

    def __init__(self, config: AppConfig, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._config = config
        self._bundle: Any | None = None
        self._worker: Optional[FunctionWorker] = None

        self.setWindowTitle("Připojení ke Spotovi")
        self.setMinimumSize(560, 520)

        root = QVBoxLayout(self)

        # Wi-Fi info
        wifi_frame = QFrame()
        wifi_frame.setFrameShape(QFrame.StyledPanel)
        wifi_layout = QVBoxLayout(wifi_frame)
        wifi_info = QLabel(
            "📶 <b>Wi-Fi:</b> Připoj Windows k síti Spota "
            "(typicky <code>spot-BD-XXXXXXXX</code>)."
        )
        wifi_info.setTextFormat(Qt.RichText)
        wifi_info.setWordWrap(True)
        wifi_layout.addWidget(wifi_info)
        wifi_btn_row = QHBoxLayout()
        self._btn_open_wifi = QPushButton("Otevřít Wi-Fi menu Windows")
        self._btn_open_wifi.clicked.connect(spot_wifi.open_windows_wifi_menu)
        wifi_btn_row.addWidget(self._btn_open_wifi)
        wifi_btn_row.addStretch(1)
        wifi_layout.addLayout(wifi_btn_row)
        root.addWidget(wifi_frame)

        # Login form
        form = QFormLayout()
        self._combo_profiles = QComboBox()
        self._combo_profiles.setEditable(False)
        self._combo_profiles.currentIndexChanged.connect(self._on_profile_picked)
        form.addRow("Uložené profily:", self._combo_profiles)

        self._ip_edit = QLineEdit(self._config.spot_default_ip)
        self._ip_edit.setPlaceholderText("např. 192.168.80.3")
        form.addRow("IP adresa Spota:", self._ip_edit)

        self._username_edit = QLineEdit()
        self._username_edit.setPlaceholderText("např. admin")
        form.addRow("Uživatelské jméno:", self._username_edit)

        self._password_edit = QLineEdit()
        self._password_edit.setEchoMode(QLineEdit.Password)
        form.addRow("Heslo:", self._password_edit)

        self._label_edit = QLineEdit()
        self._label_edit.setPlaceholderText("např. lab-robot")
        form.addRow("Název profilu:", self._label_edit)

        self._remember_cb = QCheckBox("Zapamatovat (Windows Credential Locker)")
        self._remember_cb.setChecked(True)
        form.addRow("", self._remember_cb)
        root.addLayout(form)

        # Action row
        action_row = QHBoxLayout()
        self._btn_connect = QPushButton("Ověřit spojení a přihlásit")
        self._btn_connect.setStyleSheet(
            "QPushButton { background:#1565c0; color:white; font-weight:bold; padding:10px; }"
        )
        self._btn_connect.clicked.connect(self._start_connect)
        action_row.addWidget(self._btn_connect)
        action_row.addStretch(1)
        root.addLayout(action_row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        self._status = QLabel("")
        self._status.setTextFormat(Qt.RichText)
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        # Close button (disabled until connect ok, nebo skrz 'X')
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._reload_profiles()
        self._maybe_autoselect_profile()

    @property
    def bundle(self) -> Any | None:
        return self._bundle

    # ---- Profil helpers (duplikované z ConnectPage — kompaktní verze) ----

    def _reload_profiles(self) -> None:
        self._combo_profiles.blockSignals(True)
        self._combo_profiles.clear()
        self._combo_profiles.addItem("— Nový profil —", None)
        try:
            for cred in credentials_service.list_credentials():
                self._combo_profiles.addItem(
                    f"{cred.label} ({cred.username}@{cred.hostname})", cred.id
                )
        except Exception as exc:
            _log.warning("Could not load credentials list: %s", exc)
        self._combo_profiles.blockSignals(False)

    def _maybe_autoselect_profile(self) -> None:
        try:
            creds = list(credentials_service.list_credentials())
        except Exception:
            return
        if not creds:
            return
        current_ip = self._ip_edit.text().strip()
        if current_ip:
            for cred in creds:
                if cred.hostname == current_ip:
                    idx = self._combo_profiles.findData(cred.id)
                    if idx >= 0:
                        self._combo_profiles.setCurrentIndex(idx)
                        return
        if len(creds) == 1:
            idx = self._combo_profiles.findData(creds[0].id)
            if idx >= 0:
                self._combo_profiles.setCurrentIndex(idx)

    def _on_profile_picked(self, _index: int) -> None:
        cred_id = self._combo_profiles.currentData()
        if cred_id is None:
            self._password_edit.clear()
            return
        try:
            creds = list(credentials_service.list_credentials())
        except Exception as exc:
            _log.warning("profile selection failed: %s", exc)
            self._status.setText(
                f"<span style='color:#c62828;'>Nelze načíst profil: {exc}</span>"
            )
            return
        for cred in creds:
            if cred.id == cred_id:
                self._ip_edit.setText(cred.hostname)
                self._username_edit.setText(cred.username)
                self._label_edit.setText(cred.label)
                pwd = credentials_service.load_password(
                    self._config.keyring_service, cred.keyring_ref
                )
                if pwd:
                    self._password_edit.setText(pwd)
                break

    # ---- Connect flow ----

    def _start_connect(self) -> None:
        ip = self._ip_edit.text().strip()
        user = self._username_edit.text().strip()
        password = self._password_edit.text()
        if not (ip and user and password):
            self._status.setText(
                "<span style='color:#c00;'>Vyplň IP, uživatele a heslo.</span>"
            )
            return
        self._btn_connect.setEnabled(False)
        self._progress.setVisible(True)
        self._status.setText("<i>Testuji Wi-Fi a připojuji se ke Spotovi…</i>")

        self._worker = FunctionWorker(_connect_with_wifi_check, ip, user, password)
        self._worker.finished_ok.connect(self._on_connect_ok)
        self._worker.failed.connect(self._on_connect_failed)
        self._worker.start()

    def _on_connect_ok(self, bundle) -> None:  # noqa: ANN001
        self._bundle = bundle
        self._status.setText(
            "<span style='color:#2e7d32;'>✓ Wi-Fi OK a Spot připojen.</span>"
        )
        self._maybe_save_profile()
        self.accept()

    def _on_connect_failed(self, reason: str) -> None:
        self._btn_connect.setEnabled(True)
        self._progress.setVisible(False)
        self._status.setText(
            f"<span style='color:#c62828;'>✗ Připojení selhalo: {reason}</span>"
        )
        self._password_edit.clear()
        self._password_edit.setFocus()

    def _maybe_save_profile(self) -> None:
        if not self._remember_cb.isChecked():
            return
        label = self._label_edit.text().strip() or "default"
        try:
            credentials_service.save_credentials(
                service_name=self._config.keyring_service,
                label=label,
                hostname=self._ip_edit.text().strip(),
                username=self._username_edit.text().strip(),
                password=self._password_edit.text(),
            )
        except Exception as exc:
            _log.warning("Failed to save credentials: %s", exc)

    def closeEvent(self, event) -> None:  # noqa: D401
        cleanup_worker(self._worker)
        self._worker = None
        super().closeEvent(event)


__all__ = ["ConnectDialog"]
