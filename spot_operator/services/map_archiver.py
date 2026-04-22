"""Zip/extract mapových adresářů + ověření SHA-256.

Funkčně čistý modul — žádné závislosti na DB ani Qt. Testovatelné v izolaci.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path


def zip_map_dir(map_dir: Path) -> tuple[bytes, str]:
    """Zipne celý map_dir rekurzivně do bytes + SHA-256.

    Vrací (archive_bytes, sha256_hex).
    """
    if not map_dir.is_dir():
        raise NotADirectoryError(f"Map dir does not exist: {map_dir}")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        files = sorted(p for p in map_dir.rglob("*") if p.is_file())
        if not files:
            raise ValueError(f"Map dir is empty: {map_dir}")
        for path in files:
            zf.write(path, arcname=str(path.relative_to(map_dir)).replace("\\", "/"))
    data = buf.getvalue()
    return data, hashlib.sha256(data).hexdigest()


def extract_map_archive(
    data: bytes, expected_sha256: str, target_dir: Path
) -> Path:
    """Ověří SHA-256 a vyextrahuje ZIP do target_dir. Vrátí target_dir."""
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        raise ValueError(
            f"Map archive corrupted: sha256 mismatch "
            f"(expected {expected_sha256}, got {actual})"
        )
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for member in zf.namelist():
            # Sanity: žádné ../.. nebo absolutní cesty
            safe = Path(member)
            if safe.is_absolute() or ".." in safe.parts:
                raise ValueError(f"Suspicious ZIP member: {member}")
        zf.extractall(target_dir)
    return target_dir


def count_waypoints_in_map_dir(map_dir: Path) -> int | None:
    """Odhad — spočítá soubory v waypoint_snapshots/, odpovídá počtu waypointů."""
    snapshot_dir = map_dir / "waypoint_snapshots"
    if not snapshot_dir.is_dir():
        return None
    return sum(1 for p in snapshot_dir.iterdir() if p.is_file())


__all__ = [
    "zip_map_dir",
    "extract_map_archive",
    "count_waypoints_in_map_dir",
]
