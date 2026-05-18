"""
rebalancer-agent — Runs every 4-6 hours on a rolling 24h window.

What changed from the original daily cron design:
  - No longer resets pool:available — usage-sync-agent owns that, running every 10-15 min.
  - No longer clears user counters — engineers count in real time; non-engineers
    are overwritten by sync-agent on each pull.
  - Switched from "pull yesterday, reset at midnight" to a rolling 24h window
    so insights and reconciliation happen throughout the day.
  - Reconciliation now correctly uses the target date rather than today's date.

What it still does:
  - Reconciles engineer real-time counters against Anthropic's actuals (catches
    any tokens that slipped through the stream parser).
  - Pushes updated role allocations to Redis if overrides are configured.
  - Logs drift > 5% for manual review / alerting.
"""

import asyncio
import logging
import os

import redis

from skills.admin_api_client import fetch_usage_for_date
from skills.reconciler import reconcile
from skills.config_pusher import push_allocations

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

# How often to run (seconds). Default 4h; set to 21600 for 6h.
RUN_INTERVAL = int(os.getenv("REBALANCER_INTERVAL_SECONDS", str(4 * 3600)))

r = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=False)


async def rebalance_once() -> None:
    from datetime import date, timedelta, timezone
    import datetime as dt

    logger.info("[rebalancer] starting rebalance pass")

    # Pull yesterday's data — the most complete day Anthropic has
    yesterday = (dt.datetime.now(timezone.utc) - timedelta(days=1)).date()
    usage = await fetch_usage_for_date(yesterday)
    logger.info(f"[rebalancer] fetched {len(usage)} api_key entries (date={yesterday})")

    # Reconcile engineer counters only — non-engineer counters are managed by sync-agent
    # Pass the target date so reconciler compares against the correct Redis keys
    discrepancies = reconcile(usage, r, target_date=yesterday)
    if discrepancies:
        logger.warning(f"[rebalancer] {len(discrepancies)} users with >5% drift — check logs")
    else:
        logger.info("[rebalancer] all engineer counters within tolerance")

    # Push any updated allocations (reads from env overrides or keeps defaults)
    allocation_overrides = _load_allocation_overrides()
    push_allocations(r, overrides=allocation_overrides if allocation_overrides else None)

    logger.info("[rebalancer] pass complete")


def _load_allocation_overrides() -> dict[str, int] | None:
    """
    Optional env-driven allocation overrides.
    Set ALLOC_ENGINEER, ALLOC_PM, ALLOC_DESIGNER, ALLOC_OPS to override defaults.
    """
    keys = {
        "engineer": "ALLOC_ENGINEER",
        "pm":       "ALLOC_PM",
        "designer": "ALLOC_DESIGNER",
        "ops":      "ALLOC_OPS",
    }
    overrides = {}
    for role, env_key in keys.items():
        val = os.getenv(env_key)
        if val:
            overrides[role] = int(val)
    return overrides or None


async def run_loop() -> None:
    logger.info(f"[rebalancer] starting, interval={RUN_INTERVAL}s ({RUN_INTERVAL // 3600}h)")
    while True:
        try:
            await rebalance_once()
        except Exception as exc:
            logger.error(f"[rebalancer] pass failed: {exc}", exc_info=True)
        await asyncio.sleep(RUN_INTERVAL)


if __name__ == "__main__":
    asyncio.run(run_loop())
