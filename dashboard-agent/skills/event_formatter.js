// skill: event-formatter
// Formats raw Redis ledger/event entries into human-readable log lines.

const ROLE_LABELS = {
  engineer: "Engineer",
  pm: "PM",
  designer: "Designer",
  ops: "Ops",
};

function formatRedirect({ ts, borrower, amount }) {
  const m = Math.round(amount / 1_000_000);
  const time = new Date(ts).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
  return `${time} — ${borrower} borrowed ${m}M tokens from pool`;
}

function formatPoolUpdate({ value }) {
  const b = (value / 1_000_000_000).toFixed(2);
  return `Pool updated: ${b}B tokens available`;
}

function formatConfigUpdate({ allocations }) {
  const lines = Object.entries(allocations)
    .map(([role, n]) => `${ROLE_LABELS[role] || role}: ${(n / 1000).toFixed(0)}K/day`)
    .join(", ");
  return `Allocations updated — ${lines}`;
}

function format(event) {
  switch (event.type) {
    case "event":   return formatRedirect(event);
    case "pool":    return formatPoolUpdate(event);
    case "config":  return formatConfigUpdate(event);
    default:        return JSON.stringify(event);
  }
}

module.exports = { format, formatRedirect, formatPoolUpdate, formatConfigUpdate };
