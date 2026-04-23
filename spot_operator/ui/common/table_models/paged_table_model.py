"""Abstraktní stránkovaný ``QAbstractTableModel`` s lazy fetchMore + async DB.

Podtřídy (viz ``photos_model.py``, ``plates_model.py``, ``runs_model.py``)
musí implementovat:

- ``columns()`` — pořadí a názvy sloupců,
- ``sort_keys()`` — mapování sloupec → název DB sloupce (nebo ``None`` = nelze
  sortovat po tomto sloupci),
- ``default_sort_column() -> str`` + ``default_sort_desc() -> bool``,
- ``cell(row_dto, col_idx) -> str`` — převod DTO na text buňky,
- ``initial_load(session) -> tuple[int, list]`` — vrátí (total_count, první stránku),
- ``fetch_page(session, offset, limit) -> list`` — další stránka.

Thread safety: ``initial_load`` / ``fetch_page`` běží v BG threadu. Musí být
self-contained (nečíst atributy modelu které může UI thread měnit — sort a
filtry se načtou do argumentů fn přes closure v podtřídě).

Race protection: každý fetch má ``request_id``. Pokud mezitím proběhne
``reset()`` nebo ``sort()``, starší výsledek se zahodí.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from spot_operator.constants import CRUD_PAGE_SIZE, CRUD_WORKER_STOP_TIMEOUT_MS
from spot_operator.logging_config import get_logger
from spot_operator.ui.common.workers import DbQueryWorker

_log = get_logger(__name__)


class PagedTableModel(QAbstractTableModel):
    """Základ pro CRUD stránkované tabulky."""

    PAGE_SIZE: int = CRUD_PAGE_SIZE

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[Any] = []
        self._total: int = 0
        self._fetching: bool = False
        self._request_id: int = 0
        self._workers: list[DbQueryWorker] = []
        self._sort_by: str = self.default_sort_column()
        self._sort_desc: bool = self.default_sort_desc()
        self._error: str | None = None

    # ---- Qt model API ----

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.columns())

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.DisplayRole,
    ) -> Any:
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal and 0 <= section < len(self.columns()):
            return self.columns()[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid() or role != Qt.DisplayRole:
            return None
        row = index.row()
        col = index.column()
        if not (0 <= row < len(self._rows)):
            return None
        return self.cell(self._rows[row], col)

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    # ---- Pagination ----

    def canFetchMore(self, parent: QModelIndex) -> bool:
        if parent.isValid():
            return False
        if self._fetching:
            return False
        return len(self._rows) < self._total

    def fetchMore(self, parent: QModelIndex) -> None:
        if parent.isValid() or self._fetching:
            return
        if len(self._rows) >= self._total:
            return
        offset = len(self._rows)
        self._fetching = True
        self._request_id += 1
        req_id = self._request_id
        sort_by = self._sort_by
        sort_desc = self._sort_desc

        worker = DbQueryWorker(
            lambda s: self.fetch_page(
                s, offset=offset, limit=self.PAGE_SIZE,
                sort_by=sort_by, sort_desc=sort_desc,
            ),
            parent=self,
        )
        worker.ok.connect(lambda rows, rid=req_id, w=worker: self._on_page(rid, rows, w))
        worker.failed.connect(lambda err, rid=req_id, w=worker: self._on_fail(rid, err, w))
        worker.finished.connect(worker.deleteLater)
        self._workers.append(worker)
        worker.start()

    # ---- Reset / Sort ----

    def reset(self) -> None:
        """Znovu načte count + první stránku. Volá se při otevření tabu,
        po změně filtru, nebo po externí invalidaci (např. úspěšný upsert)."""
        self._request_id += 1
        req_id = self._request_id
        self.beginResetModel()
        self._rows = []
        self._total = 0
        self._error = None
        self._fetching = True
        self.endResetModel()

        sort_by = self._sort_by
        sort_desc = self._sort_desc

        worker = DbQueryWorker(
            lambda s: self.initial_load(
                s, limit=self.PAGE_SIZE, sort_by=sort_by, sort_desc=sort_desc,
            ),
            parent=self,
        )
        worker.ok.connect(lambda res, rid=req_id, w=worker: self._on_initial(rid, res, w))
        worker.failed.connect(lambda err, rid=req_id, w=worker: self._on_fail(rid, err, w))
        worker.finished.connect(worker.deleteLater)
        self._workers.append(worker)
        worker.start()

    def sort(self, column: int, order: Qt.SortOrder = Qt.AscendingOrder) -> None:
        keys = self.sort_keys()
        if column < 0 or column >= len(keys):
            return
        key = keys[column]
        if key is None:
            return
        new_desc = order == Qt.DescendingOrder
        if key == self._sort_by and new_desc == self._sort_desc:
            return
        self._sort_by = key
        self._sort_desc = new_desc
        self.reset()

    # ---- Worker callbacks ----

    def _on_initial(self, req_id: int, result: Any, worker: DbQueryWorker) -> None:
        if req_id != self._request_id:
            return
        try:
            total, rows = result
        except (TypeError, ValueError):
            _log.warning("Initial load vrátil nečekaný formát: %r", result)
            self._fetching = False
            return
        self.beginResetModel()
        self._total = int(total or 0)
        self._rows = list(rows or [])
        self.endResetModel()
        self._fetching = False

    def _on_page(self, req_id: int, rows: list, worker: DbQueryWorker) -> None:
        if req_id != self._request_id:
            return
        rows = list(rows or [])
        if not rows:
            self._fetching = False
            return
        start = len(self._rows)
        end = start + len(rows) - 1
        self.beginInsertRows(QModelIndex(), start, end)
        self._rows.extend(rows)
        self.endInsertRows()
        self._fetching = False

    def _on_fail(self, req_id: int, err: str, worker: DbQueryWorker) -> None:
        if req_id != self._request_id:
            return
        _log.warning("Paged model DB query failed: %s", err)
        self._error = err
        self._fetching = False

    # ---- Cleanup ----

    def stop_all_workers(self, timeout_ms: int = CRUD_WORKER_STOP_TIMEOUT_MS) -> None:
        """Volat z ``closeEvent`` parent widgetu. Odpojí + počká na workery."""
        self._request_id += 1  # invalidate all pending results
        for w in list(self._workers):
            w.stop_and_wait(timeout_ms)
        self._workers.clear()

    # ---- Přístup k řádkům pro double-click handlery ----

    def row_at(self, index: int) -> Any | None:
        if 0 <= index < len(self._rows):
            return self._rows[index]
        return None

    def total(self) -> int:
        return self._total

    def loaded(self) -> int:
        return len(self._rows)

    def error(self) -> str | None:
        return self._error

    # ---- Abstract API (podtřídy) ----

    @abstractmethod
    def columns(self) -> tuple[str, ...]: ...

    @abstractmethod
    def sort_keys(self) -> tuple[str | None, ...]: ...

    @abstractmethod
    def default_sort_column(self) -> str: ...

    @abstractmethod
    def default_sort_desc(self) -> bool: ...

    @abstractmethod
    def cell(self, row_dto: Any, col: int) -> str: ...

    @abstractmethod
    def initial_load(
        self, session, *, limit: int, sort_by: str, sort_desc: bool,
    ) -> tuple[int, list]: ...

    @abstractmethod
    def fetch_page(
        self, session, *, offset: int, limit: int, sort_by: str, sort_desc: bool,
    ) -> list: ...


def apply_default_sort_indicator(view, model: PagedTableModel) -> None:  # noqa: ANN001
    """Nastaví sort indicator v headeru podle modelu ``default_sort_*``.

    Volej **před** ``view.setSortingEnabled(True)``, jinak Qt při enable
    zavolá ``model.sort(0, Qt.AscendingOrder)`` (defaultní indicator) a
    přepíše tím naše defaulty.
    """
    keys = model.sort_keys()
    try:
        col = keys.index(model.default_sort_column())
    except ValueError:
        col = 0
    order = Qt.DescendingOrder if model.default_sort_desc() else Qt.AscendingOrder
    view.horizontalHeader().setSortIndicator(col, order)


__all__ = ["PagedTableModel", "apply_default_sort_indicator"]
