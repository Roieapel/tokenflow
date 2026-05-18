# skill: api-poller
# Fetches per-API-key usage from Anthropic Admin API for the current UTC day.
#
# We pull from midnight-to-now rather than a rolling window because the Admin
# API reports cumulative daily totals, not deltas. Overwriting Redis counters
# with the full-day value on every sync is idempotent and correct.

import os
import httpx
from datetime import datetime, timezone

ANTHROPIC_ADMIN_KEY = os.environ["ANTHROPIC_ADMIN_KEY"]
ORG_ID = os.environ["ANTHROPIC_ORG_ID"]

BASE_URL = "https://api.anthropic.com/v1"
HEADERS = {
    "x-api-key": ANTHROPIC_ADMIN_KEY,
    "anthropic-version": "2023-06-01",
}


async def fetch_today_usage() -> list[dict]:
    """
    Fetch per-API-key usage from UTC midnight to now.
    Returns the data[] list from the Admin API response.
    Each entry: {api_key_id, input_tokens, output_tokens, total_cost_usd, ...}
    """
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    url = f"{BASE_URL}/organizations/{ORG_ID}/usage"
    params = {
        "start_time": day_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_time":   now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "granularity": "day",
        "group_by": "api_key_id",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=HEADERS, params=params)
        resp.raise_for_status()
        return resp.json().get("data", [])
