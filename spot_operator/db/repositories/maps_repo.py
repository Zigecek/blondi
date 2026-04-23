"""CRUD nad tabulkou maps."""

from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from spot_operator.db.models import Map


def create(
    session: Session,
    *,
    name: str,
    archive_bytes: bytes,
    archive_sha256: str,
    archive_size_bytes: int,
    fiducial_id: int | None,
    start_waypoint_id: str | None,
    default_capture_sources: list[str],
    checkpoints_json: dict[str, Any] | None,
    metadata_version: int,
    archive_is_valid: bool,
    archive_validation_error: str | None,
    waypoints_count: int | None,
    checkpoints_count: int | None,
    note: str | None,
    created_by_operator: str | None,
) -> Map:
    m = Map(
        name=name,
        archive_bytes=archive_bytes,
        archive_sha256=archive_sha256,
        archive_size_bytes=archive_size_bytes,
        fiducial_id=fiducial_id,
        start_waypoint_id=start_waypoint_id,
        default_capture_sources=default_capture_sources,
        checkpoints_json=checkpoints_json,
        metadata_version=metadata_version,
        archive_is_valid=archive_is_valid,
        archive_validation_error=archive_validation_error,
        waypoints_count=waypoints_count,
        checkpoints_count=checkpoints_count,
        note=note,
        created_by_operator=created_by_operator,
    )
    session.add(m)
    session.flush()
    return m


def get(session: Session, map_id: int) -> Map | None:
    return session.get(Map, map_id)


def get_by_name(session: Session, name: str) -> Map | None:
    return session.execute(
        select(Map).where(Map.name == name)
    ).scalar_one_or_none()


def list_all(session: Session, *, limit: int | None = None) -> Sequence[Map]:
    """Vrací mapy seřazené dle created_at DESC. Nepoužívá archive_bytes v SELECT (ten se načte lazy)."""
    stmt = select(Map).order_by(Map.created_at.desc())
    if limit:
        stmt = stmt.limit(limit)
    return session.execute(stmt).scalars().all()


def list_all_validated(
    session: Session,
    *,
    limit: int | None = None,
    include_invalid: bool = False,
) -> Sequence[Map]:
    """Jako ``list_all`` ale default vyfiltruje mapy s ``archive_is_valid=False``.

    PR-03 FIND-020: operátor by neměl vidět mapy, které při posledním pokusu
    selhaly validaci. Admin UI může explicit set ``include_invalid=True``.
    """
    stmt = select(Map).order_by(Map.created_at.desc())
    if not include_invalid:
        stmt = stmt.where(Map.archive_is_valid.is_(True))
    if limit:
        stmt = stmt.limit(limit)
    return session.execute(stmt).scalars().all()


def delete(session: Session, map_id: int) -> bool:
    m = session.get(Map, map_id)
    if m is None:
        return False
    session.delete(m)
    return True


def exists_by_name(session: Session, name: str) -> bool:
    return session.execute(
        select(Map.id).where(Map.name == name).limit(1)
    ).scalar_one_or_none() is not None


def update_validation(
    session: Session,
    map_id: int,
    *,
    archive_is_valid: bool,
    archive_validation_error: str | None,
    metadata_version: int | None = None,
) -> None:
    m = session.get(Map, map_id)
    if m is None:
        raise KeyError(f"Map {map_id} not found.")
    m.archive_is_valid = archive_is_valid
    m.archive_validation_error = archive_validation_error
    if metadata_version is not None:
        m.metadata_version = metadata_version


__all__ = [
    "create",
    "get",
    "get_by_name",
    "list_all",
    "list_all_validated",
    "delete",
    "exists_by_name",
    "update_validation",
]
