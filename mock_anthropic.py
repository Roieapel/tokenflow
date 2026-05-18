#!/usr/bin/env python3
"""
mock_anthropic.py — Fake Anthropic API server for local testing.

Simulates both:
  - POST /v1/messages          → the real API that proxy.py forwards to
  - GET  /v1/organizations/... → the Admin API that test_admin_key.py calls

Every /v1/messages call returns 6,000 fake tokens (1k input + 5k output).
With test_config.json limits (15k engineer, 8k pool) you hit all three
enforce outcomes in 4 requests.

Usage:
    python mock_anthropic.py
    # runs on http://localhost:9090
"""

import json
import time
import random
import string
from datetime import date, timedelta

from flask import Flask, Response, jsonify, request

app = Flask(__name__)

FAKE_TOKENS_INPUT  = 3_000   # realistic prompt size
FAKE_TOKENS_OUTPUT = 2_000   # realistic response size (5k total per call)

# ── /v1/messages ─────────────────────────────────────────────────────────────

def _fake_msg_id() -> str:
    return "msg_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))


def _sse_response(msg_id: str) -> str:
    """Build a realistic Anthropic SSE stream as a single string."""
    events = [
        # message_start — carries input token count
        f'event: message_start\ndata: {json.dumps({"type":"message_start","message":{"id":msg_id,"type":"message","role":"assistant","content":[],"model":"claude-3-5-sonnet-20241022","stop_reason":None,"stop_sequence":None,"usage":{"input_tokens":FAKE_TOKENS_INPUT,"output_tokens":0}}})}\n\n',

        # content_block_start
        'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',

        # ping
        'event: ping\ndata: {"type":"ping"}\n\n',

        # content
        'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"[mock] This is a fake Anthropic response used for local testing."}}\n\n',

        # content_block_stop
        'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',

        # message_delta — carries output token count at TOP LEVEL (where proxy reads it)
        f'event: message_delta\ndata: {json.dumps({"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":None},"usage":{"input_tokens":FAKE_TOKENS_INPUT,"output_tokens":FAKE_TOKENS_OUTPUT}})}\n\n',

        # message_stop
        'event: message_stop\ndata: {"type":"message_stop"}\n\n',
    ]
    return "".join(events)


@app.route("/v1/messages", methods=["POST"])
def messages():
    body = request.get_json(silent=True) or {}
    is_stream = body.get("stream", False)
    msg_id = _fake_msg_id()

    if is_stream:
        def generate():
            for chunk in _sse_response(msg_id).split("\n\n"):
                if chunk.strip():
                    yield chunk + "\n\n"
                    time.sleep(0.02)   # simulate streaming delay

        return Response(generate(), content_type="text/event-stream")

    # Non-streaming — simpler JSON response (proxy reads usage from here too)
    return jsonify({
        "id":           msg_id,
        "type":         "message",
        "role":         "assistant",
        "content":      [{"type": "text",
                          "text": "[mock] This is a fake Anthropic response used for local testing."}],
        "model":        "claude-3-5-sonnet-20241022",
        "stop_reason":  "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens":  FAKE_TOKENS_INPUT,
            "output_tokens": FAKE_TOKENS_OUTPUT,
        },
    })


# ── Admin API endpoints (for test_admin_key.py) ───────────────────────────────

@app.route("/v1/organizations/<org_id>/usage")
def org_usage(org_id: str):
    yesterday = str(date.today() - timedelta(days=1))
    return jsonify({
        "data": [
            {
                "api_key_id":     "apikey_mock_engineers",
                "input_tokens":   45_000_000,
                "output_tokens":  12_000_000,
                "total_cost_usd": 171.00,
                "period_start":   f"{yesterday}T00:00:00Z",
                "period_end":     f"{yesterday}T23:59:59Z",
            },
            {
                "api_key_id":     "apikey_mock_pms",
                "input_tokens":   4_200_000,
                "output_tokens":  1_100_000,
                "total_cost_usd": 15.90,
                "period_start":   f"{yesterday}T00:00:00Z",
                "period_end":     f"{yesterday}T23:59:59Z",
            },
        ]
    })


@app.route("/v1/organizations/<org_id>/api_keys")
def org_api_keys(org_id: str):
    return jsonify({
        "data": [
            {"id": "apikey_mock_engineers", "name": "Engineers shared key", "status": "active"},
            {"id": "apikey_mock_pms",       "name": "PM + design key",     "status": "active"},
        ]
    })


# ── Health ────────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "ok", "mock": True,
                    "tokens_per_call": FAKE_TOKENS_INPUT + FAKE_TOKENS_OUTPUT})


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    total = FAKE_TOKENS_INPUT + FAKE_TOKENS_OUTPUT
    print(f"\n[mock-anthropic] listening on http://localhost:9090")
    print(f"[mock-anthropic] returns {total:,} tokens per /v1/messages call")
    print(f"[mock-anthropic] also serves fake Admin API at /v1/organizations/...\n")
    app.run(host="0.0.0.0", port=9090)
