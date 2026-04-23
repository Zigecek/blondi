"""CRUD okno — tabulky SPZ, Běhy, Fotky."""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QMainWindow, QTabWidget, QWidget

from spot_operator.config import AppConfig
from spot_operator.ui.crud.photos_tab import PhotosTab
from spot_operator.ui.crud.runs_tab import RunsTab
from spot_operator.ui.crud.spz_tab import SpzTab


class CrudWindow(QMainWindow):
    """Samostatné CRUD okno se třemi taby (SPZ, Běhy, Fotky)."""

    def __init__(self, config: AppConfig, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._config = config
        self.setWindowTitle("Správa SPZ a běhů")
        self.resize(1100, 720)

        tabs = QTabWidget(self)
        tabs.addTab(SpzTab(self._config, parent=self), "SPZ registr")
        tabs.addTab(RunsTab(self._config, parent=self), "Běhy")
        tabs.addTab(PhotosTab(self._config, self), "Fotky")
        self.setCentralWidget(tabs)


__all__ = ["CrudWindow"]
