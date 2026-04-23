"""SpotWizard — base QWizard s unified E-Stop a close-guard chováním."""

from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import QWidget, QWizard

from spot_operator.config import AppConfig
from spot_operator.logging_config import get_logger
from spot_operator.ui.common.dialogs import confirm_dialog

_log = get_logger(__name__)


class SpotWizard(QWizard):
    """Základní třída pro oba wizardy.

    Poskytuje:
      - ukládání AppConfig + shared bundle (SpotBundle)
      - safe_abort() — uvolní všechny prostředky (E-Stop, lease, session)
      - closeEvent confirm
      - F1 shortcut pro E-Stop
    """

    def __init__(
        self,
        config: AppConfig,
        *,
        window_title: str,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._config = config
        self._bundle: Any | None = None
        self._estop_callback: Optional[Callable[[], None]] = None
        self._estop_release_callback: Optional[Callable[[], None]] = None

        self.setWindowTitle(window_title)
        self.setWizardStyle(QWizard.ModernStyle)
        self.setOption(QWizard.NoBackButtonOnStartPage, True)
        self.setOption(QWizard.NoCancelButton, False)
        self.setOption(QWizard.HaveHelpButton, False)
        self.setMinimumSize(1000, 700)

        self._f1_shortcut = QShortcut(QKeySequence("F1"), self)
        self._f1_shortcut.setContext(Qt.ApplicationShortcut)
        self._f1_shortcut.activated.connect(self.trigger_estop)

        self.setButtonText(QWizard.NextButton, "Další ▶")
        self.setButtonText(QWizard.BackButton, "◀ Zpět")
        self.setButtonText(QWizard.CancelButton, "Zrušit")
        self.setButtonText(QWizard.FinishButton, "Dokončit ✓")

    # ---- Public API pro stránky ----

    @property
    def config(self) -> AppConfig:
        return self._config

    def set_bundle(self, bundle: Any) -> None:
        self._bundle = bundle

    def bundle(self) -> Any | None:
        return self._bundle

    def set_estop_callback(
        self,
        on_trigger: Optional[Callable[[], None]],
        on_release: Optional[Callable[[], None]] = None,
    ) -> None:
        """Registruje callbacky pro F1 shortcut (alternativa k klikání widgetu).

        `on_trigger` je povinný, `on_release` volitelný (pokud None, F1 v
        triggered stavu nic neudělá — operátor musí kliknout widget).
        """
        self._estop_callback = on_trigger
        self._estop_release_callback = on_release

    def trigger_estop(self) -> None:
        """F1 shortcut handler. V triggered stavu = release, jinak = trigger.

        Preferovaně deleguje na floating E-Stop widget aktuální stránky (ten
        zná svůj stav). Fallback: naše callbacks + bundle.estop přímo.
        """
        current = self.currentPage()
        widget = getattr(current, "_estop_widget", None) if current is not None else None

        # Pokud má stránka vlastní EstopFloating widget, nechme ho rozhodnout.
        if widget is not None and hasattr(widget, "trigger_from_shortcut"):
            widget.trigger_from_shortcut()
            return

        # Fallback: bez widgetu a bez informace o triggered stavu — jen trigger.
        _log.warning("E-Stop triggered (F1, fallback path)")
        if self._estop_callback is not None:
            try:
                self._estop_callback()
            except Exception as exc:
                _log.exception("E-Stop callback failed: %s", exc)
            return
        bundle = self._bundle
        if bundle is not None and getattr(bundle, "estop", None) is not None:
            try:
                bundle.estop.trigger()
            except Exception as exc:
                _log.exception("EstopManager.trigger failed: %s", exc)

    # ---- Cleanup / close ----

    def safe_abort(self) -> None:
        """Uvolní všechny prostředky. Voláno při zavření wizardu nebo chybě.

        Pořadí je důležité:
          1. Teardown aktuální stránky (zastaví image pipeline, run threads,
             skryje E-Stop widget, abortuje recording service). Qt nevolá
             `cleanupPage` při `closeEvent`, proto to musíme zařídit sami.
          2. Disconnect bundle (lease release, estop shutdown, SDK session).
        """
        # 1) Teardown aktuální stránky (pokud ho implementuje).
        current = self.currentPage()
        if current is not None and hasattr(current, "_teardown"):
            try:
                current._teardown()
            except Exception as exc:
                _log.exception("Page _teardown failed: %s", exc)

        # 2) Disconnect bundle.
        if self._bundle is not None:
            try:
                self._bundle.disconnect()
            except Exception as exc:
                _log.exception("bundle.disconnect failed: %s", exc)
            self._bundle = None

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: D401
        if self._should_confirm_close():
            if not confirm_dialog(
                self,
                "Opravdu zavřít?",
                self._close_confirmation_message(),
                destructive=True,
            ):
                event.ignore()
                return
        self.safe_abort()
        event.accept()

    def _should_confirm_close(self) -> bool:
        """Override v subclassech — True pokud je probíhá kritická fáze."""
        return self._bundle is not None

    def _close_confirmation_message(self) -> str:
        return (
            "Wizard má aktivní spojení se Spotem nebo běžící akci. "
            "Po zavření se vše bezpečně ukončí."
        )


__all__ = ["SpotWizard"]
