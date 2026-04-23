"""Smoke test — ověří, že autonomy veřejné API je dostupné a naše additive
moduly ho nerozbily. Spouští se **bez reálného Spota** (jen importy + hasattr).

Důvod: chráníme projekt před tichým selháním, kdyby:
 - autonomy projekt změnil signaturu `SpotSession.connect()` nebo přejmenoval klíčový symbol,
 - naše additive moduly v `autonomy/app/robot/` (fiducial_check, return_home,
   waypoint_namer) začaly být nekonzistentní po nějakém refactoru,
 - autonomy API ztratilo `LocalizationStrategy.SPECIFIC_FIDUCIAL`, kterou používá
   `spot_operator/services/playback_service.py`.

Pokud test padne, neznamená to nutně chybu v autonomy — může to být chyba u nás
(špatný import, špatná signatura). Oprav podle konkrétního assertion message.

Spouštění: `pytest tests/integration/test_autonomy_smoke.py -v`
Bez flag `SPOT_INTEGRATION_TESTS` (běží v defaultu).
"""

from __future__ import annotations


def test_additive_modules_importable() -> None:
    """Additive moduly v autonomy/app/robot/ musí být importovatelné a mít očekávané API."""
    from app.robot.fiducial_check import FiducialObservation, visible_fiducials
    from app.robot.return_home import return_home
    from app.robot.waypoint_namer import WaypointNameGenerator

    assert callable(visible_fiducials)
    assert callable(return_home)
    # FiducialObservation musí být dataclass s těmito atributy
    obs = FiducialObservation(tag_id=1, distance_m=1.0, frame_name="fiducial_1")
    assert obs.tag_id == 1
    assert obs.distance_m == 1.0

    # WaypointNameGenerator má dokumentované API
    gen = WaypointNameGenerator()
    assert gen.next_waypoint() == "WP_001"
    assert gen.next_checkpoint() == "CP_001"
    assert gen.next_waypoint() == "WP_002"


def test_autonomy_core_api_importable() -> None:
    """Klíčové autonomy symboly musí být stále dostupné a mít očekávané metody."""
    from app.models import LocalizationStrategy, NavigationOutcome  # noqa: F401
    from app.robot.commands import MoveCommandDispatcher, MoveCommandManager
    from app.robot.estop import EstopManager
    from app.robot.graphnav_navigation import GraphNavNavigator, NavigationResult  # noqa: F401
    from app.robot.graphnav_recording import GraphNavRecorder
    from app.robot.images import ImagePoller
    from app.robot.lease import LeaseManager  # noqa: F401
    from app.robot.power import PowerManager  # noqa: F401
    from app.robot.sdk_session import SpotSession

    # Instance bez .connect() musí jít (SpotSession.__init__ nic nevolá)
    session = SpotSession()
    assert not session.is_connected

    # Metody, které spot_operator volá, existují
    for cls, method in (
        (SpotSession, "connect"),
        (SpotSession, "disconnect"),
        (EstopManager, "trigger"),
        (EstopManager, "start"),
        (EstopManager, "shutdown"),
        (GraphNavRecorder, "start_recording"),
        (GraphNavRecorder, "stop_recording"),
        (GraphNavRecorder, "create_waypoint"),
        (GraphNavRecorder, "download_map"),
        (GraphNavNavigator, "navigate_to"),
        (GraphNavNavigator, "upload_map"),
        (GraphNavNavigator, "localize"),
        (GraphNavNavigator, "request_abort"),
        (GraphNavNavigator, "relocalize_nearest_fiducial"),
        (ImagePoller, "list_sources"),
        (ImagePoller, "capture"),
        (ImagePoller, "capture_many"),
        # MoveCommandDispatcher API: žádná .start() (thread v __init__),
        # .send_velocity() nikoli .send(), .shutdown() nikoli .stop() pro lifecycle.
        (MoveCommandDispatcher, "send_velocity"),
        (MoveCommandDispatcher, "stop"),       # stop ROBOTA, ne threadu
        (MoveCommandDispatcher, "shutdown"),   # stop THREADU
    ):
        assert hasattr(cls, method), f"{cls.__name__}.{method} chybí v autonomy"

    # Explicitně ověř, že autonomy v budoucnu NEZAVEDE `.start()` metodu, která by
    # tiše změnila kontrakt. Pokud by přibyla, máme chtít si o tom rozhodnout.
    assert not hasattr(MoveCommandDispatcher, "start"), (
        "MoveCommandDispatcher.start() existuje — spot_operator ji nevolá a nemá "
        "očekávat; překontroluj session_factory.connect()."
    )


def test_localization_strategy_enum() -> None:
    """Enum hodnoty, které spot_operator používá, musí existovat.

    Pokud test padne, autonomy přejmenoval/odstranil enum hodnotu a
    `spot_operator/services/playback_service.py::_localize_with_fallback` padne
    až za runtime. Tento test to chytí build-time.
    """
    from app.models import LocalizationStrategy, NavigationOutcome

    # Hodnoty používané v playback_service._localize_with_fallback:
    for name in ("SPECIFIC_FIDUCIAL", "FIDUCIAL_NEAREST"):
        assert hasattr(LocalizationStrategy, name), (
            f"LocalizationStrategy.{name} chybí — spot_operator.playback_service ho používá."
        )

    # Hodnoty, které playback_service dispeč (REACHED, LOST, STUCK, ABORTED, TIMEOUT, ...)
    for name in ("REACHED", "LOST", "STUCK", "ABORTED", "TIMEOUT"):
        assert hasattr(NavigationOutcome, name), (
            f"NavigationOutcome.{name} chybí — spot_operator ho používá."
        )


def test_spot_operator_imports_autonomy() -> None:
    """spot_operator moduly, které používají autonomy, musí jít importovat
    bez reálné DB ani Spota (jen statická analýza importů)."""
    import spot_operator.robot.session_factory  # noqa: F401
    import spot_operator.services.playback_service  # noqa: F401
    import spot_operator.services.recording_service  # noqa: F401

    # Také nepřímé volání — vyrobíme WaypointNameGenerator instanci přes
    # recording_service path, aby se ověřilo, že autonomy import v konstruktoru projde.
    # (RecordingService vyžaduje bundle, takže jen importujeme třídu.)
    from spot_operator.services.recording_service import RecordingService
    assert RecordingService is not None
