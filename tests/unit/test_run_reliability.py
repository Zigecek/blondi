from __future__ import annotations

from datetime import datetime, timezone

import pytest

from spot_operator.robot.session_factory import SpotBundle
from spot_operator.db.repositories import runs_repo


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _SessionStub:
    def __init__(self, values):
        self._values = list(values)

    def execute(self, _stmt):
        value = self._values.pop(0) if self._values else None
        return _ScalarResult(value)


def test_generate_unique_run_code_retries_on_collision() -> None:
    session = _SessionStub([123, None])

    code = runs_repo.generate_unique_run_code(
        session, now=datetime(2026, 4, 23, 9, 30, 0, tzinfo=timezone.utc)
    )

    assert code == "run_20260423_093000_01"


def test_spot_bundle_operator_ready_requires_all_capabilities() -> None:
    bundle = SpotBundle(session=object())

    with pytest.raises(RuntimeError) as exc:
        bundle.ensure_operator_ready()

    assert "estop" in str(exc.value)
    assert "lease" in str(exc.value)
    assert "power" in str(exc.value)
    assert "move_dispatcher" in str(exc.value)

