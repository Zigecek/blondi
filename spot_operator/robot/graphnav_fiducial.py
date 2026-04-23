"""Wrapper nad autonomy ``read_observed_fiducial_ids``.

PR-13 FIND-179: izoluje autonomy internal API za jednu funkci v
spot_operator — pokud se v budoucnu autonomy pohne (rename / refaktor),
měníme jen toto místo, ne callsites v recording_service.
"""

from __future__ import annotations

from pathlib import Path


def read_observed_fiducial_ids(map_dir: Path) -> list[int]:
    """Načte fiducial IDs observované v ``waypoint_snapshots/*.pb``.

    Raises:
        RuntimeError: pokud autonomy API není dostupné.
    """
    try:
        from app.robot.graphnav_recording import (
            read_observed_fiducial_ids as _impl,
        )
    except ImportError as exc:
        raise RuntimeError(
            "autonomy.app.robot.graphnav_recording.read_observed_fiducial_ids "
            "není dostupné — updatovali jsme autonomy?"
        ) from exc
    return list(_impl(map_dir))


__all__ = ["read_observed_fiducial_ids"]
