export const SCORECARD_DIMENSIONS = Object.freeze(["correctness", "tests", "code_quality", "security", "performance"]);
export const DEFAULT_SCORECARD_THRESHOLD = 85;
export const SCORECARD_SEVERITY_RANK = new Map([["critical", 0], ["high", 1], ["medium", 2], ["low", 3]]);

function assertPlainObject(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be a JSON object.`);
  }
}

function allowedKeys(value, allowed, label) {
  const extras = Object.keys(value).filter((key) => !allowed.includes(key));
  if (extras.length) {
    throw new Error(`${label} has unsupported fields: ${extras.join(", ")}.`);
  }
}

function normalizeEnum(value, allowed, label) {
  const normalized = String(value ?? "").trim().toLowerCase();
  if (!allowed.includes(normalized)) {
    throw new Error(`${label} must be one of: ${allowed.join(", ")}.`);
  }
  return normalized;
}

function boundedNumber(value, label, min = 0, max = 100) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < min || value > max) {
    throw new Error(`${label} must be a number from ${min} to ${max}.`);
  }
  return value;
}

function stringList(value) {
  return Array.isArray(value)
    ? value.filter((item) => typeof item === "string" && item.trim()).map((item) => item.trim())
    : [];
}

function normalizeDimension(value, name) {
  assertPlainObject(value, `score dimension ${name}`);
  allowedKeys(value, name === "tests"
    ? ["weight", "score", "evidence", "exempt", "exemption_reason"]
    : ["weight", "score", "evidence"], `score dimension ${name}`);
  const normalized = {
    weight: boundedNumber(value.weight, `${name}.weight`, 0, 1),
    score: boundedNumber(value.score, `${name}.score`),
    evidence: stringList(value.evidence)
  };
  if (name === "tests") {
    normalized.exempt = Boolean(value.exempt);
    normalized.exemption_reason = typeof value.exemption_reason === "string" ? value.exemption_reason.trim() : "";
    if (normalized.exempt && !normalized.exemption_reason) {
      throw new Error("tests exemption requires exemption_reason.");
    }
  }
  return normalized;
}

function normalizeFinding(value, index) {
  assertPlainObject(value, `scorecard finding ${index + 1}`);
  const required = ["severity", "blocking", "file", "line", "description", "evidence", "recommendation"];
  const missing = required.filter((key) => !Object.prototype.hasOwnProperty.call(value, key));
  if (missing.length) {
    throw new Error(`scorecard finding ${index + 1} is missing required fields: ${missing.join(", ")}.`);
  }
  allowedKeys(value, required, `scorecard finding ${index + 1}`);
  const severity = normalizeEnum(value.severity, [...SCORECARD_SEVERITY_RANK.keys()], "scorecard finding severity");
  if ((value.blocking && typeof value.evidence !== "string") || (value.blocking && !value.evidence.trim())) {
    throw new Error("blocking scorecard findings require evidence.");
  }
  const line = Number(value.line);
  if (!Number.isInteger(line) || line < 1) {
    throw new Error("scorecard finding line must be a positive integer.");
  }
  for (const key of ["file", "description", "evidence", "recommendation"]) {
    if (typeof value[key] !== "string") {
      throw new Error(`scorecard finding ${key} must be a string.`);
    }
  }
  return {
    severity,
    blocking: Boolean(value.blocking),
    file: value.file.trim(),
    line,
    description: value.description.trim(),
    evidence: value.evidence.trim(),
    recommendation: value.recommendation.trim()
  };
}

export function recomputeScoreTotal(dimensions) {
  const weightTotal = SCORECARD_DIMENSIONS.reduce((sum, name) => sum + dimensions[name].weight, 0);
  if (Math.abs(weightTotal - 1) > 0.001) {
    throw new Error(`scorecard weights must sum to 1, got ${weightTotal.toFixed(3)}.`);
  }
  return Math.round(SCORECARD_DIMENSIONS.reduce((sum, name) => sum + dimensions[name].weight * dimensions[name].score, 0));
}

export function normalizeScorecardOutput(value) {
  assertPlainObject(value, "scorecard output");
  const required = ["verdict", "score", "findings", "residual_risks", "next_steps"];
  const missing = required.filter((key) => !Object.prototype.hasOwnProperty.call(value, key));
  if (missing.length) {
    throw new Error(`scorecard output is missing required fields: ${missing.join(", ")}.`);
  }
  allowedKeys(value, required, "scorecard output");
  const verdict = normalizeEnum(value.verdict, ["approve", "needs-attention"], "scorecard verdict");
  assertPlainObject(value.score, "scorecard score");
  allowedKeys(value.score, ["total", "threshold", "dimensions"], "scorecard score");
  assertPlainObject(value.score.dimensions, "scorecard dimensions");
  const dimensions = Object.fromEntries(SCORECARD_DIMENSIONS.map((name) => [name, normalizeDimension(value.score.dimensions[name], name)]));
  const threshold = value.score.threshold === undefined ? DEFAULT_SCORECARD_THRESHOLD : boundedNumber(value.score.threshold, "score.threshold");
  const total = recomputeScoreTotal(dimensions);
  const findings = Array.isArray(value.findings) ? value.findings.map(normalizeFinding) : [];
  return {
    verdict,
    score: { total, threshold, dimensions },
    findings: findings.sort((left, right) => SCORECARD_SEVERITY_RANK.get(left.severity) - SCORECARD_SEVERITY_RANK.get(right.severity)),
    residual_risks: stringList(value.residual_risks),
    next_steps: stringList(value.next_steps)
  };
}
