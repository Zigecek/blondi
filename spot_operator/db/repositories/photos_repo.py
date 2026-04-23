"""CRUD nad tabulkou photos — včetně claim_next_pending pro OCR worker.

Navíc obsahuje lehké DTO (`PhotoRow`, `DetectionRow`, `PhotoMetadata`)
a dotazy ``list_page_light`` / ``count_photos`` / ``fetch_image_bytes`` /
``get_photo_metadata``. Tyto DTO slouží pro CRUD tabulky a detail dialogy
tak, aby se **nikdy** nenačítalo ``Photo.image_bytes`` do list views
(jinak je několik set fotek × 2 MB BYTEA hlavní příčina laggu).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, defer, selectinload

from spot_operator.db.enums import OcrStatus
from spot_operator.db.models import Photo, PlateDetection


# --- DTO pro CRUD ---

@dataclass(frozen=True)
class DetectionRow:
    """DTO pro jednu detekci (pro Photo detail dialog)."""

    plate_text: str | None
    text_confidence: float | None
    detection_confidence: float | None
    engine_name: str


@dataclass(frozen=True)
class PhotoRow:
    """DTO pro řádek v tabulce Fotky (bez BYTEA)."""

    id: int
    run_id: int
    checkpoint_name: str | None
    camera_source: str
    ocr_status: str
    captured_at: datetime | None
    plates: tuple[str, ...]


@dataclass(frozen=True)
class PhotoMetadata:
    """DTO pro Photo detail dialog (bez BYTEA, s detekcemi)."""

    id: int
    run_id: int
    checkpoint_name: str | None
    camera_source: str
    ocr_status: str
    captured_at: datetime | None
    detections: tuple[DetectionRow, ...]


# Povolené sloupce pro ORDER BY ve `list_page_light`.
_SORTABLE_PHOTO_COLS: frozenset[str] = frozenset(
    {"id", "run_id", "checkpoint_name", "camera_source", "ocr_status", "captured_at"}
)


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


# --- Nové lightweight dotazy pro CRUD (bez BYTEA) ---

def _to_photo_row(photo: Photo) -> PhotoRow:
    return PhotoRow(
        id=photo.id,
        run_id=photo.run_id,
        checkpoint_name=photo.checkpoint_name,
        camera_source=photo.camera_source,
        ocr_status=photo.ocr_status.value,
        captured_at=photo.captured_at,
        plates=tuple(d.plate_text or "?" for d in photo.detections),
    )


def list_page_light(
    session: Session,
    *,
    run_id: int | None = None,
    offset: int = 0,
    limit: int = 100,
    sort_by: str = "captured_at",
    sort_desc: bool = True,
) -> list[PhotoRow]:
    """Stránka fotek bez ``image_bytes`` + všechny detekce v jednom SELECTu.

    ``defer(Photo.image_bytes)`` zajistí, že se LargeBinary sloupec nenačítá.
    ``selectinload(Photo.detections)`` nahrazuje N+1 pattern jedním dodatečným
    SELECTem přes ``plate_detections``.
    """
    if sort_by not in _SORTABLE_PHOTO_COLS:
        sort_by = "captured_at"
    col = getattr(Photo, sort_by)
    order = col.desc() if sort_desc else col.asc()

    stmt = (
        select(Photo)
        .options(
            defer(Photo.image_bytes),
            selectinload(Photo.detections),
        )
        .order_by(order, Photo.id.desc())
        .offset(max(offset, 0))
        .limit(max(limit, 1))
    )
    if run_id is not None:
        stmt = stmt.where(Photo.run_id == run_id)
    rows = session.execute(stmt).scalars().all()
    return [_to_photo_row(p) for p in rows]


def count_photos(session: Session, *, run_id: int | None = None) -> int:
    """Počet fotek (s volitelným filtrem na run_id) pro pagination header."""
    stmt = select(func.count(Photo.id))
    if run_id is not None:
        stmt = stmt.where(Photo.run_id == run_id)
    return int(session.execute(stmt).scalar_one() or 0)


def fetch_image_bytes(session: Session, photo_id: int) -> bytes | None:
    """Stáhne **pouze** ``image_bytes`` pro dané photo. Žádná další metadata.

    Používá se v detail dialogu v BG threadu, aby se JPEG transferoval z DB
    bez zbytečného natažení celého Photo ORM objektu.
    """
    return session.execute(
        select(Photo.image_bytes).where(Photo.id == photo_id)
    ).scalar_one_or_none()


def get_photo_metadata(session: Session, photo_id: int) -> PhotoMetadata | None:
    """Metadata fotky + detekce bez ``image_bytes``. Vhodné pro detail dialog."""
    stmt = (
        select(Photo)
        .options(
            defer(Photo.image_bytes),
            selectinload(Photo.detections),
        )
        .where(Photo.id == photo_id)
    )
    photo = session.execute(stmt).scalar_one_or_none()
    if photo is None:
        return None
    detections = tuple(
        DetectionRow(
            plate_text=d.plate_text,
            text_confidence=d.text_confidence,
            detection_confidence=d.detection_confidence,
            engine_name=d.engine_name,
        )
        for d in sorted(
            photo.detections,
            key=lambda d: (d.text_confidence is None, -(d.text_confidence or 0)),
        )
    )
    return PhotoMetadata(
        id=photo.id,
        run_id=photo.run_id,
        checkpoint_name=photo.checkpoint_name,
        camera_source=photo.camera_source,
        ocr_status=photo.ocr_status.value,
        captured_at=photo.captured_at,
        detections=detections,
    )


def fetch_last_image_bytes_for_plate(
    session: Session, plate_text: str
) -> tuple[bytes, int, datetime | None] | None:
    """Pro SpzDetailDialog: najde poslední fotku s danou SPZ a vrátí její bytes + metadata.

    Vrací (bytes, run_id, captured_at) nebo None pokud žádná fotka neexistuje.
    Efektivnější než získat celé Photo + samostatně pak image_bytes.
    """
    normalized = (plate_text or "").strip().upper()
    if not normalized:
        return None
    stmt = (
        select(Photo.image_bytes, Photo.run_id, Photo.captured_at)
        .join(PlateDetection, PlateDetection.photo_id == Photo.id)
        .where(PlateDetection.plate_text == normalized)
        .order_by(Photo.captured_at.desc())
        .limit(1)
    )
    row = session.execute(stmt).first()
    if row is None:
        return None
    img_bytes, run_id, captured_at = row
    if img_bytes is None:
        return None
    return bytes(img_bytes), run_id, captured_at


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
    "DetectionRow",
    "PhotoRow",
    "PhotoMetadata",
    "insert",
    "get",
    "list_page_light",
    "count_photos",
    "fetch_image_bytes",
    "get_photo_metadata",
    "fetch_last_image_bytes_for_plate",
    "list_for_run",
    "get_last_photo_for_plate",
    "claim_next_pending",
    "mark_done",
    "mark_failed",
    "reset_to_pending",
    "sweep_zombies",
]
