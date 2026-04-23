"""Krok 1: Wi-Fi + Login v jednom. Ověří spojení a přihlásí k Spotovi.

Sloučeno z dříve samostatných `WifiPage` a `LoginPage`. Flow:

1. Operátor vybere existující profil (nebo nechá "— Nový profil —") a vyplní
   zbytek.
2. Klikne "Ověřit spojení a přihlásit":
   a) Nejdřív proběhne Wi-Fi check (ping + TCP port 443).
   b) Pokud je Wi-Fi OK → pokračuje session_factory.connect().
   c) Při úspěchu → set_bundle + uložení profilu do keyringu (pokud "zapamatovat").
3. Stránka je complete.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
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
from spot_operator.services import credentials_service, spot_wifi
from spot_operator.ui.common.workers import FunctionWorker

_log = get_logger(__name__)


def _connect_with_wifi_check(
    ip: str, username: str, password: str, wifi_timeout: float = 3.0
):
    """Background job: Wi-Fi check → spot session connect. Vrací bundle.

    Raises pokud Wi-Fi check selže nebo connect selže — volající pozná přes
    `failed` signál.
    """
    wifi_result = spot_wifi.check_connection(ip)
    if not wifi_result.ok:
        raise RuntimeError(
            f"Wi-Fi k Spotovi nefunguje ({wifi_result.detail}). "
            "Zkontroluj, že jsi připojen k Spot síti."
        )
    from spot_operator.robot.session_factory import connect as connect_spot

    return connect_spot(ip, username, password)


class ConnectPage(QWizardPage):
    """Kombinovaný krok: Wi-Fi check + Spot session connect."""

    def __init__(self, config: AppConfig, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._config = config
        self._connected = False
        self._worker: Optional[FunctionWorker] = None
        self._selected_cred_id: Optional[int] = None

        self.setTitle("1. Připojení ke Spotovi")
        self.setSubTitle(
            "Připoj se k Wi-Fi Spota a zadej přihlašovací údaje. "
            "Ověření spojení proběhne automaticky před přihlášením."
        )

        root = QVBoxLayout(self)

        # --- Wi-Fi sekce (info + tlačítko otevřít menu) ---
        wifi_frame = QFrame()
        wifi_frame.setFrameShape(QFrame.StyledPanel)
        wifi_layout = QVBoxLayout(wifi_frame)
        wifi_info = QLabel(
            "📶 <b>Wi-Fi:</b> Připoj Windows k síti Spota "
            "(typicky <code>spot-BD-XXXXXXXX</code>). Heslo je na QR štítku "
            "Spota nebo v dokumentaci."
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

        # --- Login formulář ---
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

        # --- Tlačítka + progress + status ---
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

        root.addStretch(1)

    def initializePage(self) -> None:
        self._reload_profiles()
        self._maybe_autoselect_profile()

    def isComplete(self) -> bool:
        return self._connected

    def validatePage(self) -> bool:
        return self._connected

    # ---- Profil helpers ----

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

    def _maybe_autoselect_profile(self) -> None:
        """Vyber profil podle zadané IP nebo pokud je jen jeden profil."""
        try:
            creds = list(credentials_service.list_credentials())
        except Exception as exc:
            _log.warning("auto-select profile: list_credentials failed: %s", exc)
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
        self._selected_cred_id = cred_id
        if cred_id is None:
            self._password_edit.clear()
            return
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
        self._btn_connect.setEnabled(True)
        self._progress.setVisible(False)
        self._connected = True
        # Spot_ip property používají následující stránky (FiducialPage etc.).
        self.wizard().setProperty("spot_ip", self._ip_edit.text().strip())
        self.wizard().set_bundle(bundle)  # type: ignore[attr-defined]

        # Auto-detect dostupných image sources.
        try:
            from app.robot.images import ImagePoller

            poller = ImagePoller(bundle.session)
            sources = poller.list_sources()
            self.wizard().setProperty("available_sources", list(sources))
            _log.info("Spot advertise %d image sources: %s", len(sources), sources)
        except Exception as exc:
            _log.warning(
                "Could not list image sources (fallback to defaults): %s", exc
            )
            self.wizard().setProperty("available_sources", None)

        self._status.setText(
            "<span style='color:#2e7d32;'>✓ Wi-Fi OK a Spot připojen.</span>"
        )
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


__all__ = ["ConnectPage"]
