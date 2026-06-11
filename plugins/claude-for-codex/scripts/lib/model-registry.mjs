const ALIAS_ROWS = Object.freeze([
  { alias: "default", family: "default", selectable: true, topCandidate: false, supportsOneMillionSuffix: false },
  { alias: "best", family: "best", selectable: true, topCandidate: true, supportsOneMillionSuffix: false },
  { alias: "fable", family: "fable", selectable: true, topCandidate: true, supportsOneMillionSuffix: false },
  { alias: "opus", family: "opus", selectable: true, topCandidate: true, supportsOneMillionSuffix: true },
  { alias: "sonnet", family: "sonnet", selectable: true, topCandidate: false, supportsOneMillionSuffix: true },
  { alias: "haiku", family: "haiku", selectable: true, topCandidate: false, supportsOneMillionSuffix: false },
  { alias: "opusplan", family: "opusplan", selectable: true, topCandidate: false, supportsOneMillionSuffix: false },
  { alias: "inherit", family: "inherit", selectable: true, topCandidate: false, supportsOneMillionSuffix: false }
]);

export const MODEL_ALIAS_REGISTRY = Object.freeze(ALIAS_ROWS);
export const MODEL_ALIASES = Object.freeze(ALIAS_ROWS.map((entry) => entry.alias));
export const ONE_MILLION_SUFFIX = "[1m]";
export const DEFAULT_TOP_MODEL_FALLBACK = "opus,sonnet";

const CLAUDE_MODEL_ID_PATTERN = /^claude-[a-z0-9][a-z0-9-]*(?:\[1m\])?$/i;

function normalized(value) {
  return String(value ?? "").trim().toLowerCase();
}

function aliasEntry(alias) {
  return MODEL_ALIAS_REGISTRY.find((entry) => entry.alias === alias) ?? null;
}

export function normalizeModelSelection(value, { allowDefault = false, allowInherit = true } = {}) {
  const raw = String(value ?? "").trim();
  if (!raw || raw.startsWith("-") || /[\r\n\0]/.test(raw)) {
    return { valid: false, model: "", reason: "empty-or-unsafe" };
  }
  const lower = raw.toLowerCase();
  if (lower.endsWith(ONE_MILLION_SUFFIX)) {
    const base = lower.slice(0, -ONE_MILLION_SUFFIX.length);
    const entry = aliasEntry(base);
    if (entry?.supportsOneMillionSuffix) {
      return { valid: true, model: `${base}${ONE_MILLION_SUFFIX}`, alias: base, family: entry.family, oneMillionContext: true };
    }
  }
  const entry = aliasEntry(lower);
  if (entry) {
    if (entry.alias === "default" && !allowDefault) {
      return { valid: false, model: "", reason: "default-not-accepted" };
    }
    if (entry.alias === "inherit" && !allowInherit) {
      return { valid: false, model: "", reason: "inherit-not-accepted" };
    }
    return { valid: true, model: lower, alias: lower, family: entry.family, oneMillionContext: false };
  }
  if (CLAUDE_MODEL_ID_PATTERN.test(raw)) {
    return { valid: true, model: raw, alias: "", family: "model-id", oneMillionContext: raw.toLowerCase().endsWith(ONE_MILLION_SUFFIX) };
  }
  return { valid: true, model: raw, alias: "", family: "custom", oneMillionContext: false };
}

export function assertSafeModelAliasOrId(value, options = {}) {
  if (value === undefined || value === "") {
    return "";
  }
  const selection = normalizeModelSelection(value, options);
  if (!selection.valid) {
    throw new Error(`Invalid --model value: ${selection.reason}.`);
  }
  return selection.model;
}

function safeModelAliasOrId(value, options = {}) {
  try {
    return assertSafeModelAliasOrId(value, options);
  } catch {
    return "";
  }
}

function booleanCapability(value) {
  return value === true || normalized(value) === "true" || normalized(value) === "1";
}

export function topModelFallback(env = {}) {
  const configured = safeModelAliasOrId(env.CLAUDE_FOR_CODEX_TOP_MODEL_FALLBACK ?? DEFAULT_TOP_MODEL_FALLBACK);
  return configured || DEFAULT_TOP_MODEL_FALLBACK;
}

export function resolveTopModelFromCapabilities(capabilities = {}, env = {}) {
  const requested = env.CLAUDE_FOR_CODEX_TOP_MODEL ? assertSafeModelAliasOrId(env.CLAUDE_FOR_CODEX_TOP_MODEL) : "";
  if (requested) {
    return {
      model: requested,
      source: "CLAUDE_FOR_CODEX_TOP_MODEL",
      fallbackModel: requested === "opus" ? "" : topModelFallback(env)
    };
  }
  const aliases = capabilities.modelAliases ?? capabilities;
  if (booleanCapability(aliases.best)) {
    return { model: "best", source: "claude-capabilities", fallbackModel: topModelFallback(env) };
  }
  if (booleanCapability(aliases.fable)) {
    return { model: "fable", source: "claude-capabilities", fallbackModel: topModelFallback(env) };
  }
  if (booleanCapability(aliases.opus)) {
    return { model: "opus", source: "claude-capabilities", fallbackModel: "" };
  }
  return { model: "opus", source: "default", fallbackModel: "" };
}
