"""Durable fleet gate checks; run only against an isolated disposable PostgreSQL DB."""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import pytest
import requests
from sqlalchemy import select

URL = os.environ.get('BF4SW_GEO_TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not URL, reason='isolated PostgreSQL URL required')
GUID = '28773abe-e620-4d36-9512-c6f4b128f0ad'

@pytest.fixture(autouse=True)
def isolated_gate():
    from db import SessionLocal
    from models import BF4Server, BF4GeographyRequestGate
    with SessionLocal() as s:
        s.query(BF4Server).update({BF4Server.geo_next_check_at: datetime.now(timezone.utc)+timedelta(days=30)})
        row=s.get(BF4Server, GUID)
        assert row is not None
        row.geo_next_check_at=None
        gate=s.get(BF4GeographyRequestGate,1)
        assert gate is not None
        gate.next_request_at=None
        gate.blocked_until=None
        s.commit()
    yield
    with SessionLocal() as s:
        gate=s.get(BF4GeographyRequestGate,1)
        gate.next_request_at=None
        gate.blocked_until=None
        s.commit()

def test_future_cooldown_blocks_network():
    import geography_worker as worker
    from db import SessionLocal
    from models import BF4GeographyRequestGate
    with SessionLocal() as s:
        gate=s.get(BF4GeographyRequestGate,1)
        gate.next_request_at=datetime.now(timezone.utc)+timedelta(minutes=10)
        gate.blocked_until=datetime.now(timezone.utc)+timedelta(minutes=40)
        s.commit()
    def capture_wait(seconds):
        assert 2300 < seconds < 2401
        raise RuntimeError('expected wait')
    with patch.object(worker.time,'sleep',side_effect=capture_wait), patch.object(worker,'enrich_one',side_effect=AssertionError('network attempted')):
        with pytest.raises(RuntimeError,match='expected wait'):
            worker.run(1,10)

def test_http_429_persists_fleet_block():
    import geography_worker as worker
    from db import SessionLocal
    from models import BF4GeographyRequestGate
    response=requests.Response(); response.status_code=429
    error=requests.HTTPError('blocked',response=response)
    with patch.object(worker,'enrich_one',side_effect=error), patch.object(worker.time,'sleep',return_value=None):
        assert worker.run(1,10)==0
    with SessionLocal() as s:
        gate=s.get(BF4GeographyRequestGate,1)
        assert gate.next_request_at > datetime.now(timezone.utc)
        assert gate.blocked_until > datetime.now(timezone.utc)+timedelta(minutes=59)
