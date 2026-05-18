# TokenFlow

> **Same budget. Same contract. Zero waste. Engineers unblocked.**

TokenFlow is a proxy layer between every employee's Claude tool and the Anthropic API. It identifies callers in real time, tracks token consumption by role, and automatically routes idle PM/designer bandwidth to engineers the moment they hit their limit.

---

## The Problem

Engineers use Claude Code — agentic sessions that read entire codebases and loop autonomously, burning **200K–500K tokens per run**. PMs and designers use Claude.ai — short conversational sessions, **10K–50K tokens per day**.

Both draw from the same company account.

| Role | Tool | Typical daily usage |
|---|---|---|
| Engineers | Claude Code | 200K–500K tokens |
| PMs / Designers | Claude.ai | 10K–50K tokens |
| HR / Finance / Legal | Claude.ai | 2K–5K tokens |

One Claude Code session consumes what a PM uses in a week. The engineer hits the rate limit and stops dead. The PM's unused allocation sits idle and **expires at month end**. The company paid for both.

---

## The Market

A typical 700-person tech company on Anthropic Enterprise:

| Metric | Value |
|---|---|
| Monthly AI spend | **$85,000** |
| Tokens wasted monthly | **12B tokens (43%)** |
| **Annual waste** | **$432,000** |

*Sources: Ramp AI Index 2026 · CloudZero 2025 · Claude Sonnet $3/M tokens*

TokenFlow recovers that $432K without touching the Anthropic contract, account, or model.

---

## How It Works

```
Employee IDE / Browser
        │
        │  Bearer vtk_<uuid>   ← virtual key, one per employee
        ▼
┌─────────────────────────────────────────┐
│           TokenFlow Proxy               │
│                                         │
│  Who is this?  →  How many tokens?  →  Allow / Borrow / Block
└─────────────────────────────────────────┘
        │
        │  Bearer sk-ant-<real_key>
        ▼
   Anthropic API
```

1. **Engineer hits limit** → proxy finds idle PM/designer bandwidth → call goes through instantly
2. **Idle quota** → pooled and available within 10–15 minutes of the last sync
3. **Admin dashboard** → approve or auto-route token requests, see live usage per person

Employees change one thing: paste a virtual key into their IDE. Everything else is invisible.

---

## Architecture

Five microservices, each owning one part of the flow:

| Service | Language | Role |
|---|---|---|
| `identity-agent` | Go | Maps virtual key → user → role in < 1ms |
| `counter-agent` | Go | Counts engineer tokens live from SSE stream |
| `pool-agent` | Go | Atomic Lua borrow from shared pool |
| `usage-sync-agent` | Python | Syncs PM/designer usage from Admin API every 10 min |
| `rebalancer-agent` | Python | Reconciles counters against Anthropic actuals every 4–6h |
| `dashboard-agent` | Node.js | REST + WebSocket admin dashboard |

---

## Quick Start (local demo)

**Requirements:** Python 3.9+, Node 18+

```bash
# 1. Clone and install deps
git clone https://github.com/Roieapel/tokenflow
cd tokenflow
pip3 install flask httpx

# 2. Start mock Anthropic (terminal 1)
python3 mock_anthropic.py

# 3. Start proxy (terminal 2)
ANTHROPIC_URL=http://localhost:9090 TOKENFLOW_CONFIG=test_config.json python3 proxy.py

# 4. Run demo (terminal 3)
node demo.js
```

Open **http://localhost:8080/admin/dashboard** — watch engineer usage fill up, hit the pool, and trigger an approval request in real time.

### Demo flow

The demo runs 3 users against a `test_config.json` with realistic limits:

| User | Role | Limit | Calls to fill |
|---|---|---|---|
| engineer@test.com | engineer | 40k tokens | 8 calls |
| pm@test.com | pm | 20k tokens | 4 calls |
| hr@test.com | hr | 10k tokens | 2 calls |

Call 9 from the engineer hits the pool. The demo submits a token request and waits for you to approve it on the dashboard.

---

## Configuration

Copy `.env.example` to `.env` and fill in:

```bash
ANTHROPIC_API_KEY=sk-ant-...          # forwarded to Anthropic
ANTHROPIC_URL=https://api.anthropic.com  # or http://localhost:9090 for mock
TOKENFLOW_CONFIG=config.json          # path to allocations config
PORT=8080
```

`config.json` controls allocations and users:

```json
{
  "pool_tokens": 1000000,
  "allocations": {
    "engineer": 500000,
    "pm":        50000,
    "designer":  50000,
    "ops":       10000
  },
  "users": {
    "alice@company.com": { "role": "engineer", "name": "Alice" },
    "bob@company.com":   { "role": "pm",       "name": "Bob"   }
  }
}
```

---

## What Gets Built

| Stage | Component | When |
|---|---|---|
| 1 | Identity layer — virtual keys, role mapping | Week 1–2 |
| 2A | Token counter — live SSE stream parsing | Week 1–2 |
| 2B | Usage sync — Admin API poll every 10 min | Week 2–3 |
| 3 | Pool manager — atomic Lua borrow | Week 3–4 |
| 4 | Rebalancer — 4–6h reconciliation | Week 3–4 |
| 5 | Admin dashboard — REST + WebSocket | Week 5–6 |

---

## Key Design Decisions

- **Engineers counted in real time** (SSE stream parsing, sub-millisecond) — they're the ones who hit quota
- **Non-engineers counted async** (Admin API every 10–15 min) — no streaming overhead for roles that never need it
- **Atomic Lua pool borrow** — single Redis round-trip, no TOCTOU race under concurrent load
- **No Anthropic contract changes** — proxy swaps virtual keys for real keys transparently

---

*Sources: Ramp AI Index 2026 · CloudZero 2025 · docs.anthropic.com/en/api/rate-limits*
