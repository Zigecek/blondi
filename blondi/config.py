"""Aplikační konfigurace z .env + environment proměnných.

Poznámka k ``load_dotenv`` prioritě: ``override=False`` (default) znamená,
že env var v shellu má přednost před hodnotou v ``.env``. Při debugování
"proč se mi .env nenačte" ověř: ``echo $FOO`` — pokud vrací něco,
.env je ignorován pro tuto proměnnou.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from blondi.bootstrap import ROOT
from blondi.constants import LOGS_DIR, TEMP_ROOT

_VALID_LOG_LEVELS: frozenset[str] = frozenset(
    {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
)


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
    demo_mode: bool

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

        demo_mode = os.environ.get("BLONDI_DEMO", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        database_url = _resolve_database_url(demo_mode=demo_mode)
        spot_default_ip = os.environ.get("SPOT_DEFAULT_IP", "192.168.80.3")
        spot_timeout_seconds = _require_float(
            "SPOT_TIMEOUT_SECONDS", "15", min_val=1.0, max_val=300.0
        )
        fiducial_distance_threshold_m = _require_float(
            "FIDUCIAL_DISTANCE_THRESHOLD_M", "2.0", min_val=0.1, max_val=20.0
        )
        ocr_yolo_model_rel = os.environ.get(
            "OCR_YOLO_MODEL", "ocr/license-plate-finetune-v1m.pt"
        )
        # PR-10 FIND-193: absolutní cesta se nechá as-is, relativní vs ROOT.
        p = Path(ocr_yolo_model_rel)
        ocr_yolo_model_path = p if p.is_absolute() else ROOT / p
        ocr_text_engine = os.environ.get(
            "OCR_TEXT_ENGINE", "european-plates-mobile-vit-v2-model"
        )
        ocr_detection_min_confidence = _require_float(
            "OCR_DETECTION_MIN_CONFIDENCE", "0.5", min_val=0.0, max_val=1.0
        )
        keyring_service = os.environ.get("KEYRING_SERVICE", "blondi.spot")
        operator_label = os.environ.get("OPERATOR_LABEL", "")
        log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
        if log_level not in _VALID_LOG_LEVELS:
            raise RuntimeError(
                f"LOG_LEVEL={log_level!r} není platná úroveň. "
                f"Povolené hodnoty: {', '.join(sorted(_VALID_LOG_LEVELS))}."
            )

        config = cls(
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
            demo_mode=demo_mode,
            logs_dir=LOGS_DIR,
            temp_root=TEMP_ROOT,
            root_dir=ROOT,
        )
        _set_active_config(config)
        return config

    def ensure_runtime_dirs(self) -> None:
        """Vytvoří logs/ a temp/ pokud neexistují."""
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.temp_root.mkdir(parents=True, exist_ok=True)


_CACHED_CONFIG: AppConfig | None = None


def _set_active_config(config: AppConfig) -> None:
    """Uloží aktivní AppConfig pro pozdější dotazy přes get_active_config().

    Cache umožňuje dispatch helperům (session_factory, spot_wifi, mock services)
    číst demo_mode flag bez nutnosti propagovat AppConfig skrz všechna API.
    """
    global _CACHED_CONFIG
    _CACHED_CONFIG = config


def get_active_config() -> AppConfig:
    """Vrátí aktuálně načtený AppConfig. Raise pokud ještě nebyl inicializován.

    Volat z dispatch helperů které potřebují vědět o demo_mode (např.
    session_factory.connect, spot_wifi.check_connection). Hlavní entry pointy
    (main.py) předávají config explicitně — tahle funkce je fallback pro
    transitivně volaný kód.
    """
    if _CACHED_CONFIG is None:
        raise RuntimeError(
            "AppConfig nebyl ještě načten — zavolej AppConfig.load_from_env() "
            "při startu aplikace."
        )
    return _CACHED_CONFIG


def _require(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        example_path = ROOT / ".env.example"
        if example_path.is_file():
            hint = "Zkopíruj .env.example na .env a doplň ji."
        else:
            hint = f"Nastav env proměnnou {key!r} nebo vytvoř soubor .env."
        raise RuntimeError(
            f"Chybí povinná proměnná prostředí {key!r}. {hint}"
        )
    return value


def _require_float(
    key: str,
    default: str,
    *,
    min_val: float | None = None,
    max_val: float | None = None,
) -> float:
    """Načte env var jako float s range kontrolou a CZ chybami (PR-10 FIND-003)."""
    raw = os.environ.get(key, default)
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(
            f"Proměnná prostředí {key}={raw!r} není platné číslo: {exc}"
        ) from exc
    if min_val is not None and value < min_val:
        raise RuntimeError(
            f"Proměnná prostředí {key}={value} je menší než povolené minimum {min_val}."
        )
    if max_val is not None and value > max_val:
        raise RuntimeError(
            f"Proměnná prostředí {key}={value} je větší než povolené maximum {max_val}."
        )
    return value


def _resolve_database_url(*, demo_mode: bool = False) -> str:
    """Vyřeší DATABASE_URL. PR-10 FIND-001: podpora opt-in keyring pro heslo
    (DATABASE_URL_TEMPLATE s ``{password}`` placeholder + keyring key).

    Kompatibilita: ``DATABASE_URL`` stále funguje jako primární cesta
    (plaintext password). Migrace: ``.env`` s template → heslo v keyringu.

    Demo režim: pokud ``demo_mode=True``, **vyžaduje** explicitní
    ``BLONDI_DEMO_DATABASE_URL`` v env. Bez něj raise s CZ chybou — chrání
    proti omylu, kdy by demo seed přepsal produkční data.
    """
    if demo_mode:
        demo_url = os.environ.get("BLONDI_DEMO_DATABASE_URL", "").strip()
        if not demo_url:
            raise RuntimeError(
                "Demo režim (BLONDI_DEMO=1) vyžaduje samostatnou databázi "
                "kvůli ochraně produkčních dat. Nastav BLONDI_DEMO_DATABASE_URL "
                "(např. v launch_demo.bat) na prázdnou demo DB. Příklad: "
                "postgresql://blondi:heslo@localhost:5432/blondi_demo"
            )
        return demo_url
    template = os.environ.get("DATABASE_URL_TEMPLATE")
    keyring_key = os.environ.get("DATABASE_PASSWORD_KEYRING_KEY")
    if template and keyring_key:
        try:
            import keyring  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "DATABASE_URL_TEMPLATE set, ale keyring package není nainstalován."
            ) from exc
        password = keyring.get_password("blondi.db", keyring_key)
        if not password:
            raise RuntimeError(
                f"Heslo k DB pod klíčem {keyring_key!r} není ve Windows Credential "
                "Locker. Nastav ho přes keyring CLI nebo credentials utility."
            )
        return template.format(password=password)
    return _require("DATABASE_URL")
