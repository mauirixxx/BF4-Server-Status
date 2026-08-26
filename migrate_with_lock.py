from __future__ import annotations
import os, sys, time
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

LOCK_ID = 0x424634535752  # stable BF4SWR advisory-lock key
url=os.environ.get("DATABASE_URL","").strip()
if not url:
    raise SystemExit("DATABASE_URL is required")
engine=create_engine(url, pool_pre_ping=True)
with engine.connect() as conn:
    print("INFO Waiting for cluster-wide PostgreSQL migration lock", flush=True)
    conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": LOCK_ID})
    try:
        print("INFO Acquired cluster-wide PostgreSQL migration lock", flush=True)
        cfg=Config("alembic.ini")
        command.upgrade(cfg,"head")
        db_rev=conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        script=__import__("alembic.script",fromlist=["ScriptDirectory"]).ScriptDirectory.from_config(cfg)
        heads=script.get_heads()
        if len(heads)!=1 or db_rev!=heads[0]:
            raise RuntimeError(f"schema verification failed db={db_rev!r} expected={heads!r}")
        print(f"INFO Database schema verified head={db_rev}", flush=True)
    finally:
        conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_ID})
