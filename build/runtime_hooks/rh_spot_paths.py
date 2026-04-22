"""PyInstaller runtime hook — spot.exe path redirect.

spot.exe ma autonomy/ a ocr/ zabalene uvnitr sve _MEIPASS slozky. Tento hook
emuluje chovani spot_operator.bootstrap.inject_paths pro zmrazeny runtime:

  1) Injektuje _MEIPASS/autonomy a _MEIPASS/ocr na sys.path.
  2) Prepisuje spot_operator.bootstrap.ROOT (a derived konstanty v constants.py)
     na exe_dir tak, aby logs/ a temp/ vznikaly vedle .exe (portable).
  3) Pri prvnim spusteni zkopiruje .env/.env.example/autonomy.env z bundlu
     vedle .exe, aby slo editovat bez rebuildu.
  4) CWD nastavi na _MEIPASS, aby alembic.ini a alembic/ byly dohledatelne
     (alembic.ini ma 'prepend_sys_path = .' a 'script_location = alembic').
  5) Pro OCR vzorky modelu: copy to exe_dir pro konzistenci s ocr.exe layoutu
     (config.ocr_yolo_model_path ukazuje na ROOT/ocr/..., coz po patchi bude
     exe_dir/ocr/..., takze tam musi byt).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _copy_if_missing(src: Path, dst: Path) -> None:
    if src.is_file() and not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, dst)
        except OSError:
            pass


def _apply() -> None:
    if not getattr(sys, "frozen", False):
        return

    exe_dir = Path(sys.executable).resolve().parent
    meipass = Path(getattr(sys, "_MEIPASS", exe_dir))

    # 1) sys.path injection (analog inject_paths)
    for sub in ("autonomy", "ocr"):
        p = str(meipass / sub)
        if (meipass / sub).is_dir() and p not in sys.path:
            sys.path.insert(0, p)

    # 2) First-run kopie .env vedle .exe (uzivatel muze editovat)
    _copy_if_missing(meipass / ".env", exe_dir / ".env")
    _copy_if_missing(meipass / ".env.example", exe_dir / ".env.example")
    _copy_if_missing(meipass / "autonomy" / ".env", exe_dir / "autonomy" / ".env")

    # 3) OCR modely vedle .exe (AppConfig odvozuje ocr_yolo_model_path z ROOT)
    _copy_if_missing(
        meipass / "ocr" / "license-plate-finetune-v1m.pt",
        exe_dir / "ocr" / "license-plate-finetune-v1m.pt",
    )
    _copy_if_missing(
        meipass / "ocr" / "anpr_ocr_eu_2-cpu.pb",
        exe_dir / "ocr" / "anpr_ocr_eu_2-cpu.pb",
    )

    # 4) CWD -> meipass kvuli alembic.ini a alembic/ (prepend_sys_path = .)
    os.chdir(str(meipass))

    # 5) Monkey-patch bootstrap.ROOT + constants derived paths.
    #    Musi prijit PO zmene CWD, pred importem v uzivatelskem main.py.
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
    _const.LOGS_DIR = exe_dir / "logs"
    _const.TEMP_ROOT = exe_dir / "temp"

    # Nahradit _verify_presence, aby nepadal na overeni souboru (jsou v meipass)
    def _noop() -> None:
        return None

    _bs._verify_presence = _noop  # type: ignore[attr-defined]

    for d in (_const.LOGS_DIR, _const.TEMP_ROOT):
        d.mkdir(parents=True, exist_ok=True)


_apply()
