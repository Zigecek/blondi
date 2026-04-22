"""CRUD operace nad tabulkou license_plates."""

from __future__ import annotations

from datetime import date
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from spot_operator.db.enums import PlateStatus
from spot_operator.db.models import LicensePlate


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


def upsert(
    session: Session,
    *,
    plate_text: str,
    status: PlateStatus = PlateStatus.unknown,
    valid_until: date | None = None,
    note: str | None = None,
) -> LicensePlate:
    plate_text = normalize_plate_text(plate_text)
    if not plate_text:
        raise ValueError("Plate text must not be empty.")
    existing = get_by_text(session, plate_text)
    if existing:
        existing.status = status
        existing.valid_until = valid_until
        if note is not None:
            existing.note = note
        return existing
    plate = LicensePlate(
        plate_text=plate_text,
        status=status,
        valid_until=valid_until,
        note=note,
    )
    session.add(plate)
    return plate


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
    "normalize_plate_text",
    "get_by_text",
    "list_all",
    "upsert",
    "delete",
    "set_status",
]
