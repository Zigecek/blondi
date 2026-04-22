"""Factory pro sestavení SpotSession + všech manažerů v bezpečném pořadí.

Připojení: SDK session → authenticate → time sync → E-Stop endpoint + keep-alive →
lease acquire → PowerManager + MoveCommandManager připravené. Teardown je reversní.

Používá existující třídy z autonomy — neduplikujeme jejich logiku.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from spot_operator.logging_config import get_logger

_log = get_logger(__name__)


@dataclass
class SpotBundle:
    """Seskupení všech aktivních manažerů pro Spot robota.

    Atribut `session` je vždy neprázdný (pokud bundle existuje jako "connected").
    Ostatní jsou None pokud se je nepodařilo inicializovat.
    """

    session: object  # SpotSession from autonomy
    estop: object | None = None  # EstopManager
    lease: object | None = None  # LeaseManager
    power: object | None = None  # PowerManager
    move_dispatcher: object | None = None  # MoveCommandDispatcher

    def disconnect(self) -> None:
        """Uklidí všechny manažery v opačném pořadí než při connect."""
        try:
            if self.move_dispatcher is not None:
                self.move_dispatcher.stop()  # type: ignore[attr-defined]
        except Exception as exc:
            _log.warning("move_dispatcher.stop failed: %s", exc)
        try:
            if self.lease is not None:
                self.lease.release()  # type: ignore[attr-defined]
        except Exception as exc:
            _log.warning("lease.release failed: %s", exc)
        try:
            if self.estop is not None:
                self.estop.shutdown()  # type: ignore[attr-defined]
        except Exception as exc:
            _log.warning("estop.shutdown failed: %s", exc)
        try:
            self.session.disconnect()  # type: ignore[attr-defined]
        except Exception as exc:
            _log.warning("session.disconnect failed: %s", exc)


def connect(
    hostname: str,
    username: str,
    password: str,
    *,
    with_lease: bool = True,
    with_estop: bool = True,
) -> SpotBundle:
    """Připojí se ke Spotu a postaví kompletní bundle manažerů.

    Nevyhazuje na dílčí chybu — vrací bundle s tím, co se podařilo. Volající
    pozná chybějící klienty podle `None`.
    """
    from app.robot.sdk_session import SpotSession

    session = SpotSession()
    session.connect(hostname, username, password)
    _log.info("Spot session connected to %s", hostname)

    bundle = SpotBundle(session=session)

    if with_estop:
        try:
            from app.robot.estop import EstopManager

            estop = EstopManager(session)
            estop.start()
            bundle.estop = estop
            _log.info("E-Stop endpoint registered")
        except Exception as exc:
            _log.exception("Failed to start E-Stop manager: %s", exc)

    if with_lease:
        try:
            from app.robot.lease import LeaseManager

            lease = LeaseManager(session)
            lease.acquire()
            bundle.lease = lease
            _log.info("Lease acquired")
        except Exception as exc:
            _log.exception("Failed to acquire lease: %s", exc)

    try:
        from app.robot.power import PowerManager

        bundle.power = PowerManager(session)
    except Exception as exc:
        _log.exception("Failed to create PowerManager: %s", exc)

    try:
        from app.robot.commands import MoveCommandDispatcher, MoveCommandManager

        mgr = MoveCommandManager(session)
        dispatcher = MoveCommandDispatcher(mgr)
        dispatcher.start()
        bundle.move_dispatcher = dispatcher
    except Exception as exc:
        _log.exception("Failed to start move dispatcher: %s", exc)

    return bundle


__all__ = ["SpotBundle", "connect"]
