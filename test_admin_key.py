#!/usr/bin/env python3
"""
test_admin_key.py — Anthropic Admin API key tester.

This script goes where a real ANTHROPIC_API_KEY would normally live.
Run it first to verify your Admin API credentials and see exactly what
usage data Anthropic returns for your org before wiring up the live proxy.

Usage:
    pip install httpx
    ANTHROPIC_ADMIN_KEY=sk-ant-admin-... ANTHROPIC_ORG_ID=org-... python test_admin_key.py

What it tests:
    1. Key format validation
    2. GET /v1/organizations/{org}/usage  — yesterday's token totals by API key
    3. GET /v1/organizations/{org}/api_keys — keys registered in your org

When all checks pass, set ANTHROPIC_API_KEY in .env to the key you want
the proxy to forward requests under, then start proxy.py.
"""

import asyncio
import json
import os
from datetime import date, timedelta

import httpx

# ── Credentials ───────────────────────────────────────────────────────────────

ADMIN_KEY = os.environ.get("ANTHROPIC_ADMIN_KEY", "")
ORG_ID    = os.environ.get("ANTHROPIC_ORG_ID", "")
BASE_URL  = "https://api.anthropic.com/v1"
HEADERS   = {
    "x-api-key":         ADMIN_KEY,
    "anthropic-version": "2023-06-01",
    "anthropic-beta":    "admin-api-2024-05-01",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _sep(title: str) -> None:
    print(f"\n{'─' * 58}")
    print(f"  {title}")
    print("─" * 58)


def _ok(msg: str)   -> None: print(f"  ✓  {msg}")
def _warn(msg: str) -> None: print(f"  ⚠  {msg}")
def _fail(msg: str) -> None: print(f"  ✗  {msg}")


# ── Tests ─────────────────────────────────────────────────────────────────────

def check_credentials() -> tuple[bool, bool]:
    _sep("1 · Credentials check")

    key_ok = bool(ADMIN_KEY)
    org_ok = bool(ORG_ID)

    if not key_ok:
        _fail("ANTHROPIC_ADMIN_KEY not set")
        print("     export ANTHROPIC_ADMIN_KEY=sk-ant-admin-...")
    elif not ADMIN_KEY.startswith("sk-ant-admin"):
        _warn(f"Key doesn't look like an admin key: {ADMIN_KEY[:24]}...")
        print("     Admin keys start with 'sk-ant-admin-'")
        print("     Generate one at: console.anthropic.com → Settings → API Keys")
        key_ok = True   # might still work; let the API decide
    else:
        _ok(f"Key format valid:  {ADMIN_KEY[:28]}...")

    if not org_ok:
        _fail("ANTHROPIC_ORG_ID not set")
        print("     export ANTHROPIC_ORG_ID=org-...")
        print("     Find it at: console.anthropic.com → Settings → Organization")
    else:
        _ok(f"Org ID:            {ORG_ID}")

    return key_ok, org_ok


async def check_usage() -> list[dict]:
    _sep("2 · Usage endpoint  (yesterday's totals by API key)")

    yesterday = date.today() - timedelta(days=1)
    url       = f"{BASE_URL}/organizations/{ORG_ID}/usage"
    params    = {
        "start_time":  f"{yesterday}T00:00:00Z",
        "end_time":    f"{yesterday}T23:59:59Z",
        "granularity": "day",
        "group_by":    "api_key_id",
    }

    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.get(url, headers=HEADERS, params=params)
        except httpx.ConnectError:
            _fail("Connection failed — check network")
            return []
        except httpx.TimeoutException:
            _fail("Request timed out")
            return []

    print(f"  HTTP {resp.status_code}")

    if resp.status_code == 200:
        entries = resp.json().get("data", [])
        _ok(f"{len(entries)} API key entr{'y' if len(entries)==1 else 'ies'} for {yesterday}")

        for e in entries[:5]:
            total = e.get("input_tokens", 0) + e.get("output_tokens", 0)
            cost  = e.get("total_cost_usd", 0)
            kid   = e.get("api_key_id", "?")
            print(f"      {kid[:26]:<26}  {total:>12,} tokens   ${cost:>7.2f}")

        if len(entries) > 5:
            print(f"      … and {len(entries) - 5} more")

        if entries:
            print(f"\n  Raw sample (first entry):")
            pretty = json.dumps(entries[0], indent=4)
            for line in pretty.splitlines():
                print(f"      {line}")

        return entries

    elif resp.status_code == 401:
        _fail("Unauthorized — check ANTHROPIC_ADMIN_KEY")
        print(f"     {resp.text[:200]}")
    elif resp.status_code == 403:
        _fail("Forbidden — key may not have Admin API access")
        print("     Request Admin API access at: console.anthropic.com")
        print(f"     {resp.text[:200]}")
    elif resp.status_code == 404:
        _fail("Org not found — check ANTHROPIC_ORG_ID")
        print(f"     {resp.text[:200]}")
    else:
        _fail(f"Unexpected {resp.status_code}")
        print(f"     {resp.text[:300]}")

    return []


async def check_api_keys() -> None:
    _sep("3 · API keys in your org")

    url = f"{BASE_URL}/organizations/{ORG_ID}/api_keys"

    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.get(url, headers=HEADERS)
        except Exception as e:
            _fail(str(e))
            return

    print(f"  HTTP {resp.status_code}")

    if resp.status_code == 200:
        keys = resp.json().get("data", [])
        _ok(f"{len(keys)} API key{'s' if len(keys) != 1 else ''} found")
        for k in keys[:8]:
            kid    = k.get("id", "?")[:28]
            name   = k.get("name", "unnamed")[:24]
            status = k.get("status", "?")
            mark   = "✓" if status == "active" else "·"
            print(f"      {mark}  {kid:<28}  {name:<24}  {status}")
        if len(keys) > 8:
            print(f"      … and {len(keys) - 8} more")
    elif resp.status_code == 404:
        _warn("API keys endpoint returned 404 — may not be available on this plan")
    else:
        _fail(f"HTTP {resp.status_code}: {resp.text[:200]}")


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    print("\n  TokenFlow — Admin API Key Tester")
    print("  " + "=" * 36)
    print("  Verifies your Anthropic Admin API credentials")
    print("  and shows what usage data your org exposes.\n")
    print(f"  ANTHROPIC_ADMIN_KEY : {ADMIN_KEY[:28] + '…' if ADMIN_KEY else 'NOT SET'}")
    print(f"  ANTHROPIC_ORG_ID    : {ORG_ID or 'NOT SET'}")

    key_ok, org_ok = check_credentials()

    if not (key_ok and org_ok):
        print("\n  Fix the missing credentials above and re-run.\n")
        return

    entries = await check_usage()
    await check_api_keys()

    _sep("Summary")

    if entries:
        _ok("Admin API is working — usage data is available")
        _ok("You can now set ANTHROPIC_API_KEY in .env and start proxy.py")
        print("\n  Note: the proxy uses ANTHROPIC_API_KEY (a regular API key)")
        print("  to forward requests. The Admin key is only for reconciliation.")
        print("  They are different keys with different permissions.")
    else:
        _warn("No usage entries returned for yesterday")
        print("   This is normal if your org had no API traffic yesterday,")
        print("   or if the Admin API isn't enabled on your plan.")
        print("   Contact Anthropic support if you expect usage data here.")

    print(f"\n{'─' * 58}\n")


if __name__ == "__main__":
    asyncio.run(main())
