"""Detekce aktuálního power state Spota — je zapnutý? stojí?

Používá bosdyn `robot.is_powered_on()` pro rychlou "power on" detekci.
Pro "stojí/sedí" by teoreticky šel `robot_state_client.get_robot_state()` +
koukat na `kinematic_state.behavior_state`, ale v praxi stačí power check —
`stand` je idempotentní, takže pokud je motor on, předpokládáme že je
v použitelném stavu a nepotřebuje další tlačítko.

Lazy import bosdyn — tento modul se importuje ze spot_operator.robot.
"""

from __future__ import annotations

from typing import Any

from spot_operator.logging_config import get_logger

_log = get_logger(__name__)


def is_motors_powered(bundle: Any) -> bool:
    """Vrací True pokud jsou motory Spota zapnuté.

    Používá bosdyn ``robot.is_powered_on()`` — RPC call ~100 ms, volat jen
    jednou při inicializaci stránky. Při chybě (odpojené Wi-Fi, zombie
    session) vrací False, takže UI ukáže "Spot vypnutý" a user klikne
    "Zapnout" → explicitní error dialog.
    """
    robot = getattr(bundle.session, "robot", None) if bundle is not None else None
    if robot is None:
        return False
    try:
        return bool(robot.is_powered_on())
    except Exception as exc:
        _log.warning("is_powered_on check failed: %s", exc)
        return False


__all__ = ["is_motors_powered"]
