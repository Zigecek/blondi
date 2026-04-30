"""SQLAlchemy engine + thread-local Session factory.

Jedna engine instance pro celý proces, thread-local `scoped_session` aby každý thread
(Qt main, OCR worker, ...) měl svou session.

Key invarianty (PR-07 FIND-014):

- ``expire_on_commit=False`` — po commit zůstávají ORM objekty usable; to je
  nutné pro pattern ``photos_repo.insert() → session.commit() → return photo.id``.
  Důsledek: čtení staré instance nemá automatický refresh. Repo funkce
  **nesmí** propouštět ORM objekty mimo vlastní session — vracet DTO nebo
  primitiva (viz runs_repo.RunRow, photos_repo.PhotoRow).
- ``autoflush=False`` — repo kód musí explicitně ``session.flush()``, pokud
  potřebuje ID generované DB před commit (vzor v ``maps_repo.create`` atd.).
- Worker threads by měly v ``finally`` své run() volat
  ``thread_local_session_remove`` aby se neudržovala zombie session po
  skončení threadu.
"""

from __future__ import annotations

import threading
from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as _Session
from sqlalchemy.orm import scoped_session, sessionmaker

from blondi.logging_config import get_logger

_log = get_logger(__name__)

_engine: Engine | None = None
_session_factory: scoped_session | None = None
_lock = threading.Lock()


def init_engine(database_url: str, *, echo: bool = False) -> Engine:
    """Vytvoří globální engine. Idempotentní — opakované volání vrátí stávající."""
    global _engine, _session_factory
    with _lock:
        if _engine is not None:
            return _engine
        _engine = create_engine(
            database_url,
            echo=echo,
            pool_size=5,
            max_overflow=5,
            pool_pre_ping=True,
            pool_recycle=1800,
            future=True,
        )
        _session_factory = scoped_session(
            sessionmaker(
                bind=_engine,
                autoflush=False,
                expire_on_commit=False,
                future=True,
            ),
            scopefunc=threading.get_ident,
        )
        _log.info("DB engine initialized (%s)", _mask_url(database_url))
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("DB engine not initialized. Call init_engine() first.")
    return _engine


def Session() -> _Session:
    """Vrátí session pro aktuální thread. Používej jako context manager:

    >>> with Session() as s:
    ...     s.add(obj); s.commit()
    """
    if _session_factory is None:
        raise RuntimeError("Session factory not initialized. Call init_engine() first.")
    return _session_factory()


def ping() -> bool:
    """Ověří, že DB odpovídá. Vrátí True/False, nevyhazuje."""
    if _engine is None:
        return False
    try:
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        _log.warning("DB ping failed: %s", exc)
        return False


def shutdown_engine() -> None:
    """Zavře connection pool — volá se při exit aplikace."""
    global _engine, _session_factory
    with _lock:
        if _session_factory is not None:
            _session_factory.remove()
            _session_factory = None
        if _engine is not None:
            _engine.dispose()
            _engine = None
            _log.info("DB engine disposed")


def thread_local_session_remove() -> None:
    """Odstraní thread-local session pro aktuální thread.

    Worker threads by to měly volat v ``finally`` své ``run()`` metody,
    aby se neudržovala zapleněná session po skončení threadu
    (PR-07 FIND-015).
    """
    if _session_factory is None:
        return
    try:
        _session_factory.remove()
    except Exception as exc:
        _log.debug("thread_local_session_remove failed: %s", exc)


def _mask_url(url: str) -> str:
    """Vrátí DSN bez hesla pro logování."""
    try:
        from sqlalchemy.engine.url import make_url

        u = make_url(url)
        if u.password:
            u = u.set(password="***")
        return str(u)
    except Exception:
        return "postgresql+psycopg://***"


__all__ = ["init_engine", "get_engine", "Session", "ping", "shutdown_engine"]
