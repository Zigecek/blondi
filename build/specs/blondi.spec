# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: blondi.exe (PySide6 GUI + autonomy + ocr zabalene uvnitr).

blondi.exe je plne samostatny — obsahuje:
  - blondi/ balik (entry point je root main.py)
  - autonomy/ (sys.path inject v runtime hooku, importy z autonomy.app...)
  - ocr/ (fallback.py importuje 'ocrtest' jako modul + YOLO vahy)
  - alembic/ + alembic.ini (DB migrace se spousti pri startu)
  - .env soubory

Spoustet z root projektu:
    pyinstaller --noconfirm --workpath build/_pyinstaller --distpath dist build/specs/blondi.spec
"""

from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
)

SPEC_DIR = Path(SPECPATH).resolve()
ROOT = SPEC_DIR.parent.parent
AUTONOMY_DIR = ROOT / "autonomy"
OCR_DIR = ROOT / "ocr"
HOOK_DIR = ROOT / "build" / "runtime_hooks"

hidden = [
    *collect_submodules("bosdyn"),
    *collect_submodules("ultralytics"),
    *collect_submodules("blondi"),
    *collect_submodules("alembic"),
    # autonomy 'app' balicek — fallback.py-style importy
    *collect_submodules("app"),
    "PySide6.QtSvg",
    "psycopg",
    "psycopg.pq",
    "sqlalchemy.dialects.postgresql",
    "alembic.runtime.migration",
    "alembic.ddl.postgresql",
    "keyring.backends.Windows",
    "fast_plate_ocr.inference_hub",
    "onnxruntime",
    "cv2",
]

# Datove soubory. Tuple (src, dest_dir) — dest relativni k _MEIPASS.
datas = []


def _add_file(src: Path, dest_dir: str) -> None:
    if src.is_file():
        datas.append((str(src), dest_dir))


def _add_tree(src_dir: Path, dest_dir: str) -> None:
    """Pridat celou slozku rekurzivne, zachovat strukturu."""
    if not src_dir.is_dir():
        return
    for p in src_dir.rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts:
            rel = p.relative_to(src_dir).parent
            target = dest_dir if str(rel) in (".", "") else f"{dest_dir}/{rel.as_posix()}"
            datas.append((str(p), target))


# Root .env + alembic
_add_file(ROOT / ".env", ".")
_add_file(ROOT / ".env.example", ".")
_add_file(ROOT / "alembic.ini", ".")
_add_tree(ROOT / "alembic", "alembic")

# autonomy — .env (hiddenimports pres collect_submodules("app") zajisti balik)
_add_file(AUTONOMY_DIR / ".env", "autonomy")
_add_file(AUTONOMY_DIR / ".env.example", "autonomy")

# ocr — ocrtest.py (importuje ho fallback.py jako modul) + modely
_add_file(OCR_DIR / "ocrtest.py", "ocr")
_add_file(OCR_DIR / "license-plate-finetune-v1m.pt", "ocr")
_add_file(OCR_DIR / "anpr_ocr_eu_2-cpu.pb", "ocr")

# Datove soubory balicku
for pkg in ("ultralytics", "fast_plate_ocr"):
    try:
        datas.extend(collect_data_files(pkg))
    except Exception:
        pass

excludes = [
    "PyQt5",
    "PyQt6",
    "tkinter",
    "matplotlib",
    "pytest",
    "pytest_postgresql",
    "pytest_qt",
]


a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT), str(AUTONOMY_DIR), str(OCR_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(HOOK_DIR / "rh_blondi_paths.py")],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="blondi",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="blondi",
)
