"""Spot credentials — metadata v DB, heslo v Windows Credential Locker přes `keyring`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import keyring
import keyring.errors

from spot_operator.db.engine import Session
from spot_operator.db.repositories import credentials_repo
from spot_operator.logging_config import get_logger

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SpotCredentialView:
    """View-only DTO předávané z DB do UI (bez hesla)."""

    id: int
    label: str
    hostname: str
    username: str
    keyring_ref: str


def list_credentials() -> list[SpotCredentialView]:
    with Session() as s:
        rows = credentials_repo.list_all(s)
        return [
            SpotCredentialView(
                id=r.id,
                label=r.label,
                hostname=r.hostname,
                username=r.username,
                keyring_ref=r.keyring_ref,
            )
            for r in rows
        ]


def save_credentials(
    *,
    service_name: str,
    label: str,
    hostname: str,
    username: str,
    password: str,
) -> SpotCredentialView:
    """Uloží credentials: heslo do keyringu, metadata do DB.

    Pokud label už existuje, překryje existující záznam + keyring entry.
    """
    if not password:
        raise ValueError("Password must not be empty.")
    keyring_ref = _build_keyring_ref(label, username)

    try:
        keyring.set_password(service_name, keyring_ref, password)
    except keyring.errors.KeyringError as exc:
        raise RuntimeError(
            f"Nelze uložit heslo do Windows Credential Locker: {exc}"
        ) from exc

    with Session() as s:
        existing = credentials_repo.get_by_label(s, label)
        if existing:
            existing.hostname = hostname
            existing.username = username
            existing.keyring_ref = keyring_ref
            s.commit()
            return SpotCredentialView(
                id=existing.id,
                label=existing.label,
                hostname=existing.hostname,
                username=existing.username,
                keyring_ref=existing.keyring_ref,
            )
        row = credentials_repo.create(
            s,
            label=label,
            hostname=hostname,
            username=username,
            keyring_ref=keyring_ref,
        )
        s.commit()
        return SpotCredentialView(
            id=row.id,
            label=row.label,
            hostname=row.hostname,
            username=row.username,
            keyring_ref=row.keyring_ref,
        )


def load_password(service_name: str, keyring_ref: str) -> Optional[str]:
    try:
        return keyring.get_password(service_name, keyring_ref)
    except keyring.errors.KeyringError as exc:
        _log.warning("keyring.get_password failed: %s", exc)
        return None


def delete_credentials(service_name: str, cred_id: int) -> bool:
    """Smaže credential z DB i z keyringu."""
    from spot_operator.db.models import SpotCredential

    with Session() as s:
        row = s.get(SpotCredential, cred_id)
        if row is None:
            return False
        keyring_ref = row.keyring_ref
        s.delete(row)
        s.commit()
    try:
        keyring.delete_password(service_name, keyring_ref)
    except keyring.errors.KeyringError as exc:
        _log.warning("keyring.delete_password failed (ignoring): %s", exc)
    return True


def touch_last_used(cred_id: int) -> None:
    with Session() as s:
        credentials_repo.touch_last_used(s, cred_id)
        s.commit()


def _build_keyring_ref(label: str, username: str) -> str:
    return f"{label}:{username}"


__all__ = [
    "SpotCredentialView",
    "list_credentials",
    "save_credentials",
    "load_password",
    "delete_credentials",
    "touch_last_used",
]
