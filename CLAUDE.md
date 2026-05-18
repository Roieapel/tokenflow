# TokenFlow — Build Timeline & Architecture

> Enterprise AI Token Redistribution for Anthropic Enterprise accounts.  
> Same budget. Same contract. Zero waste. Engineers unblocked.

---

## Project Overview

TokenFlow is a **proxy layer** between every employee's Claude tool (Claude Code / Claude.ai) and the Anthropic API. It identifies callers in real time, tracks token consumption by role, and automatically routes idle PM/designer bandwidth to engineers the moment they hit their limit.

**Key design principle:** Engineers are counted in real time (stream parsing, sub-millisecond). Non-engineers (PM/designer/ops) are counted via the Admin API on a 10-15 min sync cycle — no SSE overhead for roles that never hit quota.

---

## Build Stages

### Stage 1 · Identity Layer (Week 1–2)

**Goal:** Map every inbound request to a real employee in under 1ms.

**What gets built:**
- Virtual key issuance: each employee receives a unique proxy key (format: `vtk_<uuid>`)
- Key→user mapping store (Redis, TTL=30 days)
- Role tagging: `engineer`, `pm`, `designer`, `ops` — sourced from HR/SSO directory (Okta SCIM)
- Middleware that extracts the key from `Authorization: Bearer vtk_...` before forwarding to Anthropic

**Redis data model (written by directory-sync):**
```
key:<vtk_key>              → string: user_id
registry:key_to_user       → hash:   {vtk_key: user_id}  (used by sync-agent & rebalancer)
user:<uid>:name            → string: display name
user:<uid>:role            → string: "engineer" | "pm" | "designer" | "ops"
role:<role>                → hash:   {daily_tokens: <int>}
```

**Subagent: `identity-agent`**
- Runs as a sidecar in the proxy pod
- Responsibilities: key validation, user resolution, role lookup
- Latency budget: < 1ms (hot-path Redis read)
- Skills: `key-validator`, `role-resolver`, `directory-sync`

---

### Stage 2A · Engineer Token Counter (Week 1–2, parallel)

**Goal:** Count every engineer token, input and output, live — per user, per session.

**What gets built:**
- Streaming interceptor: wraps Anthropic SSE stream, counts tokens from `usage` fields in each chunk
- Per-user counters in Redis (`INCRBY user:<uid>:tokens:<date> <n>`, 48h TTL)
- Session tracker: groups counts by `X-Session-ID` for agentic Claude Code runs
- **Role gate:** non-engineer requests pass through without SSE interception — no counting overhead

**Subagent: `counter-agent`**
- Lives in the proxy request pipeline (synchronous with the stream)
- Engineers only: parse SSE chunks, accumulate counts, flush to Redis every 10 tokens or on stream end
- Non-engineers: proxy passthrough only; `usage-sync-agent` counts them asynchronously
- Skills: `stream-parser`, `redis-writer`, `session-tracker`

---

### Stage 2B · Non-Engineer Usage Sync (Week 2–3)

**Goal:** Maintain authoritative token counts for PM/designer/ops without real-time stream parsing. Pool is never more than 10-15 minutes stale.

**What gets built:**
- Long-running daemon polling Anthropic Admin API every 10 min (configurable via `SYNC_INTERVAL_SECONDS`)
- Fetches cumulative today-totals (midnight UTC → now) for all non-engineer API keys
- Writes authoritative `SET` (not `INCRBY`) to Redis — idempotent overwrite, correct because Admin API returns full-day totals
- After writing, recalculates `pool:available` = Σ(allocation − used) across all non-engineers
- Publishes `pool:updated` and `sync:completed` events for dashboard and pool-agent

**Why `SET` not `INCRBY`:**  
The Admin API returns cumulative daily totals, not deltas. On each sync the new value is the ground truth for the entire day so far. Overwriting is safe and idempotent.

