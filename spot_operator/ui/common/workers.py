"""Qt worker na obecné blokující operace (connect, fiducial check, save map...)."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QThread, Signal


class FunctionWorker(QThread):
    """Spustí libovolnou funkci v background threadu a emituje výsledek/chybu.

    Signal `finished_ok(object)` s návratovou hodnotou,
    `failed(str)` s chybovou zprávou. Funkce musí být thread-safe (netýká se Qt UI).
    """

    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, func: Callable[..., Any], *args: Any, **kwargs: Any):
        super().__init__()
        self._func = func
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:  # noqa: D401
        try:
            result = self._func(*self._args, **self._kwargs)
            self.finished_ok.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


__all__ = ["FunctionWorker"]
