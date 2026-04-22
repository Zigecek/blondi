"""Entry point — Spot Operator desktop aplikace.

Pořadí startu je fixní (viz plán):
  1) bootstrap.inject_paths    -> autonomy/ + ocr/ na sys.path
  2) logging                   -> log file + console + Qt
  3) AppConfig.load_from_env
  4) single-instance lock
  5) Alembic migrate head
  6) DB engine init + ping
  7) QApplication
  8) OcrWorker start
  9) MainWindow show
 10) app.exec()
"""

from __future__ import annotations

import sys
from pathlib import Path

# KROK 1 — sys.path. Nesmí být NIC importováno z autonomy/ocr před tímhle.
from spot_operator.bootstrap import inject_paths  # noqa: E402

inject_paths()

# Teprve teď můžeme importovat cokoliv z autonomy/ocr.
from spot_operator.config import AppConfig  # noqa: E402
from spot_operator.logging_config import dump_environment_diagnostics, get_logger, setup  # noqa: E402


def _single_instance_lock(config: AppConfig):
    """Zabrání spuštění druhé instance ve stejném user účtu."""
    from PySide6.QtCore import QLockFile

    from spot_operator.constants import LOCK_FILE_NAME

    lock_path = config.temp_root / LOCK_FILE_NAME
    lock = QLockFile(str(lock_path))
    lock.setStaleLockTime(10_000)  # 10 s — předchozí crash
    if not lock.tryLock(100):
        raise RuntimeError(
            f"Another instance of Spot Operator is already running "
            f"(lock file {lock_path})."
        )
    return lock


def main() -> int:
    # CLI flag --diag: vypíše verze balíčků a skončí.
    diag = "--diag" in sys.argv

    config = AppConfig.load_from_env()
    config.ensure_runtime_dirs()
    setup(config)
    log = get_logger("main")

    if diag:
        dump_environment_diagnostics(log)
        return 0

    log.info("Spot Operator starting (root=%s)", config.root_dir)

    try:
        lock = _single_instance_lock(config)
    except Exception as exc:
        log.error("Single instance lock failed: %s", exc)
        _fatal_dialog(str(exc))
        return 1

    try:
        from spot_operator.db import init_engine, ping, shutdown_engine
        from spot_operator.db.migrations import upgrade_to_head

        log.info("Running DB migrations...")
        upgrade_to_head(config.database_url)

        log.info("Initializing DB engine...")
        init_engine(config.database_url)
        if not ping():
            raise RuntimeError("DB ping failed after init. Check DATABASE_URL.")
    except Exception as exc:
        log.exception("DB init failed: %s", exc)
        _fatal_dialog(
            f"Databáze není dostupná:\n\n{exc}\n\n"
            "Zkontroluj DATABASE_URL v .env a že PostgreSQL server běží."
        )
        return 2

    # Cleanup starých temp map extrakcí
    try:
        from spot_operator.services.map_storage import cleanup_temp_root

        cleanup_temp_root(config.temp_root)
    except Exception as exc:
        log.warning("temp cleanup failed: %s", exc)

    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName("Spot Operator")
    app.setOrganizationName("spot_operator")
    app.setStyle("Fusion")

    # OCR worker
    ocr_worker = None
    try:
        from spot_operator.ocr.pipeline import create_default_pipeline
        from spot_operator.services.ocr_worker import OcrWorker

        pipeline = create_default_pipeline(config)
        ocr_worker = OcrWorker(pipeline)
        ocr_worker.start()
        log.info("OCR worker started")
    except Exception as exc:
        log.warning("OCR worker could not be started: %s", exc)

    exit_code = 1
    try:
        from spot_operator.ui.main_window import MainWindow

        window = MainWindow(config)
        window.show()
        exit_code = app.exec()
    finally:
        if ocr_worker is not None:
            ocr_worker.request_stop()
            ocr_worker.wait(5000)
        shutdown_engine()
        lock.unlock()
        log.info("Spot Operator exited (code=%s)", exit_code)

    return int(exit_code)


def _fatal_dialog(message: str) -> None:
    """Zobrazí QMessageBox s fatální chybou (pokud už QApplication existuje)."""
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        _ = QApplication(sys.argv) if QApplication.instance() is None else None
        box = QMessageBox()
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle("Spot Operator — fatální chyba")
        box.setText(message)
        box.exec()
    except Exception:
        print("FATAL:", message, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
