"""DTO objekty pro OCR pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """Axis-aligned bounding box (x1,y1,x2,y2) v pixelech."""

    x1: int
    y1: int
    x2: int
    y2: int

    def to_json(self) -> dict[str, int]:
        return {"x1": self.x1, "y1": self.y1, "x2": self.x2, "y2": self.y2}


@dataclass(frozen=True, slots=True)
class Detection:
    """Jedna detekce SPZ — YOLO box + OCR text + obě confidence."""

    plate: str
    detection_confidence: float
    text_confidence: float | None
    bbox: BoundingBox
    engine_name: str
    engine_version: str = ""

    def to_db_row(self, photo_id: int) -> dict[str, Any]:
        # PR-06 FIND-118: raise pokud plate je prázdný — signalizuje bug
        # upstream. Pipeline filtruje prázdné texty před vytvořením Detection,
        # takže sem by nikdy neměl přijít empty string.
        if not self.plate:
            raise ValueError(
                "Detection.plate is empty — upstream filter nebyl aplikován."
            )
        return {
            "photo_id": photo_id,
            "plate_text": self.plate,
            "detection_confidence": self.detection_confidence,
            "text_confidence": self.text_confidence,
            "bbox": self.bbox.to_json(),
            "engine_name": self.engine_name,
            "engine_version": self.engine_version or None,
        }


__all__ = ["BoundingBox", "Detection"]
