"""Unit test pro `pick_side_source` — bez autonomy ani DB."""

from __future__ import annotations

from spot_operator.constants import (
    CAMERA_FRONT_LEFT,
    CAMERA_FRONT_RIGHT,
    CAMERA_LEFT,
    CAMERA_RIGHT,
    PREFERRED_LEFT_CANDIDATES,
    PREFERRED_RIGHT_CANDIDATES,
    pick_side_source,
)


def test_picks_primary_if_available() -> None:
    """Když je primární kandidát dostupný, vrátí ho (i když je dostupný i fallback)."""
    assert pick_side_source(
        [CAMERA_LEFT, CAMERA_FRONT_LEFT], PREFERRED_LEFT_CANDIDATES
    ) == CAMERA_LEFT


def test_falls_back_to_secondary() -> None:
    """Primární není k dispozici, ale fallback je — vrátí fallback."""
    assert pick_side_source(
        [CAMERA_FRONT_LEFT], PREFERRED_LEFT_CANDIDATES
    ) == CAMERA_FRONT_LEFT
    assert pick_side_source(
        [CAMERA_FRONT_RIGHT], PREFERRED_RIGHT_CANDIDATES
    ) == CAMERA_FRONT_RIGHT


def test_returns_none_if_nothing_matches() -> None:
    """Pokud ani primární ani fallback nejsou dostupné, vrátí None."""
    assert pick_side_source(["some_other_source"], PREFERRED_LEFT_CANDIDATES) is None
    assert pick_side_source([], PREFERRED_LEFT_CANDIDATES) is None


def test_order_preserves_preference() -> None:
    """Když jsou v available oba kandidáti ve 'špatném' pořadí, vrátí primární."""
    # Even if front_left is listed first in available, we still prefer `left_fisheye_image`.
    assert pick_side_source(
        [CAMERA_FRONT_LEFT, CAMERA_LEFT], PREFERRED_LEFT_CANDIDATES
    ) == CAMERA_LEFT


def test_works_with_tuple_available() -> None:
    """Funguje i s tuple (ne jen list) jako available."""
    assert pick_side_source(
        (CAMERA_LEFT,), PREFERRED_LEFT_CANDIDATES
    ) == CAMERA_LEFT
