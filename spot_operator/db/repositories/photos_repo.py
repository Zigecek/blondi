"""CRUD nad tabulkou photos — včetně claim_next_pending pro OCR worker."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from spot_operator.db.enums import OcrStatus
from spot_operator.db.models import Photo


def insert(
    session: Session,
    *,
    run_id: int,
    checkpoint_name: str | None,
    camera_source: str,
    image_bytes: bytes,
    width: int | None = None,
    height: int | None = None,
    image_mime: str = "image/jpeg",
) -> Photo:
    photo = Photo(
        run_id=run_id,
        checkpoint_name=checkpoint_name,
        camera_source=camera_source,
        image_bytes=image_bytes,
        image_mime=image_mime,
        width=width,
        height=height,
        ocr_status=OcrStatus.pending,
    )
    session.add(photo)
    session.flush()
    return photo


def get(session: Session, photo_id: int) -> Photo | None:
    return session.get(Photo, photo_id)


def list_for_run(session: Session, run_id: int) -> Sequence[Photo]:
    return (
        session.execute(
            select(Photo).where(Photo.run_id == run_id).order_by(Photo.captured_at)
        )
        .scalars()
        .all()
    )


def get_last_photo_for_plate(session: Session, plate_text: str) -> Photo | None:
    """Poslední fotka (captured_at DESC), na které OCR detekoval danou SPZ.

    JOIN přes `plate_detections.plate_text` (registr `license_plates` není
    FK; detekce nese čistě text. Kdokoli hledá fotky pro SPZ, filtruje po
    textu.)
    """
    from spot_operator.db.models import PlateDetection

    normalized = (plate_text or "").strip().upper()
    if not normalized:
        return None
    stmt = (
        select(Photo)
        .join(PlateDetection, PlateDetection.photo_id == Photo.id)
        .where(PlateDetection.plate_text == normalized)
        .order_by(Photo.captured_at.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def claim_next_pending(session: Session, worker_id: str) -> Photo | None:
    """Najde jednu fotku s ocr_status='pending' a atomicky ji zamkne.

    Používá FOR UPDATE SKIP LOCKED, takže je bezpečné spouštět paralelně více workerů.
    Vrací Photo s ocr_status = 'processing' a ocr_locked_by = worker_id, nebo None.
    """
    stmt = (
        select(Photo)
        .where(Photo.ocr_status == OcrStatus.pending)
        .order_by(Photo.captured_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    photo = session.execute(stmt).scalar_one_or_none()
    if photo is None:
        return None
    photo.ocr_status = OcrStatus.processing
    photo.ocr_locked_by = worker_id
    photo.ocr_locked_at = datetime.now(timezone.utc)
    return photo


def mark_done(session: Session, photo_id: int) -> None:
    session.execute(
        update(Photo)
        .where(Photo.id == photo_id)
        .values(
            ocr_status=OcrStatus.done,
            ocr_processed_at=datetime.now(timezone.utc),
            ocr_locked_by=None,
            ocr_locked_at=None,
        )
    )


def mark_failed(session: Session, photo_id: int) -> None:
    session.execute(
        update(Photo)
        .where(Photo.id == photo_id)
        .values(
            ocr_status=OcrStatus.failed,
            ocr_processed_at=datetime.now(timezone.utc),
            ocr_locked_by=None,
            ocr_locked_at=None,
        )
    )


def reset_to_pending(session: Session, photo_id: int) -> None:
    """Ruční reset (pro re-OCR tlačítko)."""
    session.execute(
        update(Photo)
        .where(Photo.id == photo_id)
        .values(
            ocr_status=OcrStatus.pending,
            ocr_processed_at=None,
            ocr_locked_by=None,
            ocr_locked_at=None,
        )
    )


def sweep_zombies(session: Session, timeout_minutes: int = 5) -> int:
    """Resetuje photos co se zasekly v 'processing' déle než timeout_minutes.

    Vrací počet resetovaných řádků.
    """
    from sqlalchemy import text

    result = session.execute(
        text(
            """
            UPDATE photos
            SET ocr_status = 'pending',
                ocr_locked_by = NULL,
                ocr_locked_at = NULL
            WHERE ocr_status = 'processing'
              AND (ocr_locked_at IS NULL OR ocr_locked_at < now() - (:minutes * interval '1 minute'))
            """
        ),
        {"minutes": timeout_minutes},
    )
    return result.rowcount or 0


__all__ = [
    "insert",
    "get",
    "list_for_run",
    "get_last_photo_for_plate",
    "claim_next_pending",
    "mark_done",
    "mark_failed",
    "reset_to_pending",
    "sweep_zombies",
]
