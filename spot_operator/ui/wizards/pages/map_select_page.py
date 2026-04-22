"""Krok 3 playbacku: Výběr existující mapy z DB."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
    QWizardPage,
)

from spot_operator.logging_config import get_logger
from spot_operator.services.map_storage import MapMetadata, list_all_metadata

_log = get_logger(__name__)


class MapSelectPage(QWizardPage):
    """Seznam map z DB + detail vybrané mapy vpravo."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._selected: Optional[MapMetadata] = None

        self.setTitle("3. Vyber mapu k projetí")
        self.setSubTitle("Všechny mapy jsou uložené v databázi.")

        root = QHBoxLayout(self)

        self._list = QListWidget()
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        root.addWidget(self._list, stretch=1)

        detail_frame = QFrame()
        detail_frame.setFrameShape(QFrame.StyledPanel)
        detail_frame.setFixedWidth(320)
        self._detail_form = QFormLayout(detail_frame)
        self._detail_name = QLabel("—")
        self._detail_fiducial = QLabel("—")
        self._detail_sources = QLabel("—")
        self._detail_waypoints = QLabel("—")
        self._detail_checkpoints = QLabel("—")
        self._detail_size = QLabel("—")
        self._detail_note = QLabel("—")
        self._detail_note.setWordWrap(True)
        self._detail_form.addRow("Jméno:", self._detail_name)
        self._detail_form.addRow("Fiducial ID:", self._detail_fiducial)
        self._detail_form.addRow("Strany focení:", self._detail_sources)
        self._detail_form.addRow("Waypointů:", self._detail_waypoints)
        self._detail_form.addRow("Checkpointů:", self._detail_checkpoints)
        self._detail_form.addRow("Velikost:", self._detail_size)
        self._detail_form.addRow("Poznámka:", self._detail_note)
        root.addWidget(detail_frame)

    def initializePage(self) -> None:
        self._list.clear()
        try:
            maps = list_all_metadata()
        except Exception as exc:
            _log.warning("Loading maps failed: %s", exc)
            maps = []
        if not maps:
            item = QListWidgetItem("— Žádné mapy v databázi —")
            item.setFlags(Qt.NoItemFlags)
            self._list.addItem(item)
            return
        for meta in maps:
            label = meta.name
            if meta.fiducial_id is not None:
                label += f"  (fiducial {meta.fiducial_id})"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, meta)
            self._list.addItem(item)

    def isComplete(self) -> bool:
        return self._selected is not None

    def validatePage(self) -> bool:
        if self._selected is None:
            return False
        self.wizard().setProperty("selected_map_id", self._selected.id)
        self.wizard().setProperty(
            "selected_fiducial_id",
            self._selected.fiducial_id if self._selected.fiducial_id is not None else -1,
        )
        self.wizard().setProperty("selected_start_waypoint_id", self._selected.start_waypoint_id or "")
        self.wizard().setProperty(
            "selected_capture_sources", list(self._selected.default_capture_sources)
        )
        return True

    def _on_selection_changed(self) -> None:
        items = self._list.selectedItems()
        if not items:
            self._selected = None
        else:
            meta = items[0].data(Qt.UserRole)
            if isinstance(meta, MapMetadata):
                self._selected = meta
                self._render_detail(meta)
            else:
                self._selected = None
        self.completeChanged.emit()

    def _render_detail(self, m: MapMetadata) -> None:
        self._detail_name.setText(m.name)
        self._detail_fiducial.setText(
            str(m.fiducial_id) if m.fiducial_id is not None else "—"
        )
        self._detail_sources.setText(
            ", ".join(m.default_capture_sources) or "—"
        )
        self._detail_waypoints.setText(
            str(m.waypoints_count) if m.waypoints_count is not None else "—"
        )
        self._detail_checkpoints.setText(
            str(m.checkpoints_count) if m.checkpoints_count is not None else "—"
        )
        self._detail_size.setText(f"{m.archive_size_bytes / 1024:.1f} KB")
        self._detail_note.setText(m.note or "(bez poznámky)")


__all__ = ["MapSelectPage"]
