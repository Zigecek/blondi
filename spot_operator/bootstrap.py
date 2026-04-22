"""Bootstrap — příprava sys.path tak, aby šly importovat moduly z autonomy/ a ocr/.

Tyto dvě složky nejsou Python balíčky (nemají pyproject.toml a autonomy/ má top-level
balíček `app`, což je kolizní jméno). Místo editable installu jim přidáme jejich kořeny
na sys.path. Volá se jako úplně první věc v main.py — před jakýmkoli importem z autonomy
nebo ocr.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parent.parent
AUTONOMY_DIR: Path = ROOT / "autonomy"
OCR_DIR: Path = ROOT / "ocr"


def inject_paths() -> None:
    """Přidá autonomy/ a ocr/ na začátek sys.path.

    Idempotentní — opakovaná volání nic nepokazí.
    """
    _prepend(str(AUTONOMY_DIR))
    _prepend(str(OCR_DIR))
    _verify_presence()


def _prepend(path: str) -> None:
    if path in sys.path:
        return
    sys.path.insert(0, path)


def _verify_presence() -> None:
    """Ověří, že autonomy a ocr složky existují. Bez toho nemá aplikace smysl běžet."""
    missing: list[str] = []
    if not (AUTONOMY_DIR / "app" / "robot" / "sdk_session.py").is_file():
        missing.append(f"autonomy SDK session: {AUTONOMY_DIR / 'app/robot/sdk_session.py'}")
    if not (OCR_DIR / "ocrtest.py").is_file():
        missing.append(f"ocr skript: {OCR_DIR / 'ocrtest.py'}")
    if missing:
        raise RuntimeError(
            "Chybí povinné podprojekty (autonomy/ocr). Zkontroluj rozložení adresářů.\n"
            + "\n".join(f" - {m}" for m in missing)
        )
