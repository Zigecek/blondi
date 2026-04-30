"""Entry point — Blondi desktop aplikace.

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
from blondi.bootstrap import inject_paths  # noqa: E402

inject_paths()

# Teprve teď můžeme importovat cokoliv z autonomy/ocr.
from blondi.config import AppConfig  # noqa: E402
from blondi.logging_config import dump_environment_diagnostics, get_logger, setup  # noqa: E402


def _single_instance_lock(config: AppConfig):
    """Zabrání spuštění druhé instance **ve stejném Windows účtu**.

    Druhý Windows user na stejném stroji (nebo sdíleném profilu) má vlastní
    lock soubor a není blokovaný. Jméno souboru obsahuje `getpass.getuser()`
    pro izolaci.

    PR-11 FIND-175: lock je v %LOCALAPPDATA%/blondi/ místo
    temp_root, aby ho nevyčistil ``cleanup_temp_root`` nebo user mazáním
    temp složky.
    """
    import getpass
    import os
    from pathlib import Path

    from PySide6.QtCore import QLockFile

    user = getpass.getuser() or "unknown"
    # Sanitizuj user jméno pro filesystem (žádné \\, /, :, ...).
    safe_user = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in user)

    lockdir = Path(os.getenv("LOCALAPPDATA") or config.temp_root) / "blondi"
    try:
        lockdir.mkdir(parents=True, exist_ok=True)
    except Exception:
        lockdir = config.temp_root
        lockdir.mkdir(parents=True, exist_ok=True)
    lock_path = lockdir / f"blondi_{safe_user}.lock"

    lock = QLockFile(str(lock_path))
    lock.setStaleLockTime(10_000)  # 10 s — předchozí crash
    if not lock.tryLock(100):
        raise RuntimeError(
            f"Another instance of Blondi is already running for user "
            f"{user!r} (lock file {lock_path})."
        )
    return lock


def main() -> int:
    # PR-11 FIND-196: Python version guard.
    if sys.version_info[:2] != (3, 10):
        _fatal_dialog(
            f"Blondi vyžaduje Python 3.10 (bosdyn SDK nepodporuje novější). "
            f"Aktuální verze: {sys.version_info.major}.{sys.version_info.minor}. "
            f"Reinstaluj .venv s Pythonem 3.10 (setup_venv.bat)."
        )
        return 3

    # CLI flag --diag: vypíše verze balíčků a skončí.
    diag = "--diag" in sys.argv

    config = AppConfig.load_from_env()
    config.ensure_runtime_dirs()
    setup(config)
    log = get_logger("main")

    if diag:
        dump_environment_diagnostics(log)
        return 0

    log.info("Blondi starting (root=%s)", config.root_dir)

    try:
        lock = _single_instance_lock(config)
    except Exception as exc:
        log.error("Single instance lock failed: %s", exc)
        _fatal_dialog(str(exc))
        return 1

    try:
        from blondi.db import init_engine, ping, shutdown_engine
        from blondi.db.migrations import upgrade_to_head

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
        from blondi.services.map_storage import cleanup_temp_root

        cleanup_temp_root(config.temp_root)
    except Exception as exc:
        # PR-11 FIND-172: log.error místo warning — cleanup failure je
        # neškodný pro bootstrap, ale user by měl vidět že disk se plní.
        log.error("temp cleanup failed: %s — pokračuji bez úklidu", exc)

    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName("Blondi")
    app.setOrganizationName("blondi")
    app.setStyle("Fusion")

    # OCR worker
    ocr_worker = None
    try:
        from blondi.ocr.pipeline import create_default_pipeline
        from blondi.services.ocr_worker import OcrWorker

        pipeline = create_default_pipeline(config)
        ocr_worker = OcrWorker(pipeline)
        ocr_worker.start()
        log.info("OCR worker started")
    except Exception as exc:
        log.warning("OCR worker could not be started: %s", exc)

    exit_code = 1
    try:
        from blondi.ui.main_window import MainWindow

        window = MainWindow(config, ocr_worker=ocr_worker)
        window.showMaximized()
        exit_code = app.exec()
    finally:
        if ocr_worker is not None:
            ocr_worker.request_stop()
            # PR-11 FIND-171: 30 s timeout (YOLO warmup na slabším CPU
            # může trvat 5-10 s, 5 s z originálu byl nedostatečný a
            # zanechával zombie thread).
            ocr_worker.wait(30000)
        shutdown_engine()
        lock.unlock()
        log.info("Blondi exited (code=%s)", exit_code)

    return int(exit_code)


def _fatal_dialog(message: str) -> None:
    """Zobrazí QMessageBox s fatální chybou (pokud už QApplication existuje).

    PR-11 FIND-173: správný Qt lifecycle — pokud QApplication vytvoříme
    ephemerně pro dialog, po exec musíme quit, aby nezanechávala
    proces za sebou.
    """
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        existing = QApplication.instance()
        ephemeral: QApplication | None = None
        if existing is None:
            ephemeral = QApplication(sys.argv)
        box = QMessageBox()
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle("Blondi — fatální chyba")
        box.setText(message)
        box.exec()
        if ephemeral is not None:
            ephemeral.quit()
    except Exception:
        print("FATAL:", message, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