**Subagent: `usage-sync-agent`**
- Runs as a long-lived daemon (or tight cron)
- Skills: `api-poller`, `counter-writer`

---

### Stage 3 · Pool Manager (Week 3–4)

**Goal:** Detect idle bandwidth and route it to blocked engineers in real time.

**What gets built:**
- Per-role allocation config (e.g. Engineers: 500K/day, PMs: 50K/day, Ops: 5K/day)
- `pool:available` key in Redis — updated every 10-15 min by `usage-sync-agent` (not just midnight)
- Block detection: when engineer's counter hits `allocation`, request is evaluated against pool
- Pool borrow: **atomic Lua script** checks and decrements pool in one round-trip (eliminates TOCTOU race)
- If borrow succeeds: proxy reissues the held request under pool key; publishes `event:redirect` to dashboard
- Borrow ledger: Redis Stream (audit trail) + O(1) per-user daily counter for fast reporting

**Pool decision logic (atomic Lua):**
```lua
-- Single round-trip — no TOCTOU race between check and decrement
local pool = tonumber(redis.call('GET', 'pool:available') or '0')
local amount = tonumber(ARGV[1])
if pool >= amount then
    redis.call('DECRBY', 'pool:available', amount)
    return 1   -- borrow approved
end
return 0       -- pool exhausted
```

**Pool staleness:** In the original design, pool was set once at midnight. Now `usage-sync-agent` recalculates it every 10-15 min — always reflecting actual PM/designer usage, not a 24h-old guess.

**Subagent: `pool-agent`**
- Stateful daemon, reads pool balance from Redis (written by `usage-sync-agent`)
- Skills: `pool-calculator`, `borrow-ledger`, `redirect-executor`

---

### Stage 4 · Reconciler / Rebalancer (Week 3–4, parallel)

**Goal:** Verify engineer real-time counts against Anthropic actuals every 4-6 hours; adjust role allocations dynamically.

**What changed from the original daily-cron design:**
- Runs every **4-6 hours** (rolling) instead of once at midnight
- Does **not** reset `pool:available` — `usage-sync-agent` owns the pool
- Does **not** clear user counters — engineers count in real time; non-engineers are overwritten by sync-agent
- Reconciles **engineer counters only** — non-engineer Redis values are already Admin API ground truth
- Uses an explicit `target_date` parameter so comparisons use the correct Redis key

