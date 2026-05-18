// skill: websocket-server
// Fans out Redis pub/sub events (pool:updated, event:redirect) to browser WebSocket clients.

const { createClient } = require("redis");
const { WebSocketServer } = require("ws");

async function startWebSocketServer(httpServer) {
  const sub = createClient({ url: process.env.REDIS_URL || "redis://localhost:6379" });
  await sub.connect();

  const wss = new WebSocketServer({ server: httpServer, path: "/admin/ws" });

  function broadcast(payload) {
    const msg = JSON.stringify(payload);
    wss.clients.forEach((c) => {
      if (c.readyState === 1) c.send(msg);
    });
  }

  await sub.subscribe("pool:updated", (msg) => {
    broadcast({ type: "pool", value: Number(msg) });
  });

  await sub.subscribe("event:redirect", (msg) => {
    try {
      broadcast({ type: "event", ...JSON.parse(msg) });
    } catch (_) {}
  });

  await sub.subscribe("config:updated", (msg) => {
    try {
      broadcast({ type: "config", allocations: JSON.parse(msg) });
    } catch (_) {}
  });

  wss.on("connection", (ws) => {
    ws.send(JSON.stringify({ type: "connected", ts: new Date().toISOString() }));
  });

  console.log("[websocket-server] listening on /admin/ws");
  return wss;
}

module.exports = { startWebSocketServer };
