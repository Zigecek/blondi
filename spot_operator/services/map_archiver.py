"""Zip/extract mapových adresářů + ověření SHA-256 a integrity GraphNav archivu.

PR-12:
- FIND-052: zip_map_dir vynechává temp / swap / hidden files.
- FIND-053: velké archivy se extrahují přes temp file (ne BytesIO celé).
- FIND-054: bosdyn import je module-level (lazy jen jako fallback pro testy).
- FIND-055: ``count_waypoints_in_map_dir`` byl dead — odstraněn.
"""

from __future__ import annotations

import hashlib
import io
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

try:  # pragma: no cover — bosdyn-only, v testech bez robota může chybět
    from bosdyn.api.graph_nav import map_pb2 as _graphnav_pb2
except Exception:  # pragma: no cover
    _graphnav_pb2 = None  # type: ignore[assignment]

# Suffixy, které se nikdy nezipují — temp / swap / system files.
_EXCLUDE_SUFFIXES: frozenset[str] = frozenset({
    ".tmp", ".swp", ".swo", ".lock", ".bak", ".~",
})

_LARGE_ARCHIVE_THRESHOLD: int = 100 * 1024 * 1024  # 100 MB


@dataclass(frozen=True, slots=True)
class MapArchiveValidation:
    waypoint_ids: tuple[str, ...]
    waypoint_snapshot_ids: tuple[str, ...]
    edge_snapshot_ids: tuple[str, ...]


def _should_include_in_archive(path: Path) -> bool:
    """PR-12 FIND-052: vynechá temp / swap / hidden files."""
    name = path.name
    if name.startswith(".") or name.startswith("~"):
        return False
    return path.suffix.lower() not in _EXCLUDE_SUFFIXES


def zip_map_dir(map_dir: Path) -> tuple[bytes, str]:
    """Zipne celý map_dir rekurzivně do bytes + SHA-256.

    Vrací (archive_bytes, sha256_hex). Temp / swap / hidden soubory jsou
    vynechány (PR-12 FIND-052).
    """
    if not map_dir.is_dir():
        raise NotADirectoryError(f"Map dir does not exist: {map_dir}")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        files = sorted(
            p for p in map_dir.rglob("*")
            if p.is_file() and _should_include_in_archive(p)
        )
        if not files:
            raise ValueError(f"Map dir is empty: {map_dir}")
        for path in files:
            zf.write(path, arcname=str(path.relative_to(map_dir)).replace("\\", "/"))
    data = buf.getvalue()
    return data, hashlib.sha256(data).hexdigest()


def extract_map_archive(
    data: bytes, expected_sha256: str, target_dir: Path
) -> Path:
    """Ověří SHA-256 a vyextrahuje ZIP do target_dir. Vrátí target_dir.

    PR-12 FIND-053: pro velké archivy (>100 MB) streamujeme přes temp
    soubor místo držení celého BytesIO v paměti.
    """
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        raise ValueError(
            f"Map archive corrupted: sha256 mismatch "
            f"(expected {expected_sha256}, got {actual})"
        )
    target_dir.mkdir(parents=True, exist_ok=True)

    if len(data) > _LARGE_ARCHIVE_THRESHOLD:
        # Spill do temp file pro memory efficiency.
        with tempfile.NamedTemporaryFile(
            suffix=".zip", delete=False, dir=str(target_dir.parent)
        ) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        try:
            _extract_zip(tmp_path, target_dir)
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass
    else:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            _validate_zip_members(zf)
            zf.extractall(target_dir)
    return target_dir


def _extract_zip(zip_path: Path, target_dir: Path) -> None:
    with zipfile.ZipFile(str(zip_path)) as zf:
        _validate_zip_members(zf)
        zf.extractall(target_dir)


def _validate_zip_members(zf: zipfile.ZipFile) -> None:
    """Sanity check — žádné ``..`` nebo absolutní cesty."""
    for member in zf.namelist():
        safe = Path(member)
        if safe.is_absolute() or ".." in safe.parts:
            raise ValueError(f"Suspicious ZIP member: {member}")


def validate_map_dir(
    map_dir: Path,
    *,
    expected_start_waypoint_id: str | None = None,
    checkpoint_waypoint_ids: list[str] | tuple[str, ...] = (),
) -> MapArchiveValidation:
    """Validates that the GraphNav archive is complete and internally consistent."""
    if _graphnav_pb2 is None:
        raise RuntimeError(
            "bosdyn.api.graph_nav.map_pb2 není dostupné — "
            "validate_map_dir nemůže zkontrolovat graf."
        )
    map_pb2 = _graphnav_pb2

    graph_path = map_dir / "graph" / "graph"
    if not graph_path.is_file():
        raise FileNotFoundError(f"Graph file not found: {graph_path}")

    graph = map_pb2.Graph()
    try:
        graph.ParseFromString(graph_path.read_bytes())
    except Exception as exc:
        raise ValueError(f"Graph file is not a valid GraphNav protobuf: {exc}") from exc

    waypoint_ids = [wp.id for wp in graph.waypoints if wp.id]
    if not waypoint_ids:
        raise ValueError("Graph archive contains no waypoints.")

    wp_dir = map_dir / "waypoint_snapshots"
    edge_dir = map_dir / "edge_snapshots"
    missing_waypoint_snapshots = [
        wp.snapshot_id
        for wp in graph.waypoints
        if wp.snapshot_id and not (wp_dir / wp.snapshot_id).is_file()
    ]
    if missing_waypoint_snapshots:
        raise ValueError(
            "Missing waypoint snapshots referenced by graph: "
            + ", ".join(sorted(missing_waypoint_snapshots))
        )

    missing_edge_snapshots = [
        edge.snapshot_id
        for edge in graph.edges
        if edge.snapshot_id and not (edge_dir / edge.snapshot_id).is_file()
    ]
    if missing_edge_snapshots:
        raise ValueError(
            "Missing edge snapshots referenced by graph: "
            + ", ".join(sorted(missing_edge_snapshots))
        )

    if expected_start_waypoint_id and expected_start_waypoint_id not in waypoint_ids:
        raise ValueError(
            "Recorded start_waypoint_id is not present in the graph: "
            f"{expected_start_waypoint_id}"
        )

    missing_checkpoint_waypoints = sorted(
        {
            waypoint_id
            for waypoint_id in checkpoint_waypoint_ids
            if waypoint_id and waypoint_id not in waypoint_ids
        }
    )
    if missing_checkpoint_waypoints:
        raise ValueError(
            "Checkpoint waypoint(s) are missing from the graph: "
            + ", ".join(missing_checkpoint_waypoints)
        )

    return MapArchiveValidation(
        waypoint_ids=tuple(waypoint_ids),
        waypoint_snapshot_ids=tuple(
            wp.snapshot_id for wp in graph.waypoints if wp.snapshot_id
        ),
        edge_snapshot_ids=tuple(
            edge.snapshot_id for edge in graph.edges if edge.snapshot_id
        ),
    )


__all__ = [
    "MapArchiveValidation",
    "zip_map_dir",
    "extract_map_archive",
    "validate_map_dir",
]
