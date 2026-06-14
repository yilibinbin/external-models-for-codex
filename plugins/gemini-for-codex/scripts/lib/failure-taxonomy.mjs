export const FAILURE_CATEGORIES = Object.freeze([
  "capacity_blocked",
  "timeout",
  "auth",
  "quota",
  "rate_limit",
  "network",
  "context_overflow",
  "empty_output",
  "malformed_json",
  "model_unavailable",
  "invalid_stream",
  "provider_compatibility",
  "invalid_round",
  "clamped_findings",
  "validation_error",
  "unsafe_input",
  "unknown"
]);

const FAILURE_CATEGORY_SET = new Set(FAILURE_CATEGORIES);

function textOf(...parts) {
  return parts.filter(Boolean).map((part) => String(part)).join("\n");
}

export function normalizeFailureCategory(value, fallback = "unknown") {
  const normalized = String(value ?? "").trim().toLowerCase().replaceAll("-", "_");
  if (FAILURE_CATEGORY_SET.has(normalized)) {
    return normalized;
  }
  return FAILURE_CATEGORY_SET.has(fallback) ? fallback : "unknown";
}

export function classifyProviderFailure(result = {}, options = {}) {
  const status = Number.isInteger(result.status) ? result.status : 1;
  const stdout = String(result.stdout || "").trim();
  const errorCode = String(result.errorCode || result.error?.code || "");
  const text = textOf(result.stderr, result.error?.message || result.error, options.diagnostic, options.message);
  if (String(options.category || "")) {
    return normalizeFailureCategory(options.category);
  }
  if (status === 0 && stdout && !errorCode && !text) {
    return "";
  }
  if (/capacity_blocked|maximum active|resource lease|global.*capacity/i.test(text)) {
    return "capacity_blocked";
  }
  if (errorCode === "ETIMEDOUT" || /timed?\s*out|timeout|ETIMEDOUT/i.test(text)) {
    return "timeout";
  }
  if (/not logged in|not logged into|unauthenticated|authentication failed|login required|invalid api key/i.test(text)) {
    return "auth";
  }
  if (/rate[_ -]?limit|too many requests|\b429\b/i.test(text)) {
    return "rate_limit";
  }
  if (/quota|RESOURCE_EXHAUSTED/i.test(text)) {
    return "quota";
  }
  if (/ENOTFOUND|ECONNRESET|ECONNREFUSED|network|fetch failed|socket hang up/i.test(text)) {
    return "network";
  }
  if (/context.*(overflow|length|window)|too many tokens|token limit|maximum context/i.test(text)) {
    return "context_overflow";
  }
  if (/Invalid stream|invalid_stream|malformed tool call|empty response/i.test(text)) {
    return "invalid_stream";
  }
  if (/malformed|invalid json|did not contain a JSON object|JSON output|parse/i.test(text)) {
    return "malformed_json";
  }
  if (/model.*(not available|unavailable|not found)|unknown model|unsupported model/i.test(text)) {
    return "model_unavailable";
  }
  if (/permission|unsupported flag|provider compatibility|not supported/i.test(text)) {
    return "provider_compatibility";
  }
  if (status === 0 && !stdout) {
    return "empty_output";
  }
  return "unknown";
}

export function failureCategoryReport() {
  return {
    categories: [...FAILURE_CATEGORIES],
    count: FAILURE_CATEGORIES.length
  };
}
