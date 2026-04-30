"""CRUD nad tabulkou spot_credentials (jen metadata; heslo v keyringu)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from blondi.db.models import SpotCredential


def create(
    session: Session,
    *,
    label: str,
    hostname: str,
    username: str,
    keyring_ref: str,
) -> SpotCredential:
    cred = SpotCredential(
        label=label,
        hostname=hostname,
        username=username,
        keyring_ref=keyring_ref,
    )
    session.add(cred)
    session.flush()
    return cred


def list_all(session: Session) -> Sequence[SpotCredential]:
    return (
        session.execute(select(SpotCredential).order_by(SpotCredential.label))
        .scalars()
        .all()
    )


def get_by_label(session: Session, label: str) -> SpotCredential | None:
    return session.execute(
        select(SpotCredential).where(SpotCredential.label == label)
    ).scalar_one_or_none()


def delete(session: Session, cred_id: int) -> bool:
    cred = session.get(SpotCredential, cred_id)
    if cred is None:
        return False
    session.delete(cred)
    return True


def touch_last_used(session: Session, cred_id: int) -> None:
    session.execute(
        update(SpotCredential)
        .where(SpotCredential.id == cred_id)
        .values(last_used_at=datetime.now(timezone.utc))
    )


__all__ = ["create", "list_all", "get_by_label", "delete", "touch_last_used"]
