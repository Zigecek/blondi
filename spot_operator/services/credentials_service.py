"""Spot credentials — metadata v DB, heslo v Windows Credential Locker přes `keyring`.

PR-10 FIND-119: save_credentials teď commituje DB *první*, pak keyring —
zabraňuje osiřelým keyring záznamům při DB failure.
PR-10 FIND-120: při change username se starý keyring_ref explicitně maže.
PR-10 FIND-121: load_password_strict raise místo return None pro UI errors.
PR-10 FIND-122: delete_credentials vrací (bool, bool) pro DB + keyring.
PR-10 FIND-123: keyring_ref je URL-encoded (label:username s oddělovačem).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

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


class KeyringUnavailableError(RuntimeError):
    """Raise když Windows Credential Locker není dostupný
    (služba vypnutá, permission, backend neinstalovaný)."""


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
    """Uloží credentials: metadata do DB, heslo do keyringu.

    Pořadí (PR-10 FIND-119):
      1. DB insert/update metadata (commit).
      2. keyring.set_password.
      3. Pokud se změnil keyring_ref, smaže starý záznam.

    Pokud kterýkoliv krok selže, volající dostane RuntimeError s CZ zprávou.
    Atomicity: DB je committed před keyring touchem — při keyring failure
    zůstane v DB záznam s neplatným keyring_ref, ale operátor to uvidí
    v CRUD okně a může re-set heslo.
    """
    if not password:
        raise ValueError("Password must not be empty.")
    keyring_ref = _build_keyring_ref(label, username)

    # Phase 1 — DB update (committed). Save starý keyring_ref pro cleanup.
    old_keyring_ref: str | None = None
    with Session() as s:
        existing = credentials_repo.get_by_label(s, label)
        if existing:
            old_keyring_ref = existing.keyring_ref
            existing.hostname = hostname
            existing.username = username
            existing.keyring_ref = keyring_ref
            s.commit()
            row_id = existing.id
        else:
            row = credentials_repo.create(
                s,
                label=label,
                hostname=hostname,
                username=username,
                keyring_ref=keyring_ref,
            )
            s.commit()
            row_id = row.id

    # Phase 2 — keyring set (mimo session).
    try:
        keyring.set_password(service_name, keyring_ref, password)
    except keyring.errors.KeyringError as exc:
        raise RuntimeError(
            f"Heslo nebylo uloženo do Windows Credential Locker: {exc}. "
            f"Metadata v DB zůstala; v CRUD můžeš heslo nastavit znovu."
        ) from exc

    # Phase 3 — cleanup stareho keyring_ref pokud se změnil (PR-10 FIND-120).
    if old_keyring_ref and old_keyring_ref != keyring_ref:
        try:
            keyring.delete_password(service_name, old_keyring_ref)
        except keyring.errors.KeyringError as exc:
            _log.warning(
                "Nepodařilo se smazat starý keyring_ref %r: %s", old_keyring_ref, exc
            )

    return SpotCredentialView(
        id=row_id,
        label=label,
        hostname=hostname,
        username=username,
        keyring_ref=keyring_ref,
    )


def load_password(service_name: str, keyring_ref: str) -> Optional[str]:
    """Backward-compat varianta — None při keyring failure, jen warning log."""
    try:
        return keyring.get_password(service_name, keyring_ref)
    except keyring.errors.KeyringError as exc:
        _log.warning("keyring.get_password failed: %s", exc)
        return None


def load_password_strict(service_name: str, keyring_ref: str) -> str:
    """Jako ``load_password`` ale raise při nedostupném keyringu (PR-10 FIND-121).

    UI ji používá při explicit "Použít uložené heslo" — operátor pak vidí
    friendly dialog "WCL není dostupné" místo generického fallback na
    manuálně zadané heslo.
    """
    try:
        pwd = keyring.get_password(service_name, keyring_ref)
    except keyring.errors.KeyringError as exc:
        raise KeyringUnavailableError(
            f"Windows Credential Locker není dostupný: {exc}"
        ) from exc
    if pwd is None:
        raise KeyringUnavailableError(
            f"Heslo pod keyring_ref={keyring_ref!r} nenalezeno."
        )
    return pwd


def delete_credentials(
    service_name: str, cred_id: int
) -> tuple[bool, bool]:
    """Smaže credential z DB i z keyringu.

    Vrací (db_deleted, keyring_deleted). UI může zobrazit warning pokud
    keyring delete neuspěl (PR-10 FIND-122).
    """
    from spot_operator.db.models import SpotCredential

    with Session() as s:
        row = s.get(SpotCredential, cred_id)
        if row is None:
            return False, False
        keyring_ref = row.keyring_ref
        s.delete(row)
        s.commit()
    db_deleted = True
    keyring_deleted = True
    try:
        keyring.delete_password(service_name, keyring_ref)
    except keyring.errors.KeyringError as exc:
        _log.warning("keyring.delete_password failed (ignoring): %s", exc)
        keyring_deleted = False
    return db_deleted, keyring_deleted


def touch_last_used(cred_id: int) -> None:
    with Session() as s:
        credentials_repo.touch_last_used(s, cred_id)
        s.commit()


def _build_keyring_ref(label: str, username: str) -> str:
    """Keyring key format: ``<label>:<username>`` s URL-encoded částmi,
    aby ``label="foo:bar"`` + ``username="baz"`` nekolidovalo s
    ``label="foo"`` + ``username="bar:baz"`` (PR-10 FIND-123).
    """
    return f"{quote(label, safe='')}:{quote(username, safe='')}"


__all__ = [
    "SpotCredentialView",
    "KeyringUnavailableError",
    "list_credentials",
    "save_credentials",
    "load_password",
    "load_password_strict",
    "delete_credentials",
    "touch_last_used",
]
