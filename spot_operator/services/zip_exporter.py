"""Export run z DB do ZIP bytes.

Struktura ZIP:
  run.json             — metadata runu + seznam checkpointů + detekce
  photos/<cp>_<src>_<id>.jpg   — JPEG fotky
  photos/<cp>_<src>_<id>.json  — detekce dané fotky (jeden záznam na engine)
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from spot_operator.db.engine import Session
from spot_operator.db.repositories import detections_repo, photos_repo, runs_repo
from spot_operator.logging_config import get_logger

_log = get_logger(__name__)


def build_run_zip(run_id: int) -> tuple[bytes, str]:
    """Sestaví ZIP z DB pro daný run. Vrací (bytes, suggested_filename)."""
    with Session() as s:
        run = runs_repo.get(s, run_id)
        if run is None:
            raise KeyError(f"Run {run_id} not found in DB.")

        photos = photos_repo.list_for_run(s, run_id)

        run_meta = {
            "run_id": run.id,
            "run_code": run.run_code,
            "map_name": run.map_name_snapshot,
            "map_id": run.map_id,
            "start_time": _iso(run.start_time),
            "end_time": _iso(run.end_time),
            "status": run.status.value,
            "checkpoints_reached": run.checkpoints_reached,
            "checkpoints_total": run.checkpoints_total,
            "operator_label": run.operator_label,
            "start_waypoint_id": run.start_waypoint_id,
            "abort_reason": run.abort_reason,
            "notes": run.notes,
            "checkpoint_results": list(getattr(run, "checkpoint_results_json", []) or []),
            "return_home": {
                "status": getattr(run, "return_home_status", "not_requested"),
                "reason": getattr(run, "return_home_reason", None),
            },
            "photos": [],
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for photo in photos:
                safe_cp = _safe_name(photo.checkpoint_name or "none")
                safe_src = _safe_name(photo.camera_source)
                base = f"{safe_cp}__{safe_src}__{photo.id}"
                jpg_name = f"photos/{base}.jpg"
                zf.writestr(jpg_name, photo.image_bytes)

                detections = detections_repo.list_for_photo(s, photo.id)
                det_payload = [_detection_to_dict(d) for d in detections]
                if det_payload:
                    zf.writestr(
                        f"photos/{base}.json",
                        json.dumps(det_payload, indent=2, ensure_ascii=False),
                    )

                run_meta["photos"].append(
                    {
                        "photo_id": photo.id,
                        "checkpoint_name": photo.checkpoint_name,
                        "camera_source": photo.camera_source,
                        "captured_at": _iso(photo.captured_at),
                        "ocr_status": photo.ocr_status.value,
                        "file": jpg_name,
                        "width": photo.width,
                        "height": photo.height,
                        "detections": det_payload,
                    }
                )

            zf.writestr(
                "run.json",
                json.dumps(run_meta, indent=2, ensure_ascii=False, default=str),
            )

        filename = f"{run.run_code or f'run_{run.id}'}.zip"
        return buf.getvalue(), filename


def _detection_to_dict(d: Any) -> dict:
    return {
        "plate_text": d.plate_text,
        "detection_confidence": d.detection_confidence,
        "text_confidence": d.text_confidence,
        "bbox": d.bbox,
        "engine_name": d.engine_name,
        "engine_version": d.engine_version,
        "created_at": _iso(d.created_at),
    }


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_name(name: str) -> str:
    return _SAFE_RE.sub("_", name)[:60]


__all__ = ["build_run_zip"]
