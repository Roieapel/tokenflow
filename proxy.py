#!/usr/bin/env python3
"""
TokenFlow Proxy — simplified, no Redis.

Replaces identity-agent + counter-agent + pool-agent in a single process.
Reads user identity from the X-User-Email header (set by your SSO/gateway).
Tracks token usage in-process. Enforces per-role quotas with pool borrowing.

Modes:
  auto   — proxy decides instantly (borrow from pool or block)
  manual — admin sees a pending request on the dashboard and decides

Usage:
    pip install flask httpx
    ANTHROPIC_URL=http://localhost:9090 TOKENFLOW_CONFIG=test_config.json python3 proxy.py

Endpoints forwarded:  POST /v1/messages  (and any other /v1/* path)
Admin endpoints:      GET  /admin/usage  /admin/pool  /admin/events  /admin/requests
User endpoints:       POST /request-tokens   GET /request-tokens/<id>
"""

import json
import os
import threading
import time
import uuid
from datetime import date

import httpx
from flask import Flask, Response, jsonify, request, send_file

# ── Config ────────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "fake-key-for-testing")
ANTHROPIC_URL     = os.environ.get("ANTHROPIC_URL", "https://api.anthropic.com")
PORT              = int(os.environ.get("PORT", 8080))
CONFIG_FILE       = os.environ.get("TOKENFLOW_CONFIG", "config.json")

with open(os.path.join(os.path.dirname(__file__), CONFIG_FILE)) as _f:
    CONFIG = json.load(_f)

RESERVE = 4_096   # conservative per-request token reserve

# ── In-memory state ───────────────────────────────────────────────────────────

_lock         = threading.Lock()
_counters     = {}    # { email: {date, tokens} }
_pool         = {"available": CONFIG.get("pool_tokens", 1_000_000)}
_events       = []    # last 200 events
_extra_tokens = {}    # { email: extra tokens granted by admin today }
_pending      = {}    # { request_id: request dict }
_mode         = {"value": "manual"} # "auto" or "manual"


def _today():
    return str(date.today())


def _get_used(email):
    c = _counters.get(email)
    if not c or c["date"] != _today():
        return 0
    return c["tokens"]


def _add_tokens(email, n):
    with _lock:
        c = _counters.setdefault(email, {"date": _today(), "tokens": 0})
        if c["date"] != _today():
            c["date"]   = _today()
            c["tokens"] = 0
        c["tokens"] += n


def _get_role(email):
    return CONFIG["users"].get(email, {}).get("role", "engineer")


def _get_allocation(email):
    role  = _get_role(email)
    base  = CONFIG["allocations"].get(role, 100_000)
    extra = _extra_tokens.get(email, 0)
    return base + extra


def _log_event(kind, email, extra=None):
    entry = {"ts":   time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "type": kind, "user": email, **(extra or {})}
    with _lock:
        _events.append(entry)
        if len(_events) > 200:
            _events.pop(0)


# ── Enforce ───────────────────────────────────────────────────────────────────

def enforce(email):
    """Return 'allow', 'borrow', or 'block'."""
    used  = _get_used(email)
    alloc = _get_allocation(email)

    if used + RESERVE <= alloc:
        _log_event("allow", email, {"used": used, "alloc": alloc})
        return "allow"

    with _lock:
        if _pool["available"] >= RESERVE:
            _pool["available"] -= RESERVE
            pool_after = _pool["available"]
        else:
            pool_after = None

    if pool_after is not None:
        _log_event("borrow", email, {"amount": RESERVE, "pool_after": pool_after})
        return "borrow"

    _log_event("block", email, {"used": used, "alloc": alloc})
    return "block"


# ── SSE token counting ────────────────────────────────────────────────────────

def _stream_and_count(resp, email):
    for line in resp.iter_lines():
        if line:
            yield (line + "\n\n").encode()
            if line.startswith("data: ") and line != "data: [DONE]":
                try:
                    u = json.loads(line[6:]).get("usage", {})
                    n = u.get("input_tokens", 0) + u.get("output_tokens", 0)
                    if n:
                        _add_tokens(email, n)
                except (json.JSONDecodeError, AttributeError):
                    pass


# ── Flask app ─────────────────────────────────────────────────────────────────

app = Flask(__name__)


# ── Proxy route ───────────────────────────────────────────────────────────────

