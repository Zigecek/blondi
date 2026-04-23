"""PyInstaller runtime hook — spot.exe path redirect.

spot.exe ma autonomy/ a ocr/ zabalene uvnitr sve _MEIPASS slozky. Tento hook
emuluje chovani spot_operator.bootstrap.inject_paths pro zmrazeny runtime.

Strategie k .env:
  - .env soubory zustavaji UVNITR bundlu (_internal/.env, _internal/autonomy/.env),
    NEROZBALUJI se vedle .exe. Pokud user nema vlastni .env vedle .exe, hook
    nacte bundled .env do os.environ (override=False).
  - Pokud user vytvori vlastni .env vedle .exe, hook ignoruje bundled a user
    .env ma prioritu (spot_operator.config.load_from_env ho nacte).

Strategie k OCR modelum:
  - Modely zustavaji v _MEIPASS/ocr/. Presmerujeme je via env promennou
    OCR_YOLO_MODEL (spot AppConfig ji cte).

Strategie k runtime slozkam (logs, temp):
  - Smerujeme do %LOCALAPPDATA%\\spot\\ (Windows konvence, per-user, writable).
  - Lazy — slozky nevytvarime predem; spot_operator.config.ensure_runtime_dirs()
    je vytvori az v okamziku prvniho pouziti.
  - Na atexit zavreme file loggery a celou %LOCALAPPDATA%\\spot\\ smazeme.
    Pri tvrdem padu (segfault, kill) atexit nebezi -> log zustane pro post-mortem.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _apply() -> None:
    if not getattr(sys, "frozen", False):
        return

    exe_dir = Path(sys.executable).resolve().parent
    meipass = Path(getattr(sys, "_MEIPASS", exe_dir))

    # 1) sys.path injection — emulace spot_operator.bootstrap.inject_paths
    for sub in ("autonomy", "ocr"):
        p = str(meipass / sub)
        if (meipass / sub).is_dir() and p not in sys.path:
            sys.path.insert(0, p)

    # 2) Fallback load bundled .env do os.environ — POUZE pokud user .env
    #    vedle .exe neexistuje. Tim padem user .env (pokud ho vytvori) ma
    #    vzdy prioritu.
    try:
        from dotenv import load_dotenv
    except ImportError:
        load_dotenv = None

    if load_dotenv is not None:
        for user_path, bundle_path in [
            (exe_dir / ".env", meipass / ".env"),
            (exe_dir / "autonomy" / ".env", meipass / "autonomy" / ".env"),
        ]:
            if not user_path.is_file() and bundle_path.is_file():
                load_dotenv(bundle_path, override=False)

    # 3) OCR YOLO model — absolutni cesta do _MEIPASS. Pathlib spravne
    #    zpracuje (ROOT / absolute) = absolute, takze ROOT prepis neovlivni.
    yolo_bundle = meipass / "ocr" / "license-plate-finetune-v1m.pt"
    if yolo_bundle.is_file() and not os.environ.get("OCR_YOLO_MODEL_OVERRIDE"):
        os.environ["OCR_YOLO_MODEL"] = str(yolo_bundle)

    # 4) CWD -> meipass kvuli alembic.ini a alembic/ (prepend_sys_path = .)
    os.chdir(str(meipass))

    # 5) Monkey-patch bootstrap.ROOT + constants derived paths.
    try:
        from spot_operator import bootstrap as _bs
    except Exception:
        return
    _bs.ROOT = exe_dir
    _bs.AUTONOMY_DIR = meipass / "autonomy"
    _bs.OCR_DIR = meipass / "ocr"

    try:
        from spot_operator import constants as _const
    except Exception:
        return

    # Runtime slozka — %LOCALAPPDATA%\spot\ (fallback vedle .exe, kdyby LOCALAPPDATA
    # nebyla nastavena, coz na Windows nema nastat).
    appdata = os.environ.get("LOCALAPPDATA") or str(exe_dir)
    runtime_dir = Path(appdata) / "spot"
    _const.LOGS_DIR = runtime_dir / "logs"
    _const.TEMP_ROOT = runtime_dir / "temp"

    # migrations.py ma ROOT importovany jako `from spot_operator.bootstrap import ROOT`
    # a pouziva ho pro alembic.ini + alembic/ — to jsou BUNDLED resources (lezi v
    # _MEIPASS, ne vedle .exe). Prepsani bootstrap.ROOT na exe_dir tyhle cesty
    # rozbije. Donutime import migrations a prepiseme jeho lokalni ROOT na meipass.
    try:
        from spot_operator.db import migrations as _mig
        _mig.ROOT = meipass
    except Exception:
        pass

    # _verify_presence kontroluje soubory pod puvodnim ROOT — v bundlu jsou
    # pod _MEIPASS/autonomy a _MEIPASS/ocr, a my ROOT prepisujeme na exe_dir.
    # Noop je tady OK, protoze jsme uz sami naimportovali zprava v kroku 1.
    def _noop() -> None:
        return None

    _bs._verify_presence = _noop  # type: ignore[attr-defined]

    # atexit cleanup — zavre file loggery a smaze celou runtime slozku.
    # Pri tvrdem padu (segfault, TerminateProcess) atexit nebezi -> log prezije.
    import atexit
    import logging
    import shutil

    def _cleanup_runtime() -> None:
        root_logger = logging.getLogger()
        for h in list(root_logger.handlers):
            try:
                h.close()
            except Exception:
                pass
            root_logger.removeHandler(h)

        if runtime_dir.exists():
            shutil.rmtree(runtime_dir, ignore_errors=True)

    atexit.register(_cleanup_runtime)


_apply()
