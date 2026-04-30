"""Strict fiducial localization — start_waypoint hint + SPECIFIC_FIDUCIAL refine.

**Proč tento modul existuje:** autonomy's `GraphNavNavigator.localize` podporuje
jednu strategii v jednu chvíli — buď `SPECIFIC_FIDUCIAL` (použít konkrétní
AprilTag ID), nebo `NEAR_WAYPOINT` (hint, žes u konkrétního waypointu), ale
**NE oboje najednou**.

Problém bez hintu: fiducial má v mapě obvykle víc observací (z různých waypointů,
jak robot během nahrávání vícekrát viděl tentýž tag z různých úhlů). Při
playbackové `SPECIFIC_FIDUCIAL` init si bosdyn vybere **kteroukoli** z těchto
observací → poloha robota může být odvozena z waypointu uprostřed mapy, ne od
startu. Výsledek: robot si myslí že je uprostřed mapy a `navigate_to(CP_001)`
plánuje cestu z mis-lokalizované pozice → "jede na nejvzdálenější místo".

Tento helper volá bosdyn `set_localization` přímo s **oběma** parametry:
  - `initial_guess_localization.waypoint_id = start_waypoint_id` (hint)
  - `fiducial_init = FIDUCIAL_INIT_SPECIFIC` + `use_fiducial_id = tag_id` (precision)
  - `refine_fiducial_result_with_icp = True` (ICP dotuning pro přesnost)

Bosdyn pak bude preferovat observaci fiducialu která leží poblíž start_waypoint,
místo aby vybíral náhodně.
"""

from __future__ import annotations

from typing import Any

from blondi.logging_config import get_logger

_log = get_logger(__name__)


def localize_at_start(
    session: Any,
    *,
    fiducial_id: int,
    start_waypoint_id: str,
) -> str:
    """Lokalizuje robota ke startovnímu waypointu s doladěním přes fiducial.

    Vrací skutečně lokalizovaný waypoint_id (ten co bosdyn vrátil v response).
    Volající by měl ověřit, že odpovídá ``start_waypoint_id`` — pokud ne,
    fiducial observace byly ambigní a localizace skončila jinde.

    Raises ``RuntimeError`` pokud klient není dostupný nebo bosdyn SDK odmítl
    request (např. fiducial není vidět v kameře, žádný známý waypoint).
    """
    client = getattr(session, "graph_nav_client", None)
    if client is None:
        raise RuntimeError("GraphNav client není dostupný — session není navázaná.")

    from bosdyn.api.graph_nav import graph_nav_pb2, nav_pb2

    initial_guess = nav_pb2.Localization()
    if start_waypoint_id:
        initial_guess.waypoint_id = start_waypoint_id

    kwargs = {
        "initial_guess_localization": initial_guess,
        "ko_tform_body": None,
        "fiducial_init": graph_nav_pb2.SetLocalizationRequest.FIDUCIAL_INIT_SPECIFIC,
        "use_fiducial_id": int(fiducial_id),
        "do_ambiguity_check": True,
        "refine_fiducial_result_with_icp": True,
    }

    _log.info(
        "localize_at_start: fiducial_id=%d, start_waypoint=%s",
        fiducial_id, (start_waypoint_id or "(none)"),
    )
    try:
        resp = client.set_localization(**kwargs)
    except Exception as exc:
        # PR-05 FIND-068: klasifikace specifických bosdyn chyb by bylo
        # ideální (FiducialNotFoundError vs NetworkError), ale autonomy
        # jejich typy nereflektuje public. Minimálně CZ wrap + pass exc.
        msg = str(exc).lower()
        if "fiducial" in msg and ("not" in msg or "visible" in msg):
            raise RuntimeError(
                f"Fiducial {fiducial_id} není vidět v kameře nebo není v mapě. "
                "Přibliž Spota k fiducialu a zkus znovu."
            ) from exc
        raise RuntimeError(
            f"Bosdyn set_localization selhal (fiducial_id={fiducial_id}, "
            f"start_waypoint={start_waypoint_id}): {exc}"
        ) from exc

    # PR-05 FIND-067: pokud bosdyn reportuje ambiguity, logujeme warning.
    # Pole response se liší mezi bosdyn verzemi — použij getattr.
    ambiguity = getattr(resp, "ambiguity_result", None)
    if ambiguity is not None:
        ratio = getattr(ambiguity, "ambiguous_ratio", None)
        if ratio is not None and ratio > 0.5:
            _log.warning(
                "localize_at_start: ambiguity_result ratio=%.2f (>0.5) — "
                "bosdyn vybral jednu z podobných fiducial observací. "
                "Pokud robot jede zmatečně, přibliž ho blíž k fiducialu.",
                ratio,
            )

    try:
        localized_wp = resp.localization.waypoint_id
    except AttributeError:
        localized_wp = getattr(resp, "waypoint_id", "")

    if not localized_wp:
        raise RuntimeError(
            "Lokalizace vrátila prázdný waypoint_id — robot není na mapě."
        )

    _log.info(
        "localize_at_start OK: robot localized at waypoint %s (expected start=%s)",
        localized_wp, (start_waypoint_id or "(any)"),
    )
    return localized_wp


__all__ = ["localize_at_start"]
