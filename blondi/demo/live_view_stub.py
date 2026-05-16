"""Live view stub pro demo režim.

Místo skutečného streamu z ``ImagePipeline`` vrací statický QPixmap
ze souborů v ``<project_root>/demo/``:

- ``front.jpg`` — front_composite (kompozit přední dvojice kamer), zobrazí se
  v live view (FiducialPage / TeleopRecordPage / PlaybackRunPage).
- ``left.jpg`` / ``right.jpg`` — náhledy v PhotoConfirmOverlay při focení.

Pokud asset chybí, vrátí placeholder s textem.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap

from blondi.logging_config import get_logger

_log = get_logger(__name__)

# Project root = parent dvou úrovní nad blondi/demo/.
_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent
_DEMO_ASSETS_DIR: Path = _PROJECT_ROOT / "demo"
_FRONT_JPG: Path = _DEMO_ASSETS_DIR / "front.jpg"
_LEFT_JPG: Path = _DEMO_ASSETS_DIR / "left.jpg"
_RIGHT_JPG: Path = _DEMO_ASSETS_DIR / "right.jpg"

_PLACEHOLDER_W = 1280
_PLACEHOLDER_H = 720


def _load_or_placeholder(path: Path, label: str) -> QPixmap:
    """Načte PNG z disku nebo vrátí placeholder se textem."""
    if path.is_file():
        pix = QPixmap(str(path))
        if not pix.isNull():
            return pix
        _log.warning("Demo asset %s je neplatný PNG", path)
    else:
        _log.info("Demo asset %s neexistuje — generuji placeholder", path)
    return _make_placeholder(label)


def _make_placeholder(label: str) -> QPixmap:
    """Vytvoří šedý placeholder s textem (pro případ chybějícího assetu)."""
    pix = QPixmap(_PLACEHOLDER_W, _PLACEHOLDER_H)
    pix.fill(QColor("#3a3a3a"))
    painter = QPainter(pix)
    try:
        painter.setPen(QColor("#cccccc"))
        font = QFont("Segoe UI", 48, QFont.Bold)
        painter.setFont(font)
        painter.drawText(pix.rect(), Qt.AlignCenter, f"DEMO {label.upper()}")
    finally:
        painter.end()
    return pix


def compose_front_view() -> QPixmap:
    """Vrátí ``front.jpg`` jako jeden composite (front_composite view).

    V reálném provozu Spot SDK vrací ``front_composite`` jako sjednocený
    image source, takže demo používá taky jediný předem připravený obrázek.
    """
    return _load_or_placeholder(_FRONT_JPG, "front")


def compose_single(side: Literal["left", "right"]) -> QPixmap:
    """Vrátí jeden obrázek pro daný source (``left`` nebo ``right``).

    Použije se v PhotoConfirmOverlay při focení.
    """
    if side == "left":
        return _load_or_placeholder(_LEFT_JPG, "left")
    return _load_or_placeholder(_RIGHT_JPG, "right")


def pixmap_for_source(source: str) -> QPixmap:
    """Mapuje názvy image sources (``left_fisheye_image`` apod.) na placeholder."""
    src_lower = source.lower()
    if "left" in src_lower:
        return compose_single("left")
    if "right" in src_lower:
        return compose_single("right")
    if "front" in src_lower or "composite" in src_lower:
        return compose_front_view()
    return compose_front_view()


__all__ = ["compose_front_view", "compose_single", "pixmap_for_source"]
