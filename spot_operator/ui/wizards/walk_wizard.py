"""Walk wizard — jednoduchá chůze se Spotem bez recording/playbacku.

Obsahuje stejnou `FiducialPage` jako oba hlavní wizardy (power-on + WASD
teleop + live view + kontrola fiducialu), ale bez navazujících kroků.
Operátor zde může volně procházet se Spotem, otestovat fiducial, uvolnit
E-Stop a ověřit si že connectivita funguje.
"""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtWidgets import QWidget, QWizard

from spot_operator.config import AppConfig
from spot_operator.logging_config import get_logger
from spot_operator.ui.wizards.base_wizard import SpotWizard
from spot_operator.ui.wizards.pages.connect_page import ConnectPage
from spot_operator.ui.wizards.pages.fiducial_page import FiducialPage

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

    def _populate_props_from_bundle(self, bundle: Any) -> None:
        try:
            from app.robot.images import ImagePoller

            poller = ImagePoller(bundle.session)
            sources = poller.list_sources()
            self.setProperty("available_sources", list(sources))
        except Exception as exc:
            _log.warning("populate_props: list_sources failed: %s", exc)
            self.setProperty("available_sources", None)
        ip = getattr(bundle.session, "hostname", None) or getattr(
            bundle.session, "_hostname", None
        )
        if ip:
            self.setProperty("spot_ip", str(ip))

    def _close_confirmation_message(self) -> str:
        return (
            "Chůze se Spotem — po zavření se WASD teleop zastaví a uvolní "
            "image pipeline. Spot zůstane zapnutý (neodpojuje se)."
        )


__all__ = ["WalkWizard"]
