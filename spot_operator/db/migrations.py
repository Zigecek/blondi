"""Programatické spuštění Alembic migrací při startu aplikace."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from spot_operator.bootstrap import ROOT
from spot_operator.logging_config import get_logger

_log = get_logger(__name__)


def upgrade_to_head(database_url: str) -> None:
    """Spustí `alembic upgrade head`. Voláno při startu aplikace.

    Idempotentní — pokud je DB aktuální, nic se nezmění.
    Při selhání raise RuntimeError s CZ zprávou (PR-07 FIND-012) aby
    uživatel u startu aplikace viděl fatální dialog s user-actionable
    hintem (zkontroluj DATABASE_URL, spusť ``alembic downgrade -1``...).
    """
    alembic_ini = ROOT / "alembic.ini"
    if not alembic_ini.is_file():
        raise FileNotFoundError(f"alembic.ini not found at {alembic_ini}")

    cfg = Config(str(alembic_ini))
    cfg.set_main_option("sqlalchemy.url", database_url)
    cfg.set_main_option("script_location", str(ROOT / "alembic"))

    _log.info("Running alembic upgrade head...")
    try:
        command.upgrade(cfg, "head")
    except Exception as exc:
        _log.exception("Alembic upgrade failed")
        raise RuntimeError(
            f"Migrace databáze selhala: {exc}. "
            "Zkontroluj připojení k DB (DATABASE_URL) a logy alembic/versions/*.py. "
            "V nouzi zkus `alembic downgrade -1`."
        ) from exc
    _log.info("Alembic migrations applied")


def current_revision(database_url: str) -> str | None:
    """Vrátí aktuální revision DB (nebo None).

    PR-07 FIND-013: re-use existujícího engine místo ephemeral create/dispose,
    pokud už je inicializovaný.
    """
    from alembic.runtime.migration import MigrationContext

    from spot_operator.db import engine as db_engine

    existing = db_engine._engine  # type: ignore[attr-defined]
    if existing is not None:
        with existing.connect() as conn:
            context = MigrationContext.configure(conn)
            return context.get_current_revision()

    # Fallback: ephemeral engine (např. test / diagnostic předtím, než
    # init_engine proběhlo).
    from sqlalchemy import create_engine

    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            return context.get_current_revision()
    finally:
        engine.dispose()


__all__ = ["upgrade_to_head", "current_revision"]
