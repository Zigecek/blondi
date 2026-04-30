"""Unit testy pro RecordingService flow (PR-02, PR-04, PR-14).

Mock autonomy GraphNavRecorder + WaypointNameGenerator.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_service() -> "object":
    """Vytvoří RecordingService s mock bundle/recorder."""
    from blondi.services.recording_service import RecordingService

    bundle = MagicMock()
    recorder = MagicMock()
    recorder.is_recording = False
    namer = MagicMock()
    waypoint_counter = {"wp": 0, "cp": 0}

    def _next_waypoint() -> str:
        waypoint_counter["wp"] += 1
        return f"WP_{waypoint_counter['wp']:03d}"

    def _next_checkpoint() -> str:
        waypoint_counter["cp"] += 1
        return f"CP_{waypoint_counter['cp']:03d}"

    namer.next_waypoint.side_effect = _next_waypoint
    namer.next_checkpoint.side_effect = _next_checkpoint

    wp_counter = {"n": 0}

    def _create_waypoint(name: str) -> str:
        wp_counter["n"] += 1
        return f"wp_id_{wp_counter['n']}"

    recorder.create_waypoint.side_effect = _create_waypoint

    with patch(
        "blondi.services.recording_service.RecordingService.__init__",
        return_value=None,
    ):
        svc = RecordingService.__new__(RecordingService)
        svc._bundle = bundle
        svc._recorder = recorder
        svc._namer = namer
        svc._checkpoints = []
        svc._start_waypoint_id = None
        svc._fiducial_id = None
        svc._default_capture_sources = []
    return svc


def test_start_waypoint_set_by_first_add_unnamed_waypoint() -> None:
    svc = _make_service()
    svc._recorder.is_recording = False

    svc.start(
        map_name_prefix="test",
        default_capture_sources=["left"],
        fiducial_id=5,
    )
    cp = svc.add_unnamed_waypoint()
    assert svc.start_waypoint_id == cp.waypoint_id
    assert cp.kind == "waypoint"


def test_start_waypoint_set_by_first_checkpoint_if_no_waypoint() -> None:
    """Bez explicit Waypoint klik — první checkpoint se stane startem.
    UI (TeleopRecordPage) to disallowuje, ale service tomu sám nebrání.
    """
    svc = _make_service()
    svc._recorder.is_recording = False

    svc.start(
        map_name_prefix="test",
        default_capture_sources=["left"],
        fiducial_id=5,
    )
    # Simuluj capture přes mock
    with patch(
        "blondi.robot.dual_side_capture.capture_sources",
        return_value={"left": b"bgr_data"},
    ), patch(
        "blondi.services.photo_sink.encode_bgr_to_jpeg",
        return_value=(b"jpeg_bytes", 640, 480),
    ):
        cp = svc.capture_and_record_checkpoint(
            ["left"], image_poller=MagicMock()
        )
    assert svc.start_waypoint_id == cp.waypoint_id
    assert cp.kind == "checkpoint"


def test_capture_failure_raises_CaptureFailedError() -> None:
    from blondi.services.contracts import CaptureFailedError

    svc = _make_service()
    svc._recorder.is_recording = False
    svc.start(
        map_name_prefix="test",
        default_capture_sources=["left"],
        fiducial_id=None,
    )
    with patch(
        "blondi.robot.dual_side_capture.capture_sources",
        return_value={},  # žádné frame
    ):
        with pytest.raises(CaptureFailedError) as excinfo:
            svc.capture_and_record_checkpoint(
                ["left", "right"], image_poller=MagicMock()
            )
    assert excinfo.value.name.startswith("CP_")
    assert excinfo.value.saved_sources == ()
    assert "left" in excinfo.value.failed_sources


def test_start_resets_previous_state() -> None:
    svc = _make_service()
    svc._recorder.is_recording = False

    # První session.
    svc.start(
        map_name_prefix="test",
        default_capture_sources=["left"],
        fiducial_id=None,
    )
    svc.add_unnamed_waypoint()
    assert svc.waypoint_count == 1

    # Simuluj abort a start znovu — service by neměl nést staré checkpointy.
    svc._recorder.is_recording = False
    svc.start(
        map_name_prefix="test2",
        default_capture_sources=["left"],
        fiducial_id=None,
    )
    assert svc.waypoint_count == 0
    assert svc.start_waypoint_id is None
