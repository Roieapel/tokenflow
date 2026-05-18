# skill: counter-writer
# Writes authoritative non-engineer token counts from Admin API to Redis,
# then recalculates and publishes the available pool.
#
# Data model (written by Go directory-sync):
#   user:<uid>:role          → string  ("pm" | "designer" | "ops" | "engineer")
#   user:<uid>:tokens:<date> → string  (int, per-day counter)
#   role:<role>              → hash    {daily_tokens: <int>}
#   registry:key_to_user     → hash    {vtk_key: uid}

import logging
from datetime import date

import redis

logger = logging.getLogger(__name__)


def write_non_engineer_counts(
    usage_data: list[dict],
    r: redis.Redis,
    non_engineer_roles: set[str],
) -> int:
    """
    For each non-engineer in usage_data, SET (overwrite) their today counter
    with the Admin API's authoritative total. Uses SET not INCRBY — the Admin
    API value is cumulative for the day, so idempotent overwrites are correct.

    Returns the number of users updated.
    """
    today = date.today().isoformat()
    key_to_user: dict[bytes, bytes] = r.hgetall("registry:key_to_user")

    updated = 0
    pipe = r.pipeline()

    for entry in usage_data:
        uid_bytes = key_to_user.get(entry["api_key_id"].encode())
        if uid_bytes is None:
            continue
        uid = uid_bytes.decode()

        role_raw = r.get(f"user:{uid}:role")  # string key written by directory-sync
        if role_raw is None:
            continue
        role = role_raw.decode() if isinstance(role_raw, bytes) else role_raw

        if role not in non_engineer_roles:
            continue  # engineers are counted in real-time by counter-agent

        total = entry["input_tokens"] + entry["output_tokens"]
        counter_key = f"user:{uid}:tokens:{today}"
        pipe.set(counter_key, total, ex=48 * 3600)  # 48h TTL so yesterday lingers for rebalancer
        updated += 1

    if updated:
        pipe.execute()

    return updated


def calculate_and_set_pool(r: redis.Redis, non_engineer_roles: set[str]) -> int:
    """
    Pool = sum(allocation - used_today) across every non-engineer user, floored at 0.
    Batch-fetches roles and counters via a single pipeline, then reads allocations
    (only 3-4 distinct role hashes — cheap).

    Writes pool:available and publishes pool:updated.
    Returns the new pool token count.
    """
    today = date.today().isoformat()

    # All unique user IDs known to the proxy
    key_to_user: dict[bytes, bytes] = r.hgetall("registry:key_to_user")
    all_uids = list(set(uid.decode() for uid in key_to_user.values()))

    if not all_uids:
        r.set("pool:available", 0)
        r.publish("pool:updated", 0)
        return 0

    # Batch-fetch role + today's counter for every user in one round-trip
    pipe = r.pipeline()
    for uid in all_uids:
        pipe.get(f"user:{uid}:role")
        pipe.get(f"user:{uid}:tokens:{today}")
    results = pipe.execute()

    # Cache role allocations to avoid repeated hget calls
    alloc_cache: dict[str, int] = {}

    pool = 0
    for i, uid in enumerate(all_uids):
        role_raw = results[i * 2]
        used_raw = results[i * 2 + 1]

        if role_raw is None:
            continue
        role = role_raw.decode() if isinstance(role_raw, bytes) else role_raw

        if role not in non_engineer_roles:
            continue

        if role not in alloc_cache:
            alloc_raw = r.hget(f"role:{role}", "daily_tokens")
            alloc_cache[role] = int(alloc_raw) if alloc_raw else 0

        allocation = alloc_cache[role]
        used = int(used_raw) if used_raw else 0
        pool += max(0, allocation - used)

    r.set("pool:available", pool)
    r.publish("pool:updated", pool)

    logger.debug(f"[counter-writer] pool={pool:,} from {len(all_uids)} users")
    return pool
