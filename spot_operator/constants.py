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

# Kandidáti pro "levá strana Spota" od nejpreferovanějšího. Spot SDK / firmware
# mezi verzemi mění jména (některé advertise `left_fisheye_image`, jiné
# `frontleft_fisheye_image`). `pick_side_source` zkusí kandidáty po pořadí a
# vrátí první, který je dostupný v `available`.
PREFERRED_LEFT_CANDIDATES: tuple[str, ...] = (CAMERA_LEFT, CAMERA_FRONT_LEFT)
PREFERRED_RIGHT_CANDIDATES: tuple[str, ...] = (CAMERA_RIGHT, CAMERA_FRONT_RIGHT)


def pick_side_source(
    available: list[str] | tuple[str, ...],
    candidates: tuple[str, ...],
) -> str | None:
    """Vrátí první kandidát z `candidates`, který je v `available`.

    Používané v `TeleopRecordPage.initializePage` a `LoginPage._on_connect_ok`
    pro adaptaci na konkrétní Spot robot.
    """
    for cand in candidates:
        if cand in available:
            return cand
    return None

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
# Navigation timeout per checkpoint — zvýšeno 30→60 s pro dlouhé trasy.
# Při 0.5 m/s walk speed pokryje ~30 m segment. Kratší nechávalo robot timeout
# u vzdálených checkpointů uprostřed cesty (ended with timeout on the robot).
PLAYBACK_NAV_TIMEOUT_SEC: float = 60.0
PLAYBACK_RETURN_HOME_TIMEOUT_SEC: float = 180.0
PLAYBACK_LOW_BATTERY_PERCENT: int = 15

# Substring markery pro RobotLostError detekci v NavigationResult.message.
# Bosdyn mění přesný formát exception message mezi verzemi — proto multi-substring
# match. Case-insensitive (porovnáváme proti .lower()).
# RobotLostError je TERMINÁLNÍ (bosdyn odmítá všechny navigate_to) na rozdíl od
# TIMEOUT/STUCK/NO_ROUTE které jsou recoverable (GraphNav se občas probere).
ROBOT_LOST_ERROR_MARKERS: tuple[str, ...] = (
    "robotlosterror",
    "already lost",
    "robot is already lost",
)

# --- Obstacle avoidance strength (0–100, mapuje se na padding 0–0.35 m) ---
# WASD manuální teleop: agresivnější (menší padding ~0.035 m).
# Autonomní playback: konzervativnější (padding ~0.070 m).
WASD_AVOIDANCE_STRENGTH: int = 10
PLAYBACK_AVOIDANCE_STRENGTH: int = 20

# --- Teleop speed profily pro WASD (linear m/s, angular rad/s) ---
# Sladěno s autonomy/app/constants.py TELEOP_SPEED_PROFILES.
TELEOP_SPEED_PROFILES: dict[str, tuple[float, float]] = {
    "slow": (0.25, 0.25),
    "normal": (0.5, 0.5),
    "fast": (1.0, 1.0),
}
TELEOP_SPEED_LABELS: dict[str, str] = {
    "slow": "Pomalu",
    "normal": "Normálně",
    "fast": "Rychle",
}
TELEOP_DEFAULT_SPEED_PROFILE: str = "normal"

# --- UI konstanty (wizardy, side panely, minima okna) ---
UI_SIDE_PANEL_WIDTH: int = 320
UI_WIZARD_MIN_WIDTH: int = 1000
UI_WIZARD_MIN_HEIGHT: int = 700
UI_ESTOP_BOTTOM_MARGIN: int = 96
UI_PHOTO_OVERLAY_MIN_WIDTH: int = 600

# --- CRUD tabulky (pagination) ---
CRUD_PAGE_SIZE: int = 100
CRUD_SEARCH_DEBOUNCE_MS: int = 200
CRUD_WORKER_STOP_TIMEOUT_MS: int = 3000
