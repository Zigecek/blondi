# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: ocr.exe (CLI wrapper nad ocr/ocrtest.py).

Pozn.: ocrtest.py neakceptuje CLI argumenty — iteruje hardcoded './test' slozku.
Po buildu proto uzivatel polozi slozku 'test/' s obrazky SPZ vedle ocr.exe.
Runtime hook zkopiruje modely z bundlu vedle .exe pri prvnim spusteni.

Spoustet z root projektu:
    pyinstaller --noconfirm --workpath build/_pyinstaller --distpath dist build/specs/ocr.spec
"""

from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
)

SPEC_DIR = Path(SPECPATH).resolve()
ROOT = SPEC_DIR.parent.parent
OCR_DIR = ROOT / "ocr"
HOOK_DIR = ROOT / "build" / "runtime_hooks"

hidden = [
    *collect_submodules("ultralytics"),
    *collect_submodules("nomeroff_net"),
    "cv2",
    "onnxruntime",
    "fast_plate_ocr.inference_hub",
    "torch",
]

datas = []
for name in ("license-plate-finetune-v1m.pt", "anpr_ocr_eu_2-cpu.pb"):
    p = OCR_DIR / name
    if p.is_file():
        datas.append((str(p), "."))

# Baliky s vlastnimi datovymi soubory (YAML configs, fonts, ...).
# collect_data_files je tolerantni — vraci [] kdyz balicek chybi.
for pkg in ("ultralytics", "fast_plate_ocr", "nomeroff_net"):
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
    "PySide6",
    "bosdyn",
    "spot_operator",
    "sqlalchemy",
    "alembic",
    "psycopg",
]


a = Analysis(
    [str(OCR_DIR / "ocrtest.py")],
    pathex=[str(OCR_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(HOOK_DIR / "rh_ocr_paths.py")],
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
    name="ocr",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
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
    name="ocr",
)
