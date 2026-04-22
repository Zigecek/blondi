"""Krok 3 recordingu: Info o trase + volba strany focení."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
    QWizardPage,
)

from spot_operator.constants import (
    CAMERA_LEFT,
    CAMERA_RIGHT,
    PREFERRED_LEFT_CANDIDATES,
    PREFERRED_RIGHT_CANDIDATES,
    pick_side_source,
)
from spot_operator.db.enums import FiducialSide
from spot_operator.logging_config import get_logger

_log = get_logger(__name__)


class RecordingSidePage(QWizardPage):
    """Radio Buttons: Levá / Pravá / Obě strany. Schematický popis trasy.

    Radio buttons se aktivují/deaktivují podle toho, jaké image sources
    advertise reálný Spot (uložené do wizard property "available_sources"
    z LoginPage._on_connect_ok).
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

        self._resolved_left: str = CAMERA_LEFT
        self._resolved_right: str = CAMERA_RIGHT

        self.setTitle("3. Jak bude Spot fotit auta")
        self.setSubTitle(
            "Vyber, z jaké strany bude Spot projíždět parkoviště."
        )

        root = QVBoxLayout(self)

        explainer = QLabel(
            "<p>Spot má dvě boční fisheye kamery (levá a pravá). Pokud parkuješ "
            "auta po jedné straně cesty, stačí vybrat jen tu stranu. "
            "Pokud parkují po obou stranách, vyber <b>Obě strany</b> "
            "(projetí zabere více času a vytvoří dvakrát víc fotek).</p>"
            "<p><b>Trasa musí být kruhová:</b><br>"
            "&nbsp;&nbsp;<code>fiducial u nabíječky → parkoviště → fotky aut → "
            "zpět k fiducialu</code><br>"
            "Jakmile projdeš do dalšího kroku, nahrávání se automaticky spustí.</p>"
        )
        explainer.setWordWrap(True)
        explainer.setTextFormat(Qt.RichText)
        root.addWidget(explainer)

        self._btn_group = QButtonGroup(self)
        self._rb_left = QRadioButton("Levá strana auta (levá kamera Spota)")
        self._rb_right = QRadioButton("Pravá strana auta (pravá kamera Spota)")
        self._rb_both = QRadioButton("Obě strany (dvakrát více fotek, pomalejší)")
        self._btn_group.addButton(self._rb_left)
        self._btn_group.addButton(self._rb_right)
        self._btn_group.addButton(self._rb_both)
        self._btn_group.buttonToggled.connect(lambda *_: self.completeChanged.emit())

        root.addWidget(self._rb_left)
        root.addWidget(self._rb_right)
        root.addWidget(self._rb_both)

        self._availability_note = QLabel("")
        self._availability_note.setStyleSheet("color:#c62828; font-style:italic;")
        self._availability_note.setWordWrap(True)
        root.addWidget(self._availability_note)

        root.addStretch(1)

    def initializePage(self) -> None:
        """Adapt radio buttons podle toho, co reálný Spot vidí.

        Pokud LoginPage nastavil "available_sources", zkusíme resolve nejlepší
        jméno pro levou a pravou kameru. Pokud jedna chybí, disable radio
        button + zobraz note.
        """
        available = self.wizard().property("available_sources")
        if not available:
            # LoginPage nezískala seznam (např. kvůli SDK chybě). Necháme
            # hardcoded defaults a doufáme, že `left_fisheye_image` funguje.
            self._resolved_left = CAMERA_LEFT
            self._resolved_right = CAMERA_RIGHT
            self._availability_note.setText("")
            self._rb_left.setEnabled(True)
            self._rb_right.setEnabled(True)
            self._rb_both.setEnabled(True)
            return

        left = pick_side_source(available, PREFERRED_LEFT_CANDIDATES)
        right = pick_side_source(available, PREFERRED_RIGHT_CANDIDATES)
        self._resolved_left = left or CAMERA_LEFT
        self._resolved_right = right or CAMERA_RIGHT

        missing_parts: list[str] = []
        if left is None:
            self._rb_left.setEnabled(False)
            self._rb_left.setToolTip("Spot tuto kameru neadvertisuje.")
            missing_parts.append("levou")
        else:
            self._rb_left.setEnabled(True)
            self._rb_left.setToolTip(f"Source: {left}")
        if right is None:
            self._rb_right.setEnabled(False)
            self._rb_right.setToolTip("Spot tuto kameru neadvertisuje.")
            missing_parts.append("pravou")
        else:
            self._rb_right.setEnabled(True)
            self._rb_right.setToolTip(f"Source: {right}")
        # "Obě" je dostupné pouze pokud obě kamery jsou OK.
        self._rb_both.setEnabled(left is not None and right is not None)

        if missing_parts:
            self._availability_note.setText(
                f"Tento robot neadvertisuje {' a '.join(missing_parts)} kameru — "
                f"možnost je vypnutá."
            )
        else:
            self._availability_note.setText("")

        _log.info(
            "Capture sources resolved: left=%s right=%s (from %d available)",
            self._resolved_left,
            self._resolved_right,
            len(available),
        )

    def isComplete(self) -> bool:
        btn = self._btn_group.checkedButton()
        if btn is None:
            return False
        # Radio buttons disabled při missing source — pokud operátor vybral
        # před initializePage, isEnabled() je False → isComplete False.
        return btn.isEnabled()

    def validatePage(self) -> bool:
        if self._rb_left.isChecked():
            sources = [self._resolved_left]
            side = FiducialSide.left
        elif self._rb_right.isChecked():
            sources = [self._resolved_right]
            side = FiducialSide.right
        else:
            sources = [self._resolved_left, self._resolved_right]
            side = FiducialSide.both
        self.wizard().setProperty("capture_sources", sources)
        self.wizard().setProperty("fiducial_side", side.value)
        return True


__all__ = ["RecordingSidePage"]
