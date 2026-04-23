"""Most mezi Map v DB a GraphNav soubory — save/load + context manager s cleanupem.

Recording flow:
  stop_recording → download_map(temp) → save_map_to_db(name, temp, ...) → smaž temp

Playback flow:
  map_extracted(map_id) jako context manager → yield (map_dir, meta) → auto cleanup
"""

from __future__ import annotations

import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4


def safe_rmtree(path: Path, *, retries: int = 3, delay_s: float = 0.5) -> bool:
    """Smaže ``path`` s retry na OSError (Windows filelock).

    PR-12 FIND-051: ``shutil.rmtree(..., ignore_errors=True)`` na Windows
    tiše ignorovala zamčené soubory → temp se plnil. Tato helper retryuje
    a při neúspěchu loguje warning.

    Vrací True pokud se podařilo smazat.
    """
    from spot_operator.logging_config import get_logger

    log = get_logger(__name__)
    for attempt in range(retries):
        if not path.exists():
            return True
        try:
            shutil.rmtree(path)
            return True
        except OSError as exc:
            log.debug("rmtree %s attempt %d failed: %s", path, attempt + 1, exc)
            time.sleep(delay_s * (attempt + 1))
    log.warning(
        "rmtree %s failed after %d retries — zanechávám (může zaplnit disk)",
        path, retries,
    )
    return False

from spot_operator.db.engine import Session
from spot_operator.db.models import Map
from spot_operator.db.repositories import maps_repo
from spot_operator.logging_config import get_logger
from spot_operator.services.contracts import (
    MAP_METADATA_SCHEMA_VERSION,
    parse_checkpoint_plan,
    validate_plan_invariants,
)
from spot_operator.services.map_archiver import (
    extract_map_archive,
    validate_map_dir,
    zip_map_dir,
)

_log = get_logger(__name__)


class MapNameAlreadyExistsError(RuntimeError):
    """Raise při pokusu o uložení mapy se jménem, které už v DB existuje.

    Samostatná exception class umožňuje SaveMapPage nabídnout retry s jiným
    jménem bez zbytečného dialog cycle.
    """

    def __init__(self, name: str):
        self.name = name
        super().__init__(f"Mapa s názvem {name!r} už v DB existuje.")


@dataclass(frozen=True, slots=True)
class MapMetadata:
    """Lehký DTO pro čtení z DB bez zatažení celého BYTEA.

    PR-12 FIND-056: ``default_capture_sources`` je tuple (immutable)
    pro konzistenci s ``MapPlan.default_capture_sources``.
    """

    id: int
    name: str
    fiducial_id: int | None
    start_waypoint_id: str | None
    default_capture_sources: tuple[str, ...]
    waypoints_count: int | None
    checkpoints_count: int | None
    checkpoints_json: dict[str, Any] | None
    metadata_version: int
    archive_is_valid: bool
    archive_validation_error: str | None
    note: str | None
    archive_size_bytes: int


