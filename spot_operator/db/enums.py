"""DB enumy — zrcadlí PostgreSQL native ENUM typy."""

from __future__ import annotations

import enum


class PlateStatus(str, enum.Enum):
    """Status SPZ v registru."""

    active = "active"
    expired = "expired"
    banned = "banned"
    unknown = "unknown"


class RunStatus(str, enum.Enum):
    """Stav spot-runu (jednoho spuštění mapy s focením)."""

    running = "running"
    completed = "completed"
    aborted = "aborted"
    failed = "failed"
    partial = "partial"


class OcrStatus(str, enum.Enum):
    """OCR zpracování jednotlivé fotky."""

    pending = "pending"
    processing = "processing"
    done = "done"
    failed = "failed"


class FiducialSide(str, enum.Enum):
    """Strana focení SPZ při recordingu mapy."""

    left = "left"
    right = "right"
    both = "both"


__all__ = ["PlateStatus", "RunStatus", "OcrStatus", "FiducialSide"]
