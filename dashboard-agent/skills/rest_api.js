// skill: rest-api
// Serves /admin/* endpoints with Bearer token auth.

const { createClient } = require("redis");

const ADMIN_TOKEN = process.env.ADMIN_DASHBOARD_TOKEN;

function authMiddleware(req, res, next) {
  const auth = req.headers["authorization"] || "";
  if (auth !== `Bearer ${ADMIN_TOKEN}`) {
    return res.status(401).json({ error: "unauthorized" });
  }
  next();
}

async function registerRoutes(app) {
  const redis = createClient({ url: process.env.REDIS_URL || "redis://localhost:6379" });
  await redis.connect();

  // GET /admin/pool — current pool balance
  app.get("/admin/pool", authMiddleware, async (req, res) => {
    const available = parseInt(await redis.get("pool:available") || "0");
    const total = parseInt(await redis.get("pool:total") || "0");
    const pct = total > 0 ? ((available / total) * 100).toFixed(1) : "0.0";
    res.json({ available_tokens: available, total_pool: total, pct: parseFloat(pct) });
  });

  // GET /admin/usage — per-user token counts + role
  app.get("/admin/usage", authMiddleware, async (req, res) => {
    const today = new Date().toISOString().slice(0, 10);
    const userKeys = await redis.keys(`user:*:tokens:${today}`);

    const users = await Promise.all(userKeys.map(async (key) => {
      const uid = key.split(":")[1];
      const role = await redis.get(`user:${uid}:role`);
      const [name, used, alloc, borrowed] = await Promise.all([
        redis.get(`user:${uid}:name`),
        redis.get(key),
        redis.hGet(`role:${role}`, "daily_tokens"),
        redis.get(`user:${uid}:borrowed:${today}`),
      ]);
      return {
        id: uid,
        name: name || uid,
        role: role || "unknown",
        used: parseInt(used || "0"),
        allocation: parseInt(alloc || "0"),
        borrowed: parseInt(borrowed || "0"),
      };
    }));

    res.json({ users });
  });

  // GET /admin/events?limit=20 — recent ledger events
  app.get("/admin/events", authMiddleware, async (req, res) => {
    const limit = parseInt(req.query.limit || "20");
    const raw = await redis.xRevRange("ledger:borrows", "+", "-", { COUNT: limit });
    const events = await Promise.all(raw.map(async (msg) => {
      let borrower = msg.message.borrower;
      // resolve uid (e.g. "u1") to display name
      if (/^u\d+$/.test(borrower)) {
        borrower = (await redis.get(`user:${borrower}:name`)) || borrower;
      }
      return {
        ts: msg.message.ts,
        type: "redirect",
        borrower,
        amount: parseInt(msg.message.amount),
      };
    }));
    res.json({ events });
  });

  // GET /admin/savings — month-to-date waste recovered
  app.get("/admin/savings", authMiddleware, async (req, res) => {
    const recovered = parseInt(await redis.get("savings:mtd_tokens") || "0");
    // $15 per 1M output tokens — rough blended rate
    const usd = ((recovered / 1_000_000) * 15).toFixed(2);
    res.json({ mtd_tokens_recovered: recovered, mtd_usd_saved: parseFloat(usd) });
  });

  // POST /admin/borrow — simulate a pool borrow event for a user
  // Body: { uid: string, amount: number }  (amount in tokens)
  app.post("/admin/borrow", authMiddleware, async (req, res) => {
    const { uid, amount } = req.body || {};
    if (!uid || !amount || amount <= 0) {
      return res.status(400).json({ error: "uid and positive amount required" });
    }
    const today = new Date().toISOString().slice(0, 10);
    const ts = new Date().toISOString();

    // Resolve display name for pub/sub event
    const name = (await redis.get(`user:${uid}:name`)) || uid;

    await Promise.all([
      redis.incrBy(`user:${uid}:tokens:${today}`, amount),
      redis.incrBy(`user:${uid}:borrowed:${today}`, amount),
      redis.decrBy("pool:available", amount),
      redis.incrBy("savings:mtd_tokens", amount),
      redis.xAdd("ledger:borrows", "*", { borrower: uid, amount: String(amount), ts }),
    ]);

    // Publish live event to dashboard WebSocket fans
    const pool = parseInt(await redis.get("pool:available") || "0");
    await Promise.all([
      redis.publish("pool:updated", String(pool)),
      redis.publish("event:redirect", JSON.stringify({ borrower: name, amount, ts })),
    ]);

    res.json({ ok: true, uid, amount, pool_remaining: pool });
  });

  // POST /admin/add-usage — add raw token usage to a user (no borrow)
  // Body: { uid: string, amount: number }
  app.post("/admin/add-usage", authMiddleware, async (req, res) => {
    const { uid, amount } = req.body || {};
    if (!uid || !amount || amount <= 0) {
      return res.status(400).json({ error: "uid and positive amount required" });
    }
    const today = new Date().toISOString().slice(0, 10);
    await redis.incrBy(`user:${uid}:tokens:${today}`, amount);
    const used = parseInt(await redis.get(`user:${uid}:tokens:${today}`) || "0");
    res.json({ ok: true, uid, used });
  });
}

module.exports = { registerRoutes };
