# skill: admin-api-client
# Authenticated calls to the Anthropic Admin API for per-key usage data.

import os
import httpx
from datetime import date, timedelta

ANTHROPIC_ADMIN_KEY = os.environ["ANTHROPIC_ADMIN_KEY"]
ORG_ID = os.environ["ANTHROPIC_ORG_ID"]

BASE_URL = "https://api.anthropic.com/v1"
HEADERS = {
    "x-api-key": ANTHROPIC_ADMIN_KEY,
    "anthropic-version": "2023-06-01",
}


async def fetch_usage_for_date(target_date: date) -> list[dict]:
    """
    Fetch per-API-key usage for a single UTC day.
    Returns the data[] array from the Admin API response.
    """
    url = f"{BASE_URL}/organizations/{ORG_ID}/usage"
    params = {
        "start_time": f"{target_date}T00:00:00Z",
        "end_time":   f"{target_date}T23:59:59Z",
        "granularity": "day",
        "group_by": "api_key_id",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=HEADERS, params=params)
        resp.raise_for_status()
        return resp.json().get("data", [])


async def fetch_yesterday_usage() -> list[dict]:
    """Convenience wrapper — fetches the most recently completed UTC day."""
    yesterday = date.today() - timedelta(days=1)
    return await fetch_usage_for_date(yesterday)