def _to_metadata(m: Map) -> MapMetadata:
    return MapMetadata(
        id=m.id,
        name=m.name,
        fiducial_id=m.fiducial_id,
        start_waypoint_id=m.start_waypoint_id,
        default_capture_sources=tuple(m.default_capture_sources or []),
        waypoints_count=m.waypoints_count,
        checkpoints_count=m.checkpoints_count,
        checkpoints_json=m.checkpoints_json,
        metadata_version=m.metadata_version,
        archive_is_valid=m.archive_is_valid,
        archive_validation_error=m.archive_validation_error,
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
    plan = parse_checkpoint_plan(
        checkpoints_json,
        fallback_map_name=name,
        fallback_start_waypoint_id=start_waypoint_id,
        fallback_default_capture_sources=default_capture_sources,
        fallback_fiducial_id=fiducial_id,
    )
    # Semantic validace — kontroluje duplicitní jména, start_waypoint_id
    # přítomnost v checkpoints, atd. Hodí ValueError s CZ zprávou
    # (PR-03 FIND-037).
    validate_plan_invariants(plan)

    effective_start_waypoint_id = start_waypoint_id or plan.start_waypoint_id
    if not effective_start_waypoint_id:
        raise ValueError("Mapa nemá start_waypoint_id a nelze ji bezpečně uložit.")

    try:
        validation = validate_map_dir(
            source_dir,
            expected_start_waypoint_id=effective_start_waypoint_id,
            checkpoint_waypoint_ids=[cp.waypoint_id for cp in plan.checkpoints],
        )
    except (FileNotFoundError, ValueError) as exc:
        # Wrap bosdyn/archiver errors do CZ zprávy pro user dialog
        # (PR-03 FIND-058).
        raise RuntimeError(
            f"Mapa je neúplná nebo poškozená: {exc}"
        ) from exc
    archive, sha = zip_map_dir(source_dir)
    waypoints_count = len(validation.waypoint_ids)

    from sqlalchemy.exc import IntegrityError

    with Session() as s:
        if maps_repo.exists_by_name(s, name):
            raise MapNameAlreadyExistsError(name)
        try:
            m = maps_repo.create(
                s,
                name=name,
                archive_bytes=archive,
                archive_sha256=sha,
                archive_size_bytes=len(archive),
                fiducial_id=fiducial_id,
                start_waypoint_id=effective_start_waypoint_id,
                default_capture_sources=default_capture_sources,
                checkpoints_json=checkpoints_json,
                metadata_version=MAP_METADATA_SCHEMA_VERSION,
                archive_is_valid=True,
                archive_validation_error=None,
                waypoints_count=waypoints_count,
                checkpoints_count=checkpoints_count,
                note=note,
                created_by_operator=created_by_operator,
            )
            s.commit()
        except IntegrityError as exc:
            # TOCTOU race: někdo jiný insertoval stejné name mezi naším
            # exists_by_name a commit. Rollback + friendly raise.
            s.rollback()
            raise MapNameAlreadyExistsError(name) from exc
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
    """Extrahuje mapu z DB do temp_root/map_<id>_<uuid>/. Vrací (target_dir, metadata).

    PR-12 FIND-047: load je pure read — validace in-memory bez DB side-effectu.
    Pokud chceš revalidovat a updatovat ``archive_is_valid`` v DB,
    použij explicit :func:`revalidate_map_in_db`.
    """
    with Session() as s:
        m = s.get(Map, map_id)
        if m is None:
            raise KeyError(f"Map id {map_id} not found in DB")
        archive = m.archive_bytes
        sha = m.archive_sha256
        meta = _to_metadata(m)

    target = temp_root / f"map_{map_id}_{uuid4().hex}"
    extract_map_archive(archive, sha, target)
    meta = _validate_loaded_map_in_memory(map_id, target, meta)
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
        if target is not None:
            safe_rmtree(target)
            _log.debug("Temp map dir cleaned up: %s", target)


def read_map_metadata(map_id: int) -> MapMetadata | None:
    """Lehké čtení metadat (bez archive_bytes)."""
    with Session() as s:
        m = s.get(Map, map_id)
        if m is None:
            return None
        return _to_metadata(m)


def _validate_loaded_map_in_memory(
    map_id: int, map_dir: Path, meta: MapMetadata
) -> MapMetadata:
    """In-memory validate (PR-12 FIND-047) — žádný side-effect do DB.

    Pokud chceš DB update, použij :func:`revalidate_map_in_db`.
    """
    plan = parse_checkpoint_plan(
        meta.checkpoints_json,
        fallback_map_name=meta.name,
        fallback_start_waypoint_id=meta.start_waypoint_id,
        fallback_default_capture_sources=meta.default_capture_sources,
        fallback_fiducial_id=meta.fiducial_id,
    )
    effective_start_waypoint_id = meta.start_waypoint_id or plan.start_waypoint_id
    if not effective_start_waypoint_id:
        raise RuntimeError(
            f"Mapa '{meta.name}' nemá start_waypoint_id — playback ji odmítá načíst."
        )
    try:
        validate_map_dir(
            map_dir,
            expected_start_waypoint_id=effective_start_waypoint_id,
            checkpoint_waypoint_ids=[cp.waypoint_id for cp in plan.checkpoints],
        )
    except Exception as exc:
        raise RuntimeError(
            f"Mapa '{meta.name}' je neplatná a playback ji odmítl načíst: {exc}"
        ) from exc
    return replace(
        meta,
        start_waypoint_id=effective_start_waypoint_id,
        metadata_version=max(meta.metadata_version, MAP_METADATA_SCHEMA_VERSION),
    )


def revalidate_map_in_db(map_id: int, temp_root: Path) -> MapMetadata:
    """Explicit revalidate mapy (extract + validate + update DB).

    Volá se z admin UI / CLI, ne z playback loadu. Pokud se validace
    nepodaří, update ``archive_is_valid=False`` s error zprávou.
    Pokud projde, update ``archive_is_valid=True``.
    """
    target, meta = load_map_to_temp(map_id, temp_root)
    try:
        # load_map_to_temp už validuje in-memory. Pokud projde bez raise,
        # updatujeme DB na valid.
        with Session() as s:
            maps_repo.update_validation(
                s,
                map_id,
                archive_is_valid=True,
                archive_validation_error=None,
                metadata_version=max(
                    meta.metadata_version, MAP_METADATA_SCHEMA_VERSION
                ),
            )
            s.commit()
        return replace(
            meta,
            archive_is_valid=True,
            archive_validation_error=None,
        )
    except Exception as exc:
        with Session() as s:
            maps_repo.update_validation(
                s,
                map_id,
                archive_is_valid=False,
                archive_validation_error=str(exc),
                metadata_version=max(
                    meta.metadata_version, MAP_METADATA_SCHEMA_VERSION
                ),
            )
            s.commit()
        raise
    finally:
        safe_rmtree(target)


def list_all_metadata(limit: int | None = None) -> list[MapMetadata]:
    """Lightweight listing: **defers** `archive_bytes` (BYTEA, často MB) aby
    listing nebyl O(map_count × archive_size) přes síť do ORM.

    `_to_metadata` archive_bytes nepotřebuje, takže defer nikdy nevyvolá
    dodatečný fetch.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import defer

    with Session() as s:
        stmt = (
            select(Map)
            .options(defer(Map.archive_bytes))
            .order_by(Map.created_at.desc())
        )
        if limit:
            stmt = stmt.limit(limit)
        maps = s.execute(stmt).scalars().all()
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
    "MapNameAlreadyExistsError",
    "save_map_to_db",
    "load_map_to_temp",
    "map_extracted",
    "read_map_metadata",
    "revalidate_map_in_db",
    "list_all_metadata",
    "cleanup_temp_root",
    "safe_rmtree",
]
