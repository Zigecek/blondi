"""Most mezi Map v DB a GraphNav soubory — save/load + context manager s cleanupem.

Recording flow:
  stop_recording → download_map(temp) → save_map_to_db(name, temp, ...) → smaž temp

Playback flow:
  map_extracted(map_id) jako context manager → yield (map_dir, meta) → auto cleanup
"""

from __future__ import annotations

import json
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from spot_operator.db.engine import Session
from spot_operator.db.models import Map
from spot_operator.db.repositories import maps_repo
from spot_operator.logging_config import get_logger
from spot_operator.services.map_archiver import (
    count_waypoints_in_map_dir,
    extract_map_archive,
    zip_map_dir,
)

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class MapMetadata:
    """Lehký DTO pro čtení z DB bez zatažení celého BYTEA."""

    id: int
    name: str
    fiducial_id: int | None
    start_waypoint_id: str | None
    default_capture_sources: list[str]
    waypoints_count: int | None
    checkpoints_count: int | None
    checkpoints_json: dict[str, Any] | None
    note: str | None
    archive_size_bytes: int


def _to_metadata(m: Map) -> MapMetadata:
    return MapMetadata(
        id=m.id,
        name=m.name,
        fiducial_id=m.fiducial_id,
        start_waypoint_id=m.start_waypoint_id,
        default_capture_sources=list(m.default_capture_sources or []),
        waypoints_count=m.waypoints_count,
        checkpoints_count=m.checkpoints_count,
        checkpoints_json=m.checkpoints_json,
        note=m.note,
        archive_size_bytes=m.archive_size_bytes,
    )


def save_map_to_db(
    *,
    name: str,
    source_dir: Path,
    fiducial_id: int | None,
    start_waypoint_id: str | None,
    default_capture_sources: list[str],
    checkpoints_json: dict[str, Any] | None,
    checkpoints_count: int | None,
    note: str | None = None,
    created_by_operator: str | None = None,
) -> int:
    """Zazipuje source_dir, spočítá SHA, uloží do DB. Vrátí map.id."""
    archive, sha = zip_map_dir(source_dir)
    waypoints_count = count_waypoints_in_map_dir(source_dir)

    with Session() as s:
        if maps_repo.exists_by_name(s, name):
            raise ValueError(f"Mapa s názvem '{name}' už v DB existuje.")
        m = maps_repo.create(
            s,
            name=name,
            archive_bytes=archive,
            archive_sha256=sha,
            archive_size_bytes=len(archive),
            fiducial_id=fiducial_id,
            start_waypoint_id=start_waypoint_id,
            default_capture_sources=default_capture_sources,
            checkpoints_json=checkpoints_json,
            waypoints_count=waypoints_count,
            checkpoints_count=checkpoints_count,
            note=note,
            created_by_operator=created_by_operator,
        )
        s.commit()
        map_id = m.id

    _log.info(
        "Map saved to DB: id=%s name=%r size=%d bytes sha=%s",
        map_id,
        name,
        len(archive),
        sha[:12],
    )
    return map_id


def load_map_to_temp(map_id: int, temp_root: Path) -> tuple[Path, MapMetadata]:
    """Extrahuje mapu z DB do temp_root/map_<id>_<uuid>/. Vrací (target_dir, metadata)."""
    with Session() as s:
        m = s.get(Map, map_id)
        if m is None:
            raise KeyError(f"Map id {map_id} not found in DB")
        archive = m.archive_bytes
        sha = m.archive_sha256
        meta = _to_metadata(m)

    target = temp_root / f"map_{map_id}_{uuid4().hex}"
    extract_map_archive(archive, sha, target)
    _log.info("Map %s extracted to %s", map_id, target)
    return target, meta


@contextmanager
def map_extracted(map_id: int, temp_root: Path) -> Iterator[tuple[Path, MapMetadata]]:
    """Context manager: extract mapy z DB + automatický cleanup."""
    target: Path | None = None
    try:
        target, meta = load_map_to_temp(map_id, temp_root)
        yield target, meta
    finally:
        if target is not None and target.exists():
            shutil.rmtree(target, ignore_errors=True)
            _log.debug("Temp map dir cleaned up: %s", target)


def read_map_metadata(map_id: int) -> MapMetadata | None:
    """Lehké čtení metadat (bez archive_bytes)."""
    with Session() as s:
        m = s.get(Map, map_id)
        if m is None:
            return None
        return _to_metadata(m)


def list_all_metadata(limit: int | None = None) -> list[MapMetadata]:
    with Session() as s:
        maps = maps_repo.list_all(s, limit=limit)
        return [_to_metadata(m) for m in maps]


def cleanup_temp_root(temp_root: Path) -> None:
    """Smaže všechny `map_*` složky v temp/. Voláno při startu aplikace."""
    if not temp_root.exists():
        temp_root.mkdir(parents=True, exist_ok=True)
        return
    for child in temp_root.iterdir():
        if child.name.startswith("map_"):
            shutil.rmtree(child, ignore_errors=True)


__all__ = [
    "MapMetadata",
    "save_map_to_db",
    "load_map_to_temp",
    "map_extracted",
    "read_map_metadata",
    "list_all_metadata",
    "cleanup_temp_root",
]
