const SAFE_STATE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

export function assertSafeStateId(value, label = "state id") {
  const text = String(value ?? "").trim();
  if (!SAFE_STATE_ID.test(text) || text.includes("/") || text.includes("\\") || text === "." || text === "..") {
    throw new Error(`${label} must be a safe identifier without path separators.`);
  }
  return text;
}
