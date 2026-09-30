from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import get_settings

settings = get_settings()


def _configure_connection(conn) -> None:
    # Defense in depth: the DB login itself must still be SELECT-only.
    conn.execute("SET default_transaction_read_only = on")
    conn.execute(f"SET statement_timeout = '{settings.db_statement_timeout_ms}ms'")
    # psycopg_pool requires configure() to return the connection in IDLE state.
    conn.commit()


pool = ConnectionPool(
    conninfo=settings.database_url,
    min_size=1,
    max_size=5,
    kwargs={"row_factory": dict_row},
    configure=_configure_connection,
    open=False,
)


def open_pool() -> None:
    pool.open(wait=True)


def close_pool() -> None:
    pool.close()


@contextmanager
def db_connection():
    with pool.connection() as conn:
        # Keep individual request work explicitly read-only too.
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            yield conn
