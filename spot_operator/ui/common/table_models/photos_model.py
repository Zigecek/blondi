"""Stránkovaný model pro tabulku Fotky (CRUD).

Lze filtrovat na ``run_id`` (pro ``RunDetailDialog``) nebo použít bez filtru
(global Photos tab).
"""

from __future__ import annotations

from typing import Optional

from spot_operator.db.repositories import photos_repo
from spot_operator.db.repositories.photos_repo import PhotoRow
from spot_operator.ui.common.table_models.paged_table_model import PagedTableModel


_COLUMNS: tuple[str, ...] = (
    "ID", "Run", "Checkpoint", "Kamera", "OCR", "Přečteno", "Zachyceno",
)
_SORT_KEYS: tuple[Optional[str], ...] = (
    "id", "run_id", "checkpoint_name", "camera_source", "ocr_status", None, "captured_at",
)


class PhotosModel(PagedTableModel):
    """Model fotek — s volitelným ``run_id`` filtrem."""

    def __init__(self, parent=None, *, run_id: int | None = None) -> None:
        self._run_id = run_id
        super().__init__(parent)

    # ---- Filtr ----

    def set_run_id(self, run_id: int | None) -> None:
        if run_id == self._run_id:
            return
        self._run_id = run_id
        self.reset()

    # ---- Abstract impl ----

    def columns(self) -> tuple[str, ...]:
        return _COLUMNS

    def sort_keys(self) -> tuple[Optional[str], ...]:
        return _SORT_KEYS

    def default_sort_column(self) -> str:
        return "captured_at"

    def default_sort_desc(self) -> bool:
        return True

    def cell(self, row: PhotoRow, col: int) -> str:
        if col == 0:
            return str(row.id)
        if col == 1:
            return str(row.run_id)
        if col == 2:
            return row.checkpoint_name or ""
        if col == 3:
            return row.camera_source
        if col == 4:
            return row.ocr_status
        if col == 5:
            # PR-07 FIND-030: repo vrací None pro plate_text=NULL, UI formátuje "?".
            if not row.plates:
                return "—"
            return ", ".join(p if p else "?" for p in row.plates)
        if col == 6:
            return (
                row.captured_at.isoformat(timespec="seconds")
                if row.captured_at
                else ""
            )
        return ""

    def initial_load(
        self, session, *, limit: int, sort_by: str, sort_desc: bool,
    ) -> tuple[int, list[PhotoRow]]:
        run_id = self._run_id
        total = photos_repo.count_photos(session, run_id=run_id)
        rows = photos_repo.list_page_light(
            session,
            run_id=run_id,
            offset=0,
            limit=limit,
            sort_by=sort_by,
            sort_desc=sort_desc,
        )
        return total, rows

    def fetch_page(
        self, session, *, offset: int, limit: int, sort_by: str, sort_desc: bool,
    ) -> list[PhotoRow]:
        return photos_repo.list_page_light(
            session,
            run_id=self._run_id,
            offset=offset,
            limit=limit,
            sort_by=sort_by,
            sort_desc=sort_desc,
        )


__all__ = ["PhotosModel"]
