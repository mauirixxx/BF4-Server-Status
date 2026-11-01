import uuid
import os
import pytest
from db import SessionLocal
from models import BF4Server
from serverwatcher import persist_bflist_pc_discovery, persist_gametools_console_discovery

pytestmark = pytest.mark.skipif(not os.environ.get('BF4SW_GEO_TEST_DATABASE_URL'), reason='isolated PostgreSQL required')

def test_discovery_produces_due_rows():
    pc, ps, xbox = (str(uuid.uuid4()) for _ in range(3))
    assert persist_bflist_pc_discovery([{'guid':pc,'name':'Test PC'}])['discovered'] == 1
    assert persist_gametools_console_discovery('ps4',[{'serverId':ps,'prefix':'Test PS'}])['discovered'] == 1
    assert persist_gametools_console_discovery('xboxone',[{'serverId':xbox,'prefix':'Test Xbox'}])['discovered'] == 1
    with SessionLocal() as session:
        for guid, platform in ((pc,'PC'),(ps,'PS4/5'),(xbox,'XBox')):
            row = session.get(BF4Server,guid)
            assert row is not None and row.platform == platform
            assert row.geo_checked_at is None and row.geo_next_check_at is None
    assert persist_bflist_pc_discovery([{'guid':pc,'name':'Test PC'}])['known'] == 1
