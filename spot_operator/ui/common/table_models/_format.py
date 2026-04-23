"""Sdílené cell formatters pro CRUD table modely (PR-15 FIND-170)."""

from __future__ import annotations

from datetime import datetime


def format_local_datetime(dt: datetime | None) -> str:
    """Formátuje timestamp v lokální časové zóně jako '23. 04. 2026 15:30'.

    Dřívější ``dt.isoformat(timespec="seconds")`` produkoval UTC ISO,
    což nebylo pro operátora čitelné.
    """
    if dt is None:
        return ""
    return dt.astimezone().strftime("%d. %m. %Y %H:%M")


def format_optional_plate(plate: str | None) -> str:
    """Repo vrací None pro prázdnou detekci. UI zobrazí '?' (PR-07 FIND-030)."""
    return plate if plate else "?"


__all__ = ["format_local_datetime", "format_optional_plate"]
