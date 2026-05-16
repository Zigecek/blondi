"""Globální F12 hotkey pro screenshoty v demo režimu.

``DemoScreenshotter`` se nainstaluje jako event filter na ``QApplication``
a zachycuje všechny ``KeyPress`` s ``Qt.Key_F12``. Po stisku zachytí top-level
aktivní okno (modal dialog má přednost), uloží PNG do ``screens/`` s názvem
odvozeným z aktuální obrazovky / typu wizardu.

Naming registry:
- MainWindow → ``00_main.png``
- ConnectDialog → ``12_dialog_connect_spot.png``
- RecordingWizard pages → 01..04
- WalkWizard pages → 10..11
- PlaybackWizard pages → 05..09
- CrudWindow tabs → 13..15, detail dialogy → 13b..15b
- QMessageBox → 16/17 podle ikony

Při kolizi názvu se přidá suffix ``_2``, ``_3``, atd.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent, QPixmap
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QWidget, QWizard

from blondi.logging_config import get_logger

_log = get_logger(__name__)

# Mapování: třída wizardu × třída page → (prefix číslo, slug).
_RECORDING_PAGES = {
    "ConnectPage": ("01", "recording_connect"),
    "FiducialPage": ("02", "recording_fiducial"),
    "TeleopRecordPage": ("03", "recording_teleop"),
    "SaveMapPage": ("04", "recording_save_map"),
}
_PLAYBACK_PAGES = {
    "ConnectPage": ("05", "playback_connect"),
    "MapSelectPage": ("06", "playback_map_select"),
    "FiducialPage": ("07", "playback_fiducial"),
    "PlaybackRunPage": ("08", "playback_run"),
    "PlaybackResultPage": ("09", "playback_result"),
}
_WALK_PAGES = {
    "ConnectPage": ("10", "walk_connect"),
    "FiducialPage": ("11", "walk_fiducial"),
}

_CRUD_TABS = {
    0: ("13", "crud_spz_tab"),
    1: ("14", "crud_runs_tab"),
    2: ("15", "crud_photos_tab"),
}
_CRUD_DETAIL_DIALOGS = {
    "SpzDetailDialog": ("13b", "crud_spz_detail_dialog"),
    "RunDetailDialog": ("14b", "crud_run_detail_dialog"),
    "PhotoDetailDialog": ("15b", "crud_photo_detail_dialog"),
}


class DemoScreenshotter(QObject):
    """Globální F12 hotkey + automatický name detection.

    Použití:
        screenshotter = DemoScreenshotter(output_dir)
        screenshotter.install(app)
    """

    def __init__(self, output_dir: Path, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._output_dir = output_dir

    def install(self, app: QApplication) -> None:
        """Zaregistruje event filter — chytá F12 globálně přes celou app."""
        self._output_dir.mkdir(parents=True, exist_ok=True)
        app.installEventFilter(self)
        _log.info(
            "DemoScreenshotter installed — F12 ukládá do %s", self._output_dir
        )

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: D401
        if event.type() == QEvent.KeyPress and isinstance(event, QKeyEvent):
            if event.key() == Qt.Key_F12:
                self.capture()
                return True
        return super().eventFilter(obj, event)

    # ---- Capture ----

    def capture(self) -> Optional[Path]:
        """Pořídí screenshot aktuálního aktivního okna a uloží PNG."""
        target = self._pick_target()
        if target is None:
            _log.warning("Screenshot: žádné aktivní okno k zachycení.")
            return None

        # PhotoConfirmOverlay je child wizardu — pokud je viditelný, zachytíme
        # jen overlay (jinak by se na screenshot dostal celý wizard se overlayí
        # nahoře, což je často OK, ale pro samostatný screenshot je to lepší).
        overlay = self._find_visible_overlay(target)
        widget_to_grab: QWidget = overlay if overlay is not None else target
        try:
            pixmap: QPixmap = widget_to_grab.grab()
        except Exception as exc:
            _log.exception("Screenshot grab selhal: %s", exc)
            return None

        prefix, slug = self._derive_name(target, overlay=overlay is not None)
        filename = f"{prefix}_{slug}.png"
        path = self._unique_path(filename)
        try:
            pixmap.save(str(path), "PNG")
        except Exception as exc:
            _log.exception("Screenshot save selhal (%s): %s", path, exc)
            return None
        _log.info("Screenshot uložen: %s", path)
        # Krátký vizuální feedback ve statusBaru pokud existuje.
        self._notify_user(target, path)
        return path

    def _pick_target(self) -> Optional[QWidget]:
        app = QApplication.instance()
        if app is None:
            return None
        # Modal dialog má přednost před active window.
        modal = app.activeModalWidget()
        if modal is not None and modal.isVisible():
            return modal
        active = app.activeWindow()
        if active is not None:
            return active
        # Fallback: první visible top-level widget.
        for w in app.topLevelWidgets():
            if w.isVisible():
                return w
        return None

    def _find_visible_overlay(self, target: QWidget) -> Optional[QWidget]:
        """Najde visible PhotoConfirmOverlay child v aktivním okně (pokud je)."""
        try:
            from blondi.ui.common.photo_confirm_overlay import PhotoConfirmOverlay
        except Exception:
            return None
        for child in target.findChildren(PhotoConfirmOverlay):
            if child.isVisible():
                return child
        return None

    # ---- Name derivation ----

    def _derive_name(self, target: QWidget, *, overlay: bool) -> tuple[str, str]:
        cls_name = type(target).__name__

        # PhotoConfirmOverlay zachytíme zvlášť.
        if overlay:
            return ("03b", "recording_photo_confirm_overlay")

        # MainWindow.
        if cls_name == "MainWindow":
            return ("00", "main")

        # Wizards.
        if isinstance(target, QWizard):
            return self._derive_wizard_name(target)

        # CRUD window.
        if cls_name == "CrudWindow":
            return self._derive_crud_tab_name(target)

        # CRUD detail dialogy.
        if cls_name in _CRUD_DETAIL_DIALOGS:
            return _CRUD_DETAIL_DIALOGS[cls_name]

        # ConnectDialog (modal z MainWindow).
        if cls_name == "ConnectDialog":
            return ("12", "dialog_connect_spot")

        # QMessageBox — error/confirm.
        if isinstance(target, QMessageBox):
            icon = target.icon()
            if icon == QMessageBox.Critical:
                return ("16", "dialog_error")
            if icon == QMessageBox.Warning:
                return ("17", "dialog_confirm")
            if icon == QMessageBox.Question:
                return ("17", "dialog_confirm")
            return ("18", "dialog_info")

        # Generic dialog fallback.
        if isinstance(target, QDialog):
            slug = _slugify(target.windowTitle() or cls_name)
            return ("99", f"dialog_{slug}")

        # Unknown.
        slug = _slugify(target.windowTitle() or cls_name)
        return ("99", slug)

    def _derive_wizard_name(self, wizard: QWizard) -> tuple[str, str]:
        wiz_cls = type(wizard).__name__
        page = wizard.currentPage()
        page_cls = type(page).__name__ if page is not None else ""
        registry: dict[str, tuple[str, str]] = {}
        if wiz_cls == "RecordingWizard":
            registry = _RECORDING_PAGES
        elif wiz_cls == "PlaybackWizard":
            registry = _PLAYBACK_PAGES
        elif wiz_cls == "WalkWizard":
            registry = _WALK_PAGES
        if page_cls in registry:
            return registry[page_cls]
        # Fallback.
        return ("99", _slugify(wizard.windowTitle() or wiz_cls))

    def _derive_crud_tab_name(self, crud_window: QWidget) -> tuple[str, str]:
        # CrudWindow má _tabs (QTabWidget). Bezpečně dosáhneme přes findChild.
        from PySide6.QtWidgets import QTabWidget

        tabs = crud_window.findChild(QTabWidget)
        if tabs is None:
            return ("13", "crud")
        idx = tabs.currentIndex()
        if idx in _CRUD_TABS:
            return _CRUD_TABS[idx]
        return ("13", f"crud_tab_{idx}")

    # ---- File path ----

    def _unique_path(self, filename: str) -> Path:
        """Pokud `filename` existuje, přidá suffix ``_2``, ``_3``, ..."""
        base = self._output_dir / filename
        if not base.exists():
            return base
        stem = base.stem
        suffix = base.suffix
        n = 2
        while True:
            candidate = self._output_dir / f"{stem}_{n}{suffix}"
            if not candidate.exists():
                return candidate
            n += 1

    def _notify_user(self, target: QWidget, path: Path) -> None:
        """Zobrazí krátkou notifikaci v statusBaru pokud existuje."""
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMainWindow, QStatusBar

        # Top-level je QMainWindow nebo wizard — zkus najít statusBar.
        status: Optional[QStatusBar] = None
        if isinstance(target, QMainWindow):
            status = target.statusBar()
        else:
            mw = target.window() if hasattr(target, "window") else None
            if isinstance(mw, QMainWindow):
                status = mw.statusBar()
        if status is not None:
            status.showMessage(f"📸 Screenshot: {path.name}", 3000)


def _slugify(text: str) -> str:
    """Snake_case slug z UI textu (windowTitle apod.)."""
    out: list[str] = []
    for ch in text:
        if ch.isalnum():
            out.append(ch.lower())
        elif ch in (" ", "_", "-", "."):
            out.append("_")
    s = "".join(out).strip("_")
    while "__" in s:
        s = s.replace("__", "_")
    return s or "screen"


__all__ = ["DemoScreenshotter"]
