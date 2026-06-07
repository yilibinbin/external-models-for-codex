const REVIEW_VERDICTS = new Set(["approve", "needs-attention"]);
const FINDING_SEVERITIES = new Set(["critical", "high", "medium", "low"]);

export function extractJsonObject(rawOutput) {
  const text = String(rawOutput ?? "").trim();
  if (!text) {
    throw new Error("Antigravity returned empty structured output.");
  }
  try {
    return JSON.parse(text);
  } catch {
    const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/i);
    if (fenced) {
      return JSON.parse(fenced[1].trim());
    }
    const start = text.indexOf("{");
    const end = text.lastIndexOf("}");
    if (start !== -1 && end > start) {
      return JSON.parse(text.slice(start, end + 1));
    }
    throw new Error("Antigravity output did not contain a JSON object.");
  }
}

function assertPlainObject(value, message) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(message);
  }
}

function assertString(value, message, allowEmpty = false) {
  if (typeof value !== "string" || (!allowEmpty && value.length === 0)) {
    throw new Error(message);
  }
}

function assertArray(value, message) {
  if (!Array.isArray(value)) {
    throw new Error(message);
  }
}

export function validateStructuredReview(value) {
  assertPlainObject(value, "Structured review output must be a JSON object.");
  if (!REVIEW_VERDICTS.has(value.verdict)) {
    throw new Error("Structured review output verdict must be approve or needs-attention.");
  }
  assertString(value.summary, "Structured review output summary must be a non-empty string.");
  assertArray(value.findings, "Structured review output findings must be an array.");
  assertArray(value.next_steps, "Structured review output next_steps must be an array.");

  for (const [index, finding] of value.findings.entries()) {
    assertPlainObject(finding, `Structured review finding ${index} must be a JSON object.`);
    if (!FINDING_SEVERITIES.has(finding.severity)) {
      throw new Error(`Structured review finding ${index} severity must be critical, high, medium, or low.`);
    }
    assertString(finding.title, `Structured review finding ${index} title must be a non-empty string.`);
    assertString(finding.body, `Structured review finding ${index} body must be a non-empty string.`);
    assertString(finding.file, `Structured review finding ${index} file must be a non-empty string.`);
    if (!Number.isInteger(finding.line_start) || finding.line_start < 1) {
      throw new Error(`Structured review finding ${index} line_start must be an integer >= 1.`);
    }
    if (!Number.isInteger(finding.line_end) || finding.line_end < 1) {
      throw new Error(`Structured review finding ${index} line_end must be an integer >= 1.`);
    }
    if (typeof finding.confidence !== "number" || finding.confidence < 0 || finding.confidence > 1) {
      throw new Error(`Structured review finding ${index} confidence must be a number between 0 and 1.`);
    }
    assertString(finding.recommendation, `Structured review finding ${index} recommendation must be a string.`, true);
  }

  for (const [index, step] of value.next_steps.entries()) {
    assertString(step, `Structured review next_steps ${index} must be a non-empty string.`);
  }
  return value;
}

export function appendStructuredReviewInstructions(prompt) {
  return [
    String(prompt).trim(),
    "<structured_output_contract>",
    "Return only one JSON object. Do not include Markdown fences, prose, or any text outside the JSON object.",
    "The JSON object must match this shape:",
    "{",
    '  "verdict": "approve" | "needs-attention",',
    '  "summary": "short summary",',
    '  "findings": [',
    "    {",
    '      "severity": "critical" | "high" | "medium" | "low",',
    '      "title": "short finding title",',
    '      "body": "specific evidence and impact",',
    '      "file": "relative/path.ext",',
    '      "line_start": 1,',
    '      "line_end": 1,',
    '      "confidence": 0.0,',
    '      "recommendation": "specific remediation"',
    "    }",
    "  ],",
    '  "next_steps": ["ship"]',
    "}",
    'Use "approve" only when there are no findings. Use "needs-attention" when any finding should be reviewed before shipping.',
    "</structured_output_contract>"
  ].join("\n");
}

