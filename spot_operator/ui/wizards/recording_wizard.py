"""Recording wizard — 4 kroky: Connect (Wi-Fi+Login), Fiducial, Teleop-recording, Save.

Oproti 1.1.x: zrušen krok "Strana focení" (`RecordingSidePage`). Volba strany
je per-checkpoint v TeleopRecordPage přes tlačítka Foto vlevo/vpravo/obě.
Oproti 1.2.x: sloučeny kroky Wi-Fi + Login do `ConnectPage`.
Od 1.4: pokud MainWindow má sdílený Spot bundle, skipni ConnectPage.
"""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtWidgets import QWidget

from spot_operator.config import AppConfig
from spot_operator.logging_config import get_logger
from spot_operator.ui.wizards.base_wizard import SpotWizard
from spot_operator.ui.wizards.pages.connect_page import ConnectPage
from spot_operator.ui.wizards.pages.fiducial_page import FiducialPage
from spot_operator.ui.wizards.pages.save_map_page import SaveMapPage
from spot_operator.ui.wizards.pages.teleop_record_page import TeleopRecordPage
from spot_operator.ui.wizards.state import RecordingWizardState

_log = get_logger(__name__)


class RecordingWizard(SpotWizard):
    """QWizard pro nahrávání nové mapy. 4 kroky (3 pokud je bundle sdílen)."""

    def __init__(
        self,
        config: AppConfig,
        parent: Optional[QWidget] = None,
        *,
        bundle: Any | None = None,
    ):
        super().__init__(config, window_title="Nahrávání nové mapy", parent=parent)
        self.set_flow_state(RecordingWizardState())

        if bundle is not None:
            self.set_bundle(bundle, owned=False)
            self._populate_props_from_bundle(bundle)
        else:
            self.addPage(ConnectPage(config, parent=self))
        # Recording — required_id=None (libovolný startovací fiducial).
        # FiducialPage nově obsahuje live view + power-on + WASD teleop, takže
        # operátor tu může fyzicky dovézt Spota k fiducialu.
        self.addPage(FiducialPage(config, required_id=None, parent=self))
        self.addPage(TeleopRecordPage(parent=self))
        self.addPage(SaveMapPage(config, parent=self))

    def recording_state(self) -> RecordingWizardState:
        state = self.flow_state()
        # PR-09 FIND-131: raise místo assert (v `python -O` mode by assert skipl
        # a volající by dostal AttributeError někde dál).
        if not isinstance(state, RecordingWizardState):
            raise RuntimeError(
                f"RecordingWizard flow_state je {type(state).__name__}, "
                "očekáván RecordingWizardState — inicializace wizardu byla přeskočena?"
            )
        return state

    def _populate_props_from_bundle(self, bundle: Any) -> None:
        """Když je bundle dodán externě (MainWindow), ConnectPage se neprojde —
        nastavíme properties manuálně. PR-09 FIND-136: sdílený helper.
        """
        info = bundle.get_info()
        state = self.recording_state()
        state.available_sources = list(info.available_sources)
        if info.hostname:
            state.spot_ip = info.hostname

    def _should_confirm_close(self) -> bool:
        # Pokud je aktivní recording nebo připojený Spot, zeptej se před zavřením.
        return super()._should_confirm_close()

    def _close_confirmation_message(self) -> str:
        from spot_operator.ui.wizards.messages import CLOSE_WARNING_RECORDING

        return CLOSE_WARNING_RECORDING


__all__ = ["RecordingWizard"]
