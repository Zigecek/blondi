"""OCR pipeline — YOLO detector + text reader v jednom API.

Vstup: `image_bytes` (JPEG/PNG). Výstup: list[Detection].
Jedna instance pro celý proces (inicializace modelů je drahá).
"""

from __future__ import annotations

import threading
from pathlib import Path

import cv2
import numpy as np

from spot_operator.constants import OCR_ENGINE_FAST_PLATE
from spot_operator.logging_config import get_logger
from spot_operator.ocr.detector import YoloDetector
from spot_operator.ocr.dtos import BoundingBox, Detection
from spot_operator.ocr.reader import FastPlateReader

_log = get_logger(__name__)


class OcrPipeline:
    """Kombinace YOLO detektoru a fast-plate-ocr reader.

    Thread-safe pro čtení (jeden interní lock okolo inference, YOLO ultralytics
    je sám thread-safe, ale radši to svážeme).
    """

    def __init__(
        self,
        *,
        yolo_model_path: Path,
        text_engine: str = "european-plates-mobile-vit-v2-model",
        min_detection_confidence: float = 0.5,
    ):
        self._detector = YoloDetector(
            yolo_model_path, min_confidence=min_detection_confidence
        )
        self._reader = FastPlateReader(text_engine)
        self._lock = threading.Lock()

    def warmup(self) -> None:
        """Explicitně načte modely (běhá pomalu na začátku — lepší hned)."""
        with self._lock:
            self._detector._ensure_loaded()  # type: ignore[attr-defined]
            self._reader._ensure_loaded()  # type: ignore[attr-defined]

    def process(self, image_bytes: bytes) -> list[Detection]:
        """Zpracuje jednu fotku → list detekcí SPZ.

        PR-06 FIND-108: corrupted JPEG (cv2.imdecode vrátí None) teď
        raise RuntimeError místo silent []. OCR worker to chytne a
        mark_failed — uživatel vidí status, ne "done s 0 detekcí".
        """
        if not image_bytes:
            _log.warning("OCR pipeline: called with empty image_bytes")
            return []

        _log.info("OCR pipeline start: %d bytes", len(image_bytes))
        arr = np.frombuffer(image_bytes, np.uint8)
        image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError(
                f"OCR pipeline: failed to decode image "
                f"(size={len(image_bytes)} bytes — corrupted JPEG)."
            )

        h, w = image.shape[:2]
        channels = image.shape[2] if image.ndim == 3 else 1
        _log.info(
            "OCR pipeline: image decoded %dx%d, %d channels", w, h, channels
        )

        with self._lock:
            boxes = self._detector.detect(image)
            if boxes:
                _log.info("OCR pipeline: %d candidate box(es) from YOLO", len(boxes))
            else:
                _log.warning("OCR pipeline: YOLO returned NO candidates")

            detections: list[Detection] = []
            total = len(boxes)
            for idx, (bbox, det_conf) in enumerate(boxes, start=1):
                _log.info(
                    "OCR pipeline: box %d/%d bbox=(%d,%d,%d,%d) det_conf=%.2f",
                    idx, total, bbox.x1, bbox.y1, bbox.x2, bbox.y2, det_conf,
                )
                crop = image[bbox.y1 : bbox.y2, bbox.x1 : bbox.x2]
                if crop.size == 0:
                    _log.warning(
                        "OCR pipeline: box %d/%d has empty crop, skipping", idx, total
                    )
                    continue
                text, text_conf = self._reader.read(crop)
                if not text:
                    _log.warning(
                        "OCR pipeline: box %d/%d reader returned empty text, skipping",
                        idx, total,
                    )
                    continue
                detections.append(
                    Detection(
                        plate=text,
                        detection_confidence=det_conf,
                        text_confidence=text_conf,
                        bbox=bbox,
                        engine_name=OCR_ENGINE_FAST_PLATE,
                        engine_version=self._reader.engine_version,
                    )
                )

            if detections:
                _log.info(
                    "OCR pipeline done: %d valid detection(s) [%s]",
                    len(detections),
                    ", ".join(d.plate for d in detections),
                )
            else:
                _log.warning(
                    "OCR pipeline done: 0 detections (boxes=%d)", total
                )
            return detections


def create_default_pipeline(config) -> OcrPipeline:  # type: ignore[no-untyped-def]
    """Vytvoří pipeline z AppConfig. Volitelně warmup se spustí v OCR workeru."""
    return OcrPipeline(
        yolo_model_path=config.ocr_yolo_model_path,
        text_engine=config.ocr_text_engine,
        min_detection_confidence=config.ocr_detection_min_confidence,
    )


__all__ = ["OcrPipeline", "create_default_pipeline", "BoundingBox", "Detection"]
