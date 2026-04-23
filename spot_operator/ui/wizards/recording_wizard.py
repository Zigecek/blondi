"""Recording wizard — 5 kroků: Wi-Fi, Login, Fiducial (s teleopem), Teleop-recording, Save.

Oproti 1.1.x: zrušen krok "Strana focení" (`RecordingSidePage`). Volba strany
je per-checkpoint v TeleopRecordPage přes tlačítka Foto vlevo/vpravo/obě.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QWidget

from spot_operator.config import AppConfig
from spot_operator.ui.wizards.base_wizard import SpotWizard
from spot_operator.ui.wizards.pages.fiducial_page import FiducialPage
from spot_operator.ui.wizards.pages.login_page import LoginPage
from spot_operator.ui.wizards.pages.save_map_page import SaveMapPage
from spot_operator.ui.wizards.pages.teleop_record_page import TeleopRecordPage
from spot_operator.ui.wizards.pages.wifi_page import WifiPage


class RecordingWizard(SpotWizard):
    """QWizard pro nahrávání nové mapy. 5 kroků."""

    def __init__(self, config: AppConfig, parent: Optional[QWidget] = None):
        super().__init__(config, window_title="Nahrávání nové mapy", parent=parent)

        self.addPage(WifiPage(config, parent=self))
        self.addPage(LoginPage(config, parent=self))
        # Recording — required_id=None (libovolný startovací fiducial).
        # FiducialPage nově obsahuje live view + power-on + WASD teleop, takže
        # operátor tu může fyzicky dovézt Spota k fiducialu.
        self.addPage(FiducialPage(config, required_id=None, parent=self))
        self.addPage(TeleopRecordPage(parent=self))
        self.addPage(SaveMapPage(config, parent=self))

    def _should_confirm_close(self) -> bool:
        # Pokud je aktivní recording nebo připojený Spot, zeptej se před zavřením.
        return super()._should_confirm_close()

    def _close_confirmation_message(self) -> str:
        return (
            "Nahrávání probíhá. Po zavření se nahrávka zruší a nic se "
            "neuloží do databáze. Pokračovat?"
        )


__all__ = ["RecordingWizard"]
