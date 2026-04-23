"""SpotWizard — base QWizard s unified E-Stop a close-guard chováním."""

from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import QWidget, QWizard

from spot_operator.config import AppConfig
from spot_operator.constants import UI_WIZARD_MIN_HEIGHT, UI_WIZARD_MIN_WIDTH
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
        # True pokud wizard sám bundle vytvořil (ConnectPage uvnitř).
        # False pokud bundle dodal MainWindow — při close ho NEdisconnect.
        self._bundle_owned: bool = True
        self._estop_callback: Optional[Callable[[], None]] = None
        self._estop_release_callback: Optional[Callable[[], None]] = None
        self._flow_state: Any | None = None

        self.setWindowTitle(window_title)
        self.setWizardStyle(QWizard.ModernStyle)
        self.setOption(QWizard.NoBackButtonOnStartPage, True)
        self.setOption(QWizard.NoCancelButton, False)
        self.setOption(QWizard.HaveHelpButton, False)
        self.setMinimumSize(UI_WIZARD_MIN_WIDTH, UI_WIZARD_MIN_HEIGHT)

        self._f1_shortcut = QShortcut(QKeySequence("F1"), self)
        # PR-09 FIND-137: WindowShortcut místo ApplicationShortcut, aby F1
        # v jiném okně (CRUD) neaktivovalo E-Stop.
        self._f1_shortcut.setContext(Qt.WindowShortcut)
        self._f1_shortcut.activated.connect(self.trigger_estop)

        self.setButtonText(QWizard.NextButton, "Další ▶")
        self.setButtonText(QWizard.BackButton, "◀ Zpět")
        self.setButtonText(QWizard.CancelButton, "Zrušit")
        self.setButtonText(QWizard.FinishButton, "Dokončit ✓")

    # ---- Public API pro stránky ----

    @property
    def config(self) -> AppConfig:
        return self._config

    def set_bundle(self, bundle: Any, *, owned: bool = True) -> None:
        """Nastaví aktivní bundle. ``owned=False`` znamená externí bundle
        (typicky z MainWindow) — při close ho nebudeme disconnectovat."""
        self._bundle = bundle
        self._bundle_owned = bool(owned)

    def bundle(self) -> Any | None:
        return self._bundle

    def set_flow_state(self, state: Any) -> None:
        self._flow_state = state

    def flow_state(self) -> Any | None:
        return self._flow_state

    def set_estop_callback(
        self,
        on_trigger: Optional[Callable[[], None]],
        on_release: Optional[Callable[[], None]] = None,
    ) -> None:
        """Registruje callbacky pro F1 shortcut (alternativa k klikání widgetu).

        Obě volby mohou být ``None`` — stránka při teardown volá
        ``set_estop_callback(None, None)`` pro explicit reset, aby F1
        na další stránce nezavolal zničené handlery.
        """
        self._estop_callback = on_trigger
        self._estop_release_callback = on_release

    def trigger_estop(self) -> None:
        """F1 shortcut handler. V triggered stavu = release, jinak = trigger.

        Preferovaně deleguje na floating E-Stop widget aktuální stránky (ten
        zná svůj stav). Fallback: naše callbacks + bundle.estop přímo.
        Pokud ani widget ani bundle.estop nejsou dostupné, loguje ERROR a
        dává user-facing feedback (PR-01 FIND-134) — F1 nesmí být silent no-op.
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
            return

        # Nikdo nepřijal — E-Stop není v tomto stavu dostupný. Nemůžeme
        # silently ignorovat (safety-critical); zalogovat ERROR a zobrazit
        # neblokující status bar hint / window title hint. Dialog by byl
        # přes příliš — user prostě čekal E-Stop, musí vidět že nezabralo.
        _log.error(
            "E-Stop stisknut (F1), ale není dostupný — robot není připojen nebo "
            "je v kroku bez E-Stop endpointu. Připojte se ke Spotovi a zkuste znovu."
        )
        try:
            # Krátký vizuální hint přes window title flash.
            original_title = self.windowTitle()
            self.setWindowTitle(f"{original_title} — E-Stop není dostupný!")
            from PySide6.QtCore import QTimer
            QTimer.singleShot(2000, lambda: self.setWindowTitle(original_title))
        except Exception:
            pass

    # ---- Cleanup / close ----

    def safe_abort(self) -> bool:
        """Uvolní všechny prostředky. Voláno při zavření wizardu nebo chybě.

        Vrací True pokud cleanup proběhl bez chyb, False jinak (PR-09 FIND-135
        — closeEvent může podle toho rozhodnout).

        Pořadí:
          1. Teardown aktuální stránky (zastaví image pipeline, run threads,
             skryje E-Stop widget, abortuje recording service). Qt nevolá
             `cleanupPage` při `closeEvent`, proto to musíme zařídit sami.
          2. Disconnect bundle (lease release, estop shutdown, SDK session).
        """
        ok = True
        # 1) Teardown aktuální stránky (pokud ho implementuje).
        current = self.currentPage()
        if current is not None and hasattr(current, "_teardown"):
            try:
                current._teardown()
            except Exception as exc:
                _log.exception("Page _teardown failed: %s", exc)
                ok = False

        # 2) Disconnect bundle — JEN pokud ho wizard vlastní. Externí bundle
        # z MainWindow má svůj vlastní lifecycle (sdílený napříč wizardy).
        if self._bundle is not None:
            if self._bundle_owned:
                try:
                    self._bundle.disconnect()
                except Exception as exc:
                    _log.exception("bundle.disconnect failed: %s", exc)
                    ok = False
                self._bundle = None
            else:
                _log.info(
                    "Wizard close: externí bundle ponecháván pro MainWindow."
                )
                self._bundle = None
        return ok

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
        ok = self.safe_abort()
        if not ok:
            # PR-09 FIND-135: pokud cleanup selhal, nabídnout retry /
            # force close. User rozhodne — nechceme silent leak.
            from spot_operator.ui.wizards.messages import (
                CLEANUP_FAILED_MESSAGE,
                CLEANUP_FAILED_TITLE,
            )

            if not confirm_dialog(
                self,
                CLEANUP_FAILED_TITLE,
                CLEANUP_FAILED_MESSAGE,
                destructive=True,
            ):
                event.ignore()
                return
        event.accept()

    def _should_confirm_close(self) -> bool:
        """Override v subclassech — True pokud probíhá kritická fáze.

        PR-09 FIND-133: confirm dialog jen při RUNNING/ABORTING/RETURNING
        lifecycle, ne vždy když bundle existuje. User otevře wizard,
        klikne X → žádné zbytečné "Opravdu zavřít?" na prázdné stránce.
        """
        from spot_operator.ui.wizards.state import (
            WIZARD_LIFECYCLE_ABORTING,
            WIZARD_LIFECYCLE_RETURNING,
            WIZARD_LIFECYCLE_RUNNING,
        )

        state = self._flow_state
        lifecycle = getattr(state, "lifecycle", None)
        if lifecycle in {
            WIZARD_LIFECYCLE_RUNNING,
            WIZARD_LIFECYCLE_ABORTING,
            WIZARD_LIFECYCLE_RETURNING,
        }:
            return True
        # Recording má vlastní sémantiku — is_recording flag na service.
        service = getattr(state, "recording_service", None)
        if service is not None and getattr(service, "is_recording", False):
            return True
        return False

    def _close_confirmation_message(self) -> str:
        return (
            "Wizard má aktivní spojení se Spotem nebo běžící akci. "
            "Po zavření se vše bezpečně ukončí."
        )


__all__ = ["SpotWizard"]
