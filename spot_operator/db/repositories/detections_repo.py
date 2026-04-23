"""CRUD nad tabulkou plate_detections."""

from __future__ import annotations

from typing import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from spot_operator.db.models import PlateDetection


def insert_many(session: Session, rows: Iterable[dict]) -> int:
    """Vloží batch detekcí. Používá ON CONFLICT DO NOTHING kvůli idempotenci re-runu.

    Každý row musí mít klíče: photo_id, engine_name, plate_text, detection_confidence,
    text_confidence, bbox, engine_version.
    """
    rows = list(rows)
    if not rows:
        return 0
    stmt = pg_insert(PlateDetection).values(rows).on_conflict_do_nothing(
        index_elements=["photo_id", "engine_name", "plate_text"]
    )
    result = session.execute(stmt)
    return result.rowcount or 0


def list_for_photo(session: Session, photo_id: int) -> Sequence[PlateDetection]:
    return (
        session.execute(
            select(PlateDetection)
            .where(PlateDetection.photo_id == photo_id)
            .order_by(PlateDetection.text_confidence.desc().nullslast())
        )
        .scalars()
        .all()
    )


def list_by_plate(session: Session, plate_text: str) -> Sequence[PlateDetection]:
    plate_text = plate_text.upper()
    return (
        session.execute(
            select(PlateDetection)
            .where(PlateDetection.plate_text == plate_text)
            .order_by(PlateDetection.created_at.desc())
        )
        .scalars()
        .all()
    )


def delete_for_photo_engine(session: Session, photo_id: int, engine_name: str) -> int:
    """Pro re-OCR: smaže předchozí detekce daného engine na dané fotce."""
    from sqlalchemy import delete as sqldelete

    result = session.execute(
        sqldelete(PlateDetection).where(
            PlateDetection.photo_id == photo_id,
            PlateDetection.engine_name == engine_name,
        )
    )
    return result.rowcount or 0


def delete_for_photo(session: Session, photo_id: int) -> int:
    from sqlalchemy import delete as sqldelete

    result = session.execute(
        sqldelete(PlateDetection).where(PlateDetection.photo_id == photo_id)
    )
    return result.rowcount or 0


def delete_for_run(session: Session, run_id: int) -> int:
    from sqlalchemy import delete as sqldelete
    from spot_operator.db.models import Photo

    result = session.execute(
        sqldelete(PlateDetection).where(
            PlateDetection.photo_id.in_(
                select(Photo.id).where(Photo.run_id == run_id)
            )
        )
    )
    return result.rowcount or 0


__all__ = [
    "insert_many",
    "list_for_photo",
    "list_by_plate",
    "delete_for_photo_engine",
    "delete_for_photo",
    "delete_for_run",
]
