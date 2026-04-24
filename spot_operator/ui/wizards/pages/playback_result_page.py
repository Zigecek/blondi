"""Krok 6 playbacku: Shrnutí běhu + seznam SPZ + stažení ZIP."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QWizardPage,
)

from spot_operator.constants import ROBOT_LOST_ERROR_MARKERS
from spot_operator.db.engine import Session
from spot_operator.db.repositories import photos_repo, runs_repo
from spot_operator.logging_config import get_logger
from spot_operator.services.contracts import parse_checkpoint_results
from spot_operator.services.zip_exporter import build_run_zip
from spot_operator.ui.common.dialogs import error_dialog, info_dialog

_log = get_logger(__name__)


class PlaybackResultPage(QWizardPage):
    """Shrnutí + tabulka přečtených SPZ + stažení ZIP."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._run_id: Optional[int] = None

        self.setTitle("6. Výsledek jízdy")
        self.setSubTitle("Stáhni ZIP s fotkami a metadaty nebo ukonči wizard.")

        root = QVBoxLayout(self)

        self._summary = QLabel("—")
        self._summary.setTextFormat(Qt.RichText)
        self._summary.setWordWrap(True)
        root.addWidget(self._summary)

        # Actionable tip box — viditelný jen pokud run padl na RobotLostError
        # nebo jiné terminální chybě. Skrytý pro úspěšné runy.
        self._tip_box = QLabel("")
        self._tip_box.setTextFormat(Qt.RichText)
        self._tip_box.setWordWrap(True)
        self._tip_box.setStyleSheet(
            "QLabel { background:#fff3e0; border:2px solid #ef6c00; "
            "border-radius:4px; padding:10px; color:#bf360c; }"
        )
        self._tip_box.setVisible(False)
        root.addWidget(self._tip_box)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["Checkpoint", "Kamera", "Stav OCR", "SPZ", "Conf (text/det)"]
        )
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        root.addWidget(self._table, stretch=1)

        action_row = QHBoxLayout()
        self._btn_export = QPushButton("Stáhnout ZIP")
        self._btn_export.clicked.connect(self._on_export)
        action_row.addWidget(self._btn_export)
        action_row.addStretch(1)
        root.addLayout(action_row)

    def initializePage(self) -> None:
        state = self.wizard().playback_state()  # type: ignore[attr-defined]
        self._run_id = state.completed_run_id
        if self._run_id is None or self._run_id < 0:
            self._summary.setText("<span style='color:#c00;'>Run id nebyl zapsán.</span>")
            return
        self._populate()

    def _populate(self) -> None:
        assert self._run_id is not None
        with Session() as s:
            run = runs_repo.get(s, self._run_id)
            if run is None:
                self._summary.setText(
                    f"<span style='color:#c00;'>Run id {self._run_id} není v DB.</span>"
                )
                return
            photos = photos_repo.list_for_run_light(s, self._run_id)
            checkpoint_results = parse_checkpoint_results(
                getattr(run, "checkpoint_results_json", None) or []
            )
            abort_reason = getattr(run, "abort_reason", None) or ""
            partial_count = sum(1 for item in checkpoint_results if not item.is_complete)
            summary_lines = [
                f"<b>Run:</b> {run.run_code}",
                f"<b>Mapa:</b> {run.map_name_snapshot or '—'}",
                f"<b>Stav:</b> {run.status.value}",
                f"<b>Checkpointů:</b> {run.checkpoints_reached}/{run.checkpoints_total}",
                f"<b>Fotek:</b> {len(photos)}",
                f"<b>Dílčí checkpointy:</b> {partial_count}",
            ]
            if getattr(run, "return_home_status", "not_requested") != "not_requested":
                summary_lines.append(
                    f"<b>Návrat domů:</b> {run.return_home_status}"
                    + (
                        f" ({run.return_home_reason})"
                        if getattr(run, "return_home_reason", None)
                        else ""
                    )
                )
            if abort_reason:
                summary_lines.append(
                    f"<b>Důvod ukončení:</b> <span style='color:#c62828;'>"
                    f"{abort_reason}</span>"
                )
            self._summary.setText("<br>".join(summary_lines))

            # Pokud run selhal kvůli RobotLostError, ukaž actionable tip.
            lost_reason = any(
                marker in abort_reason.lower()
                for marker in ROBOT_LOST_ERROR_MARKERS
            )
            # Fix 4: run skončil kvůli E-STOP / impaired stavu.
            abort_lower = abort_reason.lower()
            impaired_reason = (
                "impaired" in abort_lower
                or "estop" in abort_lower
                or "e-stop" in abort_lower
            )
            if lost_reason:
                self._tip_box.setText(
                    "⚠ <b>Robot ztratil GraphNav lokalizaci během jízdy.</b><br>"
                    "<br>"
                    "S jediným fiducialem u startu se Spot po ~15–20 m začíná "
                    "ztrácet kvůli odometry driftu. Řešení:"
                    "<ul>"
                    "<li>Přidej <b>2–3 fiducialy podél trasy</b> (1 na začátek, "
                    "1 v půlce, 1 na konci) — robot si průběžně opravuje polohu.</li>"
                    "<li>Nebo nahraj <b>kratší trasu</b>, aby drift nestihl "
                    "překročit mez.</li>"
                    "<li>Spusť playback <b>znovu</b> z fiducialu — s trochou "
                    "štěstí projde (drift je stochastický).</li>"
                    "</ul>"
                )
                self._tip_box.setVisible(True)
            elif impaired_reason:
                self._tip_box.setText(
                    "⚠ <b>Run byl zastaven kvůli E-STOP / robot impaired stavu.</b><br>"
                    "<br>"
                    "Motory jsou vypnuty. Před dalším pokusem:"
                    "<ul>"
                    "<li>Klikni na <b>E-STOP widget</b> (v triggered stavu) pro "
                    "uvolnění, nebo stiskni <b>F1</b>.</li>"
                    "<li>Dovez Spota (fyzicky) znovu k fiducialu.</li>"
                    "<li>Spusť playback znovu — power-on proběhne automaticky "
                    "na Fiducial kroku.</li>"
                    "</ul>"
                )
                self._tip_box.setVisible(True)
            else:
                self._tip_box.setVisible(False)

            self._table.setRowCount(len(photos))
            for row, photo in enumerate(photos):
                detections = sorted(
                    photo.detections,
                    key=lambda d: (
                        d.text_confidence is None,
                        -(d.text_confidence or 0),
                        -(d.detection_confidence or 0),
                    ),
                )
                plate_text = ", ".join(d.plate_text or "?" for d in detections) or "—"
                if detections:
                    best = detections[0]
                    conf_text = (
                        f"{(best.text_confidence or 0) * 100:.0f} % / "
                        f"{(best.detection_confidence or 0) * 100:.0f} %"
                    )
                else:
                    conf_text = "—"
                self._table.setItem(row, 0, QTableWidgetItem(photo.checkpoint_name or ""))
                self._table.setItem(row, 1, QTableWidgetItem(photo.camera_source))
                self._table.setItem(row, 2, QTableWidgetItem(photo.ocr_status.value))
                self._table.setItem(row, 3, QTableWidgetItem(plate_text))
                self._table.setItem(row, 4, QTableWidgetItem(conf_text))

    def _on_export(self) -> None:
        if self._run_id is None:
            return
        try:
            data, filename = build_run_zip(self._run_id)
        except Exception as exc:
            _log.exception("ZIP export failed: %s", exc)
            error_dialog(self, "Chyba", f"Export selhal: {exc}")
            return

        target, _ = QFileDialog.getSaveFileName(
            self,
            "Uložit ZIP",
            filename,
            "ZIP archiv (*.zip)",
        )
        if not target:
            return
        try:
            Path(target).write_bytes(data)
        except Exception as exc:
            error_dialog(self, "Chyba", f"Nelze zapsat soubor: {exc}")
            return
        info_dialog(self, "Hotovo", f"Soubor uložen: {target}")


__all__ = ["PlaybackResultPage"]
