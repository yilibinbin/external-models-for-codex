const SAFE_STATE_ID = /^[A-Za-z0-9_-]{1,96}$/;

export function assertSafeStateId(value, label = "state id") {
  const text = String(value ?? "");
  if (!SAFE_STATE_ID.test(text) || text.includes("..") || text.includes("/") || text.includes("\\") || text.includes("\0")) {
    throw new Error(`${label} must contain only letters, numbers, underscore, and hyphen.`);
  }
  return text;
}
