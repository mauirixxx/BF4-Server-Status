"""PostgreSQL-only integration checks. Set BF4SW_GEO_TEST_DATABASE_URL to isolated DB."""
import os
import multiprocessing
import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text

URL = os.environ.get('BF4SW_GEO_TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not URL, reason='isolated PostgreSQL URL required')
GUID1 = '28773abe-e620-4d36-9512-c6f4b128f0ad'
GUID2 = '86fd73d2-c37e-45a4-bac2-474b93cf7c56'


def contender(url, gate, results):
    from sqlalchemy import create_engine, text
    engine = create_engine(url)
    with engine.connect() as conn:
        acquired = conn.scalar(text('SELECT pg_try_advisory_lock(44240024)'))
        results.put(acquired)
        if acquired:
            gate.wait(timeout=8)
            conn.execute(text('SELECT pg_advisory_unlock(44240024)'))
    engine.dispose()


def test_two_processes_one_lock():
    ctx = multiprocessing.get_context('spawn')
    gate = ctx.Event()
    results = ctx.Queue()
    a = ctx.Process(target=contender, args=(URL, gate, results))
    a.start()
    first = results.get(timeout=10)
    assert first is True
    b = ctx.Process(target=contender, args=(URL, gate, results))
    b.start()
    second = results.get(timeout=10)
    assert second is False
    gate.set()
    a.join(timeout=10)
    b.join(timeout=10)
    assert a.exitcode == 0 and b.exitcode == 0


def test_worker_persists_and_schedules(monkeypatch):
    from geography_worker import run
    from models import BF4Server
    from db import SessionLocal
    import geography_worker
    assert geography_worker.engine.dialect.name == 'postgresql'
    calls = []
    def fake_enrich(session, server):
        calls.append(server.server_guid)
        server.country_code = 'us'
        server.ping_site = 'iad'
        server.region = '1'
        server.geo_source = 'battlelog_server_page'
        server.geo_checked_at = datetime.now(timezone.utc)
        server.geo_updated_at = server.geo_checked_at
        from datetime import timedelta
        server.geo_next_check_at = server.geo_checked_at + timedelta(days=30)
    monkeypatch.setattr(geography_worker, 'enrich_one', fake_enrich)
    monkeypatch.setattr(geography_worker.time, 'sleep', lambda _: None)
    assert run(limit=2, delay=10) == 2
    assert len(set(calls)) == 2
    with SessionLocal() as session:
        assert session.get(BF4Server, GUID1).country_code == 'us'
        assert session.get(BF4Server, GUID2).country_code == 'us'
    assert run(limit=2, delay=10) == 0
