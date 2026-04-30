"""OCR vrstva — YOLO detekce SPZ + fast-plate-ocr text reader.

Primární engine: YOLO (ocr/license-plate-finetune-v1m.pt) + fast-plate-ocr.
Fallback engine: nomeroff_net v subprocess (kvůli izolaci torch/protobuf).
"""

from blondi.ocr.dtos import BoundingBox, Detection

__all__ = ["BoundingBox", "Detection"]
