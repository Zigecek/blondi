"""Konfigurace loggingu — rotující soubor + konzole + Qt log forward."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from spot_operator.config import AppConfig

_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s :: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_LOG_FILE_NAME = "spot_operator.log"
_LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_LOG_BACKUP_COUNT = 5


class _UndistortionNoiseFilter(logging.Filter):
    """Tichý filter pro opakující se WARN 'Cannot build undistortion for X: k1'
    z autonomy ImagePoller. Není to funkční vada — pipeline pokračuje bez
    undistortion — ale spamuje log při každém startu image pipeline (2× per
    wizard × 2 kamery). Zbytek warningů z ``app.robot.images`` zůstává
    viditelný (např. skutečné capture erroru)."""

    _MSG_SUBSTRING = "Cannot build undistortion"

    def filter(self, record: logging.LogRecord) -> bool:  # True = nechat
        try:
            return self._MSG_SUBSTRING not in record.getMessage()
        except Exception:
            return True


def setup(config: AppConfig) -> None:
    """Nainicializuje root logger. Volá se jednou při startu aplikace."""
    config.ensure_runtime_dirs()
    log_file = config.logs_dir / _LOG_FILE_NAME

    root = logging.getLogger()
    root.setLevel(config.log_level)

    # Odstraní případné default handlery (pokud se setup volá vícekrát).
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=_LOG_MAX_BYTES,
        backupCount=_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    # Ztiš nadměrně upovídané knihovny
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("bosdyn").setLevel(logging.INFO)
    logging.getLogger("ultralytics").setLevel(logging.WARNING)

    # Potlač opakovaný WARN z autonomy ImagePoller undistortion setupu —
    # autonomy nemůžeme editovat (rule additive), takže aplikujeme filter.
    logging.getLogger("app.robot.images").addFilter(_UndistortionNoiseFilter())

    _install_qt_handler()

    logging.getLogger(__name__).info(
        "Logging initialized (level=%s, file=%s)", config.log_level, log_file
    )


def _install_qt_handler() -> None:
    """Přesměruje Qt warnings/errors do Python loggingu."""
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    except Exception:  # pragma: no cover - Qt není v testech povinný
        return

    qt_logger = logging.getLogger("qt")

    def handler(msg_type: QtMsgType, _context, message: str) -> None:  # type: ignore[no-untyped-def]
        if msg_type == QtMsgType.QtDebugMsg:
            qt_logger.debug(message)
        elif msg_type == QtMsgType.QtInfoMsg:
            qt_logger.info(message)
        elif msg_type == QtMsgType.QtWarningMsg:
            qt_logger.warning(message)
        elif msg_type == QtMsgType.QtCriticalMsg:
            qt_logger.error(message)
        elif msg_type == QtMsgType.QtFatalMsg:
            qt_logger.critical(message)
        else:
            qt_logger.info(message)

    qInstallMessageHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Zkratka pro logging.getLogger, konzistentní napříč projektem."""
    return logging.getLogger(name)


# Pomocné: vypsat summary instalovaných balíčků při --diag
def dump_environment_diagnostics(log: logging.Logger | None = None) -> None:
    """Vypíše do logu verze klíčových balíčků a cest. Používá --diag CLI."""
    import platform
    import sys

    log = log or logging.getLogger(__name__)
    log.info("Python: %s", sys.version.replace("\n", " "))
    log.info("Platform: %s", platform.platform())
    _safe_version(log, "PySide6", "PySide6")
    _safe_version(log, "SQLAlchemy", "sqlalchemy")
    _safe_version(log, "alembic", "alembic")
    _safe_version(log, "psycopg", "psycopg")
    _safe_version(log, "bosdyn-client", "bosdyn.client")
    _safe_version(log, "ultralytics", "ultralytics")
    _safe_version(log, "onnxruntime", "onnxruntime")
    _safe_version(log, "fast-plate-ocr", "fast_plate_ocr")
    _safe_version(log, "opencv-python", "cv2")
    _safe_version(log, "numpy", "numpy")
    _safe_version(log, "keyring", "keyring")


def _safe_version(log: logging.Logger, label: str, module_name: str) -> None:
    try:
        module = __import__(module_name)
        version = getattr(module, "__version__", "?")
        log.info("  %-14s %s", label, version)
    except Exception as exc:
        log.warning("  %-14s NENAČTENO (%s)", label, exc)


__all__ = ["setup", "get_logger", "dump_environment_diagnostics"]
