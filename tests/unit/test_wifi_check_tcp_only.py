"""Testy pro WifiCheckResult.ok — TCP-only based (PR-10 FIND-128)."""

from __future__ import annotations

from blondi.services.spot_wifi import WifiCheckResult


def test_ok_true_when_tcp_reachable() -> None:
    r = WifiCheckResult(
        ip="1.2.3.4", ping_responses=0, ping_attempts=3, tcp_reachable=True,
    )
    assert r.ok is True  # ping může být firewallem blokován


def test_ok_false_when_tcp_unreachable() -> None:
    r = WifiCheckResult(
        ip="1.2.3.4", ping_responses=3, ping_attempts=3, tcp_reachable=False,
    )
    assert r.ok is False


def test_ok_false_when_all_unreachable() -> None:
    r = WifiCheckResult(
        ip="1.2.3.4", ping_responses=0, ping_attempts=3, tcp_reachable=False,
    )
    assert r.ok is False
