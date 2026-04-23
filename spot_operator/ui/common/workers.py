"""Qt workery pro BG operace.

- ``FunctionWorker`` — obecný wrapper libovolné funkce (connect, fiducial check,
  OCR fallback). Stejný interface jako dřív, jen rozšířený o ``stop_and_wait``.
- ``DbQueryWorker`` — specializace pro DB dotaz: otevře ``Session()`` v
  pracovním threadu, zavolá ``fn(session)`` a vrátí výsledek přes signál
  ``ok(object)``. Používá se v CRUD tabulkách a detail dialozích tak, aby UI
  thread nikdy nečekal na DB.

Oba pracovníci mají ``stop_and_wait(timeout_ms)``, který:
- odpojí všechny sloty (aby pozdní signál nespadl na zničený parent),
- požádá thread o ukončení a počká do timeoutu.

Volá se z ``closeEvent`` dialogu / widgetu, jehož jsme parent, abychom
zabránili Qt chybě *QThread: Destroyed while thread is still running*.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal

from spot_operator.constants import CRUD_WORKER_STOP_TIMEOUT_MS
from spot_operator.db.engine import Session
from spot_operator.logging_config import get_logger

_log = get_logger(__name__)


class _WorkerBase(QThread):
    """Společná základna — životní cyklus signálů."""

    def stop_and_wait(self, timeout_ms: int = CRUD_WORKER_STOP_TIMEOUT_MS) -> None:
        """Odpojí sloty a počká na ukončení threadu (nebo timeout)."""
        for sig in self._lifecycle_signals():
            try:
                sig.disconnect()
            except (TypeError, RuntimeError):
                pass
        if self.isRunning():
            self.requestInterruption()
            self.wait(timeout_ms)

    def _lifecycle_signals(self) -> tuple[Signal, ...]:  # pragma: no cover - abstract
        return ()


class FunctionWorker(_WorkerBase):
    """Spustí libovolnou funkci v background threadu a emituje výsledek/chybu.

    Signal ``finished_ok(object)`` s návratovou hodnotou,
    ``failed(str)`` s chybovou zprávou. Funkce musí být thread-safe
    (netýká se Qt UI).
    """

    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        func: Callable[..., Any],
        *args: Any,
        parent: QObject | None = None,
        **kwargs: Any,
    ):
        super().__init__(parent)
        self._func = func
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:  # noqa: D401
        try:
            result = self._func(*self._args, **self._kwargs)
            self.finished_ok.emit(result)
        except Exception as exc:  # pragma: no cover - defensive
            _log.exception("FunctionWorker failed in %s", self._func)
            self.failed.emit(str(exc))

    def _lifecycle_signals(self) -> tuple[Signal, ...]:
        return (self.finished_ok, self.failed)


class DbQueryWorker(_WorkerBase):
    """Spustí DB dotaz v BG threadu.

    ``fn`` dostane čerstvou ``Session()`` a vrátí libovolný výsledek.
    Výsledek je emitován přes ``ok(object)``. Chyba přes ``failed(str)``.

    Používej takto:

    >>> worker = DbQueryWorker(
    ...     lambda s: photos_repo.list_page_light(s, offset=0, limit=100),
    ...     parent=self,
    ... )
    >>> worker.ok.connect(self._on_rows)
    >>> worker.failed.connect(self._on_err)
    >>> worker.start()
    """

    ok = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        fn: Callable[..., Any],
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:  # noqa: D401
        try:
            with Session() as s:
                result = self._fn(s)
            self.ok.emit(result)
        except Exception as exc:  # pragma: no cover - defensive
            _log.exception("DbQueryWorker failed")
            self.failed.emit(str(exc))

    def _lifecycle_signals(self) -> tuple[Signal, ...]:
        return (self.ok, self.failed)


__all__ = ["FunctionWorker", "DbQueryWorker"]