@app.route("/v1/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
def proxy(path):
    email    = request.headers.get("X-User-Email", "unknown@company.com")
    decision = enforce(email)

    if decision == "block":
        return jsonify({"error":   "rate_limit",
                        "message": "Daily quota exhausted and pool is empty. Try again tomorrow."}), 429

    fwd = {k: v for k, v in request.headers
           if k.lower() not in ("host", "content-length", "authorization")}
    fwd["authorization"] = f"Bearer {ANTHROPIC_API_KEY}"
    body = request.get_data()

    is_stream = False
    try:
        is_stream = json.loads(body).get("stream", False)
    except (json.JSONDecodeError, ValueError):
        pass

    target = f"{ANTHROPIC_URL}/v1/{path}"

    if is_stream:
        client = httpx.Client(timeout=120)
        resp   = client.stream(request.method, target, headers=fwd, content=body)
        ctx    = resp.__enter__()
        return Response(
            _stream_and_count(ctx, email),
            status=ctx.status_code,
            content_type="text/event-stream",
            headers={"X-TokenFlow-Decision": decision, "X-TokenFlow-User": email},
            direct_passthrough=True,
        )
    else:
        with httpx.Client(timeout=120) as client:
            resp = client.request(request.method, target, headers=fwd, content=body)
        try:
            u = resp.json().get("usage", {})
            n = u.get("input_tokens", 0) + u.get("output_tokens", 0)
            if n:
                _add_tokens(email, n)
        except (json.JSONDecodeError, AttributeError):
            pass
        return Response(
            resp.content,
            status=resp.status_code,
            content_type=resp.headers.get("content-type", "application/json"),
            headers={"X-TokenFlow-Decision": decision, "X-TokenFlow-User": email},
        )


# ── Token request endpoints (called by users) ─────────────────────────────────

@app.route("/request-tokens", methods=["POST"])
def request_tokens():
    body   = request.get_json() or {}
    email  = body.get("email", "unknown@company.com")
    amount = int(body.get("amount", 10_000))
    reason = body.get("reason", "")
    req_id = "req_" + uuid.uuid4().hex[:8]

    _pending[req_id] = {
        "id":      req_id,
        "email":   email,
        "amount":  amount,
        "reason":  reason,
        "status":  "pending",
        "granted": 0,
        "ts":      time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _log_event("request", email, {"request_id": req_id, "amount": amount, "reason": reason})
    return jsonify({"request_id": req_id, "status": "pending"})


@app.route("/request-tokens/<req_id>")
def get_request_status(req_id):
    req = _pending.get(req_id)
    if not req:
        return jsonify({"error": "not found"}), 404
    return jsonify(req)


# ── Admin endpoints ───────────────────────────────────────────────────────────

@app.route("/admin/dashboard")
def admin_dashboard():
    return send_file(os.path.join(os.path.dirname(__file__), "dashboard.html"))


@app.route("/admin/usage")
def admin_usage():
    # Start with all configured users so chart shows them even before any request
    all_emails = set(CONFIG.get("users", {}).keys()) | set(_counters.keys())
    rows = []
    for email in sorted(all_emails):
        alloc = _get_allocation(email)
        c     = _counters.get(email)
        used  = (c["tokens"] if c and c["date"] == _today() else 0)
        extra = _extra_tokens.get(email, 0)
        name  = CONFIG.get("users", {}).get(email, {}).get("name", email)
        rows.append({"email":      email,
                     "name":       name,
                     "role":       _get_role(email),
                     "used":       used,
                     "allocation": alloc,
                     "extra":      extra,
                     "pct":        round(used / alloc * 100, 1) if alloc else 0})
    return jsonify({"users": rows, "date": _today()})


@app.route("/admin/pool")
def admin_pool():
    total = CONFIG.get("pool_tokens", 1_000_000)
    avail = _pool["available"]
    return jsonify({"available": avail,
                    "total":     total,
                    "pct":       round(avail / total * 100, 1) if total else 0})


@app.route("/admin/events")
def admin_events():
    limit = int(request.args.get("limit", 50))
    return jsonify({"events": list(reversed(_events[-limit:]))})


@app.route("/admin/requests")
def admin_requests():
    items = list(_pending.values())
    items.sort(key=lambda x: x["ts"], reverse=True)
    return jsonify({"requests": items, "mode": _mode["value"]})


@app.route("/admin/requests/<req_id>/approve", methods=["POST"])
def approve_request(req_id):
    req = _pending.get(req_id)
    if not req:
        return jsonify({"error": "not found"}), 404
    body    = request.get_json() or {}
    granted = int(body.get("amount", req["amount"]))
    req["status"]  = "approved"
    req["granted"] = granted
    email = req["email"]
    with _lock:
        _extra_tokens[email] = _extra_tokens.get(email, 0) + granted
        _pool["available"]   = max(0, _pool["available"] - granted)
    _log_event("approved", email, {"granted": granted, "request_id": req_id})
    return jsonify({"status": "approved", "granted": granted})


@app.route("/admin/requests/<req_id>/deny", methods=["POST"])
def deny_request(req_id):
    req = _pending.get(req_id)
    if not req:
        return jsonify({"error": "not found"}), 404
    req["status"] = "denied"
    _log_event("denied", req["email"], {"request_id": req_id})
    return jsonify({"status": "denied"})


@app.route("/admin/mode", methods=["GET", "POST"])
def admin_mode():
    if request.method == "POST":
        body = request.get_json() or {}
        _mode["value"] = body.get("mode", "auto")
        return jsonify({"mode": _mode["value"]})
    return jsonify({"mode": _mode["value"]})


@app.route("/admin/health")
def admin_health():
    return jsonify({"status": "ok", "key_set": bool(ANTHROPIC_API_KEY),
                    "users_seen": len(_counters), "mode": _mode["value"]})


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"[tokenflow] proxy listening on http://0.0.0.0:{PORT}")
    print(f"[tokenflow] api key : {'set ✓' if ANTHROPIC_API_KEY else 'MISSING ✗'}")
    print(f"[tokenflow] pool    : {_pool['available']:,} tokens")
    print(f"[tokenflow] mode    : {_mode['value']}")
    print(f"[tokenflow] users   : {len(CONFIG['users'])} configured\n")
    app.run(host="0.0.0.0", port=PORT, threaded=True)
