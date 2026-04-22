"""Unit testy map_archiver — zip + extract + SHA ověření."""

from __future__ import annotations

from pathlib import Path

import pytest

from spot_operator.services.map_archiver import (
    extract_map_archive,
    zip_map_dir,
)


def _make_sample_map(root: Path) -> Path:
    map_dir = root / "my_map"
    (map_dir / "graph").mkdir(parents=True, exist_ok=True)
    (map_dir / "graph" / "graph").write_bytes(b"fake-graph-proto")
    (map_dir / "waypoint_snapshots").mkdir(exist_ok=True)
    (map_dir / "waypoint_snapshots" / "snap_a").write_bytes(b"A" * 64)
    (map_dir / "waypoint_snapshots" / "snap_b").write_bytes(b"B" * 64)
    (map_dir / "checkpoints.json").write_text(
        '{"map_name": "my_map", "checkpoints": []}', encoding="utf-8"
    )
    return map_dir


def test_zip_then_extract_roundtrip(tmp_path: Path) -> None:
    src = _make_sample_map(tmp_path)
    data, sha = zip_map_dir(src)
    assert len(data) > 0
    assert len(sha) == 64

    target = tmp_path / "extracted"
    extract_map_archive(data, sha, target)
    assert (target / "graph" / "graph").read_bytes() == b"fake-graph-proto"
    assert (target / "waypoint_snapshots" / "snap_a").read_bytes() == b"A" * 64
    assert (target / "checkpoints.json").exists()


def test_extract_rejects_tampered_archive(tmp_path: Path) -> None:
    src = _make_sample_map(tmp_path)
    data, sha = zip_map_dir(src)
    tampered = bytearray(data)
    tampered[-1] ^= 0xFF  # Změň poslední byte
    with pytest.raises(ValueError):
        extract_map_archive(bytes(tampered), sha, tmp_path / "out")


def test_zip_empty_dir_fails(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError):
        zip_map_dir(empty)
