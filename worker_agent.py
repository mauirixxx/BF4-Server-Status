from __future__ import annotations

import asyncio
import logging
import os
import signal
import time

from dotenv import load_dotenv

from control_plane import (
    WORKER_ID, RuntimeSettingsCache, heartbeat_loop, register_worker,
    set_worker_status, validate_worker_id,
)
from db import wait_for_database

BOT_VERSION = "v3.0.0-pr1"

load_dotenv()
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logging.Formatter.converter = time.gmtime
log = logging.getLogger("serverwatcher.worker_agent")


async def main_async():
    worker_id = validate_worker_id(WORKER_ID)
    wait_for_database()
    register_worker(worker_id, BOT_VERSION, status="online")
    settings = RuntimeSettingsCache()
    if not settings.refresh(worker_id):
        raise RuntimeError("Initial runtime settings load failed; refusing to start without a known-good configuration")
    heartbeat_seconds = int(settings.get("worker.heartbeat_seconds", 5))
    log.info(
        "Control-plane agent ready worker_id=%s heartbeat_seconds=%s distributed_work=disabled",
        worker_id, heartbeat_seconds,
    )
    refresh_task = asyncio.create_task(settings.refresh_loop(worker_id))
    try:
        await heartbeat_loop(worker_id, heartbeat_seconds, settings_cache=settings)
    finally:
        refresh_task.cancel()
        try:
            await refresh_task
        except asyncio.CancelledError:
            pass
        try:
            set_worker_status(worker_id, "stopping")
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main_async())
