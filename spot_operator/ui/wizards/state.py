"""Typed wizard state shared across pages."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from spot_operator.services.recording_service import RecordingService


WIZARD_LIFECYCLE_PREPARING = "preparing"
WIZARD_LIFECYCLE_READY = "ready"
WIZARD_LIFECYCLE_RUNNING = "running"
WIZARD_LIFECYCLE_ABORTING = "aborting"
WIZARD_LIFECYCLE_RETURNING = "returning"
WIZARD_LIFECYCLE_COMPLETED = "completed"
WIZARD_LIFECYCLE_PARTIAL = "partial"
WIZARD_LIFECYCLE_FAILED = "failed"


@dataclass(slots=True)
class RecordingWizardState:
    spot_ip: str | None = None
    available_sources: list[str] = field(default_factory=list)
    fiducial_id: int | None = None
    recording_service: "RecordingService | None" = None
    saved_map_id: int | None = None
    lifecycle: str = WIZARD_LIFECYCLE_PREPARING


@dataclass(slots=True)
class PlaybackWizardState:
    spot_ip: str | None = None
    available_sources: list[str] = field(default_factory=list)
    selected_map_id: int | None = None
    selected_fiducial_id: int | None = None  # required ID z vybrané mapy
    selected_start_waypoint_id: str | None = None
    selected_capture_sources: list[str] = field(default_factory=list)
    # PR-09 FIND-132: renamed z `fiducial_id` — nyní jednoznačně
    # "aktuálně detekovaný fiducial z FiducialPage" (na rozdíl od
    # `selected_fiducial_id` = required ID z mapy).
    detected_fiducial_id: int | None = None
    run_id: int | None = None
    completed_run_id: int | None = None
    lifecycle: str = WIZARD_LIFECYCLE_PREPARING

    # Zpětná kompatibilita pro kód, který ještě čte `fiducial_id`.
    # Odstranit v PR-15 po kompletním přejmenování callsites.
    @property
    def fiducial_id(self) -> int | None:
        return self.detected_fiducial_id

    @fiducial_id.setter
    def fiducial_id(self, value: int | None) -> None:
        self.detected_fiducial_id = value


@dataclass(slots=True)
class WalkWizardState:
    """Stav pro Walk wizard — samostatný typ aby fiducial_page.py měla
    konzistentní API ``wizard.flow_state()`` jako recording/playback
    (PR-09 FIND-130).
    """

    spot_ip: str | None = None
    available_sources: list[str] = field(default_factory=list)
    fiducial_id: int | None = None
    lifecycle: str = WIZARD_LIFECYCLE_PREPARING


__all__ = [
    "WIZARD_LIFECYCLE_PREPARING",
    "WIZARD_LIFECYCLE_READY",
    "WIZARD_LIFECYCLE_RUNNING",
    "WIZARD_LIFECYCLE_ABORTING",
    "WIZARD_LIFECYCLE_RETURNING",
    "WIZARD_LIFECYCLE_COMPLETED",
    "WIZARD_LIFECYCLE_PARTIAL",
    "WIZARD_LIFECYCLE_FAILED",
    "RecordingWizardState",
    "PlaybackWizardState",
    "WalkWizardState",
]
