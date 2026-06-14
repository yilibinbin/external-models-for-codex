const UNKNOWN_DENY_PATTERN = /^\s*Permission deny rule "([^"\r\n]{1,128})" matches no known tool(?:\.|\s+[\u2014-]\s+check for typos\.)?\s*$/m;

export function sdkEventsFromMetadata(metadata = {}) {
  if (Array.isArray(metadata?.sdkEvents)) {
    return metadata.sdkEvents;
  }
  if (Array.isArray(metadata?.events)) {
    return metadata.events;
  }
  return [];
}

function stopReasonForEvent(event) {
  if (typeof event?.stop_reason === "string") {
    return event.stop_reason;
  }
  if (typeof event?.stopReason === "string") {
    return event.stopReason;
  }
  return "";
}

function stopDetailsForEvent(event) {
  return event?.stop_details && typeof event.stop_details === "object"
    ? event.stop_details
    : event?.stopDetails && typeof event.stopDetails === "object"
      ? event.stopDetails
      : {};
}

function firstRefusal(events) {
  return events.find((event) => event && typeof event === "object" && stopReasonForEvent(event) === "refusal") ?? null;
}

function hasFallbackServed(events) {
  return events.some((event) => {
    const iterations = event?.usage?.iterations;
    return Array.isArray(iterations) && iterations.some((entry) => entry?.type === "fallback_message");
  });
}

function classifyTextFailure(text = "", options = {}) {
  const value = String(text ?? "");
  if (UNKNOWN_DENY_PATTERN.test(value)) {
    return "unknown_deny_tool";
  }
  if (options.unknownOnly) {
    return "";
  }
  if (/not logged in|please run\s+\/login|authentication required|api key/i.test(value)) {
    return "auth";
  }
  if (/session limit|rate limit|429|too many requests|quota exceeded/i.test(value)) {
    return "rate_limit";
  }
  if (/timed out|ETIMEDOUT|timeout/i.test(value)) {
    return "timeout";
  }
  return "";
}

function statusCode(result = {}) {
  return Number.isFinite(Number(result.status)) ? Number(result.status) : 1;
}

export function classifyClaudeOutcome(result = {}) {
  const events = sdkEventsFromMetadata(result.metadata);
  const refusal = firstRefusal(events);
  if (refusal) {
    const details = stopDetailsForEvent(refusal);
    return {
      kind: "refusal",
      ok: false,
      servedByFallback: hasFallbackServed(events),
      refusalCategory: typeof details.category === "string" ? details.category : "",
      stopReason: "refusal"
    };
  }

  const status = statusCode(result);
  const allText = `${result.stderr ?? ""}\n${result.stdout ?? ""}\n${result.error ?? ""}`;
  const unknownDenyKind = classifyTextFailure(allText, { unknownOnly: true });
  if (unknownDenyKind) {
    return {
      kind: unknownDenyKind,
      ok: false,
      servedByFallback: hasFallbackServed(events),
      refusalCategory: "",
      stopReason: events.find((event) => stopReasonForEvent(event)) ? stopReasonForEvent(events.find((event) => stopReasonForEvent(event))) : ""
    };
  }

  const failureText = status === 0
    ? `${result.stderr ?? ""}\n${result.error ?? ""}`
    : allText;
  const textKind = classifyTextFailure(failureText);
  if (textKind) {
    return {
      kind: textKind,
      ok: false,
      servedByFallback: hasFallbackServed(events),
      refusalCategory: "",
      stopReason: events.find((event) => stopReasonForEvent(event)) ? stopReasonForEvent(events.find((event) => stopReasonForEvent(event))) : ""
    };
  }

  if (status !== 0) {
    return {
      kind: "provider_error",
      ok: false,
      servedByFallback: hasFallbackServed(events),
      refusalCategory: "",
      stopReason: events.find((event) => stopReasonForEvent(event)) ? stopReasonForEvent(events.find((event) => stopReasonForEvent(event))) : ""
    };
  }

  return {
    kind: "success",
    ok: true,
    servedByFallback: hasFallbackServed(events),
    refusalCategory: "",
    stopReason: events.find((event) => stopReasonForEvent(event)) ? stopReasonForEvent(events.find((event) => stopReasonForEvent(event))) : ""
  };
}

export const FAILURE_CATEGORIES = Object.freeze([
  "timeout",
  "rate_limit",
  "auth",
  "quota",
  "network",
  "context_overflow",
  "permission_compat",
  "empty_output",
  "malformed_json",
  "model_unavailable",
  "capacity_blocked",
  "unknown"
]);

export function classifyFailureCategory(result = {}) {
  const { status, stdout = "", stderr = "", error = "", metadata = {} } = result;
  if (result.status === "capacity_blocked" || result.capacityStatus === "capacity_blocked" || metadata.capacity?.status === "capacity_blocked") {
    return "capacity_blocked";
  }
  if (metadata.structuredError === "empty_output") {
    return "empty_output";
  }
  if (metadata.structuredError === "malformed_json") {
    return "malformed_json";
  }
  const classified = metadata.outcome && typeof metadata.outcome === "object"
    ? metadata.outcome
    : classifyClaudeOutcome({ status, stdout, stderr, error, metadata });
  if (classified.kind === "unknown_deny_tool") {
    return "permission_compat";
  }
  if (FAILURE_CATEGORIES.includes(classified.kind) && classified.kind !== "unknown") {
    return classified.kind;
  }
  const text = `${status === 0 ? "" : stdout}\n${stderr}\n${error}`.toLowerCase();
  if (text.includes("context length") || text.includes("context window") || text.includes("maximum context") || text.includes("token limit")) {
    return "context_overflow";
  }
  if (text.includes("billing") || text.includes("credit") || text.includes("quota exceeded")) {
    return "quota";
  }
  if (text.includes("network") || text.includes("econnreset") || text.includes("etimedout") || text.includes("enotfound")) {
    return "network";
  }
  if (text.includes("permission deny rule") && text.includes("matches no known tool")) {
    return "permission_compat";
  }
  if (text.includes("model") && (text.includes("unavailable") || text.includes("not found") || text.includes("does not exist"))) {
    return "model_unavailable";
  }
  return "unknown";
}
