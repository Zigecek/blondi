"""Unit test — RobotLostError detekce v PlaybackService._is_robot_lost_error.

Ověří, že všechny substring markery z `ROBOT_LOST_ERROR_MARKERS` jsou správně
detekovány v `NavigationResult.message`. Regressní ochrana — kdyby bosdyn
v budoucnu změnilo text exception message, test upozorní.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from blondi.constants import ROBOT_LOST_ERROR_MARKERS


def _fake_result(message: str):
    """Minimální NavigationResult-like mock (má jen atribut `message`)."""
    return SimpleNamespace(message=message)


def _get_checker():
    """Vrátí unbound `_is_robot_lost_error` na instanci; nepotřebuje bundle."""
    from blondi.services.playback_service import PlaybackService

    # Duck-type instance — `_is_robot_lost_error` nepoužívá self kromě signature.
    instance = PlaybackService.__new__(PlaybackService)
    return instance._is_robot_lost_error


@pytest.mark.parametrize(
    "message",
    [
        "bosdyn.api.graph_nav.NavigateToResponse (RobotLostError): Cannot issue a navigation request when the robot is already lost.",
        "RobotLostError: something",
        "Robot is already lost — try re-localizing.",
        "ROBOTLOSTERROR",  # uppercase
        "foo already lost bar",  # partial match substring
    ],
)
def test_robot_lost_error_detected(message: str) -> None:
    check = _get_checker()
    assert check(_fake_result(message)) is True, (
        f"Message should be detected as RobotLostError: {message!r}"
    )


@pytest.mark.parametrize(
    "message",
    [
        "Navigation command timed out on the robot.",
        "Robot is stuck.",
        "No route to waypoint.",
        "",
        "GraphNav client not available.",
    ],
)
def test_non_lost_errors_not_detected(message: str) -> None:
    check = _get_checker()
    assert check(_fake_result(message)) is False, (
        f"Message should NOT be detected as RobotLostError: {message!r}"
    )


def test_markers_list_not_empty() -> None:
    """Safety net: markers list nesmí být prázdný (detekce by nefungovala)."""
    assert len(ROBOT_LOST_ERROR_MARKERS) > 0
    assert all(isinstance(m, str) and m for m in ROBOT_LOST_ERROR_MARKERS)
