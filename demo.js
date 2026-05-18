#!/usr/bin/env node
/**
 * demo.js — TokenFlow end-to-end demo
 *
 * 1. Sends API calls for Engineer, PM, and HR to build visible usage
 * 2. Engineer hits their limit; submits a token request
 * 3. Tells you to open the dashboard and approve it there
 * 4. Polls until admin approves or denies
 *
 * Requirements:
 *   Terminal 1:  python3 mock_anthropic.py
 *   Terminal 2:  ANTHROPIC_URL=http://localhost:9090 TOKENFLOW_CONFIG=test_config.json python3 proxy.py
 *   Terminal 3:  node demo.js
 *
 * Dashboard:  http://localhost:8080/admin/dashboard
 */

const PROXY = "http://localhost:8080";
const MOCK  = "http://localhost:9090";

// ── Colours ────────────────────────────────────────────────────────────────────

const G   = "\x1b[92m";
const Y   = "\x1b[93m";
const R   = "\x1b[91m";
const DIM = "\x1b[2m";
const RST = "\x1b[0m";
const B   = "\x1b[1m";
const C   = "\x1b[96m";

// ── Helpers ────────────────────────────────────────────────────────────────────

function fmt(n) { return Number(n || 0).toLocaleString(); }
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function apiCall(email, label) {
  const resp = await fetch(`${PROXY}/v1/messages`, {
    method: "POST",
    headers: {
      "content-type":  "application/json",
      "x-user-email":  email,
      "authorization": "Bearer fake-key",
    },
    body: JSON.stringify({
      model:      "claude-3-5-sonnet-20241022",
      max_tokens: 100,
      messages:   [{ role: "user", content: "Say hello in one sentence." }],
    }),
  });

  const status = resp.status;
  const body   = await resp.json().catch(() => ({}));
  const used   = (body.usage?.input_tokens || 0) + (body.usage?.output_tokens || 0);
  const decision = resp.headers.get("x-tokenflow-decision") || (status === 429 ? "block" : "allow");

  const icon  = decision === "allow"  ? `${G}✓ ALLOW${RST}`
              : decision === "borrow" ? `${Y}~ BORROW${RST}`
              :                         `${R}✗ BLOCK${RST}`;

  console.log(`  ${icon}  ${DIM}${label}${RST}  ${DIM}(${fmt(used)} tokens)${RST}`);
  return { status, decision };
}

async function submitRequest(email, amount, reason) {
  const resp = await fetch(`${PROXY}/request-tokens`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email, amount, reason }),
  });
  const data = await resp.json();
  return data.request_id;
}

async function pollRequest(reqId) {
  let dots = 0;
  while (true) {
    await sleep(2000);
    const data   = await fetch(`${PROXY}/request-tokens/${reqId}`).then(r => r.json());
    const status = data.status;

    if (status === "approved") {
      process.stdout.write("\r" + " ".repeat(60) + "\r");
      console.log(`  ${G}${B}✓ APPROVED${RST}  ${fmt(data.granted)} tokens granted to engineer@test.com`);
      console.log(`  ${DIM}Allocation increased. Engineer can now make more requests.${RST}`);
      return "approved";
    }
    if (status === "denied") {
      process.stdout.write("\r" + " ".repeat(60) + "\r");
      console.log(`  ${R}${B}✗ DENIED${RST}  Request was rejected.`);
      return "denied";
    }

    dots = (dots + 1) % 4;
    process.stdout.write(`\r  ${DIM}Waiting for admin decision ${"·".repeat(dots + 1)}   ${RST}`);
  }
}

// ── Main ───────────────────────────────────────────────────────────────────────

