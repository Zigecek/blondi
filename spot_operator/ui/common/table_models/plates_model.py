"""Stránkovaný model pro tabulku SPZ (CRUD)."""

from __future__ import annotations

from typing import Optional

from spot_operator.db.enums import PlateStatus
from spot_operator.db.repositories import plates_repo
from spot_operator.db.repositories.plates_repo import PlateRow
from spot_operator.ui.common.table_models.paged_table_model import PagedTableModel


_COLUMNS: tuple[str, ...] = ("ID", "SPZ", "Status", "Platí do", "Poznámka")
_SORT_KEYS: tuple[Optional[str], ...] = (
    "id", "plate_text", "status", "valid_until", None,
)


class PlatesModel(PagedTableModel):
    """Model registru SPZ se 2 filtry (status, text_contains)."""

    def __init__(self, parent=None) -> None:
        self._status: PlateStatus | None = None
        self._text_contains: str | None = None
        super().__init__(parent)

    # ---- Filtry ----

    def set_filters(
        self,
        *,
        status: PlateStatus | None,
        text_contains: str | None,
    ) -> None:
        text_normalized = (text_contains or "").strip() or None
        if status == self._status and text_normalized == self._text_contains:
            return
        self._status = status
        self._text_contains = text_normalized
        self.reset()

    # ---- Abstract impl ----

    def columns(self) -> tuple[str, ...]:
        return _COLUMNS

    def sort_keys(self) -> tuple[Optional[str], ...]:
        return _SORT_KEYS

    def default_sort_column(self) -> str:
        return "plate_text"

    def default_sort_desc(self) -> bool:
        return False

    def cell(self, row: PlateRow, col: int) -> str:
        if col == 0:
            return str(row.id)
        if col == 1:
            return row.plate_text
        if col == 2:
            return row.status
        if col == 3:
            return row.valid_until.isoformat() if row.valid_until else ""
        if col == 4:
            return row.note or ""
        return ""

    def initial_load(
        self, session, *, limit: int, sort_by: str, sort_desc: bool,
    ) -> tuple[int, list[PlateRow]]:
        total = plates_repo.count(
            session, status=self._status, text_contains=self._text_contains,
        )
        rows = plates_repo.list_page(
            session,
            status=self._status,
            text_contains=self._text_contains,
            offset=0,
            limit=limit,
            sort_by=sort_by,
            sort_desc=sort_desc,
        )
        return total, rows

    def fetch_page(
        self, session, *, offset: int, limit: int, sort_by: str, sort_desc: bool,
    ) -> list[PlateRow]:
        return plates_repo.list_page(
            session,
            status=self._status,
            text_contains=self._text_contains,
            offset=offset,
            limit=limit,
            sort_by=sort_by,
            sort_desc=sort_desc,
        )


__all__ = ["PlatesModel"]
