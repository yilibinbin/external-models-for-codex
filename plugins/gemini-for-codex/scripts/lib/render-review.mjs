const REVIEW_VERDICTS = new Set(["approve", "needs-attention"]);
const REVIEW_SEVERITIES = new Set(["critical", "high", "medium", "low"]);

function ensureString(value, name) {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`Structured review ${name} must be a non-empty string.`);
  }
}

function ensureLine(value, name) {
  if (!Number.isInteger(value) || value < 1) {
    throw new Error(`Structured review ${name} must be an integer >= 1.`);
  }
}

export function validateStructuredReview(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Structured review output must be a JSON object.");
  }
  if (!REVIEW_VERDICTS.has(value.verdict)) {
    throw new Error("Structured review verdict must be approve or needs-attention.");
  }
  ensureString(value.summary, "summary");
  if (!Array.isArray(value.findings)) {
    throw new Error("Structured review findings must be an array.");
  }
  if (!Array.isArray(value.next_steps)) {
    throw new Error("Structured review next_steps must be an array.");
  }
  value.next_steps.forEach((step, index) => ensureString(step, `next_steps[${index}]`));
  value.findings.forEach((finding, index) => {
    if (!finding || typeof finding !== "object" || Array.isArray(finding)) {
      throw new Error(`Structured review findings[${index}] must be an object.`);
    }
    if (!REVIEW_SEVERITIES.has(finding.severity)) {
      throw new Error(`Structured review findings[${index}].severity is invalid.`);
    }
    ensureString(finding.title, `findings[${index}].title`);
    ensureString(finding.body, `findings[${index}].body`);
    ensureString(finding.file, `findings[${index}].file`);
    ensureLine(finding.line_start, `findings[${index}].line_start`);
    ensureLine(finding.line_end, `findings[${index}].line_end`);
    if (finding.line_end < finding.line_start) {
      throw new Error(`Structured review findings[${index}].line_end must be >= line_start.`);
    }
    if (typeof finding.confidence !== "number" || finding.confidence < 0 || finding.confidence > 1) {
      throw new Error(`Structured review findings[${index}].confidence must be between 0 and 1.`);
    }
    if (typeof finding.recommendation !== "string") {
      throw new Error(`Structured review findings[${index}].recommendation must be a string.`);
    }
  });
  return value;
}

export function renderStructuredReview(value) {
  const findings = value.findings.length
    ? value.findings.map((finding) => [
        `- [${finding.severity}] ${finding.file}:${finding.line_start}-${finding.line_end} - ${finding.title}`,
        `  ${finding.body}`,
        `  Confidence: ${finding.confidence}`,
        finding.recommendation ? `  Recommendation: ${finding.recommendation}` : ""
      ].filter(Boolean).join("\n")).join("\n")
    : "- None";
  const nextSteps = value.next_steps.length
    ? value.next_steps.map((step) => `- ${step}`).join("\n")
    : "- None";
  return [
    `## Verdict: ${value.verdict}`,
    "",
    "## Summary",
    value.summary,
    "",
    "## Findings",
    findings,
    "",
    "## Next Steps",
    nextSteps
  ].join("\n");
}
