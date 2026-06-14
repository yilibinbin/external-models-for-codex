const MINUTE_MS = 60 * 1000;
const MAX_SDK_SUBAGENT_TIMEOUT_MS = 60 * MINUTE_MS;

function positiveInteger(value) {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : 0;
}

export function sdkSubagentTimeoutMs(args = {}, env = process.env) {
  const configured = positiveInteger(env.CLAUDE_FOR_CODEX_SDK_TIMEOUT_MS);
  if (configured) {
    return Math.min(configured, MAX_SDK_SUBAGENT_TIMEOUT_MS);
  }
  const roleCount = Math.max(1, Array.isArray(args.reviewRoles) ? args.reviewRoles.length : 1);
  const resolvedQuality = String(args.qualityPolicy?.resolvedQuality || args.quality || "").trim().toLowerCase();
  const base = resolvedQuality === "max" ? 20 * MINUTE_MS : resolvedQuality === "strong" ? 12 * MINUTE_MS : 8 * MINUTE_MS;
  const perRole = resolvedQuality === "max" ? 5 * MINUTE_MS : resolvedQuality === "strong" ? 3 * MINUTE_MS : 2 * MINUTE_MS;
  return Math.min(MAX_SDK_SUBAGENT_TIMEOUT_MS, base + roleCount * perRole);
}

export function timeoutLeaseTtlFloorMs(timeoutMs, marginMs = MINUTE_MS) {
  const parsed = positiveInteger(timeoutMs);
  return parsed ? Math.min(24 * 60 * MINUTE_MS, parsed + marginMs) : 0;
}
