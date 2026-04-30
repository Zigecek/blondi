from __future__ import annotations

from datetime import datetime, timezone

import pytest

from blondi.services.contracts import (
    CAPTURE_STATUS_FAILED,
    CAPTURE_STATUS_OK,
    CheckpointResult,
    build_checkpoint_result,
    parse_checkpoint_plan,
    validate_sources_known,
)


def test_parse_checkpoint_plan_upgrades_legacy_v1_payload() -> None:
    plan = parse_checkpoint_plan(
        {
            "map_name": "legacy_map",
            "fiducial_id": 17,
            "checkpoints": [
                {
                    "name": "CP_001",
                    "waypoint_id": "waypoint-1",
                    "kind": "checkpoint",
                }
            ],
        },
        fallback_map_name="fallback",
        fallback_start_waypoint_id="start-123",
        fallback_default_capture_sources=["left_fisheye_image", "right_fisheye_image"],
        fallback_fiducial_id=None,
    )

    assert plan.schema_version == 1
    assert plan.map_name == "legacy_map"
    assert plan.start_waypoint_id == "start-123"
    assert plan.fiducial_id == 17
    assert plan.checkpoints[0].capture_sources == (
        "left_fisheye_image",
        "right_fisheye_image",
    )


def test_validate_sources_known_rejects_unknown_source() -> None:
    with pytest.raises(ValueError):
        validate_sources_known(
            ["left_fisheye_image", "mystery_source"],
            ["left_fisheye_image", "right_fisheye_image"],
            context="Playback",
        )


def test_checkpoint_result_is_complete_only_for_reached_and_ok() -> None:
    started = datetime(2026, 4, 23, 9, 0, tzinfo=timezone.utc)
    finished = datetime(2026, 4, 23, 9, 1, tzinfo=timezone.utc)
    ok_result = build_checkpoint_result(
        name="CP_001",
        waypoint_id="wp-1",
        nav_outcome="reached",
        capture_status=CAPTURE_STATUS_OK,
        expected_sources=("left",),
        saved_sources=("left",),
        failed_sources=(),
        error=None,
        started_at=started,
        finished_at=finished,
    )
    failed_capture = CheckpointResult(
        name="CP_002",
        waypoint_id="wp-2",
        nav_outcome="reached",
        capture_status=CAPTURE_STATUS_FAILED,
        expected_sources=("left",),
        saved_sources=(),
        failed_sources=("left",),
        error="no photo",
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
    )

    assert ok_result.is_complete is True
    assert failed_capture.is_complete is False

