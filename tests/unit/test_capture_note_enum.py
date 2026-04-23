"""Test pro CaptureNote enum + CaptureFailedError (PR-02, PR-14)."""

from __future__ import annotations

import pytest

from spot_operator.services.contracts import CaptureFailedError, CaptureNote


def test_capture_note_values() -> None:
    assert CaptureNote.OK.value == ""
    assert CaptureNote.CAPTURE_FAILED.value == "capture_failed"
    assert CaptureNote.CAPTURE_PARTIAL.value == "capture_partial"


def test_capture_failed_error_holds_sources() -> None:
    exc = CaptureFailedError(
        name="CP_001",
        saved_sources=[],
        failed_sources=["left_fisheye_image", "right_fisheye_image"],
    )
    assert exc.name == "CP_001"
    assert exc.saved_sources == ()
    assert exc.failed_sources == ("left_fisheye_image", "right_fisheye_image")
    assert "0 saved" in str(exc)


def test_capture_failed_error_is_runtime_error() -> None:
    exc = CaptureFailedError(name="x", saved_sources=[], failed_sources=["a"])
    assert isinstance(exc, RuntimeError)


def test_capture_failed_error_raises() -> None:
    with pytest.raises(CaptureFailedError, match="0 saved"):
        raise CaptureFailedError(name="test", saved_sources=[], failed_sources=[])
