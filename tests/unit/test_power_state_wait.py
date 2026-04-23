"""Tests pro wait_until_powered_off (PR-01, PR-14)."""

from __future__ import annotations

from unittest.mock import MagicMock

from spot_operator.robot.power_state import is_motors_powered, wait_until_powered_off


def test_wait_until_powered_off_returns_true_when_already_off() -> None:
    robot = MagicMock()
    robot.is_powered_on.return_value = False
    assert wait_until_powered_off(robot, max_wait_s=0.5, poll_interval_s=0.05) is True


def test_wait_until_powered_off_times_out_when_still_on() -> None:
    robot = MagicMock()
    robot.is_powered_on.return_value = True  # never off
    assert wait_until_powered_off(robot, max_wait_s=0.3, poll_interval_s=0.1) is False


def test_wait_until_powered_off_eventually_succeeds() -> None:
    robot = MagicMock()
    # První 2 volání True, pak False.
    robot.is_powered_on.side_effect = [True, True, False]
    assert wait_until_powered_off(robot, max_wait_s=1.0, poll_interval_s=0.05) is True


def test_is_motors_powered_returns_none_on_rpc_failure() -> None:
    """PR-13: None = unknown, ne False (dřív matoucí)."""
    bundle = MagicMock()
    bundle.session.robot.is_powered_on.side_effect = RuntimeError("network")
    assert is_motors_powered(bundle) is None


def test_is_motors_powered_returns_bool_on_success() -> None:
    bundle = MagicMock()
    bundle.session.robot.is_powered_on.return_value = True
    assert is_motors_powered(bundle) is True
    bundle.session.robot.is_powered_on.return_value = False
    assert is_motors_powered(bundle) is False


def test_is_motors_powered_returns_none_when_robot_is_none() -> None:
    bundle = MagicMock()
    bundle.session.robot = None
    assert is_motors_powered(bundle) is None
