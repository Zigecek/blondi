"""PyInstaller runtime hook — autonomy.exe path redirect.

Spoustene PyInstaller bootloaderem PRED uzivatelskym kodem. Nemeni zdrojaky.

Strategie:
  - BASE_DIR pro runtime data (maps/, runs/, exports/, logs/) = slozka vedle
    autonomy.exe (portable mod).
  - .env a .env.example zustavaji UVNITR bundlu (_internal/.env), NEROZBALUJI
    se vedle .exe. Monkey-patch _read_env_file provede fallback — pokud soubor
    neni v BASE_DIR (exe_dir), zkusi meipass (bundle).
  - Pokud uzivatel vytvori vlastni .env vedle .exe, ma prednost pred bundlem.
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

    # _read_env_file fallback: pokud soubor neni v exe_dir, zkus meipass bundle.
    _orig_read = _cfg._read_env_file

    def _patched_read(path):
        p = Path(path)
        if p.is_file():
            return _orig_read(p)
        fallback = meipass / p.name
        if fallback.is_file():
            return _orig_read(fallback)
        return {}

    _cfg._read_env_file = _patched_read

    os.chdir(str(exe_dir))


_apply()
