"""Text reader — `fast-plate-ocr` engine s confidence score.

Funguje na ONNX runtime, žádný protobuf konflikt s bosdyn.
"""

from __future__ import annotations

import re
import threading
from typing import Any

import numpy as np

from spot_operator.constants import PLATE_TEXT_REGEX
from spot_operator.logging_config import get_logger

_log = get_logger(__name__)

_PLATE_RE = re.compile(PLATE_TEXT_REGEX)


def _normalize_plate(text: str) -> str:
    """Uppercase + odstraní mezery, pomlčky a neznámé znaky + length check.

    PR-06 FIND-112: Pokud výsledek neprojde ``PLATE_TEXT_REGEX`` (délka 1-16,
    jen A-Z 0-9), vrátí prázdný string a loguje warning — OCR přečetlo
    nesmyslný text a neměl by se ukládat do DB.
    """
    if not text:
        return ""
    alphanumeric = "".join(ch for ch in text.upper() if ch.isalnum())
    if not alphanumeric:
        return ""
    if not _PLATE_RE.match(alphanumeric):
        _log.warning(
            "Normalized plate %r doesn't match PLATE_TEXT_REGEX; returning empty",
            alphanumeric,
        )
        return ""
    return alphanumeric


class FastPlateReader:
    """Obálka nad fast_plate_ocr.LicensePlateRecognizer.

    Vrací (plate_text, text_confidence). Pokud chybí confidence API, vrátí None.
    """

    def __init__(self, model_name: str = "european-plates-mobile-vit-v2-model"):
        self._model_name = model_name
        self._reader: Any | None = None
        self._version: str = ""
        self._load_lock = threading.Lock()

    @property
    def engine_version(self) -> str:
        return self._version

    def _ensure_loaded(self) -> Any:
        if self._reader is not None:
            return self._reader
        with self._load_lock:
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

        PR-06 FIND-110: refactor z 3-level nested try/except na 2 úrovně
        s extracted ``_try_run`` helperem.
        """
        reader = self._ensure_loaded()
        if crop_bgr.size == 0:
            _log.warning("FastPlate read: empty crop (size=0)")
            return "", None

        import cv2

        h, w = crop_bgr.shape[:2]
        _log.info("FastPlate read start: crop %dx%d", w, h)

        gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
        result: Any = None
        try:
            result = _try_run(reader, gray)
        except (KeyboardInterrupt, MemoryError, SystemExit):
            # Kritické exceptions propagujeme.
            raise
        except Exception as exc:
            _log.warning(
                "FastPlate grayscale run failed (%s); trying RGB fallback.",
                exc,
            )
            rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
            try:
                result = _try_run(reader, rgb)
            except (KeyboardInterrupt, MemoryError, SystemExit):
                raise
            except Exception:
                _log.exception("FastPlate RGB fallback also failed; returning empty")
                return "", None

        _log.debug("FastPlate raw result: %r", result)
        text, conf = _unpack_result(result)
        normalized = _normalize_plate(text)
        if normalized:
            _log.info(
                "FastPlate result: normalized='%s' conf=%s raw_text=%r",
                normalized, conf, text,
            )
        else:
            _log.warning(
                "FastPlate returned empty text (raw_result=%r, raw_text=%r)",
                result, text,
            )
        return normalized, conf


def _try_run(reader: Any, image: np.ndarray) -> Any:
    """Zavolá ``reader.run`` s ``return_confidence=True`` pokud to API
    podporuje, jinak fallback bez tohoto kwargs.

    Extrakce z ``FastPlateReader.read`` — odstraňuje nested try/except
    (PR-06 FIND-110).
    """
    try:
        return reader.run(image, return_confidence=True)
    except TypeError as exc:
        _log.warning(
            "FastPlate run TypeError (retry bez return_confidence): %s", exc
        )
        return reader.run(image)


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

    # list of (text, confidence) / PlatePrediction for batch — vezmi první
    if isinstance(result, list) and result:
        return _unpack_result(result[0])

    # samotný string
    if isinstance(result, str):
        return result, None

    # PlatePrediction (fast-plate-ocr >= ~0.3) — dataclass s .plate a .char_probs
    plate_attr = getattr(result, "plate", None)
    if plate_attr is not None:
        text_part = _stringify(plate_attr)
        conf_part: Any = getattr(result, "confidence", None)
        if conf_part is None:
            # char_probs = np.ndarray / list per-char, průměr přes znaky
            char_probs = getattr(result, "char_probs", None)
            if char_probs is not None:
                try:
                    conf_part = float(sum(char_probs) / len(char_probs))
                except Exception:
                    conf_part = None
        return text_part, _floatify(conf_part)

    # samotný .text atribut (jiné varianty API)
    text_attr = getattr(result, "text", None)
    if text_attr is not None:
        return _stringify(text_attr), _floatify(getattr(result, "confidence", None))

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
