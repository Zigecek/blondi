"""Krok 6 recordingu: ověř fiducial (konec smyčky) + pojmenuj a ulož mapu do DB."""

from __future__ import annotations

import re
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QWizardPage,
)

from spot_operator.config import AppConfig
from spot_operator.constants import MAP_NAME_REGEX
from spot_operator.logging_config import get_logger
from spot_operator.services.map_storage import MapNameAlreadyExistsError
from spot_operator.services.recording_service import RecordingService, RecordingSnapshot
from spot_operator.ui.common.dialogs import error_dialog, info_dialog
from spot_operator.ui.common.workers import FunctionWorker, cleanup_worker

_log = get_logger(__name__)

_NAME_RE = re.compile(MAP_NAME_REGEX)


class SaveMapPage(QWizardPage):
    """Fiducial re-check + jméno + uložit."""

    def __init__(self, config: AppConfig, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._config = config
        self._saved_map_id: Optional[int] = None
        self._worker: Optional[FunctionWorker] = None
        # Snapshot z Phase 1 (stop_and_export). Držíme ho jako atribut,
        # aby save_snapshot_to_db mohlo být retry-able po DB chybě.
        # PR-04 FIND-140.
        self._snapshot: Optional[RecordingSnapshot] = None
        self._export_worker: Optional[FunctionWorker] = None

        self.setTitle("6. Uložit mapu")
        self.setSubTitle(
            "Vrátil jsi Spota zpět k fiducialu? Ověř + pojmenuj mapu a klikni Uložit."
        )

        root = QVBoxLayout(self)

        fiducial_row = QHBoxLayout()
        self._btn_check_fiducial = QPushButton("Zkontrolovat návrat k fiducialu")
        self._btn_check_fiducial.clicked.connect(self._check_fiducial)
        fiducial_row.addWidget(self._btn_check_fiducial)
        self._fiducial_status = QLabel("")
        self._fiducial_status.setTextFormat(Qt.RichText)
        fiducial_row.addWidget(self._fiducial_status)
        fiducial_row.addStretch(1)
        root.addLayout(fiducial_row)

        root.addSpacing(10)
        root.addWidget(QLabel("Jméno mapy (A-Z, 0-9, _, -, 3-40 znaků):"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("např. parkoviste_sever_2026")
        self._name_edit.textChanged.connect(lambda _: self._update_ok_state())
        root.addWidget(self._name_edit)
        self._name_hint = QLabel("")
        self._name_hint.setStyleSheet("color:#c62828;")
        root.addWidget(self._name_hint)

        root.addWidget(QLabel("Poznámka (volitelné):"))
        self._note_edit = QPlainTextEdit()
        self._note_edit.setMinimumHeight(60)
        root.addWidget(self._note_edit)

        action_row = QHBoxLayout()
        self._btn_save = QPushButton("Uložit mapu")
        self._btn_save.setEnabled(False)
        self._btn_save.clicked.connect(self._start_save)
        action_row.addWidget(self._btn_save)
        action_row.addStretch(1)
        root.addLayout(action_row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        self._save_status = QLabel("")
        self._save_status.setTextFormat(Qt.RichText)
        self._save_status.setWordWrap(True)
        root.addWidget(self._save_status)

        root.addStretch(1)

    def initializePage(self) -> None:
        self._update_ok_state()
        # Phase 1 — stop recording + download map do temp. Běží v BG
        # threadu, UI nečeká. Pokud failne, error dialog a disable save.
        state = self.wizard().recording_state()  # type: ignore[attr-defined]
        service = state.recording_service
        if service is None or not service.is_recording:
            # Snapshot už může být z re-entry (back/next). Neopakujeme export.
            return
        end_fid = state.fiducial_id
        self._save_status.setText("<i>Stahuji mapu z robota...</i>")
        self._progress.setVisible(True)
        self._btn_save.setEnabled(False)

        self._export_worker = FunctionWorker(
            service.stop_and_export,
            end_fiducial_id=int(end_fid) if end_fid is not None else None,
        )
        self._export_worker.finished_ok.connect(self._on_export_ok)
        self._export_worker.failed.connect(self._on_export_failed)
        self._export_worker.start()

    def _on_export_ok(self, snapshot: object) -> None:
        if not isinstance(snapshot, RecordingSnapshot):
            _log.error("Export vrátil neočekávaný typ: %r", type(snapshot))
            self._on_export_failed("Interní chyba: export snapshot má špatný typ.")
            return
        self._snapshot = snapshot
        self._progress.setVisible(False)
        self._save_status.setText(
            "<span style='color:#2e7d32;'>Mapa stažena. Pojmenuj a ulož.</span>"
        )
        self._update_ok_state()

    def _on_export_failed(self, reason: str) -> None:
        self._progress.setVisible(False)
        self._save_status.setText(
            f"<span style='color:#c62828;'>Stahování selhalo: {reason}</span>"
        )
        self._btn_save.setEnabled(False)
        error_dialog(self, "Chyba při stahování mapy", reason)

    def cleanupPage(self) -> None:
        cleanup_worker(self._worker)
        cleanup_worker(self._export_worker)
        self._worker = None
        self._export_worker = None
        # Při návratu zpět uvolnit temp (pokud ještě nebyl uložen).
        if self._snapshot is not None and self._saved_map_id is None:
            self._snapshot.release_temp()
            self._snapshot = None

    def isComplete(self) -> bool:
        return self._saved_map_id is not None

    # ---- Validate name ----

    def _is_name_valid(self) -> bool:
        name = self._name_edit.text().strip()
        if not _NAME_RE.match(name):
            self._name_hint.setText(
                "Jméno musí mít 3–40 znaků: jen A-Z, a-z, 0-9, _, -."
            )
            return False
        from spot_operator.db.engine import Session
        from spot_operator.db.repositories import maps_repo

        with Session() as s:
            if maps_repo.exists_by_name(s, name):
                self._name_hint.setText("Mapa s tímto jménem už existuje.")
                return False
        self._name_hint.setText("")
        return True

    def _update_ok_state(self) -> None:
        # Save je gated na: (a) snapshot je exportovaný (phase 1 OK);
        # (b) jméno je validní a unikátní.
        self._btn_save.setEnabled(
            self._snapshot is not None and self._is_name_valid()
        )

    # ---- Fiducial re-check ----

    def _check_fiducial(self) -> None:
        bundle = self.wizard().bundle()  # type: ignore[attr-defined]
        if bundle is None:
            return
        self._btn_check_fiducial.setEnabled(False)
        self._fiducial_status.setText("<i>Hledám fiducial...</i>")

        from app.robot.fiducial_check import visible_fiducials

        state = self.wizard().recording_state()  # type: ignore[attr-defined]
        required_id = state.fiducial_id
        worker = FunctionWorker(
            visible_fiducials,
            bundle.session,
            required_id=int(required_id) if required_id is not None else None,
            max_distance_m=self._config.fiducial_distance_threshold_m,
        )
        worker.finished_ok.connect(self._on_fiducial_ok)
        worker.failed.connect(self._on_fiducial_fail)
        worker.start()
        self._worker = worker

    def _on_fiducial_ok(self, observations) -> None:  # noqa: ANN001
        self._btn_check_fiducial.setEnabled(True)
        if observations:
            self._fiducial_status.setText(
                f"<span style='color:#2e7d32;'>✓ Spot stojí u fiducialu.</span>"
            )
        else:
            self._fiducial_status.setText(
                "<span style='color:#c62828;'>✗ Fiducial nevidím — Spot se "
                "nevrátil k nabíječce. Mapu sice uložit můžeš, ale lokalizace "
                "při playbacku bude horší.</span>"
            )

    def _on_fiducial_fail(self, reason: str) -> None:
        self._btn_check_fiducial.setEnabled(True)
        self._fiducial_status.setText(
            f"<span style='color:#c62828;'>Chyba: {reason}</span>"
        )

    # ---- Save ----

    def _start_save(self) -> None:
        if not self._is_name_valid():
            return

        # Two-phase save (PR-04 FIND-140): phase 1 (export) už proběhl
        # v initializePage. Teď jen phase 2 (save snapshot do DB), které
        # je retry-safe — při DB failure může user zkusit znovu s jiným
        # jménem bez ztráty dat.
        if self._snapshot is None:
            error_dialog(
                self,
                "Chyba",
                "Snapshot mapy není k dispozici — stahování selhalo. "
                "Zavři wizard a začni znovu.",
            )
            return
        state = self.wizard().recording_state()  # type: ignore[attr-defined]
        service: Optional[RecordingService] = state.recording_service
        if service is None:
            error_dialog(self, "Chyba", "Recording service není aktivní.")
            return

        name = self._name_edit.text().strip()
        note = self._note_edit.toPlainText().strip()
        operator = self._config.operator_label or None

        self._btn_save.setEnabled(False)
        self._progress.setVisible(True)
        self._save_status.setText("<i>Ukládám mapu do databáze...</i>")

        self._worker = FunctionWorker(
            service.save_snapshot_to_db,
            self._snapshot,
            map_name=name,
            note=note,
            operator_label=operator,
        )
        self._worker.finished_ok.connect(self._on_save_ok)
        self._worker.failed.connect(self._on_save_failed)
        self._worker.start()

    def _on_save_ok(self, map_id) -> None:  # noqa: ANN001
        self._progress.setVisible(False)
        self._saved_map_id = int(map_id)
        state = self.wizard().recording_state()  # type: ignore[attr-defined]
        state.saved_map_id = self._saved_map_id
        state.recording_service = None
        self._save_status.setText(
            f"<span style='color:#2e7d32;'>✓ Mapa uložena (id={map_id}).</span>"
        )
        info_dialog(
            self,
            "Hotovo",
            f"Mapa byla úspěšně uložena do databáze s id={map_id}.",
        )
        self.completeChanged.emit()

    def _on_save_failed(self, reason: str) -> None:
        self._progress.setVisible(False)
        # PR-04: save je idempotentní — snapshot zůstává v temp a user
        # může opakovat (buď retry stejné jméno, nebo zvolit jiné, pokud
        # důvod selhání je duplicate name).
        self._btn_save.setEnabled(True)
        self._save_status.setText(
            f"<span style='color:#c62828;'>✗ Uložení selhalo: {reason}</span><br>"
            f"<span style='color:#555;'>Snapshot je stále v paměti, můžeš zkusit znovu.</span>"
        )
        if "už v DB existuje" in reason or "ux_maps_name" in reason.lower():
            error_dialog(
                self,
                "Název obsazen",
                f"Mapa s názvem už existuje. Zvol jiný a klikni Uložit znovu.",
            )
        else:
            error_dialog(self, "Chyba při ukládání", reason)


__all__ = ["SaveMapPage"]
