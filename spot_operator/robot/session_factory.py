"""Factory pro sestavení SpotSession + všech manažerů v bezpečném pořadí.

Připojení: SDK session → authenticate → time sync → E-Stop endpoint + keep-alive →
lease acquire → PowerManager + MoveCommandManager připravené. Teardown je reversní.

Používá existující třídy z autonomy — neduplikujeme jejich logiku.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from spot_operator.logging_config import get_logger

_log = get_logger(__name__)

# Timeouts pro jednotlivé disconnect kroky (s). Pokud RPC zavěsí na síti,
# zabrání to UI zamrznutí na `closeEvent`.
_DISCONNECT_STEP_TIMEOUT_S: float = 3.0
# Max doba čekání na power_off completion před start E-Stop auto-recovery.
_POWER_OFF_WAIT_S: float = 10.0


def _teardown_with_timeout(name: str, fn: Callable[[], None], timeout_s: float) -> None:
    """Spustí ``fn()`` v pool threadu s timeoutem. Při timeoutu log + pokračuj.

    Proč ne ``signal.alarm`` nebo ``threading.Timer``? Bosdyn RPC je ukryté
    uvnitř C++ knihovny; není spolehlivý přerušovací signál. Pool thread
    aspoň uvolní volající (UI thread) — visící worker bude garbage
    collected při aplikačním exitu.
    """
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"disconnect-{name}") as pool:
        future = pool.submit(fn)
        try:
            future.result(timeout=timeout_s)
        except FuturesTimeout:
            _log.warning(
                "Disconnect krok %r nedokončil za %.1f s — pokračuji, visící "
                "operace se uklidí na exit aplikace.",
                name, timeout_s,
            )
        except Exception as exc:
            _log.warning("Disconnect krok %r selhal: %s", name, exc)


@dataclass(slots=True)
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

    def missing_capabilities(self) -> list[str]:
        missing: list[str] = []
        if self.session is None:
            missing.append("session")
        if self.estop is None:
            missing.append("estop")
        if self.lease is None:
            missing.append("lease")
        if self.power is None:
            missing.append("power")
        if self.move_dispatcher is None:
            missing.append("move_dispatcher")
        return missing

    def ensure_operator_ready(self) -> None:
        missing = self.missing_capabilities()
        if not missing:
            return
        raise RuntimeError(
            "Připojení ke Spotovi je neúplné. Chybí: " + ", ".join(missing)
        )

    def disconnect(self) -> None:
        """Uklidí všechny manažery v opačném pořadí než při connect.

        Každý krok má samostatný timeout (``_DISCONNECT_STEP_TIMEOUT_S``) —
        pokud bosdyn RPC zavěsí na odpojeném Wi-Fi, UI se neblokuje
        donekonečna. Visící operace uklidí proces při exit.
        """
        if self.move_dispatcher is not None:
            # autonomy `MoveCommandDispatcher` používá `.shutdown()` pro zastavení
            # background threadu (žádná .stop() metoda — .stop() znamená 'zastav robota').
            _teardown_with_timeout(
                "move_dispatcher",
                self.move_dispatcher.shutdown,  # type: ignore[attr-defined]
                _DISCONNECT_STEP_TIMEOUT_S,
            )
        if self.lease is not None:
            _teardown_with_timeout(
                "lease.release",
                self.lease.release,  # type: ignore[attr-defined]
                _DISCONNECT_STEP_TIMEOUT_S,
            )
        if self.estop is not None:
            _teardown_with_timeout(
                "estop.shutdown",
                self.estop.shutdown,  # type: ignore[attr-defined]
                _DISCONNECT_STEP_TIMEOUT_S,
            )
        _teardown_with_timeout(
            "session.disconnect",
            self.session.disconnect,  # type: ignore[attr-defined]
            _DISCONNECT_STEP_TIMEOUT_S,
        )


def connect_partial(
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
            from app.robot.lease import LeaseManager
            from app.robot.power import PowerManager
            from bosdyn.client.estop import MotorsOnError

            try:
                estop = EstopManager(session)
                estop.start()
            except MotorsOnError:
                # Spot motory běží — typicky předchozí crash/instance
                # je nevypnula, nebo jiný klient má Spot pod sebou. Bosdyn
                # neumí měnit E-Stop config zatímco motor je ON. Auto-recovery:
                # získat lease (power_off ho vyžaduje) → power_off → retry
                # estop.start().
                _log.warning(
                    "E-Stop setup: Motors on — získávám lease a vypínám motory (auto-recovery)."
                )
                try:
                    lease = LeaseManager(session)
                    lease.acquire()
                    bundle.lease = lease
                    _log.info("Lease získán pro E-Stop auto-recovery")
                except Exception as exc:
                    _log.exception("Auto-recovery: lease acquire selhal: %s", exc)
                    raise RuntimeError(
                        "E-Stop setup selhal (motory on) a lease acquire taky selhal. "
                        "Jiný klient má Spot pod sebou — odpoj ho nebo restartuj Spota."
                    ) from exc
                try:
                    PowerManager(session).power_off()
                    _log.info("Power-off RPC odeslán pro E-Stop auto-recovery")
                except Exception as exc:
                    _log.exception("Auto-recovery power_off selhal: %s", exc)
                    raise RuntimeError(
                        "E-Stop setup selhal (motory on) a power_off taky selhal. "
                        "Restartuj Spota fyzicky."
                    ) from exc
                # Bosdyn ``power_off`` je asynchronní — musíme počkat, až motory
                # reálně vypnou, jinak další ``estop.start()`` spadne na další
                # ``MotorsOnError``. Viz PR-01 FIND-062.
                from spot_operator.robot.power_state import wait_until_powered_off

                robot = getattr(session, "robot", None)
                if robot is not None and not wait_until_powered_off(
                    robot, max_wait_s=_POWER_OFF_WAIT_S
                ):
                    raise RuntimeError(
                        "Power-off timeout — motory stále běží po "
                        f"{_POWER_OFF_WAIT_S:.0f} s. E-Stop setup nelze dokončit."
                    )
                # Retry — motory jsou off.
                estop = EstopManager(session)
                estop.start()

            bundle.estop = estop
            _log.info("E-Stop endpoint registered")
        except Exception as exc:
            _log.exception("Failed to start E-Stop manager: %s", exc)

    # Lease sekce: skip pokud už byl získán v E-Stop auto-recovery výše.
    if with_lease and bundle.lease is None:
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
        # `MoveCommandDispatcher.__init__` spouští thread sám — žádná .start() metoda.
        bundle.move_dispatcher = MoveCommandDispatcher(mgr)
    except Exception as exc:
        _log.exception("Failed to create move dispatcher: %s", exc)

    return bundle


def connect(
    hostname: str,
    username: str,
    password: str,
    *,
    with_lease: bool = True,
    with_estop: bool = True,
) -> SpotBundle:
    """Operator-facing connect: requires a fully initialized bundle."""
    bundle = connect_partial(
        hostname,
        username,
        password,
        with_lease=with_lease,
        with_estop=with_estop,
    )
    try:
        bundle.ensure_operator_ready()
    except Exception:
        try:
            bundle.disconnect()
        except Exception as exc:
            _log.warning("connect cleanup after partial failure failed: %s", exc)
        raise
    return bundle


__all__ = ["SpotBundle", "connect", "connect_partial"]
