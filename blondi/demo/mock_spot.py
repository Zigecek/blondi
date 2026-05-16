"""MockSpotBundle — náhrada za SpotBundle pro demo režim.

Implementuje stejné rozhraní jako ``blondi.robot.session_factory.SpotBundle``,
ale všechny operace jsou no-opy / log-only / fake. Použití: dispatch v
``connect()`` / ``connect_partial()`` při ``demo_mode=True``.

Rozhraní pokryté tímto mockem:
- ``session.disconnect()``, ``session.hostname``, ``session.robot``,
  ``session.graph_nav_client``
- ``estop.start()/shutdown()/trigger()/release()``
- ``lease.acquire()/release()``
- ``power.power_on()/stand()/power_off()``
- ``move_dispatcher.send_velocity(...)/stop()/shutdown()``
- ``bundle.get_info()``, ``bundle.disconnect()``,
  ``bundle.missing_capabilities()``, ``bundle.ensure_operator_ready()``
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from blondi.logging_config import get_logger
from blondi.robot.session_factory import BundleInfo, SpotBundle

_log = get_logger(__name__)

DEFAULT_DEMO_HOSTNAME = "demo-spot"
DEFAULT_DEMO_SOURCES: tuple[str, ...] = (
    "frontleft_fisheye_image",
    "frontright_fisheye_image",
    "left_fisheye_image",
    "right_fisheye_image",
    "back_fisheye_image",
)


@dataclass
class _MockRobot:
    """Náhrada za bosdyn ``robot`` objekt — minimum pro power_state."""

    powered_on: bool = False

    def is_powered_on(self) -> bool:
        return self.powered_on


@dataclass
class _MockSession:
    """Náhrada za autonomy ``SpotSession``.

    Atribut ``_demo_available_sources`` čte ``SpotBundle.get_info()`` jako
    signál že jde o mock — nepokouší se pak volat ``ImagePoller`` z autonomy
    (ten by selhal, protože session není reálný bosdyn klient).
    """

    hostname: str = DEFAULT_DEMO_HOSTNAME
    robot: _MockRobot = field(default_factory=_MockRobot)
    graph_nav_client: Any = None
    _demo_available_sources: tuple[str, ...] = DEFAULT_DEMO_SOURCES

    def disconnect(self) -> None:
        _log.info("MockSession.disconnect (no-op)")


class _MockEstop:
    """Náhrada za autonomy ``EstopManager``."""

    def __init__(self) -> None:
        self.is_triggered = False

    def start(self) -> None:
        _log.info("MockEstop.start (no-op)")

    def shutdown(self) -> None:
        _log.info("MockEstop.shutdown (no-op)")

    def trigger(self) -> None:
        _log.info("MockEstop.trigger — robot 'cut'")
        self.is_triggered = True

    def release(self) -> None:
        _log.info("MockEstop.release — E-Stop uvolněn")
        self.is_triggered = False


class _MockLease:
    """Náhrada za autonomy ``LeaseManager``."""

    def acquire(self) -> None:
        _log.info("MockLease.acquire (no-op)")

    def release(self) -> None:
        _log.info("MockLease.release (no-op)")


class _MockPower:
    """Náhrada za autonomy ``PowerManager``.

    ``power_on()`` má krátkou prodlevu (2 s), aby UI ukázalo progress bar
    realisticky. ``stand()`` je no-op (idempotentní jako reálný SDK).
    """

    def __init__(self, robot: _MockRobot):
        self._robot = robot

    def power_on(self) -> None:
        _log.info("MockPower.power_on (sleep 2 s pro UI feedback)")
        time.sleep(2.0)
        self._robot.powered_on = True

    def stand(self) -> None:
        _log.info("MockPower.stand (no-op)")

    def power_off(self) -> None:
        _log.info("MockPower.power_off (no-op)")
        self._robot.powered_on = False


class _MockMoveDispatcher:
    """Náhrada za autonomy ``MoveCommandDispatcher``."""

    def send_velocity(
        self,
        vx: float,
        vy: float,
        vyaw: float,
        *,
        avoidance_strength: float = 0.0,
    ) -> None:
        if vx == 0 and vy == 0 and vyaw == 0:
            return
        _log.debug(
            "MockMoveDispatcher.send_velocity vx=%.2f vy=%.2f vyaw=%.2f",
            vx,
            vy,
            vyaw,
        )

    def stop(self) -> None:
        _log.debug("MockMoveDispatcher.stop")

    def shutdown(self) -> None:
        _log.info("MockMoveDispatcher.shutdown (no-op)")


def build_mock_bundle(hostname: str | None = None) -> SpotBundle:
    """Vrátí ``SpotBundle`` naplněný mock managery.

    Důležité: vrací **skutečný ``SpotBundle``** (ne podtřídu), aby ``isinstance``
    a duck typing v ostatním kódu nadále fungovaly. ``SpotBundle`` je
    ``@dataclass``, takže do něj lze nasadit mock objekty.
    """
    session = _MockSession(hostname=hostname or DEFAULT_DEMO_HOSTNAME)
    bundle = SpotBundle(
        session=session,
        estop=_MockEstop(),
        lease=_MockLease(),
        power=_MockPower(session.robot),
        move_dispatcher=_MockMoveDispatcher(),
    )
    _log.info("MockSpotBundle assembled (hostname=%s)", session.hostname)
    return bundle


def build_demo_bundle_info(hostname: str | None = None) -> BundleInfo:
    """Vrátí ``BundleInfo`` pro demo režim — pro testy a UI populate."""
    return BundleInfo(
        hostname=hostname or DEFAULT_DEMO_HOSTNAME,
        available_sources=list(DEFAULT_DEMO_SOURCES),
    )


__all__ = [
    "DEFAULT_DEMO_HOSTNAME",
    "DEFAULT_DEMO_SOURCES",
    "build_mock_bundle",
    "build_demo_bundle_info",
]
