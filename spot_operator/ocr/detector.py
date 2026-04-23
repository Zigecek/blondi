"""YOLO detektor SPZ — načítá model `ocr/license-plate-finetune-v1m.pt`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from spot_operator.logging_config import get_logger
from spot_operator.ocr.dtos import BoundingBox

_log = get_logger(__name__)


class YoloDetector:
    """Tenká obálka nad ultralytics YOLO.

    Ultralytics model je thread-safe pro inference, ale inicializace je pomalá (~2s).
    Jednu instanci sdílíme napříč OCR workerem + CRUD re-runem.
    """

    def __init__(self, model_path: Path, *, min_confidence: float = 0.5):
        self._model_path = model_path
        self._min_confidence = min_confidence
        self._model: Any | None = None

    def _ensure_loaded(self) -> Any:
        if self._model is not None:
            return self._model
        if not self._model_path.is_file():
            raise FileNotFoundError(
                f"YOLO model not found: {self._model_path}. "
                "Zkontroluj OCR_YOLO_MODEL v .env."
            )
        from ultralytics import YOLO  # lazy import — těžká knihovna

        _log.info("Loading YOLO model from %s", self._model_path)
        self._model = YOLO(str(self._model_path))
        return self._model

    def detect(self, image_bgr: np.ndarray) -> list[tuple[BoundingBox, float]]:
        """Detekuje SPZ. Vrátí list (bbox, detection_confidence)."""
        model = self._ensure_loaded()
        h, w = image_bgr.shape[:2]
        _log.info(
            "YOLO detect start: image %dx%d, conf_threshold=%.2f",
            w, h, self._min_confidence,
        )
        results = model(image_bgr, conf=self._min_confidence, verbose=False)
        if not results:
            _log.warning("YOLO detect: model returned empty results object")
            return []

        first = results[0]
        boxes = getattr(first, "boxes", None)
        if boxes is None:
            _log.warning("YOLO detect: results[0].boxes is None")
            return []

        raw_confs: list[float] = []
        out: list[tuple[BoundingBox, float]] = []
        for box in boxes:
            conf = float(box.conf[0])
            raw_confs.append(conf)
            if conf < self._min_confidence:
                continue
            xyxy = box.xyxy[0].tolist()
            x1, y1, x2, y2 = (int(v) for v in xyxy)
            out.append((BoundingBox(x1, y1, x2, y2), conf))

        _log.info(
            "YOLO raw: %d box(es), confidences=%s",
            len(raw_confs),
            ["%.2f" % c for c in raw_confs],
        )
        if out:
            _log.info("YOLO kept %d box(es) above threshold", len(out))
        else:
            _log.warning(
                "YOLO kept 0 box(es) (raw=%d, threshold=%.2f)",
                len(raw_confs), self._min_confidence,
            )
        return out


__all__ = ["YoloDetector"]
