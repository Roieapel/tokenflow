#!/usr/bin/env python3
"""
request_tokens.py — A user requests more tokens from the admin.

Simulates an engineer who has hit their quota and needs more capacity.
Submits a request to the proxy, then waits while polling for the admin's decision.

Watch the dashboard at http://localhost:8080/admin/dashboard
The admin sees the request appear and can approve or deny it there.

Usage:
    python3 request_tokens.py
    python3 request_tokens.py --email dana@test.com --amount 20000 --reason "big refactor"
"""

import argparse
import sys
import time

try:
    import requests
except ImportError:
    print("Run:  pip3 install requests")
    sys.exit(1)

PROXY = "http://localhost:8080"

# ── Args ──────────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser()
parser.add_argument("--email",  default="engineer@test.com")
parser.add_argument("--amount", default=10_000, type=int)
parser.add_argument("--reason", default="Need extra tokens for a large task today")
args = parser.parse_args()

# ── Colours ───────────────────────────────────────────────────────────────────

G   = "\033[92m"
Y   = "\033[93m"
R   = "\033[91m"
DIM = "\033[2m"
RST = "\033[0m"
B   = "\033[1m"


def run():
    print(f"\n{B}  Token Request{RST}")
    print(f"  {'─' * 44}")
    print(f"  user   : {args.email}")
    print(f"  asking : {args.amount:,} tokens")
    print(f"  reason : {args.reason}")
    print()

    # Check proxy is up
    try:
        requests.get(f"{PROXY}/admin/health", timeout=3)
    except Exception:
        print(f"  {R}✗ Proxy not reachable at {PROXY}{RST}")
        print(f"  Start it first:  python3 proxy.py\n")
        sys.exit(1)

    # Submit the request
    resp = requests.post(f"{PROXY}/request-tokens", json={
        "email":  args.email,
        "amount": args.amount,
        "reason": args.reason,
    }, timeout=5)

    if resp.status_code != 200:
        print(f"  {R}✗ Failed to submit: {resp.text}{RST}\n")
        sys.exit(1)

    req_id = resp.json()["request_id"]

    print(f"  {G}✓ Request submitted{RST}  ({req_id})")
    print(f"  {DIM}⏳ Waiting for admin decision...{RST}")
    print()
    print(f"  {DIM}Open the dashboard and look for the pending card:{RST}")
    print(f"  {DIM}http://localhost:8080/admin/dashboard{RST}")
    print()

    # Poll for decision
    dots = 0
    while True:
        time.sleep(2)
        try:
            data   = requests.get(f"{PROXY}/request-tokens/{req_id}", timeout=3).json()
            status = data.get("status", "pending")
        except Exception:
            print(f"  {R}✗ Lost connection to proxy{RST}\n")
            sys.exit(1)

        if status == "approved":
            granted = data.get("granted", 0)
            print(f"\r  {G}✓ APPROVED  —  {granted:,} tokens granted to {args.email}{RST}          ")
            print(f"  {DIM}Your allocation has been increased. You can make more requests.{RST}\n")
            break

        elif status == "denied":
            print(f"\r  {R}✗ DENIED  —  request was rejected by the admin{RST}               ")
            print(f"  {DIM}Try again tomorrow or request a smaller amount.{RST}\n")
            break

        else:
            dots = (dots + 1) % 4
            indicator = "⏳ " + "." * dots + "   "
            print(f"\r  {DIM}{indicator}{RST}", end="", flush=True)


if __name__ == "__main__":
    run()
