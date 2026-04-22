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

from spot_operator.constants import CAMERA_LEFT, CAMERA_RIGHT
from spot_operator.db.enums import FiducialSide


class RecordingSidePage(QWizardPage):
    """Radio Buttons: Levá / Pravá / Obě strany. Schematický popis trasy."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

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

        root.addStretch(1)

    def isComplete(self) -> bool:
        return self._btn_group.checkedButton() is not None

    def validatePage(self) -> bool:
        if self._rb_left.isChecked():
            sources = [CAMERA_LEFT]
            side = FiducialSide.left
        elif self._rb_right.isChecked():
            sources = [CAMERA_RIGHT]
            side = FiducialSide.right
        else:
            sources = [CAMERA_LEFT, CAMERA_RIGHT]
            side = FiducialSide.both
        self.wizard().setProperty("capture_sources", sources)
        self.wizard().setProperty("fiducial_side", side.value)
        return True


__all__ = ["RecordingSidePage"]