async function main() {
  // Check proxy is up
  try {
    await fetch(`${PROXY}/admin/health`, { signal: AbortSignal.timeout(3000) });
  } catch {
    console.error(`\n  ${R}✗ Proxy not reachable at ${PROXY}${RST}`);
    console.error(`  Start it:  ANTHROPIC_URL=http://localhost:9090 TOKENFLOW_CONFIG=test_config.json python3 proxy.py\n`);
    process.exit(1);
  }

  // Check mock Anthropic is up — if it's down, API calls go through but return no token counts
  try {
    await fetch(`${MOCK}/health`, { signal: AbortSignal.timeout(3000) });
  } catch {
    console.error(`\n  ${R}✗ Mock Anthropic not reachable at ${MOCK}${RST}`);
    console.error(`  Start it first:  python3 mock_anthropic.py\n`);
    process.exit(1);
  }

  console.log(`\n${B}  TokenFlow Demo${RST}`);
  console.log(`  ${"─".repeat(50)}`);
  console.log(`  Dashboard → ${C}http://localhost:8080/admin/dashboard${RST}\n`);

  // ── Step 1: All 3 users work normally (parallel batches) ────────────────────
  console.log(`${B}  [1/4] Normal usage — Engineer (8 calls), PM (4 calls), HR (2 calls)${RST}`);
  console.log(`  ${DIM}~5k tokens per call (3k input + 2k output)${RST}\n`);

  // Run all users in parallel, logging as each resolves
  const engineerCalls = Array.from({length: 8}, (_, i) =>
    apiCall("engineer@test.com", `engineer@test.com  call ${i+1}/8  (~5k tokens)`).then(r => { return r; })
  );
  const pmCalls = Array.from({length: 4}, (_, i) =>
    apiCall("pm@test.com",       `pm@test.com        call ${i+1}/4  (~5k tokens)`).then(r => { return r; })
  );
  const hrCalls = Array.from({length: 2}, (_, i) =>
    apiCall("hr@test.com",       `hr@test.com        call ${i+1}/2  (~5k tokens)`).then(r => { return r; })
  );

  await Promise.all([...engineerCalls, ...pmCalls, ...hrCalls]);

  // ── Step 2: Engineer hits limit on next call ─────────────────────────────────
  console.log(`\n${B}  [2/4] Engineer hits quota — borrows from shared pool${RST}`);
  await apiCall("engineer@test.com", "engineer@test.com  call 9      (over 50k limit)");

  // ── Step 3: Engineer submits a token request ─────────────────────────────────
  console.log(`\n${B}  [3/4] Engineer submits a token request${RST}`);
  console.log(`  ${"─".repeat(50)}`);
  console.log(`\n  ${Y}Submitting token request — waiting for admin to approve on dashboard…${RST}`);

  const reqId = await submitRequest(
    "engineer@test.com",
    15000,
    "Sprint deadline — need extra capacity for large PR review and test generation"
  );

  console.log(`  ${G}✓ Request submitted${RST}  (${reqId})`);
  console.log(`\n  ${B}Open the dashboard and approve the pending card:${RST}`);
  console.log(`  ${C}http://localhost:8080/admin/dashboard${RST}\n`);

  const outcome = await pollRequest(reqId);

  // ── Step 4: Engineer uses the granted tokens ─────────────────────────────────
  if (outcome === "approved") {
    console.log(`\n${B}  [3/3] Engineer now unblocked — using granted tokens${RST}`);
    for (let i = 1; i <= 3; i++) {
      await apiCall("engineer@test.com", `engineer@test.com  post-grant call ${i}/3  (~5k tokens)`);
      await sleep(300);
    }
  }

  // ── Final usage snapshot ─────────────────────────────────────────────────────
  console.log();
  const usage = await fetch(`${PROXY}/admin/usage`).then(r => r.json());
  const pool  = await fetch(`${PROXY}/admin/pool`).then(r => r.json());

  console.log(`  ${"─".repeat(50)}`);
  console.log(`  ${B}Final state${RST}`);
  console.log();
  for (const u of usage.users) {
    const bar   = "█".repeat(Math.round(u.pct / 5)).padEnd(20, "░");
    const extra = u.extra > 0 ? `  ${G}+${fmt(u.extra)} granted${RST}` : "";
    console.log(`  ${(u.name || u.email).padEnd(10)} ${bar} ${String(u.pct).padStart(5)}%  ${DIM}${fmt(u.used)} / ${fmt(u.allocation)}${RST}${extra}`);
  }
  console.log();
  console.log(`  Pool remaining: ${G}${fmt(pool.available)}${RST} / ${fmt(pool.total)}  (${pool.pct}%)`);
  console.log();
}

main().catch(err => {
  console.error(`\n  ${R}Error: ${err.message}${RST}\n`);
  process.exit(1);
});
