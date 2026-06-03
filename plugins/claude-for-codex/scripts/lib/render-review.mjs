const REVIEW_VERDICTS = new Set(["approve", "needs-attention"]);
const ADVERSARIAL_VERDICTS = new Set(["PASS", "CONTESTED", "REJECT"]);
const SEVERITY_RANK = new Map([
  ["critical", 0],
  ["high", 1],
  ["medium", 2],
  ["low", 3]
]);

function assertObject(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be a JSON object.`);
  }
}

function normalizeSeverity(value) {
  if (!SEVERITY_RANK.has(value)) {
    throw new Error("Structured review finding severity must be critical, high, medium, or low.");
  }
  return value;
}

function normalizeLine(value, label) {
  if (!Number.isInteger(value) || value < 1) {
    throw new Error(`Structured review finding ${label} must be a positive integer.`);
  }
  return value;
}

function normalizeConfidence(value) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 1) {
    throw new Error("Structured review finding confidence must be a number from 0 to 1.");
  }
  return value;
}

function normalizeFinding(finding, index, options = {}) {
  const source = finding && typeof finding === "object" && !Array.isArray(finding) ? finding : {};
  const required = ["severity", "title", "body", "file", "line_start", "line_end", "confidence", "recommendation"];
  const missing = required.filter((key) => !Object.prototype.hasOwnProperty.call(source, key));
  if (missing.length) {
    throw new Error(`Structured review finding ${index + 1} is missing required fields: ${missing.join(", ")}.`);
  }
  const allowed = new Set(required);
  const extra = Object.keys(source).filter((key) => !allowed.has(key));
  if (extra.length) {
    throw new Error(`Structured review finding ${index + 1} has unsupported fields: ${extra.join(", ")}.`);
  }
  const lineStart = normalizeLine(source.line_start, "line_start");
  const lineEnd = normalizeLine(source.line_end, "line_end");
  if (lineEnd < lineStart) {
    throw new Error("Structured review finding line_end must be greater than or equal to line_start.");
  }
  for (const key of ["title", "body", "file"]) {
    if (typeof source[key] !== "string" || !source[key].trim()) {
      throw new Error(`Structured review finding ${key} must be a non-empty string.`);
    }
  }
  if (typeof source.recommendation !== "string") {
    throw new Error("Structured review finding recommendation must be a string.");
  }
  return {
    severity: normalizeSeverity(source.severity),
    title: source.title.trim(),
    body: source.body.trim(),
    file: source.file.trim(),
    line_start: lineStart,
    line_end: lineEnd,
    confidence: normalizeConfidence(source.confidence),
    recommendation: source.recommendation.trim(),
    ...(options.role ? { role: options.role } : {}),
    ...(options.lens ? { lens: options.lens } : {})
  };
}

function normalizeNextSteps(value) {
  return Array.isArray(value)
    ? value.filter((step) => typeof step === "string" && step.trim()).map((step) => step.trim())
    : [];
}

function sortFindings(findings) {
  return [...findings].sort((left, right) =>
    (SEVERITY_RANK.get(left.severity) ?? 99) - (SEVERITY_RANK.get(right.severity) ?? 99)
  );
}

export function normalizeReviewOutput(value, options = {}) {
  assertObject(value, "Structured review output");
  const required = ["verdict", "summary", "findings", "next_steps"];
  const missing = required.filter((key) => !Object.prototype.hasOwnProperty.call(value, key));
  if (missing.length) {
    throw new Error(`Structured review output is missing required fields: ${missing.join(", ")}.`);
  }
  const allowed = new Set(required);
  const extra = Object.keys(value).filter((key) => !allowed.has(key));
  if (extra.length) {
    throw new Error(`Structured review output has unsupported fields: ${extra.join(", ")}.`);
  }
  if (!REVIEW_VERDICTS.has(value.verdict)) {
    throw new Error("Structured review output verdict must be approve or needs-attention.");
  }
  if (typeof value.summary !== "string" || !value.summary.trim()) {
    throw new Error("Structured review output summary must be a non-empty string.");
  }
  if (!Array.isArray(value.findings)) {
    throw new Error("Structured review output findings must be an array.");
  }
  return {
    verdict: value.verdict,
    summary: value.summary.trim(),
    findings: sortFindings(value.findings.map((finding, index) => normalizeFinding(finding, index, options))),
    next_steps: normalizeNextSteps(value.next_steps)
  };
}

export function normalizeAdversarialOutput(value) {
  assertObject(value, "Structured adversarial output");
  if (!ADVERSARIAL_VERDICTS.has(value.verdict)) {
    throw new Error("Structured adversarial output verdict must be PASS, CONTESTED, or REJECT.");
  }
  if (typeof value.summary !== "string") {
    throw new Error("Structured adversarial output summary must be a string.");
  }
  if (!Array.isArray(value.findings)) {
    throw new Error("Structured adversarial output findings must be an array.");
  }
  if (!Array.isArray(value.next_steps)) {
    throw new Error("Structured adversarial output next_steps must be an array.");
  }
  return value;
}

export function aggregateRoleReviewOutputs(roleResults) {
  const roles = roleResults.map(({ role, result }) => ({
    role: role.name,
    result
  }));
  const findings = sortFindings(roleResults.flatMap(({ role, result }) =>
    result.findings.map((finding) => ({
      ...finding,
      role: finding.role || role.name
    }))
  ));
  const needsAttention = roleResults.some(({ result }) => result.verdict === "needs-attention");
  return {
    verdict: needsAttention ? "needs-attention" : "approve",
    summary: roleResults.map(({ role, result }) => `${role.name}: ${result.summary}`).join(" | "),
    findings,
    next_steps: roleResults.flatMap(({ role, result }) =>
      result.next_steps.map((step) => `${role.name}: ${step}`)
    ),
    roles
  };
}
