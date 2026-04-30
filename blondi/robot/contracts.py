"""Protokolové kontrakty mezi ``blondi`` a ``autonomy``.

Místo ``getattr`` ducky typing s tichým ``None`` fallback používáme
``typing.Protocol`` — static type checker upozorní na mismatch hned,
ne až v runtime. Při upgrade autonomy SDK se pak rozjede test / mypy,
ne produkce (PR-13 FIND-177, FIND-178).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class NavigationResultProtocol(Protocol):
    """Co blondi očekává od ``autonomy.GraphNavNavigator.navigate_to``."""

    outcome: Any  # NavigationOutcome enum
    message: str
    ok: bool
    is_localization_loss: bool


@runtime_checkable
class ImagePollerProtocol(Protocol):
    """Co blondi očekává od ``autonomy.ImagePoller``."""

    def capture(self, source: str) -> Any | None: ...

    def list_sources(self) -> list[str]: ...


@runtime_checkable
class SessionProtocol(Protocol):
    """Co blondi očekává od ``autonomy.SpotSession``."""

    robot: Any
    graph_nav_client: Any

    def disconnect(self) -> None: ...


@runtime_checkable
class LeaseManagerProtocol(Protocol):
    """Co blondi očekává od ``autonomy.LeaseManager``."""

    def acquire(self) -> None: ...

    def release(self) -> None: ...


__all__ = [
    "NavigationResultProtocol",
    "ImagePollerProtocol",
    "SessionProtocol",
    "LeaseManagerProtocol",
]
