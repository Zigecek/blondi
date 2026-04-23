"""Ukládání fotek do DB jako BYTEA (vstup pro OCR worker)."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

from spot_operator.db.engine import Session
from spot_operator.db.repositories import photos_repo
from spot_operator.logging_config import get_logger

_log = get_logger(__name__)


def encode_bgr_to_jpeg(image_bgr: np.ndarray, *, quality: int = 85) -> tuple[bytes, int, int]:
    """Převod OpenCV BGR ndarray na JPEG bytes. Vrátí (bytes, width, height)."""
    if image_bgr is None or image_bgr.size == 0:
        raise ValueError("Empty image, cannot encode.")
    rgb = image_bgr[:, :, ::-1]  # BGR → RGB (OpenCV → Pillow)
    height, width = rgb.shape[:2]
    img = Image.fromarray(rgb)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue(), width, height


def save_photo_to_db(
    *,
    run_id: int,
    checkpoint_name: str | None,
    camera_source: str,
    image_bytes: bytes,
    width: int | None = None,
    height: int | None = None,
    image_mime: str = "image/jpeg",
) -> int:
    """Uloží fotku s ocr_status='pending'. OCR worker ji asynchronně zpracuje.

    PR-07 FIND-032: photo_id je pouze vrácen, pokud commit byl úspěšný
    (``with Session() as s`` commituje v ``__exit__`` nebo rollbackuje).
    Pokud commit selže, exception propaguje ven před ``return`` — volající
    nedostane invalid photo_id.
    """
    with Session() as s:
        photo = photos_repo.insert(
            s,
            run_id=run_id,
            checkpoint_name=checkpoint_name,
            camera_source=camera_source,
            image_bytes=image_bytes,
            width=width,
            height=height,
            image_mime=image_mime,
        )
        s.commit()
        # Commit OK — photo.id je teď persistovaná hodnota.
        photo_id = photo.id
    _log.info(
        "Photo saved: id=%s run=%s cp=%s source=%s bytes=%d",
        photo_id,
        run_id,
        checkpoint_name,
        camera_source,
        len(image_bytes),
    )
    return photo_id


def save_bgr_photo_to_db(
    *,
    run_id: int,
    checkpoint_name: str | None,
    camera_source: str,
    image_bgr: np.ndarray,
    jpeg_quality: int = 85,
) -> int:
    """Pomocná funkce — zakóduje ndarray na JPEG a uloží."""
    jpeg, width, height = encode_bgr_to_jpeg(image_bgr, quality=jpeg_quality)
    return save_photo_to_db(
        run_id=run_id,
        checkpoint_name=checkpoint_name,
        camera_source=camera_source,
        image_bytes=jpeg,
        width=width,
        height=height,
    )


__all__ = ["encode_bgr_to_jpeg", "save_photo_to_db", "save_bgr_photo_to_db"]
