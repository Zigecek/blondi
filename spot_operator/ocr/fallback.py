"""Fallback OCR engine — Nomeroff Net z ocr/ocrtest.py, spouštěný jako subprocess.

Důvod subprocessu: `nomeroff_net` táhne torch a protobuf, který by mohl v běžícím procesu
kolidovat s bosdyn.api. Subprocess to izoluje — každé volání má samostatný Python proces.

Použití: pouze když uživatel v CRUD klikne "Re-OCR lepším enginem".
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from spot_operator.bootstrap import OCR_DIR, ROOT
from spot_operator.constants import OCR_ENGINE_NOMEROFF
from spot_operator.logging_config import get_logger
from spot_operator.ocr.dtos import BoundingBox, Detection

_log = get_logger(__name__)

_SUBPROCESS_TIMEOUT_SEC = 30


_WRAPPER_CODE = """
import json
import sys
import warnings

warnings.filterwarnings("ignore")

sys.path.insert(0, r"{ocr_dir}")

try:
    from ocrtest import CzechPlateRecognizer
except Exception as exc:
    print(json.dumps({{"error": "import_failed", "detail": str(exc)}}))
    sys.exit(2)

image_path = sys.argv[1]
model_path = sys.argv[2]

try:
    recognizer = CzechPlateRecognizer(model_path)
    result = recognizer.process_image(image_path)
    print(json.dumps({{"ok": True, "detections": result}}))
except Exception as exc:
    print(json.dumps({{"error": "process_failed", "detail": str(exc)}}))
    sys.exit(3)
"""


def reprocess_bytes(
    image_bytes: bytes,
    *,
    yolo_model_path: Path,
) -> list[Detection]:
    """Spustí nomeroff v subprocessu nad zadanými bytes. Vrátí list detekcí.

    Nomeroff nevrací text_confidence, ale detection_confidence v pipeline neposkytuje
    — vrátíme None pro obě a zapíšeme do DB s engine_name = 'yolo_v1m+nomeroff'.
    """
    if not image_bytes:
        return []

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(image_bytes)
        temp_path = Path(f.name)

    try:
        wrapper_code = _WRAPPER_CODE.format(ocr_dir=str(OCR_DIR))
        cmd = [
            sys.executable,
            "-c",
            wrapper_code,
            str(temp_path),
            str(yolo_model_path),
        ]
        _log.info("Running nomeroff subprocess for temp %s", temp_path.name)
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_SEC,
            cwd=str(ROOT),
        )
        stdout = proc.stdout.strip().splitlines()
        if not stdout:
            _log.warning("Nomeroff subprocess returned no output. stderr=%s", proc.stderr)
            return []
        payload = _find_json_line(stdout)
        if payload is None or "error" in payload:
            _log.warning("Nomeroff subprocess error: %s", payload)
            return []
        detections = _parse_nomeroff_output(payload.get("detections", []))
        return detections
    except subprocess.TimeoutExpired:
        _log.warning("Nomeroff subprocess timed out after %ds", _SUBPROCESS_TIMEOUT_SEC)
        return []
    except Exception as exc:
        _log.exception("Nomeroff subprocess failed: %s", exc)
        return []
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass


def _find_json_line(lines: list[str]) -> dict | None:
    """Najde poslední řádek, který je validní JSON."""
    for line in reversed(lines):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def _parse_nomeroff_output(raw: list[dict]) -> list[Detection]:
    detections: list[Detection] = []
    for item in raw:
        plate = str(item.get("plate", "")).upper().strip()
        if not plate:
            continue
        bbox_raw = item.get("bbox") or [0, 0, 0, 0]
        try:
            x1, y1, x2, y2 = (int(v) for v in bbox_raw[:4])
        except Exception:
            x1 = y1 = x2 = y2 = 0
        detections.append(
            Detection(
                plate=plate,
                detection_confidence=float(item.get("detection_confidence", 0.0) or 0.0),
                text_confidence=None,
                bbox=BoundingBox(x1, y1, x2, y2),
                engine_name=OCR_ENGINE_NOMEROFF,
                engine_version="ocrtest",
            )
        )
    return detections


__all__ = ["reprocess_bytes"]
