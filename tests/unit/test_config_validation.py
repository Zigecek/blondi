"""Testy pro env validation v config.py (PR-10)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from spot_operator.config import _require_float


def test_require_float_default() -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("SPOT_TIMEOUT_SECONDS", None)
        assert _require_float("SPOT_TIMEOUT_SECONDS", "15") == 15.0


def test_require_float_parses_numeric() -> None:
    with patch.dict(os.environ, {"FOO": "3.14"}):
        assert _require_float("FOO", "0") == 3.14


def test_require_float_invalid_raises_with_key() -> None:
    with patch.dict(os.environ, {"FOO": "abc"}):
        with pytest.raises(RuntimeError, match="FOO"):
            _require_float("FOO", "0")


def test_require_float_range_min() -> None:
    with patch.dict(os.environ, {"FOO": "0.05"}):
        with pytest.raises(RuntimeError, match="minimum"):
            _require_float("FOO", "0", min_val=0.1)


def test_require_float_range_max() -> None:
    with patch.dict(os.environ, {"FOO": "100"}):
        with pytest.raises(RuntimeError, match="maximum"):
            _require_float("FOO", "0", max_val=50)


def test_require_float_in_range() -> None:
    with patch.dict(os.environ, {"FOO": "2.0"}):
        assert _require_float("FOO", "0", min_val=0.1, max_val=20.0) == 2.0
