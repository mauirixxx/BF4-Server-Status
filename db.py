from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

log = logging.getLogger("serverwatcher.db")

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
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def wait_for_database(attempts: int = 5) -> None:
    for attempt in range(1, attempts + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            log.info("Database ready attempt=%s/%s", attempt, attempts)
            return
        except Exception as exc:
            log.error(
                "Database unavailable attempt=%s/%s error=%s message=%r",
                attempt, attempts, type(exc).__name__, str(exc)
            )
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
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
