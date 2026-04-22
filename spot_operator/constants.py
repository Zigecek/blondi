"""Aplikační konstanty — jména image sources, výchozí hodnoty, konfigurační klíče."""

from __future__ import annotations

from pathlib import Path

from spot_operator.bootstrap import ROOT

# --- Spot image sources ---
CAMERA_LEFT: str = "left_fisheye_image"
CAMERA_RIGHT: str = "right_fisheye_image"
CAMERA_FRONT_LEFT: str = "frontleft_fisheye_image"
CAMERA_FRONT_RIGHT: str = "frontright_fisheye_image"
CAMERA_FRONT_COMPOSITE: str = "front_composite"
CAMERA_BACK: str = "back_fisheye_image"

VALID_CAPTURE_SOURCES: tuple[str, ...] = (
    CAMERA_LEFT,
    CAMERA_RIGHT,
    CAMERA_FRONT_LEFT,
    CAMERA_FRONT_RIGHT,
    CAMERA_BACK,
)

# --- Aplikační adresáře (relativní k ROOT) ---
LOGS_DIR: Path = ROOT / "logs"
TEMP_ROOT: Path = ROOT / "temp"

# --- OCR engine identifikátory (zapisují se do plate_detections.engine_name) ---
OCR_ENGINE_FAST_PLATE: str = "yolo_v1m+fastplate"
OCR_ENGINE_NOMEROFF: str = "yolo_v1m+nomeroff"

# --- Regex / validace ---
MAP_NAME_REGEX: str = r"^[A-Za-z0-9_-]{3,40}$"
PLATE_TEXT_REGEX: str = r"^[A-Z0-9]{1,16}$"

# --- Single instance lock ---
LOCK_FILE_NAME: str = "spot_operator.lock"

# --- OCR worker ---
OCR_WORKER_ID_PREFIX: str = "ocr-worker"
OCR_POLL_INTERVAL_SEC: float = 1.0
OCR_ZOMBIE_TIMEOUT_MIN: int = 5

# --- Wi-Fi check ---
WIFI_PING_COUNT: int = 3
WIFI_PING_TIMEOUT_SEC: float = 3.0
WIFI_TCP_PORT: int = 443

# --- Playback ---
PLAYBACK_NAV_TIMEOUT_SEC: float = 30.0
PLAYBACK_RETURN_HOME_TIMEOUT_SEC: float = 180.0
PLAYBACK_LOW_BATTERY_PERCENT: int = 15
