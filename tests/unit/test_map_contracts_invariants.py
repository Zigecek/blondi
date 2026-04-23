"""Tests pro validate_plan_invariants + legacy tolerance (PR-03, PR-14)."""

from __future__ import annotations

import pytest

from spot_operator.services.contracts import (
    MapCheckpoint,
    MapPlan,
    _as_optional_int,
    _normalize_sources,
    parse_checkpoint_plan,
    validate_plan_invariants,
    validate_sources_known,
)


def _make_plan(checkpoints: list[MapCheckpoint], *, start: str | None = None) -> MapPlan:
    return MapPlan(
        schema_version=2,
        map_name="test",
        start_waypoint_id=start,
        fiducial_id=None,
        default_capture_sources=(),
        checkpoints=tuple(checkpoints),
    )


def test_validate_rejects_empty_checkpoints() -> None:
    plan = _make_plan([])
    with pytest.raises(ValueError, match="žádné checkpointy"):
        validate_plan_invariants(plan)


def test_validate_rejects_duplicate_name() -> None:
    cps = [
        MapCheckpoint("CP_001", "wp_a", "checkpoint", ()),
        MapCheckpoint("CP_001", "wp_b", "checkpoint", ()),
    ]
    with pytest.raises(ValueError, match="duplik"):
        validate_plan_invariants(_make_plan(cps))


def test_validate_rejects_duplicate_waypoint_id() -> None:
    cps = [
        MapCheckpoint("CP_001", "wp_x", "checkpoint", ()),
        MapCheckpoint("CP_002", "wp_x", "checkpoint", ()),
    ]
    with pytest.raises(ValueError, match="duplik"):
        validate_plan_invariants(_make_plan(cps))


def test_validate_rejects_start_not_in_checkpoints() -> None:
    cps = [MapCheckpoint("CP_001", "wp_a", "checkpoint", ())]
    with pytest.raises(ValueError, match="start_waypoint_id"):
        validate_plan_invariants(_make_plan(cps, start="wp_missing"))


def test_validate_accepts_valid_plan() -> None:
    cps = [
        MapCheckpoint("WP_001", "wp_a", "waypoint", ()),
        MapCheckpoint("CP_001", "wp_b", "checkpoint", ("left",)),
    ]
    # Nemělo by raise.
    validate_plan_invariants(_make_plan(cps, start="wp_a"))


def test_normalize_sources_accepts_scalar_str() -> None:
    assert _normalize_sources("left_fisheye_image") == ("left_fisheye_image",)


def test_normalize_sources_dedup() -> None:
    assert _normalize_sources(["a", "b", "a"]) == ("a", "b")


def test_normalize_sources_rejects_int() -> None:
    with pytest.raises(ValueError):
        _normalize_sources(42)


def test_as_optional_int_empty_string_returns_fallback() -> None:
    assert _as_optional_int("", fallback=7) == 7
    assert _as_optional_int("   ", fallback=7) == 7


def test_as_optional_int_accepts_numeric_string() -> None:
    assert _as_optional_int("42") == 42


def test_as_optional_int_rejects_boolean() -> None:
    with pytest.raises(ValueError):
        _as_optional_int(True)


def test_parse_plan_accepts_legacy_scalar_fiducial() -> None:
    payload = {
        "map_name": "legacy",
        "fiducial": 5,
        "start_waypoint_id": "wp_a",
        "checkpoints": [
            {
                "name": "CP_001",
                "waypoint_id": "wp_a",
                "kind": "checkpoint",
                "capture_sources": ["left"],
            }
        ],
    }
    plan = parse_checkpoint_plan(
        payload,
        fallback_map_name="fallback",
        fallback_start_waypoint_id=None,
        fallback_default_capture_sources=(),
        fallback_fiducial_id=None,
    )
    assert plan.fiducial_id == 5


def test_parse_plan_handles_string_capture_source() -> None:
    payload = {
        "map_name": "legacy",
        "checkpoints": [
            {
                "name": "CP_001",
                "waypoint_id": "wp_a",
                "kind": "checkpoint",
                "capture_sources": "left_fisheye_image",  # legacy scalar
            }
        ],
    }
    plan = parse_checkpoint_plan(
        payload,
        fallback_map_name="fallback",
        fallback_start_waypoint_id=None,
        fallback_default_capture_sources=(),
        fallback_fiducial_id=None,
    )
    assert plan.checkpoints[0].capture_sources == ("left_fisheye_image",)


def test_validate_sources_known_empty_available_passes_through() -> None:
    """Pokud available je prázdný (nemáme info), normalizace projde bez raise."""
    assert validate_sources_known(
        ["left"], [], context="test"
    ) == ["left"]


def test_validate_sources_known_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown"):
        validate_sources_known(
            ["left", "mystery"], ["left", "right"], context="test"
        )
