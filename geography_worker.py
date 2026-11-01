"""Single-owner, paced Battlelog geographic enrichment for BF4SW.

Run one instance per fleet; PostgreSQL advisory lock prevents concurrent owners.
Requires Alembic 0024. No writes to Keeper or guild tables.
"""
import argparse
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import requests
from sqlalchemy import select, text, or_, func
from db import SessionLocal, engine
from models import BF4Server
from server_geography import parse_battlelog_geography

log = logging.getLogger('bf4sw.geography')
LOCK_ID = 44240024
PLATFORM_PATH = {'PC':'pc', 'PS4/5':'ps4', 'XBox':'xboxone'}


def utcnow():
    return datetime.now(timezone.utc)


def enrich_one(session, server, *, timeout=12):
    path = PLATFORM_PATH.get(server.platform)
    if not path:
        raise ValueError('Unsupported platform')
    url = server.battlelog_url or f'https://battlelog.battlefield.com/bf4/servers/show/{path}/{server.server_guid}/'
    response = requests.get(url, headers={'User-Agent':'BF4-Server-Watcher/geography'}, timeout=timeout)
    response.raise_for_status()
    values = parse_battlelog_geography(response.text, server.server_guid)
    changed = any(getattr(server, key) != val for key, val in values.items())
    for key, val in values.items():
        setattr(server, key, val)
    now = utcnow()
    server.geo_source = 'battlelog_server_page'
    server.geo_checked_at = now
    if changed:
        server.geo_updated_at = now
    server.geo_next_check_at = now + timedelta(days=30)
    return values


def run(limit, delay, dry_run=False):
    if delay < 10:
        raise ValueError('Minimum interval is 10 seconds')
    processed = 0
    # Hold a session-level advisory lock on a dedicated connection, across
    # transactions and network requests. Other workers exit without scraping.
    with engine.connect() as owner:
        locked = owner.scalar(text('SELECT pg_try_advisory_lock(:id)'), {'id': LOCK_ID})
        if not locked:
            log.info('Another geographic enrichment worker owns the lock')
            return 0
        try:
            while limit == 0 or processed < limit:
                # Enforce pacing across worker restarts, not only inside one run.
                # Read the shared last-attempt timestamp under the fleet lock.
                with SessionLocal() as pace_session:
                    last_attempt = pace_session.scalar(select(func.max(BF4Server.geo_checked_at)))
                if last_attempt is not None and not dry_run:
                    wait = delay - (utcnow() - last_attempt).total_seconds()
                    if wait > 0:
                        time.sleep(wait)
                with SessionLocal() as session:
                    server = session.scalars(select(BF4Server).where(or_(BF4Server.geo_next_check_at <= utcnow(), BF4Server.geo_next_check_at.is_(None))).order_by(BF4Server.geo_next_check_at.asc().nullsfirst(), BF4Server.server_guid).limit(1)).first()
                    if server is None:
                        break
                    guid = server.server_guid
                    if dry_run:
                        log.info('Would enrich %s platform=%s', guid, server.platform)
                        break
                    try:
                        result = enrich_one(session, server)
                        log.info('Enriched %s: %s', guid, result)
                    except requests.HTTPError as exc:
                        code = exc.response.status_code if exc.response is not None else None
                        if code in (403, 429) or (code is not None and code >= 500):
                            log.error('Circuit breaker HTTP %s on %s; stopping batch', code, guid)
                            server.geo_checked_at = utcnow()
                            server.geo_next_check_at = utcnow() + timedelta(hours=1)
                            session.commit()
                            break
                        server.geo_checked_at = utcnow()
                        server.geo_next_check_at = utcnow() + timedelta(days=1)
                        log.warning('HTTP %s for %s; retry in 24h', code, guid)
                    except (requests.RequestException, ValueError) as exc:
                        server.geo_checked_at = utcnow()
                        server.geo_next_check_at = utcnow() + timedelta(days=1)
                        log.warning('Geography unavailable for %s: %s', guid, type(exc).__name__)
                    session.commit()
                    processed += 1
                if limit == 0 or processed < limit:
                    time.sleep(delay)
        finally:
            owner.execute(text('SELECT pg_advisory_unlock(:id)'), {'id': LOCK_ID})
    return processed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=10, help='Maximum servers; 0 runs until caught up')
    parser.add_argument('--delay', type=float, default=10, help='Seconds between requests, minimum 10')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.limit < 0:
        parser.error('--limit must be >= 0')
    logging.basicConfig(level=logging.INFO)
    print('Processed:', run(args.limit, args.delay, args.dry_run))
