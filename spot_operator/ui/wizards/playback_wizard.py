"""Playback wizard — sériový flow: Connect → Mapa → Fiducial → Run → Výsledek.

Pozn. k designu: mapa se **NE nahrává paralelně** během chůze k fiducialu.
Paralelní upload by zatížil Wi-Fi kanál který zároveň nosí velocity commandy
a image stream → WASD sekalo a kamera lagovala. Místo toho je upload součástí
PlaybackRunPage.initializePage (prepare_map) s jasným progress barem — robot
v té chvíli stojí u fiducialu, takže Wi-Fi konflikt neexistuje.
"""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtWidgets import QWidget

from spot_operator.config import AppConfig
from spot_operator.logging_config import get_logger
from spot_operator.ui.wizards.base_wizard import SpotWizard
from spot_operator.ui.wizards.pages.connect_page import ConnectPage
from spot_operator.ui.wizards.pages.fiducial_page import FiducialPage
from spot_operator.ui.wizards.pages.map_select_page import MapSelectPage
from spot_operator.ui.wizards.pages.playback_result_page import PlaybackResultPage
from spot_operator.ui.wizards.pages.playback_run_page import PlaybackRunPage
from spot_operator.ui.wizards.state import PlaybackWizardState

_log = get_logger(__name__)


class PlaybackWizard(SpotWizard):
    """QWizard pro spuštění playbacku existující mapy. Sériový flow."""

    def __init__(
        self,
        config: AppConfig,
        *,
        ocr_worker=None,  # noqa: ANN001 — OcrWorker; lazy typing kvůli import cycle
        parent: Optional[QWidget] = None,
        bundle: Any | None = None,
    ):
        super().__init__(config, window_title="Spuštění jízdy podle mapy", parent=parent)
        self.set_flow_state(PlaybackWizardState())

        # Pokud volající (MainWindow) dodal už připojený bundle, skipni Connect
        # krok a rovnou začni od výběru mapy. Jinak má wizard vlastní ConnectPage.
        if bundle is not None:
            self.set_bundle(bundle, owned=False)
            self._populate_props_from_bundle(bundle)
        else:
            self.addPage(ConnectPage(config, parent=self))
        self._map_select_page = MapSelectPage(parent=self)
        self.addPage(self._map_select_page)
        # Fiducial s required_id=None; po výběru mapy ho MapSelectPage nastaví přes property.
        self._fiducial_page = FiducialPage(config, required_id=None, parent=self)
        self.addPage(self._fiducial_page)
        # OCR worker předáváme do PlaybackRunPage, aby se jeho photo_processed
        # signál zobrazoval v live logu.
        self.addPage(PlaybackRunPage(config, ocr_worker=ocr_worker, parent=self))
        self.addPage(PlaybackResultPage(parent=self))

        self.currentIdChanged.connect(self._on_page_changed)

    def playback_state(self) -> PlaybackWizardState:
        state = self.flow_state()
        # PR-09 FIND-131: raise místo assert.
        if not isinstance(state, PlaybackWizardState):
            raise RuntimeError(
                f"PlaybackWizard flow_state je {type(state).__name__}, "
                "očekáván PlaybackWizardState — inicializace wizardu byla přeskočena?"
            )
        return state

    def _populate_props_from_bundle(self, bundle: Any) -> None:
        """PR-09 FIND-136: sdílený bundle.get_info() helper."""
        info = bundle.get_info()
        state = self.playback_state()
        state.available_sources = list(info.available_sources)
        if info.hostname:
            state.spot_ip = info.hostname

    def _on_page_changed(self, page_id: int) -> None:
        """Vstup na FiducialPage: nastav required_id podle vybrané mapy."""
        page = self.page(page_id)
        if page is self._fiducial_page:
            required = self.playback_state().selected_fiducial_id
            if required is not None and int(required) >= 0:
                self._fiducial_page.set_required_id(int(required))
            else:
                self._fiducial_page.set_required_id(None)

    def _close_confirmation_message(self) -> str:
        from spot_operator.ui.wizards.messages import CLOSE_WARNING_PLAYBACK

        return CLOSE_WARNING_PLAYBACK


__all__ = ["PlaybackWizard"]
