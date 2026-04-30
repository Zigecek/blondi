"""Jednorázová migrace Spot hesel z keyring service `spot_operator.spot` → `blondi.spot`.

Po rebrandingu aplikace ze `spot_operator` na `blondi` se mění i default
keyring service prefix. Stávající `spot_credentials` řádky v DB mají
`keyring_ref` v pořádku (ten se nemění — je to jen lookup key), ale heslo
samotné je v Windows Credential Locker uloženo pod starým service jménem
`spot_operator.spot`. Tento skript zkopíruje hesla pod nové service jméno
`blondi.spot`, aby aplikace po rebrandu fungovala bez nutnosti znovu zadat
přihlášení ke každému Spotovi.

Použití:
    python -m blondi.migrate_keyring                    # migrace, nech staré
    python -m blondi.migrate_keyring --dry-run          # jen výpis bez zápisu
    python -m blondi.migrate_keyring --delete-old       # po migraci smaž staré

Idempotent — opakované spuštění je no-op (heslo už je pod novým service).
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

import keyring
import keyring.errors

OLD_SERVICE: str = "spot_operator.spot"
NEW_SERVICE: str = "blondi.spot"


@dataclass(frozen=True, slots=True)
class MigrationResult:
    label: str
    keyring_ref: str
    status: str  # "migrated" | "already-new" | "missing-old" | "skipped-dry"
    detail: str = ""


def migrate_one(
    keyring_ref: str,
    *,
    dry_run: bool,
    delete_old: bool,
) -> MigrationResult:
    """Zmigruje jedno heslo. Bezpečně handluje neexistující staré entries."""
    label = keyring_ref  # zobrazené jméno; v logu == keyring_ref
    try:
        new_pwd = keyring.get_password(NEW_SERVICE, keyring_ref)
    except keyring.errors.KeyringError as exc:
        return MigrationResult(label, keyring_ref, "error", f"NEW lookup: {exc}")

    if new_pwd is not None:
        return MigrationResult(label, keyring_ref, "already-new")

    try:
        old_pwd = keyring.get_password(OLD_SERVICE, keyring_ref)
    except keyring.errors.KeyringError as exc:
        return MigrationResult(label, keyring_ref, "error", f"OLD lookup: {exc}")

    if old_pwd is None:
        return MigrationResult(label, keyring_ref, "missing-old")

    if dry_run:
        return MigrationResult(label, keyring_ref, "skipped-dry")

    try:
        keyring.set_password(NEW_SERVICE, keyring_ref, old_pwd)
    except keyring.errors.KeyringError as exc:
        return MigrationResult(label, keyring_ref, "error", f"NEW set: {exc}")

    if delete_old:
        try:
            keyring.delete_password(OLD_SERVICE, keyring_ref)
        except keyring.errors.KeyringError as exc:
            return MigrationResult(
                label, keyring_ref, "migrated", f"old delete failed: {exc}"
            )

    return MigrationResult(label, keyring_ref, "migrated")


def collect_keyring_refs() -> list[tuple[str, str]]:
    """Načte (label, keyring_ref) pro všechny řádky `spot_credentials`.

    Vrací prázdný list pokud DB engine není dostupný — uživatel pak skript
    musí pustit s funkčním DATABASE_URL. Bez DB zdroje nevíme, která hesla
    máme migrovat.
    """
    from blondi.config import AppConfig
    from blondi.db import init_engine
    from blondi.db.engine import Session
    from blondi.db.repositories import credentials_repo

    config = AppConfig.load_from_env()
    init_engine(config.database_url)
    with Session() as s:
        rows = credentials_repo.list_all(s)
        return [(r.label, r.keyring_ref) for r in rows]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            f"Migrace Spot hesel z keyring service {OLD_SERVICE!r} "
            f"na {NEW_SERVICE!r}."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Vypíše plánované změny bez zápisu do keyringu.",
    )
    parser.add_argument(
        "--delete-old",
        action="store_true",
        help="Po úspěšné migraci smaže starý keyring entry.",
    )
    args = parser.parse_args()

    print(f"Mode: {'DRY-RUN' if args.dry_run else 'WRITE'}")
    print(f"  OLD service: {OLD_SERVICE}")
    print(f"  NEW service: {NEW_SERVICE}")
    print(f"  delete-old:  {args.delete_old}")
    print()

    try:
        refs = collect_keyring_refs()
    except Exception as exc:
        print(f"FATAL: nelze načíst spot_credentials z DB: {exc}", file=sys.stderr)
        print(
            "Ujisti se, že DATABASE_URL v .env míří na funkční DB s tabulkou.",
            file=sys.stderr,
        )
        return 2

    if not refs:
        print("Žádné spot_credentials řádky v DB — není co migrovat.")
        return 0

    counts: dict[str, int] = {}
    for label, keyring_ref in refs:
        result = migrate_one(
            keyring_ref, dry_run=args.dry_run, delete_old=args.delete_old
        )
        counts[result.status] = counts.get(result.status, 0) + 1
        line = f"  [{result.status:<13}] {label:<20} ref={keyring_ref}"
        if result.detail:
            line += f"  ({result.detail})"
        print(line)

    print()
    print("Souhrn:")
    for status, count in sorted(counts.items()):
        print(f"  {status:<14} {count}")

    if counts.get("error", 0) > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
