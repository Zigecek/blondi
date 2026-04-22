"""Recording wizard — 6 kroků: Wi-Fi, Login, Strana, Fiducial, Teleop, Save."""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QWidget

from spot_operator.config import AppConfig
from spot_operator.ui.wizards.base_wizard import SpotWizard
from spot_operator.ui.wizards.pages.fiducial_page import FiducialPage
from spot_operator.ui.wizards.pages.login_page import LoginPage
from spot_operator.ui.wizards.pages.recording_side_page import RecordingSidePage
from spot_operator.ui.wizards.pages.save_map_page import SaveMapPage
from spot_operator.ui.wizards.pages.teleop_record_page import TeleopRecordPage
from spot_operator.ui.wizards.pages.wifi_page import WifiPage


class RecordingWizard(SpotWizard):
    """QWizard pro nahrávání nové mapy."""

    def __init__(self, config: AppConfig, parent: Optional[QWidget] = None):
        super().__init__(config, window_title="Nahrávání nové mapy", parent=parent)

        self.addPage(WifiPage(config, parent=self))
        self.addPage(LoginPage(config, parent=self))
        self.addPage(RecordingSidePage(parent=self))
        # Recording — required_id=None (libovolný startovací fiducial)
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
