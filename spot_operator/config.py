"""Aplikační konfigurace z .env + environment proměnných."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from spot_operator.bootstrap import ROOT
from spot_operator.constants import LOGS_DIR, TEMP_ROOT


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Immutable aplikační konfigurace.

    Načte se jednou při startu z .env + env vars. Předává se explicitně všude, kde
    se využije (žádné globální proměnné).
    """

    database_url: str
    spot_default_ip: str
    spot_timeout_seconds: float
    fiducial_distance_threshold_m: float
    ocr_yolo_model_path: Path
    ocr_text_engine: str
    ocr_detection_min_confidence: float
    keyring_service: str
    operator_label: str
    log_level: str

    # Derived paths
    logs_dir: Path
    temp_root: Path
    root_dir: Path

    @classmethod
    def load_from_env(cls, env_file: Path | None = None) -> "AppConfig":
        """Načte konfiguraci z .env (výchozí c:\\Users\\zige\\spot\\.env) + env vars."""
        if env_file is None:
            env_file = ROOT / ".env"
        if env_file.is_file():
            load_dotenv(env_file, override=False)

        database_url = _require("DATABASE_URL")
        spot_default_ip = os.environ.get("SPOT_DEFAULT_IP", "192.168.80.3")
        spot_timeout_seconds = float(os.environ.get("SPOT_TIMEOUT_SECONDS", "15"))
        fiducial_distance_threshold_m = float(
            os.environ.get("FIDUCIAL_DISTANCE_THRESHOLD_M", "2.0")
        )
        ocr_yolo_model_rel = os.environ.get(
            "OCR_YOLO_MODEL", "ocr/license-plate-finetune-v1m.pt"
        )
        ocr_yolo_model_path = ROOT / ocr_yolo_model_rel
        ocr_text_engine = os.environ.get(
            "OCR_TEXT_ENGINE", "european-plates-mobile-vit-v2"
        )
        ocr_detection_min_confidence = float(
            os.environ.get("OCR_DETECTION_MIN_CONFIDENCE", "0.5")
        )
        keyring_service = os.environ.get("KEYRING_SERVICE", "spot_operator.spot")
        operator_label = os.environ.get("OPERATOR_LABEL", "")
        log_level = os.environ.get("LOG_LEVEL", "INFO").upper()

        return cls(
            database_url=database_url,
            spot_default_ip=spot_default_ip,
            spot_timeout_seconds=spot_timeout_seconds,
            fiducial_distance_threshold_m=fiducial_distance_threshold_m,
            ocr_yolo_model_path=ocr_yolo_model_path,
            ocr_text_engine=ocr_text_engine,
            ocr_detection_min_confidence=ocr_detection_min_confidence,
            keyring_service=keyring_service,
            operator_label=operator_label,
            log_level=log_level,
            logs_dir=LOGS_DIR,
            temp_root=TEMP_ROOT,
            root_dir=ROOT,
        )

    def ensure_runtime_dirs(self) -> None:
        """Vytvoří logs/ a temp/ pokud neexistují."""
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.temp_root.mkdir(parents=True, exist_ok=True)


def _require(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(
            f"Chybí povinná proměnná prostředí '{key}'. "
            f"Zkopíruj .env.example na .env a doplň ji."
        )
    return value
