"""Testy pro SpotBundle.disconnect timeout + get_info (PR-01, PR-09)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock


def test_disconnect_timeout_does_not_hang() -> None:
    """Simuluj blokující lease.release — disconnect nesmí UI zamrznout.

    PR-01 FIND-061: každý krok má 3 s timeout. Session.disconnect volá
    se po nich — celkem max ~12 s. Nastavíme test lease.release na 30 s
    a ověříme, že disconnect skončí <15 s.
    """
    from blondi.robot.session_factory import SpotBundle

    def _hang() -> None:
        time.sleep(30)

    bundle = SpotBundle(session=MagicMock())
    bundle.lease = MagicMock()
    bundle.lease.release.side_effect = _hang
    bundle.estop = MagicMock()
    bundle.move_dispatcher = MagicMock()

    start = time.monotonic()
    bundle.disconnect()
    elapsed = time.monotonic() - start
    assert elapsed < 15.0, f"disconnect zabralo {elapsed:.1f}s (očekáváno <15s)"


def test_bundle_get_info_returns_dataclass() -> None:
    from blondi.robot.session_factory import BundleInfo, SpotBundle

    session = MagicMock()
    session.hostname = "1.2.3.4"
    bundle = SpotBundle(session=session)
    # ImagePoller import selže v test env bez autonomy — get_info to má
    # graceful fallback s prázdným seznamem sources.
    info = bundle.get_info()
    assert isinstance(info, BundleInfo)
    assert info.hostname == "1.2.3.4"
    assert isinstance(info.available_sources, list)
