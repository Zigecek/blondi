"""PyInstaller runtime hook — ocr.exe path redirect.

ocrtest.py ocekava 'license-plate-finetune-v1m.pt' a 'anpr_ocr_eu_2-cpu.pb'
v current working directory a testovaci slozku './test' take v CWD.

Tento hook:
  1) pri prvnim spusteni zkopiruje modely z _MEIPASS vedle .exe (persistentni)
  2) nastavi CWD na slozku .exe, aby relativni cesty v ocrtest.py fungovaly
  3) uzivatel dava test/ slozku (s obrazky SPZ) vedle ocr.exe
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


_MODEL_FILES = (
    "license-plate-finetune-v1m.pt",
    "anpr_ocr_eu_2-cpu.pb",
)


def _apply() -> None:
    if not getattr(sys, "frozen", False):
        return

    exe_dir = Path(sys.executable).resolve().parent
    meipass = Path(getattr(sys, "_MEIPASS", exe_dir))

    for name in _MODEL_FILES:
        src = meipass / name
        dst = exe_dir / name
        if src.is_file() and not dst.exists():
            try:
                shutil.copy2(src, dst)
            except OSError:
                pass

    os.chdir(str(exe_dir))


_apply()
