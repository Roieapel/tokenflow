"""
usage-sync-agent — Polls Anthropic Admin API every 10-15 min for non-engineer roles.
Writes authoritative today-totals to Redis and recalculates the available pool.

Why this exists: real-time SSE counting is only cost-effective for engineers who
hit quota limits. For PMs/designers/ops, we rely on the Admin API's authoritative
counts rather than parsing every stream chunk.

Runs as a long-lived process (or on a tight cron). Set SYNC_INTERVAL_SECONDS to
tune the polling frequency; default is 600s (10 min).
"""

import asyncio
import json
import logging
import os

import redis

from skills.api_poller import fetch_today_usage
from skills.counter_writer import write_non_engineer_counts, calculate_and_set_pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

SYNC_INTERVAL = int(os.getenv("SYNC_INTERVAL_SECONDS", "600"))  # 10 min default
NON_ENGINEER_ROLES = {"pm", "designer", "ops"}

# One shared Redis client for the whole process
r = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=False)


async def sync_once() -> None:
    """Single sync pass: fetch → write counters → recalculate pool."""
    logger.info("[sync-agent] starting sync pass")

    usage = await fetch_today_usage()
    logger.info(f"[sync-agent] fetched {len(usage)} api_key entries from Anthropic")

    written = write_non_engineer_counts(usage, r, NON_ENGINEER_ROLES)
    logger.info(f"[sync-agent] wrote authoritative counts for {written} non-engineer users")

    pool_size = calculate_and_set_pool(r, NON_ENGINEER_ROLES)
    logger.info(f"[sync-agent] pool recalculated → {pool_size:,} tokens")

    r.publish("sync:completed", json.dumps({"pool": pool_size, "users_updated": written}))


async def run_loop() -> None:
    logger.info(f"[sync-agent] starting, interval={SYNC_INTERVAL}s")
    while True:
        try:
            await sync_once()
        except Exception as exc:
            logger.error(f"[sync-agent] sync failed: {exc}", exc_info=True)
        await asyncio.sleep(SYNC_INTERVAL)


if __name__ == "__main__":
    asyncio.run(run_loop())
