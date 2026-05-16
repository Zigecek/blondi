"""Mock fiducial detection pro demo režim.

Náhrada za ``app.robot.fiducial_check.visible_fiducials`` — vrací jednu
fake observation s ``tag_id`` odpovídajícím ``required_id`` (pokud je
zadán) nebo defaultnímu ``DEMO_FIDUCIAL_ID=42``. Volá ho dispatch
v ``FiducialPage._start_check`` v demo módu.
"""

from __future__ import annotations

from dataclasses import dataclass

from blondi.logging_config import get_logger

_log = get_logger(__name__)

DEMO_FIDUCIAL_ID: int = 42


@dataclass(frozen=True, slots=True)
class _FakeObservation:
    """Náhrada za ``FiducialObservation`` z autonomy.

    UI čte ``tag_id`` a ``distance_m`` (viz ``fiducial_page._on_check_done``).
    """

    tag_id: int
    distance_m: float


def fake_observations(required_id: int | None = None) -> list[_FakeObservation]:
    """Vrátí jednu fake observation. ``required_id=None`` → použije default 42."""
    tag_id = int(required_id) if required_id is not None else DEMO_FIDUCIAL_ID
    obs = _FakeObservation(tag_id=tag_id, distance_m=1.5)
    _log.info("MockFiducial.visible: tag_id=%d distance=%.1fm", tag_id, obs.distance_m)
    return [obs]


__all__ = ["DEMO_FIDUCIAL_ID", "fake_observations"]
