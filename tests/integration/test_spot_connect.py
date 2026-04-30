"""Integration test — reálné připojení ke Spotovi.

Spouštěj jen pokud je dostupný robot:
  set SPOT_INTEGRATION_TESTS=1
  set SPOT_IP=192.168.80.3
  set SPOT_USERNAME=admin
  set SPOT_PASSWORD=...
"""

from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("SPOT_INTEGRATION_TESTS") != "1",
    reason="SPOT_INTEGRATION_TESTS not set",
)


def test_connect_and_disconnect():
    from blondi.robot.session_factory import connect

    host = os.environ["SPOT_IP"]
    user = os.environ["SPOT_USERNAME"]
    password = os.environ["SPOT_PASSWORD"]
    bundle = connect(host, user, password)
    try:
        assert bundle.session.is_connected
    finally:
        bundle.disconnect()
