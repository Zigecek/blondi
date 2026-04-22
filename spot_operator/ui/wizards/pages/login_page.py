"""Krok 2: Přihlášení ke Spotovi. Ukládá credentials do keyringu."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QWizardPage,
)

from spot_operator.config import AppConfig
from spot_operator.logging_config import get_logger
from spot_operator.services import credentials_service
from spot_operator.ui.common.workers import FunctionWorker

_log = get_logger(__name__)


class LoginPage(QWizardPage):
    """Formulář IP/user/password + uložené profily + Connect."""

    def __init__(self, config: AppConfig, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._config = config
        self._connected = False
        self._worker: Optional[FunctionWorker] = None
        self._selected_cred_id: Optional[int] = None

        self.setTitle("2. Přihlášení ke Spotovi")
        self.setSubTitle("Vyber uložený profil nebo zadej přihlašovací údaje.")

        root = QVBoxLayout(self)

        form = QFormLayout()
        self._combo_profiles = QComboBox()
        self._combo_profiles.setEditable(False)
        self._combo_profiles.currentIndexChanged.connect(self._on_profile_picked)
        form.addRow("Uložené profily:", self._combo_profiles)

        self._ip_edit = QLineEdit(self._config.spot_default_ip)
        form.addRow("IP adresa:", self._ip_edit)

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

        action_row = QHBoxLayout()
        self._btn_connect = QPushButton("Připojit")
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

        root.addStretch(1)

    def initializePage(self) -> None:
        self._reload_profiles()
        # Prefill IP ze kontroly Wi-Fi kroku.
        wiz_ip = self.wizard().property("spot_ip")
        if wiz_ip:
            self._ip_edit.setText(str(wiz_ip))

    def isComplete(self) -> bool:
        return self._connected

    def validatePage(self) -> bool:
        return self._connected

    # ---- Internal ----

    def _reload_profiles(self) -> None:
        self._combo_profiles.blockSignals(True)
        self._combo_profiles.clear()
        self._combo_profiles.addItem("— Nový profil —", None)
        try:
            for cred in credentials_service.list_credentials():
                label = f"{cred.label} ({cred.username}@{cred.hostname})"
                self._combo_profiles.addItem(label, cred.id)
        except Exception as exc:
            _log.warning("Could not load credentials list: %s", exc)
        self._combo_profiles.blockSignals(False)

    def _on_profile_picked(self, _index: int) -> None:
        cred_id = self._combo_profiles.currentData()
        self._selected_cred_id = cred_id
        if cred_id is None:
            self._password_edit.clear()
            return
        # Najdi odpovídající credential → vyplň.
        for cred in credentials_service.list_credentials():
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
        self._status.setText("<i>Připojuji se...</i>")

        from spot_operator.robot.session_factory import connect as connect_spot

        self._worker = FunctionWorker(connect_spot, ip, user, password)
        self._worker.finished_ok.connect(self._on_connect_ok)
        self._worker.failed.connect(self._on_connect_failed)
        self._worker.start()

    def _on_connect_ok(self, bundle) -> None:  # noqa: ANN001
        self._btn_connect.setEnabled(True)
        self._progress.setVisible(False)
        self._connected = True
        self.wizard().set_bundle(bundle)  # type: ignore[attr-defined]
        self._status.setText("<span style='color:#2e7d32;'>✓ Připojeno.</span>")
        self._maybe_save_profile()
        self.completeChanged.emit()

    def _on_connect_failed(self, reason: str) -> None:
        self._btn_connect.setEnabled(True)
        self._progress.setVisible(False)
        self._connected = False
        self._status.setText(
            f"<span style='color:#c62828;'>✗ Připojení selhalo: {reason}</span>"
        )
        self._password_edit.clear()
        self._password_edit.setFocus()
        self.completeChanged.emit()

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


__all__ = ["LoginPage"]
