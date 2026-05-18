#!/usr/bin/env node
/**
 * seed-redis.js — Populate Redis with test users, keys, roles, allocations,
 *                 and an initial pool so the full pipeline can be validated locally.
 *
 * Usage:
 *   REDIS_URL=redis://localhost:6379 node seed-redis.js
 *   node seed-redis.js          # defaults to redis://localhost:6379
 */

const { createClient } = require("redis");

const REDIS_URL = process.env.REDIS_URL || "redis://localhost:6379";

const ROLES = {
  engineer: 500_000_000,
  pm:        50_000_000,
  designer:  50_000_000,
  ops:        5_000_000,
};

const USERS = [
  { id: "u1", name: "Roie A.",  role: "engineer", vtk: "vtk_roie-0001" },
  { id: "u2", name: "Dan M.",   role: "engineer", vtk: "vtk_danm-0002" },
  { id: "u3", name: "Noa K.",   role: "engineer", vtk: "vtk_noak-0003" },
  { id: "u4", name: "Maya R.",  role: "pm",        vtk: "vtk_maya-0004" },
  { id: "u5", name: "Yael S.",  role: "designer",  vtk: "vtk_yael-0005" },
  { id: "u6", name: "Tom L.",   role: "pm",        vtk: "vtk_toml-0006" },
  { id: "u7", name: "Amir B.",  role: "ops",       vtk: "vtk_amir-0007" },
  { id: "u8", name: "Shira N.", role: "ops",       vtk: "vtk_shir-0008" },
];

// Simulated token usage for today (fraction of daily allocation)
const USAGE_FRACTIONS = {
  u1: 0.98, u2: 0.91, u3: 0.87,  // engineers near limit
  u4: 0.28, u5: 0.19, u6: 0.12,  // PMs/designers low usage
  u7: 0.05, u8: 0.03,             // ops barely used
};

async function seed() {
  const redis = createClient({ url: REDIS_URL });
  redis.on("error", (e) => console.error("[redis]", e));
  await redis.connect();
  console.log(`[seed] connected to ${REDIS_URL}`);

  const today = new Date().toISOString().slice(0, 10);
  const pipe = redis.multi();

  // 1. Role allocations
  for (const [role, tokens] of Object.entries(ROLES)) {
    pipe.hSet(`role:${role}`, "daily_tokens", tokens);
  }

  // 2. Users — profile + vtk key mapping
  for (const u of USERS) {
    const allocation = ROLES[u.role];
    const used = Math.floor(allocation * (USAGE_FRACTIONS[u.id] || 0));

    pipe.set(`user:${u.id}:name`, u.name,    { EX: 86400 * 2 });
    pipe.set(`user:${u.id}:role`, u.role,    { EX: 86400 * 2 });
    pipe.set(`key:${u.vtk}`,      u.id,      { EX: 86400 * 30 });
    pipe.hSet("registry:key_to_user", u.vtk, u.id);
    pipe.set(`user:${u.id}:tokens:${today}`, used, { EX: 86400 * 2 });

    console.log(`  ${u.name.padEnd(10)} ${u.role.padEnd(10)} ${(used/1000).toFixed(0).padStart(5)}K / ${(allocation/1000).toFixed(0)}K`);
  }

  // 3. Pool — sum of PM/designer/ops surplus
  let pool = 0;
  for (const u of USERS) {
    if (u.role === "engineer") continue;
    const allocation = ROLES[u.role];
    const used = Math.floor(allocation * (USAGE_FRACTIONS[u.id] || 0));
    pool += Math.max(0, allocation - used);
  }
  const totalPool = Object.entries(ROLES)
    .filter(([r]) => r !== "engineer")
    .reduce((s, [r, n]) => {
      const count = USERS.filter(u => u.role === r).length;
      return s + n * count;
    }, 0);

  pipe.set("pool:available", pool);
  pipe.set("pool:total",     totalPool);

  // 4. Seed a few ledger borrow events
  const borrowEvents = [
    { borrower: "u1", amount: 120_000, ts: new Date(Date.now() - 40*60*1000).toISOString() },
    { borrower: "u2", amount:  80_000, ts: new Date(Date.now() - 70*60*1000).toISOString() },
    { borrower: "u3", amount:  45_000, ts: new Date(Date.now() - 95*60*1000).toISOString() },
  ];
  for (const e of borrowEvents) {
    pipe.xAdd("ledger:borrows", "*", { borrower: e.borrower, amount: String(e.amount), ts: e.ts });
  }

  // 5. MTD savings (simulated)
  pipe.set("savings:mtd_tokens", 28_000_000_000); // 28B tokens recovered

  await pipe.exec();

  console.log(`\n[seed] pool: ${(pool/1000).toFixed(0)}K available / ${(totalPool/1000).toFixed(0)}K total`);
  console.log("[seed] vtk keys:");
  USERS.forEach(u => console.log(`  ${u.vtk}  →  ${u.name}`));
  console.log("\n[seed] done. Start dashboard-agent and open tokenflow_demo.html");

  await redis.quit();
}

seed().catch((e) => { console.error(e); process.exit(1); });
