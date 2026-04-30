# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: autonomy.exe (PySide6 GUI, Boston Dynamics SDK).

Spoustet z root projektu:
    pyinstaller --noconfirm --workpath build/_pyinstaller --distpath dist build/specs/autonomy.spec
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

SPEC_DIR = Path(SPECPATH).resolve()
ROOT = SPEC_DIR.parent.parent
AUTONOMY_DIR = ROOT / "autonomy"
HOOK_DIR = ROOT / "build" / "runtime_hooks"

hidden = [
    # bosdyn pouziva dynamicke importy — PyInstaller je sam nezachyti
    *collect_submodules("bosdyn"),
    "PySide6.QtSvg",
]

datas = []
for fname in (".env", ".env.example"):
    p = AUTONOMY_DIR / fname
    if p.is_file():
        datas.append((str(p), "."))

excludes = [
    "PyQt5",
    "PyQt6",
    "tkinter",
    "matplotlib",
    "pytest",
    # root projekt + ocr — autonomy je self-contained, nepotrebuje je
    "blondi",
    "ultralytics",
    "torch",
    "nomeroff_net",
    "fast_plate_ocr",
    "onnxruntime",
    "sqlalchemy",
    "alembic",
    "psycopg",
]


a = Analysis(
    [str(AUTONOMY_DIR / "main.py")],
    pathex=[str(AUTONOMY_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(HOOK_DIR / "rh_autonomy_paths.py")],
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
    name="autonomy",
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
    name="autonomy",
)
