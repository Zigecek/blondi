"""Text reader — `fast-plate-ocr` engine s confidence score.

Funguje na ONNX runtime, žádný protobuf konflikt s bosdyn.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from spot_operator.logging_config import get_logger

_log = get_logger(__name__)


def _normalize_plate(text: str) -> str:
    """Uppercase + odstraní mezery, pomlčky a neznámé znaky."""
    if not text:
        return ""
    return "".join(ch for ch in text.upper() if ch.isalnum())


class FastPlateReader:
    """Obálka nad fast_plate_ocr.LicensePlateRecognizer.

    Vrací (plate_text, text_confidence). Pokud chybí confidence API, vrátí None.
    """

    def __init__(self, model_name: str = "european-plates-mobile-vit-v2-model"):
        self._model_name = model_name
        self._reader: Any | None = None
        self._version: str = ""

    @property
    def engine_version(self) -> str:
        return self._version

    def _ensure_loaded(self) -> Any:
        if self._reader is not None:
            return self._reader
        from fast_plate_ocr import LicensePlateRecognizer  # lazy import

        _log.info("Loading fast-plate-ocr model: %s", self._model_name)
        self._reader = LicensePlateRecognizer(self._model_name)
        try:
            import fast_plate_ocr  # type: ignore

            self._version = getattr(fast_plate_ocr, "__version__", "")
        except Exception:
            self._version = ""
        return self._reader

    def read(self, crop_bgr: np.ndarray) -> tuple[str, float | None]:
        """Přečte text z crop obrázku. Vrací (text, confidence_avg) nebo ("", None).

        fast-plate-ocr modely (`european-plates-mobile-vit-v2-model` atd.)
        očekávají 1-channel grayscale vstup. Pokud bychom poslali 3-channel
        RGB/BGR, ONNX selže s:
          ``InvalidArgument: Got invalid dimensions for input: index 3
             Got: 3 Expected: 1``
        Proto konvertujeme vždy na grayscale. RGB fallback je pro případ,
        že budoucí model bude RGB variant.
        """
        reader = self._ensure_loaded()
        if crop_bgr.size == 0:
            return "", None

        import cv2

        gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)

        try:
            try:
                result = reader.run(gray, return_confidence=True)
            except TypeError:
                result = reader.run(gray)
        except Exception as exc:
            # Model nepřijal grayscale — zkus RGB variant modelů.
            _log.warning(
                "fast_plate_ocr grayscale run failed (%s); trying RGB fallback.",
                exc,
            )
            rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
            try:
                result = reader.run(rgb, return_confidence=True)
            except TypeError:
                result = reader.run(rgb)

        text, conf = _unpack_result(result)
        return _normalize_plate(text), conf


def _unpack_result(result: Any) -> tuple[str, float | None]:
    """Různé verze fast-plate-ocr vrací různé tvary. Pokryj je."""
    if result is None:
        return "", None

    # (text, confidence) tuple
    if isinstance(result, tuple) and len(result) == 2:
        text_part, conf_part = result
        return _stringify(text_part), _floatify(conf_part)

    # dict {"plate": ..., "confidence": ...}
    if isinstance(result, dict):
        text_part = result.get("plate") or result.get("text") or ""
        conf_part = result.get("confidence")
        return _stringify(text_part), _floatify(conf_part)

    # list of (text, confidence) for batch — vezmi první
    if isinstance(result, list) and result:
        return _unpack_result(result[0])

    # samotný string
    if isinstance(result, str):
        return result, None

    return "", None


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list) and value:
        value = value[0]
    return str(value)


def _floatify(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, list) and value:
        # Per-char confidence — vrátíme průměr
        try:
            return float(sum(value) / len(value))
        except Exception:
            return None
    try:
        return float(value)
    except Exception:
        return None


__all__ = ["FastPlateReader", "_normalize_plate"]