**What gets built:**
- Rolling reconciliation against Anthropic Admin API (yesterday's completed day)
- Drift detection: flags any engineer where |Anthropic − internal| > 5%
- Dynamic allocation adjustments: reads `ALLOC_*` env overrides and pushes to Redis
- Runs as a daemon loop with `REBALANCER_INTERVAL_SECONDS` (default 4h)

**Subagent: `rebalancer-agent`**
- Skills: `admin-api-client`, `reconciler`, `config-pusher`

---

### Stage 5 · Admin Dashboard (Week 5–6)

**Goal:** Real-time visibility into pool balance, per-user usage, waste recovered, alerts.

**What gets built:**
- REST API: `GET /admin/usage`, `GET /admin/pool`, `GET /admin/events`, `GET /admin/savings`
- WebSocket endpoint (`/admin/ws`) — subscribes to `pool:updated`, `event:redirect`, `config:updated`, `sync:completed`
- Frontend: `tokenflow_demo.html`

**Subagent: `dashboard-agent`**
- Skills: `rest-api`, `websocket-server`, `event-formatter`

---

## Subagent Summary

| Subagent | Stage | Language | Deployment | Key Skill |
|---|---|---|---|---|
| `identity-agent` | 1 | Go | Sidecar (proxy pod) | `key-validator` |
| `counter-agent` | 2A | Go | Inline (hot path) | `stream-parser` |
| `usage-sync-agent` | 2B | Python | Daemon (10-15 min poll) | `counter-writer` |
| `pool-agent` | 3 | Go | Daemon (same pod) | `pool-calculator` |
| `rebalancer-agent` | 4 | Python | Daemon (4-6h rolling) | `reconciler` |
| `dashboard-agent` | 5 | Node.js | Separate service | `websocket-server` |

---

## Admin API Integration

### Anthropic Admin API — What It Provides

Used by both `usage-sync-agent` (every 10-15 min, non-engineers) and `rebalancer-agent` (every 4-6h, engineer reconciliation).

```
Base URL: https://api.anthropic.com/v1
Auth:     x-api-key: <ANTHROPIC_ADMIN_KEY>
          anthropic-version: 2023-06-01
```

### Endpoint: Usage Per API Key

```http
GET /v1/organizations/{organization_id}/usage
    ?start_time=2026-05-01T00:00:00Z
    &end_time=2026-05-02T00:00:00Z
    &granularity=day
    &group_by=api_key_id
```

**Response shape:**
```json
{
  "data": [
    {
      "api_key_id": "apikey_abc123",
      "input_tokens":  48200000,
      "output_tokens": 12100000,
      "total_cost_usd": 180.90,
      "period_start": "2026-05-01T00:00:00Z",
      "period_end":   "2026-05-02T00:00:00Z"
    }
  ]
}
```

---

### Dashboard WebSocket — Live Events

```javascript
// dashboard-agent subscribes to all pub/sub channels
await sub.subscribe("pool:updated",   msg => broadcast({type:"pool",   value:Number(msg)}));
await sub.subscribe("event:redirect", msg => broadcast({type:"event",  ...JSON.parse(msg)}));
await sub.subscribe("config:updated", msg => broadcast({type:"config", ...JSON.parse(msg)}));
await sub.subscribe("sync:completed", msg => broadcast({type:"sync",   ...JSON.parse(msg)}));
```

---

### Environment Variables

```bash
# Shared
ANTHROPIC_ADMIN_KEY=sk-ant-admin-...     # Admin API key from Anthropic Console
ANTHROPIC_ORG_ID=org-...                 # Organization ID from Console
REDIS_URL=redis://localhost:6379

# usage-sync-agent
SYNC_INTERVAL_SECONDS=600                # 10 min default (600-900 recommended)

# rebalancer-agent
REBALANCER_INTERVAL_SECONDS=14400        # 4h default
ALLOC_ENGINEER=500000                    # Optional per-role token overrides
ALLOC_PM=50000
ALLOC_DESIGNER=50000
ALLOC_OPS=5000

# pool-agent
ANTHROPIC_POOL_KEY=sk-ant-...            # Real Anthropic key used for pool borrows

# dashboard-agent
ADMIN_DASHBOARD_TOKEN=...                # Random 256-bit token, shared with frontend
DASHBOARD_PORT=8080
```

---

## Skill Definitions

| Skill | Used by | What it does |
|---|---|---|
| `key-validator` | identity-agent | Validates `vtk_` prefix, checks Redis for existence |
| `role-resolver` | identity-agent | Maps user_id to role via SSO directory sync |
| `directory-sync` | identity-agent | Pulls HR directory (Okta SCIM) nightly |
| `stream-parser` | counter-agent | Parses Anthropic SSE chunks (engineers only), extracts `usage` fields |
| `redis-writer` | counter-agent | Atomic INCRBY with expiry on daily counter keys |
| `session-tracker` | counter-agent | Groups token counts by X-Session-ID |
| `api-poller` | usage-sync-agent | Fetches today's cumulative usage (midnight UTC → now) from Admin API |
| `counter-writer` | usage-sync-agent | SET non-engineer counters; recalculates and publishes pool |
| `pool-calculator` | pool-agent | Atomic Lua check-and-decrement; decides routing per request |
| `borrow-ledger` | pool-agent | Append-only Redis Stream + O(1) daily counter for fast reporting |
| `redirect-executor` | pool-agent | Rewrites Authorization to pool key, forwards, publishes event:redirect |
| `admin-api-client` | rebalancer-agent | Authenticated calls to Anthropic Admin API |
| `reconciler` | rebalancer-agent | Diffs Anthropic actuals vs engineer internal counters (date-parameterized) |
| `config-pusher` | rebalancer-agent | Updates allocation config in Redis and notifies via pub/sub |
| `rest-api` | dashboard-agent | Serves `/admin/*` endpoints with token auth |
| `websocket-server` | dashboard-agent | Fans out Redis pub/sub to browser WebSocket clients |
| `event-formatter` | dashboard-agent | Formats raw events into human-readable log entries |

---

## Token Flow Diagram

```
Employee IDE / Browser
        |
        | Bearer vtk_<uuid>
        v
+-------------------------------------------------------------------+
|                       TokenFlow Proxy                              |
|                                                                   |
|  identity-agent  →  counter-agent           →  pool-agent        |
|  (who + role?)      engineer? → count SSE      Lua atomic borrow |
|                     other?    → passthrough     publishes event   |
+-------------------------------------------------------------------+
        |
        | Bearer sk-ant-<real_key>
        v
   Anthropic API
        |
        v
+--------------------+     +----------------------+     +--------------------+
|  dashboard-agent   |←----|  usage-sync-agent    |     |  rebalancer-agent  |
|  (REST + WS)       |     |  every 10-15 min     |     |  every 4-6 hours   |
+--------------------+     +----------------------+     +--------------------+
        ↑                           |                            |
        |                    writes pool:available       reconciles engineers
        |                    publishes pool:updated       adjusts allocations
        └──────── WebSocket push to tokenflow_demo.html ────────┘
```

---

## What Each Role Gets

| Role | Counted by | Mechanism | Pool staleness |
|---|---|---|---|
| engineer | counter-agent | Real-time SSE interception | ~0 ms |
| pm | usage-sync-agent | Admin API SET overwrite | ≤ 10-15 min |
| designer | usage-sync-agent | Admin API SET overwrite | ≤ 10-15 min |
| ops | usage-sync-agent | Admin API SET overwrite | ≤ 10-15 min |

---

## Bug Fixes Applied (v1 → v2)

| Bug | File | Fix |
|---|---|---|
| TOCTOU race on pool deduction | pool_calculator.go | Lua atomic check-and-decrement in one round-trip |
| Reconciler compared wrong day | reconciler.py | `target_date` parameter replaces `_today_str()` |
| Missing `fmt` import (compile error) | borrow_ledger.go | Added `"fmt"` to imports |
| SSE buffer reset dropped partial lines | stream_parser.go | `pending []byte` slice preserves partial lines across `Write` calls |
| `event:redirect` never published | redirect_executor.go | `publishEvent()` called after each successful forward |
| Multiple Redis clients per service | all Go agents | Single `*redis.Client` created at startup, injected into skills |
| `r.hget("user:<uid>","role")` type mismatch | reconciler.py + sync-agent | `r.get("user:<uid>:role")` matches string key written by directory-sync |
| O(n) stream scan in TodayTotal | borrow_ledger.go | Dedicated per-user daily counter key alongside stream (O(1) GET) |

---

## Rollout Plan

| Week | Milestone | Owner |
|---|---|---|
| 1 | identity-agent + counter-agent in staging | Eng |
| 2 | usage-sync-agent live; verify non-engineer counts match Console | Eng + PM |
| 3 | pool-agent live with synthetic load; verify atomic Lua borrow | Eng |
| 4 | rebalancer-agent live, first 4h reconciliation validated | Eng |
| 5 | dashboard-agent + tokenflow_demo.html connected to staging | Eng |
| 6 | Pilot with 50 engineers + 100 PMs in production | All |
| 7 | Full 700-person rollout | All |

---

*Sources: Ramp AI Index 2026 · CloudZero 2025 · docs.anthropic.com/en/api/rate-limits · docs.anthropic.com/en/api/administration*
