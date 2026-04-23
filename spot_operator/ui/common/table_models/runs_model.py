"""Stránkovaný model pro tabulku Běhy (CRUD)."""

from __future__ import annotations

from typing import Optional

from spot_operator.db.repositories import runs_repo
from spot_operator.db.repositories.runs_repo import RunRow
from spot_operator.ui.common.table_models.paged_table_model import PagedTableModel


_COLUMNS: tuple[str, ...] = (
    "ID", "Kód", "Mapa", "Start", "Konec", "Status", "Checkpointů",
)
_SORT_KEYS: tuple[Optional[str], ...] = (
    "id", "run_code", None, "start_time", "end_time", "status", None,
)


class RunsModel(PagedTableModel):
    """Model běhů."""

    def columns(self) -> tuple[str, ...]:
        return _COLUMNS

    def sort_keys(self) -> tuple[Optional[str], ...]:
        return _SORT_KEYS

    def default_sort_column(self) -> str:
        return "start_time"

    def default_sort_desc(self) -> bool:
        return True

    def cell(self, row: RunRow, col: int) -> str:
        if col == 0:
            return str(row.id)
        if col == 1:
            return row.run_code
        if col == 2:
            return row.map_name_snapshot or ""
        if col == 3:
            return (
                row.start_time.isoformat(timespec="seconds")
                if row.start_time
                else ""
            )
        if col == 4:
            return (
                row.end_time.isoformat(timespec="seconds") if row.end_time else ""
            )
        if col == 5:
            return row.status
        if col == 6:
            return f"{row.checkpoints_reached}/{row.checkpoints_total}"
        return ""

    def initial_load(
        self, session, *, limit: int, sort_by: str, sort_desc: bool,
    ) -> tuple[int, list[RunRow]]:
        total = runs_repo.count(session)
        rows = runs_repo.list_page(
            session,
            offset=0,
            limit=limit,
            sort_by=sort_by,
            sort_desc=sort_desc,
        )
        return total, rows

    def fetch_page(
        self, session, *, offset: int, limit: int, sort_by: str, sort_desc: bool,
    ) -> list[RunRow]:
        return runs_repo.list_page(
            session,
            offset=offset,
            limit=limit,
            sort_by=sort_by,
            sort_desc=sort_desc,
        )


__all__ = ["RunsModel"]
