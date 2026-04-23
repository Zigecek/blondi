"""Detekce aktuálního power state Spota — je zapnutý? stojí?

Používá bosdyn `robot.is_powered_on()` pro rychlou "power on" detekci.
Pro "stojí/sedí" by teoreticky šel `robot_state_client.get_robot_state()` +
koukat na `kinematic_state.behavior_state`, ale v praxi stačí power check —
`stand` je idempotentní, takže pokud je motor on, předpokládáme že je
v použitelném stavu a nepotřebuje další tlačítko.

Lazy import bosdyn — tento modul se importuje ze spot_operator.robot.
"""

from __future__ import annotations

import time
from typing import Any

from spot_operator.logging_config import get_logger

_log = get_logger(__name__)


def is_motors_powered(bundle: Any) -> bool | None:
    """Vrací True pokud jsou motory Spota zapnuté, False pokud vypnuté,
    None pokud RPC selhal (stav je neznámý).

    Oproti 1.3 verzi (která vracela False i při RPC chybě, což mátlo UI —
    uživatel pak klikal "Zapnout" na already-on robota) teď signalizuje
    explicit neznámý stav.

    Používá bosdyn ``robot.is_powered_on()`` — RPC call ~100 ms, volat jen
    jednou při inicializaci stránky.
    """
    robot = getattr(bundle.session, "robot", None) if bundle is not None else None
    if robot is None:
        return None
    try:
        return bool(robot.is_powered_on())
    except Exception as exc:
        _log.warning("is_powered_on check failed: %s", exc)
        return None


def wait_until_powered_off(
    robot: Any,
    *,
    max_wait_s: float = 10.0,
    poll_interval_s: float = 0.2,
) -> bool:
    """Poll ``robot.is_powered_on()`` dokud není False nebo vyprší timeout.

    Bosdyn ``power_off`` je asynchronní — RPC se vrací hned, ale motory
    se vypínají v řádu 1-2 s. Pokud hned po ``power_off`` zavoláme
    ``EstopManager.start()`` nebo jiný krok, který vyžaduje motors=off,
    dostaneme ``MotorsOnError``. Tato funkce po power_off počká, až se
    stav reálně ustálí.

    Args:
        robot: bosdyn Robot objekt (z ``session.robot``).
        max_wait_s: maximální doba čekání (default 10 s).
        poll_interval_s: interval mezi polly (default 200 ms).

    Returns:
        True pokud se motory vypnuly, False pokud vyprší timeout nebo RPC selhal.
    """
    if robot is None:
        return False
    deadline = time.monotonic() + max_wait_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if not robot.is_powered_on():
                _log.info("wait_until_powered_off: motors OFF")
                return True
        except Exception as exc:
            last_error = exc
            _log.debug("is_powered_on poll failed: %s", exc)
        time.sleep(poll_interval_s)
    if last_error is not None:
        _log.warning(
            "wait_until_powered_off timed out after %.1f s (last error: %s)",
            max_wait_s, last_error,
        )
    else:
        _log.warning("wait_until_powered_off timed out after %.1f s", max_wait_s)
    return False


__all__ = ["is_motors_powered", "wait_until_powered_off"]
