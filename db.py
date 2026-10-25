from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text, event
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.orm import sessionmaker

from connectivity_logging import ConnectivityTracker

log = logging.getLogger("serverwatcher.db")
connectivity = ConnectivityTracker(log)

BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = Path(os.environ.get("SERVERWATCHER_RUNTIME_DIR", str(BASE_DIR))).resolve()
load_dotenv(RUNTIME_DIR / ".env")
load_dotenv(BASE_DIR / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing. Add it to .env or the environment.")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=1800,
    future=True,
)


@event.listens_for(engine, "handle_error")
def _database_handle_error(context):
    """Observe failures even when callers bypass session_scope()."""
    if context.is_disconnect:
        connectivity.failure("database.runtime", context.original_exception)


@event.listens_for(engine, "after_cursor_execute")
def _database_query_succeeded(conn, cursor, statement, parameters, context, executemany):
    """Record recovery only after a SQL statement really executes."""
    connectivity.recovered("database.runtime")



SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def _is_connectivity_error(exc: BaseException) -> bool:
    """Classify database transport errors without treating SQL errors as outages."""
    return isinstance(exc, (OperationalError, InterfaceError)) or (
        isinstance(exc, DBAPIError) and exc.connection_invalidated
    )


def wait_for_database(attempts: int = 5) -> None:
    for attempt in range(1, attempts + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            connectivity.recovered("database.startup")
            log.info("Database ready attempt=%s/%s", attempt, attempts)
            return
        except Exception as exc:
            if _is_connectivity_error(exc):
                connectivity.failure("database.startup", exc)
            log.debug("Database unavailable attempt=%s/%s error=%s", attempt, attempts, type(exc).__name__)
            if attempt == attempts:
                raise
            delay = min(2 ** (attempt - 1), 10)
            log.info("Database retry backoff_seconds=%s", delay)
            time.sleep(delay)


@contextmanager
def session_scope():
    session = SessionLocal()
    try:
        yield session
        session.commit()
        connectivity.recovered("database.runtime")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
