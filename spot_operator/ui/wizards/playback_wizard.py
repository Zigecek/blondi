"""Playback wizard — 6 kroků: Wi-Fi, Login, Mapa, Fiducial, Run, Výsledek."""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QWidget

from spot_operator.config import AppConfig
from spot_operator.ui.wizards.base_wizard import SpotWizard
from spot_operator.ui.wizards.pages.fiducial_page import FiducialPage
from spot_operator.ui.wizards.pages.login_page import LoginPage
from spot_operator.ui.wizards.pages.map_select_page import MapSelectPage
from spot_operator.ui.wizards.pages.playback_result_page import PlaybackResultPage
from spot_operator.ui.wizards.pages.playback_run_page import PlaybackRunPage
from spot_operator.ui.wizards.pages.wifi_page import WifiPage


class PlaybackWizard(SpotWizard):
    """QWizard pro spuštění playbacku existující mapy."""

    def __init__(
        self,
        config: AppConfig,
        *,
        ocr_worker=None,  # noqa: ANN001 — OcrWorker; lazy typing kvůli import cycle
        parent: Optional[QWidget] = None,
    ):
        super().__init__(config, window_title="Spuštění jízdy podle mapy", parent=parent)

        self.addPage(WifiPage(config, parent=self))
        self.addPage(LoginPage(config, parent=self))
        self.addPage(MapSelectPage(parent=self))
        # Fiducial s required_id=None; po výběru mapy ho MapSelectPage nastaví přes property.
        self._fiducial_page = FiducialPage(config, required_id=None, parent=self)
        self.addPage(self._fiducial_page)
        # OCR worker předáváme do PlaybackRunPage, aby se jeho photo_processed
        # signál zobrazoval v live logu.
        self.addPage(PlaybackRunPage(config, ocr_worker=ocr_worker, parent=self))
        self.addPage(PlaybackResultPage(parent=self))

        self.currentIdChanged.connect(self._on_page_changed)

    def _on_page_changed(self, page_id: int) -> None:
        """Když operátor právě vstoupil na fiducial page, nastav required_id z mapy."""
        page = self.page(page_id)
        if page is self._fiducial_page:
            required = self.property("selected_fiducial_id")
            if required is not None and int(required) >= 0:
                self._fiducial_page.set_required_id(int(required))
            else:
                self._fiducial_page.set_required_id(None)

    def _close_confirmation_message(self) -> str:
        return (
            "Autonomní jízda probíhá nebo máš aktivní spojení se Spotem. "
            "Po zavření se vše ukončí. Pokračovat?"
        )


__all__ = ["PlaybackWizard"]
