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

function classifyTextFailure(text = "") {
  const value = String(text ?? "");
  if (UNKNOWN_DENY_PATTERN.test(value)) {
    return "unknown_deny_tool";
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

  const textKind = classifyTextFailure(`${result.stderr ?? ""}\n${result.stdout ?? ""}\n${result.error ?? ""}`);
  if (textKind) {
    return {
      kind: textKind,
      ok: false,
      servedByFallback: hasFallbackServed(events),
      refusalCategory: "",
      stopReason: events.find((event) => stopReasonForEvent(event)) ? stopReasonForEvent(events.find((event) => stopReasonForEvent(event))) : ""
    };
  }

  if (statusCode(result) !== 0) {
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
