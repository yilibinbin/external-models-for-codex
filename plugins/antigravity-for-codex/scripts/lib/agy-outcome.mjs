function compactText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function combined(result = {}, options = {}) {
  return compactText([
    result.stderr,
    result.error,
    options.logDiagnostic
  ].filter(Boolean).join("\n"));
}

export function classifyAgyOutcome(result = {}, options = {}) {
  const text = combined(result, options);
  const stdout = String(result.stdout || "").trim();
  const status = Number.isInteger(result.status) ? result.status : 1;
  const errorCode = String(result.errorCode || "");
  if (errorCode === "ETIMEDOUT") {
    return { kind: "timeout", ok: false, retryable: true, message: text || "Antigravity timed out." };
  }
  if (status === 0 && stdout && !errorCode) {
    return { kind: "success", ok: true, retryable: false, message: "" };
  }
  if (status === 0 && stdout && errorCode) {
    return { kind: "provider-error", ok: false, retryable: false, message: text || `Antigravity reported ${errorCode}.` };
  }
  if (/timed out|ETIMEDOUT/i.test(text)) {
    return { kind: "timeout", ok: false, retryable: true, message: text || "Antigravity timed out." };
  }
  if (/RESOURCE_EXHAUSTED|quota|rate limit|429/i.test(text)) {
    return { kind: "quota", ok: false, retryable: true, message: text };
  }
  if (/not logged into Antigravity|UNAUTHENTICATED|authentication failed|login required/i.test(text)) {
    return { kind: "auth", ok: false, retryable: false, message: text };
  }
  if (/PERMISSION_DENIED|permission denied/i.test(text)) {
    return { kind: "permission", ok: false, retryable: false, message: text };
  }
  if (/malformed (tool call|tool_call|output|response)|MalformedResponse/i.test(text)) {
    return { kind: "malformed-output", ok: false, retryable: true, message: text };
  }
  if (/Invalid stream|empty response/i.test(text)) {
    return { kind: "invalid-stream", ok: false, retryable: true, message: text };
  }
  if (status === 0 && !stdout) {
    return { kind: "empty-output", ok: false, retryable: true, message: text || "Antigravity CLI returned empty output." };
  }
  return { kind: "provider-error", ok: false, retryable: false, message: text || `Antigravity exited with status ${status}.` };
}

export function outcomeStderr(result = {}, options = {}) {
  const outcome = classifyAgyOutcome(result, options);
  if (outcome.ok) return "";
  const suffix = outcome.message ? ` ${outcome.message}` : "";
  return `Antigravity outcome ${outcome.kind}.${suffix}`.trim();
}
