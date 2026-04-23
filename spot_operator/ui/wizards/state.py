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
    selected_fiducial_id: int | None = None
    selected_start_waypoint_id: str | None = None
    selected_capture_sources: list[str] = field(default_factory=list)
    fiducial_id: int | None = None
    run_id: int | None = None
    completed_run_id: int | None = None
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
]
