"""Walk wizard — jednoduchá chůze se Spotem bez recording/playbacku.

Obsahuje stejnou `FiducialPage` jako oba hlavní wizardy (power-on + WASD
teleop + live view + kontrola fiducialu), ale bez navazujících kroků.
Operátor zde může volně procházet se Spotem, otestovat fiducial, uvolnit
E-Stop a ověřit si že connectivita funguje.
"""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtWidgets import QWidget, QWizard

from blondi.config import AppConfig
from blondi.logging_config import get_logger
from blondi.ui.wizards.base_wizard import SpotWizard
from blondi.ui.wizards.pages.connect_page import ConnectPage
from blondi.ui.wizards.pages.fiducial_page import FiducialPage
from blondi.ui.wizards.state import WalkWizardState

_log = get_logger(__name__)


class WalkWizard(SpotWizard):
    """Jediný krok: FiducialPage v režimu "volná chůze" (required_id=None).

    Když MainWindow dodá připojený bundle, ConnectPage se přeskočí a wizard
    otevře rovnou FiducialPage s power-on detekcí.
    """

    def __init__(
        self,
        config: AppConfig,
        parent: Optional[QWidget] = None,
        *,
        bundle: Any | None = None,
    ):
        super().__init__(config, window_title="Chůze se Spotem", parent=parent)
        # PR-09 FIND-130: typed state, místo Qt property hacks ve FiducialPage.
        self.set_flow_state(WalkWizardState())

        if bundle is not None:
            self.set_bundle(bundle, owned=False)
            self._populate_props_from_bundle(bundle)
        else:
            self.addPage(ConnectPage(config, parent=self))

        # required_id=None → kontrola fiducialu je volitelná (jakýkoli fiducial
        # uspěje, bez checku prostě user klikne "Dokončit").
        self._walk_page = FiducialPage(config, required_id=None, parent=self)
        self.addPage(self._walk_page)

        # V tomto wizardu chceme, aby user mohl "Dokončit" i bez úspěšného
        # fiducial checku — FiducialPage.isComplete() vyžaduje detekci; Finish
        # tlačítko by pak bylo disabled. Povolíme Finish manuálně přes override.
        self.setOption(QWizard.HaveFinishButtonOnEarlyPages, True)
        self.setButtonText(QWizard.FinishButton, "Zavřít ✓")

    def walk_state(self) -> WalkWizardState:
        state = self.flow_state()
        if not isinstance(state, WalkWizardState):
            raise RuntimeError(
                f"WalkWizard flow_state je {type(state).__name__}, "
                "očekáván WalkWizardState."
            )
        return state

    def _populate_props_from_bundle(self, bundle: Any) -> None:
        """PR-09 FIND-136: sdílený bundle.get_info() helper."""
        info = bundle.get_info()
        state = self.walk_state()
        state.available_sources = list(info.available_sources)
        if info.hostname:
            state.spot_ip = info.hostname

    def _close_confirmation_message(self) -> str:
        from blondi.ui.wizards.messages import CLOSE_WARNING_WALK

        return CLOSE_WARNING_WALK


__all__ = ["WalkWizard"]
