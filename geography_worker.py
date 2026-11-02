"""Single-owner, paced Battlelog geographic enrichment for BF4SW.

Run one instance per fleet; PostgreSQL advisory lock prevents concurrent owners.
Requires Alembic 0024 and 0025. No writes to Keeper or guild tables.
"""
import argparse
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import requests
from sqlalchemy import select, text, or_
from db import SessionLocal, engine
from models import BF4Server, BF4GeographyRequestGate
from server_geography import parse_battlelog_geography

log = logging.getLogger('bf4sw.geography')


class ServerPageUnavailable(ValueError):
    """Battlelog returned its HTML missing-page response with HTTP 200."""


def is_missing_server_page(html):
    import re
    import html as html_module
    title = re.search(r'<title[^>]*>(.*?)</title>', html, re.I | re.S)
    if not title:
        return False
    title_text = html_module.unescape(title.group(1))
    return bool(re.search(r"404\s+That page doesn't exist", title_text, re.I))
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
    if is_missing_server_page(response.text):
        raise ServerPageUnavailable("server_page_unavailable")
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
                # Reserve a request slot durably before network I/O. The
                # advisory lock serializes owners; DB state survives crashes.
                with SessionLocal() as due_session:
                    has_due = due_session.scalar(select(BF4Server.server_guid).where(or_(
                        BF4Server.geo_next_check_at <= utcnow(),
                        BF4Server.geo_next_check_at.is_(None)
                    )).limit(1))
                if has_due is None:
                    break
                if not dry_run:
                    with SessionLocal() as gate_session:
                        gate = gate_session.get(BF4GeographyRequestGate, 1)
                        if gate is None:
                            raise RuntimeError('Geography gate missing: apply Alembic 0025')
                        ready = max((v for v in (gate.next_request_at, gate.blocked_until)
                                     if v is not None), default=None)
                        if ready is not None:
                            wait = (ready - utcnow()).total_seconds()
                            if wait > 0:
                                time.sleep(wait)
                        gate.next_request_at = utcnow() + timedelta(seconds=delay)
                        gate_session.commit()
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
                            with SessionLocal() as gate_session:
                                gate = gate_session.get(BF4GeographyRequestGate, 1)
                                gate.blocked_until = utcnow() + timedelta(hours=1)
                                gate_session.commit()
                            break
                        server.geo_checked_at = utcnow()
                        server.geo_next_check_at = utcnow() + timedelta(days=1)
                        log.warning('HTTP %s for %s; retry in 24h', code, guid)
                    except ServerPageUnavailable:
                        server.geo_checked_at = utcnow()
                        server.geo_next_check_at = utcnow() + timedelta(days=1)
                        log.warning('server_page_unavailable server=%s retry_in=24h', guid)
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
