# skill: config-pusher
# Writes updated per-role allocation config to Redis and notifies the proxy via pub/sub.

import json
import logging
import os
import redis

logger = logging.getLogger(__name__)

DEFAULT_ALLOCATIONS = {
    "engineer": 500_000,
    "pm":       50_000,
    "designer": 50_000,
    "ops":      5_000,
}


def push_allocations(r: redis.Redis, overrides: dict[str, int] | None = None) -> None:
    """
    Write role daily_tokens allocations to Redis and publish a config-updated event.
    overrides: optional dict of {role: token_count} to replace defaults.
    """
    allocations = {**DEFAULT_ALLOCATIONS, **(overrides or {})}

    pipe = r.pipeline()
    for role, tokens in allocations.items():
        pipe.hset(f"role:{role}", "daily_tokens", tokens)
    pipe.publish("config:updated", json.dumps(allocations))
    pipe.execute()

    logger.info(f"[config-pusher] allocations pushed: {allocations}")


def push_pool_size(r: redis.Redis, pool_tokens: int) -> None:
    """Reset the available pool to a new value and notify subscribers."""
    r.set("pool:available", pool_tokens)
    r.publish("pool:updated", pool_tokens)
    logger.info(f"[config-pusher] pool reset to {pool_tokens:,} tokens")
