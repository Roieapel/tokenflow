#!/usr/bin/env python3
"""
test_flow.py — End-to-end test of the TokenFlow proxy.

Sends 5 fake requests through the proxy and shows the enforce decision
(ALLOW / BORROW / BLOCK) and the usage + pool state after each one.

Run this AFTER starting both servers:
    terminal 1:  python mock_anthropic.py
    terminal 2:  ANTHROPIC_URL=http://localhost:9090 TOKENFLOW_CONFIG=test_config.json python proxy.py
    terminal 3:  python test_flow.py

Expected sequence with test_config.json (15k engineer quota, 8k pool, 6k per call):
    request 1 → ALLOW   (0 used)
    request 2 → ALLOW   (6k used)
    request 3 → BORROW  (12k used, over quota, pool covers it)
    request 4 → BLOCK   (18k used, pool exhausted)
    request 5 → BLOCK   (same)
"""

import json
import sys
import time

try:
    import requests
except ImportError:
    print("Missing dependency. Run:  pip install requests")
    sys.exit(1)

PROXY   = "http://localhost:8080"
MOCK    = "http://localhost:9090"
USER    = "engineer@test.com"

# ANSI colours
G  = "\033[92m"   # green
Y  = "\033[93m"   # yellow
R  = "\033[91m"   # red
B  = "\033[94m"   # blue
DIM = "\033[2m"
RST = "\033[0m"
BOLD = "\033[1m"


def colour_decision(d: str) -> str:
    if d == "allow":  return f"{G}✓ ALLOW{RST}"
    if d == "borrow": return f"{Y}~ BORROW{RST}"
    if d == "block":  return f"{R}✗ BLOCK{RST}"
    return d


def check_server(url: str, name: str) -> bool:
    try:
        requests.get(url, timeout=2)
        return True
    except requests.exceptions.ConnectionError:
        print(f"{R}✗{RST}  {name} not reachable at {url}")
        return False


def get_state() -> tuple[dict, dict]:
    try:
        usage = requests.get(f"{PROXY}/admin/usage", timeout=3).json()
        pool  = requests.get(f"{PROXY}/admin/pool",  timeout=3).json()
        return usage, pool
    except Exception:
        return {}, {}


def send_request(n: int) -> tuple[int, str, int]:
    """Send one fake message. Returns (status_code, decision_header, tokens_in_response)."""
    payload = {
        "model":     "claude-3-5-sonnet-20241022",
        "max_tokens": 100,
        "messages":  [{"role": "user", "content": "hello"}],
    }
    headers = {
        "X-User-Email":      USER,
        "anthropic-version": "2023-06-01",
        "content-type":      "application/json",
    }
    try:
        resp = requests.post(f"{PROXY}/v1/messages",
                             json=payload, headers=headers, timeout=10)
        decision = resp.headers.get("X-TokenFlow-Decision", "unknown")
        tokens = 0
        try:
            tokens = sum(resp.json().get("usage", {}).values())
        except Exception:
            pass
        return resp.status_code, decision, tokens
    except requests.exceptions.ConnectionError:
        return 0, "no_connection", 0


def print_state(usage: dict, pool: dict) -> None:
    users = usage.get("users", [])
    user_row = next((u for u in users if u["email"] == USER), None)

    if user_row:
        used  = user_row["used"]
        alloc = user_row["allocation"]
        pct   = user_row["pct"]
        bar_filled = int(pct / 5)
        bar = "█" * bar_filled + "░" * (20 - bar_filled)
        print(f"  {DIM}quota{RST}  [{bar}] {used:>6,} / {alloc:,}  ({pct}%)")
    else:
        print(f"  {DIM}quota{RST}  no data yet")

    avail = pool.get("available", "?")
    total = pool.get("total", "?")
    ppct  = pool.get("pct", 0)
    pbar_filled = int(ppct / 5) if ppct else 0
    pbar = "█" * pbar_filled + "░" * (20 - pbar_filled)
    print(f"  {DIM}pool {RST}  [{pbar}] {avail:>6,} / {total:,}  ({ppct}%)")


def run() -> None:
    print(f"\n{BOLD}  TokenFlow — End-to-End Test{RST}")
    print("  " + "─" * 40)
    print(f"  proxy  → {PROXY}")
    print(f"  mock   → {MOCK}")
    print(f"  user   → {USER}")
    print(f"  config → test_config.json\n")

    # Pre-flight checks
    print(f"{BOLD}  Checking servers...{RST}")
    proxy_ok = check_server(f"{PROXY}/admin/health", "proxy")
    mock_ok  = check_server(f"{MOCK}/health",        "mock-anthropic")

    if not proxy_ok:
        print(f"\n  Start the proxy first:")
        print(f"  {DIM}ANTHROPIC_URL=http://localhost:9090 TOKENFLOW_CONFIG=test_config.json python proxy.py{RST}\n")
        sys.exit(1)

    if not mock_ok:
        print(f"\n  Start the mock server first:")
        print(f"  {DIM}python mock_anthropic.py{RST}\n")
        sys.exit(1)

    print(f"  {G}✓{RST} Both servers reachable\n")
    print("  " + "─" * 40)

    # Initial state
    print(f"\n  {BOLD}Initial state{RST}")
    usage, pool = get_state()
    print_state(usage, pool)

    # Run 5 requests
    for i in range(1, 6):
        print(f"\n  {BOLD}Request {i}{RST}")
        status, decision, tokens = send_request(i)

        if status == 0:
            print(f"  {R}✗ Could not connect to proxy{RST}")
            continue

        print(f"  decision → {colour_decision(decision)}   HTTP {status}   tokens in response: {tokens:,}")
        time.sleep(0.1)   # give proxy a moment to update counters
        usage, pool = get_state()
        print_state(usage, pool)

    # Events log
    print(f"\n  {BOLD}Event log{RST}  (borrow + block events)")
    try:
        events = requests.get(f"{PROXY}/admin/events?limit=10", timeout=3).json().get("events", [])
        if events:
            for e in events:
                ts   = e.get("ts", "")
                kind = e.get("type", "?")
                user = e.get("user", "?")
                mark = colour_decision(kind) if kind in ("allow","borrow","block") else f"{B}{kind}{RST}"
                extra = ""
                if kind == "borrow":
                    extra = f"  pool after: {e.get('pool_after', '?'):,}"
                if kind == "block":
                    extra = f"  used: {e.get('used', '?'):,}  alloc: {e.get('alloc', '?'):,}"
                print(f"  {DIM}{ts}{RST}  {mark}  {user}{extra}")
        else:
            print(f"  {DIM}(no borrow or block events logged){RST}")
    except Exception:
        print(f"  {DIM}(could not fetch events){RST}")

    print(f"\n  {'─' * 40}")
    print(f"  {G}Done.{RST}  Edit test_config.json to change limits and re-run.\n")


if __name__ == "__main__":
    run()
