const http = require("http");
const express = require("express");
const cors = require("cors");
const { registerRoutes } = require("./skills/rest_api");
const { startWebSocketServer } = require("./skills/websocket_server");

const PORT = parseInt(process.env.DASHBOARD_PORT || "8080");

async function main() {
  const app = express();
  app.use(cors());
  app.use(express.json());

  await registerRoutes(app);

  const server = http.createServer(app);
  await startWebSocketServer(server);

  server.listen(PORT, () => {
    console.log(`[dashboard-agent] REST + WebSocket server on :${PORT}`);
  });
}

main().catch((err) => {
  console.error("[dashboard-agent] fatal:", err);
  process.exit(1);
});
