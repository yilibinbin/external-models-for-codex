export function extractJsonObject(rawOutput) {
  const text = String(rawOutput ?? "").trim();
  if (!text) {
    throw new Error("Claude returned empty structured output.");
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
    throw new Error("Claude output did not contain a JSON object.");
  }
}

export function validateAdversarialJson(value) {
  const allowedVerdicts = new Set(["PASS", "CONTESTED", "REJECT"]);
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Structured adversarial output must be a JSON object.");
  }
  if (!allowedVerdicts.has(value.verdict)) {
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
