# skill: reconciler
# Diffs Anthropic Admin API actuals against the internal borrow ledger.
# Flags discrepancies > 5% for manual review.
#
# Bug fix: original used _today_str() regardless of what date the Anthropic
# data was for, so the 00:05 UTC run always compared yesterday's Anthropic
# data against today's (empty) Redis keys — producing 100% drift for every user.
# Fixed: accept target_date as an explicit parameter.

import logging
from datetime import date

import redis

logger = logging.getLogger(__name__)

TOLERANCE = 0.05  # 5% acceptable drift


def reconcile(
    usage_data: list[dict],
    r: redis.Redis,
    target_date: date | None = None,
) -> dict:
    """
    Compare Anthropic-reported totals per api_key_id against internal Redis counters
    for the given target_date (defaults to today if omitted).

    Only reconciles engineer users — non-engineer counters are owned by sync-agent
    and are already authoritative overwrites from the Admin API.

    Returns a dict of {user_id: {"anthropic": n, "internal": n, "drift_pct": f}}
    for users whose drift exceeds TOLERANCE.
    """
    date_str = (target_date or date.today()).isoformat()
    key_to_user: dict[bytes, bytes] = r.hgetall("registry:key_to_user")

    discrepancies = {}

    for entry in usage_data:
        api_key_id = entry["api_key_id"]
        uid_bytes = key_to_user.get(api_key_id.encode())
        if uid_bytes is None:
            continue

        uid = uid_bytes.decode()

        # Only reconcile engineers — non-engineers are counted by sync-agent
        role_raw = r.get(f"user:{uid}:role")
        if role_raw is None:
            continue
        role = role_raw.decode() if isinstance(role_raw, bytes) else role_raw
        if role != "engineer":
            continue

        anthropic_total = entry["input_tokens"] + entry["output_tokens"]
        internal_raw = r.get(f"user:{uid}:tokens:{date_str}")
        internal_total = int(internal_raw) if internal_raw else 0

        if anthropic_total == 0:
            continue

        drift = abs(anthropic_total - internal_total) / anthropic_total
        if drift > TOLERANCE:
            discrepancies[uid] = {
                "anthropic":  anthropic_total,
                "internal":   internal_total,
                "drift_pct":  round(drift * 100, 2),
            }
            logger.warning(
                f"[reconciler] DRIFT uid={uid} date={date_str} "
                f"anthropic={anthropic_total:,} internal={internal_total:,} "
                f"drift={drift * 100:.1f}%"
            )

    if not discrepancies:
        logger.info(f"[reconciler] all engineer counters within tolerance for {date_str}")

    return discrepancies
