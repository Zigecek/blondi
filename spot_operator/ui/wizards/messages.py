"""Centralizované CZ texty pro wizard close confirmation + stavové hlášky.

PR-09 FIND-139: tři wizardy měly kopie podobných zpráv. Centralizace sem
usnadňuje budoucí úpravu (překlad, reformulace).
"""

from __future__ import annotations

CLOSE_WARNING_RECORDING: str = (
    "Nahrávání probíhá. Po zavření se nahrávka zruší a nic se neuloží "
    "do databáze. Pokračovat?"
)

CLOSE_WARNING_PLAYBACK: str = (
    "Autonomní jízda probíhá nebo máš aktivní spojení se Spotem. "
    "Po zavření se vše ukončí. Pokračovat?"
)

CLOSE_WARNING_WALK: str = (
    "Chůze se Spotem — po zavření se WASD teleop zastaví a uvolní "
    "image pipeline. Spot zůstane zapnutý (neodpojuje se)."
)

CLEANUP_FAILED_TITLE: str = "Úklid wizardu selhal"
CLEANUP_FAILED_MESSAGE: str = (
    "Nepodařilo se korektně uklidit zdroje (lease, session). "
    "Zkusit zavřít znovu nebo ignorovat?"
)

__all__ = [
    "CLOSE_WARNING_RECORDING",
    "CLOSE_WARNING_PLAYBACK",
    "CLOSE_WARNING_WALK",
    "CLEANUP_FAILED_TITLE",
    "CLEANUP_FAILED_MESSAGE",
]
