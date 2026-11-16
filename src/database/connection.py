from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator


def database_url() -> str:
    """Return the PostgreSQL DSN without logging or exposing credentials."""
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError(
            "Не задан DATABASE_URL. Например: "
            "postgresql://stereosagent:password@localhost:5432/stereosagent"
        )
    return value


@contextmanager
def connect():
    """Open a short-lived psycopg 3 connection.

    psycopg is imported lazily so the current in-memory MVP still runs
    when PostgreSQL support is not installed or configured.
    """
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "Для PostgreSQL установите зависимости: pip install 'psycopg[binary]>=3.2,<4'."
        ) from exc

    with psycopg.connect(database_url()) as connection:
        yield connection
