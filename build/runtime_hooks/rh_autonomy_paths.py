"""PyInstaller runtime hook — autonomy.exe path redirect.

Spoustene PyInstaller bootloaderem PRED uzivatelskym kodem. Nemeni zdrojaky,
jen monkey-patchuje modul-level konstanty v autonomy.app.config tak, aby
BASE_DIR ukazoval vedle .exe (portable mod), nikoliv do _MEIPASS bundle.

Ucel:
  - maps/, runs/, exports/, logs/ se vytvori vedle autonomy.exe
  - .env a .env.example se pri prvnim spusteni zkopiruji z bundlu vedle .exe,
    aby je uzivatel mohl editovat.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _apply() -> None:
    if not getattr(sys, "frozen", False):
        return

    exe_dir = Path(sys.executable).resolve().parent
    meipass = Path(getattr(sys, "_MEIPASS", exe_dir))

    for fname in (".env", ".env.example"):
        src = meipass / fname
        dst = exe_dir / fname
        if src.is_file() and not dst.exists():
            try:
                shutil.copy2(src, dst)
            except OSError:
                pass

    # Pre-import app.config, patch BASE_DIR a derived cesty.
    # Uzivatelsky main.py nasledne dostane patchovany modul ze sys.modules.
    try:
        import app.config as _cfg
    except Exception:
        return

    _cfg.BASE_DIR = exe_dir
    _cfg.MAPS_DIR = exe_dir / "maps"
    _cfg.RUNS_DIR = exe_dir / "runs"
    _cfg.EXPORTS_DIR = exe_dir / "exports"
    _cfg.LOGS_DIR = exe_dir / "logs"

    for d in (_cfg.MAPS_DIR, _cfg.RUNS_DIR, _cfg.EXPORTS_DIR, _cfg.LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)

    os.chdir(str(exe_dir))


_apply()
