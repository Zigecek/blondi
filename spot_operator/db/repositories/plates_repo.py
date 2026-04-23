"""CRUD operace nad tabulkou license_plates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from spot_operator.db.enums import PlateStatus
from spot_operator.db.models import LicensePlate


# --- DTO pro CRUD ---

@dataclass(frozen=True)
class PlateRow:
    """DTO pro řádek v tabulce SPZ."""

    id: int
    plate_text: str
    status: str
    valid_until: date | None
    note: str | None


_SORTABLE_PLATE_COLS: frozenset[str] = frozenset(
    {"id", "plate_text", "status", "valid_until"}
)


def normalize_plate_text(text: str) -> str:
    """Normalizuje SPZ: uppercase, odstraní mezery, pomlčky, prázdné znaky."""
    if not text:
        return ""
    return "".join(ch for ch in text.upper() if ch.isalnum())


def get_by_text(session: Session, plate_text: str) -> LicensePlate | None:
    plate_text = normalize_plate_text(plate_text)
    return session.execute(
        select(LicensePlate).where(LicensePlate.plate_text == plate_text)
    ).scalar_one_or_none()


def list_all(
    session: Session,
    *,
    status: PlateStatus | None = None,
    text_contains: str | None = None,
    valid_from: date | None = None,
    valid_until_before: date | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> Sequence[LicensePlate]:
    stmt = select(LicensePlate).order_by(LicensePlate.plate_text)
    if status is not None:
        stmt = stmt.where(LicensePlate.status == status)
    if text_contains:
        stmt = stmt.where(LicensePlate.plate_text.contains(text_contains.upper()))
    if valid_from is not None:
        stmt = stmt.where(LicensePlate.valid_until >= valid_from)
    if valid_until_before is not None:
        stmt = stmt.where(LicensePlate.valid_until < valid_until_before)
    if offset:
        stmt = stmt.offset(offset)
    if limit:
        stmt = stmt.limit(limit)
    return session.execute(stmt).scalars().all()


def _apply_filters(stmt, status, text_contains):
    if status is not None:
        stmt = stmt.where(LicensePlate.status == status)
    if text_contains:
        stmt = stmt.where(LicensePlate.plate_text.contains(text_contains.upper()))
    return stmt


def list_page(
    session: Session,
    *,
    status: PlateStatus | None = None,
    text_contains: str | None = None,
    offset: int = 0,
    limit: int = 100,
    sort_by: str = "plate_text",
    sort_desc: bool = False,
) -> list[PlateRow]:
    """Stránka SPZ jako lightweight DTO. Bez ``note`` truncation — `note` je Text."""
    if sort_by not in _SORTABLE_PLATE_COLS:
        sort_by = "plate_text"
    col = getattr(LicensePlate, sort_by)
    order = col.desc() if sort_desc else col.asc()

    stmt = (
        select(LicensePlate)
        .order_by(order, LicensePlate.id.asc())
        .offset(max(offset, 0))
        .limit(max(limit, 1))
    )
    stmt = _apply_filters(stmt, status, text_contains)
    rows = session.execute(stmt).scalars().all()
    return [
        PlateRow(
            id=r.id,
            plate_text=r.plate_text,
            status=r.status.value,
            valid_until=r.valid_until,
            note=r.note,
        )
        for r in rows
    ]


def count(
    session: Session,
    *,
    status: PlateStatus | None = None,
    text_contains: str | None = None,
) -> int:
    """Počet SPZ odpovídajících filtrům."""
    stmt = select(func.count(LicensePlate.id))
    stmt = _apply_filters(stmt, status, text_contains)
    return int(session.execute(stmt).scalar_one() or 0)


def upsert(
    session: Session,
    *,
    plate_text: str,
    status: PlateStatus = PlateStatus.unknown,
    valid_until: date | None = None,
    note: str | None = None,
) -> LicensePlate:
    """Upsert SPZ. Atomicky — přes ON CONFLICT DO UPDATE (PR-07 FIND-027).

    Dřívější check-then-act vzor měl TOCTOU race mezi dvěma paralelními
    volajícími. Teď je insert+update jeden atomický statement v PG.
    """
    plate_text = normalize_plate_text(plate_text)
    if not plate_text:
        raise ValueError("Plate text must not be empty.")
    values = {
        "plate_text": plate_text,
        "status": status.value if hasattr(status, "value") else status,
        "valid_until": valid_until,
    }
    if note is not None:
        values["note"] = note
    stmt = pg_insert(LicensePlate).values(**values)
    update_values = {
        "status": stmt.excluded.status,
        "valid_until": stmt.excluded.valid_until,
    }
    if note is not None:
        update_values["note"] = stmt.excluded.note
    stmt = stmt.on_conflict_do_update(
        index_elements=["plate_text"],
        set_=update_values,
    )
    session.execute(stmt)
    # Reload aktuální row pro vrácení — volající může chtít ID.
    return get_by_text(session, plate_text)  # type: ignore[return-value]


def delete(session: Session, plate_id: int) -> bool:
    plate = session.get(LicensePlate, plate_id)
    if plate is None:
        return False
    session.delete(plate)
    return True


def set_status(session: Session, plate_id: int, status: PlateStatus) -> bool:
    plate = session.get(LicensePlate, plate_id)
    if plate is None:
        return False
    plate.status = status
    return True


__all__ = [
    "PlateRow",
    "normalize_plate_text",
    "get_by_text",
    "list_all",
    "list_page",
    "count",
    "upsert",
    "delete",
    "set_status",
]
