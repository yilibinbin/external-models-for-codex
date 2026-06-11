export const QUALITY_ENV = "CLAUDE_FOR_CODEX_QUALITY";
export const TOP_MODEL_ENV = "CLAUDE_FOR_CODEX_TOP_MODEL";
export const TOP_MODEL_FALLBACK_ENV = "CLAUDE_FOR_CODEX_TOP_MODEL_FALLBACK";

export const VALID_QUALITIES = Object.freeze(["auto", "fast", "standard", "strong", "max"]);
export const VALID_EFFORTS = Object.freeze(["low", "medium", "high", "xhigh", "max"]);
export const VALID_MODEL_ALIASES = Object.freeze(["haiku", "sonnet", "opus", "fable", "best", "inherit"]);

const QUALITY_PROFILES = Object.freeze({
  fast: Object.freeze({ quality: "fast", model: "sonnet", effort: "low", topModel: false }),
  standard: Object.freeze({ quality: "standard", model: "sonnet", effort: "high", topModel: false }),
  strong: Object.freeze({ quality: "strong", model: "opus", effort: "xhigh", topModel: false }),
  max: Object.freeze({ quality: "max", model: "top", effort: "max", topModel: true })
});

export const DEFAULT_TOP_MODEL_FALLBACK = "opus,sonnet";

const COMMAND_BASE_SCORE = Object.freeze({
  review: 2,
  plan: 3,
  "multi-review": 4,
  "adversarial-review": 5,
  rescue: 5,
  "review-gate": 2
});

function normalized(value) {
  return String(value ?? "").trim().toLowerCase();
}

export function assertValidQuality(value, source = "--quality") {
  const quality = normalized(value);
  if (!VALID_QUALITIES.includes(quality)) {
    throw new Error(`Invalid ${source} "${value}". Valid values: ${VALID_QUALITIES.join(", ")}.`);
  }
  return quality;
}

export function assertValidEffort(value) {
  if (value === undefined || value === "") {
    return "";
  }
  const effort = normalized(value);
  if (!VALID_EFFORTS.includes(effort)) {
    throw new Error(`Invalid --effort "${value}". Valid values: ${VALID_EFFORTS.join(", ")}.`);
  }
  return effort;
}

export function assertSafeModelAliasOrId(value) {
  if (value === undefined || value === "") {
    return "";
  }
  const model = String(value).trim();
  if (!model || model.startsWith("-") || /[\r\n\0]/.test(model)) {
    throw new Error("Invalid --model value.");
  }
  return model;
}

function safeModelAliasOrId(value) {
  try {
    return assertSafeModelAliasOrId(value);
  } catch {
    return "";
  }
}

function booleanCapability(value) {
  return value === true || normalized(value) === "true" || normalized(value) === "1";
}

function requestedTopModel(env = process.env) {
  const configured = safeModelAliasOrId(env[TOP_MODEL_ENV]);
  return configured ? configured.toLowerCase() : "";
}

function topModelFallback(env = process.env) {
  return safeModelAliasOrId(env[TOP_MODEL_FALLBACK_ENV]) || DEFAULT_TOP_MODEL_FALLBACK;
}

export function resolveTopModel(capabilities = {}, env = process.env) {
  const requested = requestedTopModel(env);
  if (requested) {
    return {
      model: requested,
      source: TOP_MODEL_ENV,
      fallbackModel: requested === "opus" ? "" : topModelFallback(env)
    };
  }
  const fallbackModel = topModelFallback(env);
  if (booleanCapability(capabilities.best)) {
    return { model: "best", source: "claude-capabilities", fallbackModel };
  }
  if (booleanCapability(capabilities.fable)) {
    return { model: "fable", source: "claude-capabilities", fallbackModel };
  }
  return { model: "opus", source: "default", fallbackModel: "" };
}

function roleNames(args = {}) {
  const roles = args.reviewRoles ?? args.roles ?? [];
  return roles
    .map((role) => typeof role === "string" ? role : role?.name || role?.id || "")
    .filter(Boolean);
}

function diffScore(signals = {}) {
  const files = Number(signals.changedFiles ?? 0);
  const lines = Number(signals.diffLines ?? 0);
  let score = 0;
  if (files >= 15) score += 2;
  else if (files >= 6) score += 1;
  if (lines >= 1500) score += 2;
  else if (lines >= 500) score += 1;
  return score;
}

export function scoreQuality(command, args = {}, signals = {}) {
  let score = COMMAND_BASE_SCORE[command] ?? 2;
  if (args.jsonOutput) score += 1;
  if (args.semanticContext && args.semanticContext !== "off") score += 1;
  if (args.backend === "sdk") score += 1;
  if (args.agentTeam === "sdk-subagents") score += 2;
  const roles = roleNames(args);
  if (roles.some((role) => /security|release|adversarial/i.test(role))) score += 1;
  if (roles.length >= 4) score += 1;
  score += diffScore(signals);
  return score;
}

export function tierForScore(score) {
  return score >= 8 ? "max" : score >= 5 ? "strong" : "standard";
}

export function profileForQuality(quality, context = {}) {
  const resolvedQuality = quality === "auto"
    ? tierForScore(scoreQuality(context.command, context.args, context.signals))
    : quality;
  const explicitReviewGateEscalation = context.command === "review-gate" && context.explicitQuality && (quality === "strong" || quality === "max");
  const cappedQuality = context.command === "review-gate" && !explicitReviewGateEscalation && (resolvedQuality === "strong" || resolvedQuality === "max")
    ? "standard"
    : resolvedQuality;
  const profile = QUALITY_PROFILES[cappedQuality];
  if (!profile) {
    throw new Error(`Quality profile "${cappedQuality}" is not configured.`);
  }
  return profile;
}

export function resolveQualityPolicy(command, args = {}, env = process.env, signals = {}, capabilities = {}) {
  const explicitQuality = args.quality !== undefined;
  const rawQuality = explicitQuality ? args.quality : env[QUALITY_ENV] || "auto";
  const quality = assertValidQuality(rawQuality, explicitQuality ? "--quality" : QUALITY_ENV);
  const explicitModel = assertSafeModelAliasOrId(args.model);
  const explicitEffort = assertValidEffort(args.effort);
  const profile = profileForQuality(quality, {
    command,
    args,
    signals,
    explicitQuality
  });
  const topModel = profile.topModel ? resolveTopModel(capabilities, env) : null;
  const model = explicitModel || topModel?.model || profile.model;
  const effort = explicitEffort || profile.effort;
  return {
    requestedQuality: quality,
    resolvedQuality: profile.quality,
    model,
    effort,
    modelSource: explicitModel ? "explicit" : topModel?.source || "quality-policy",
    effortSource: explicitEffort ? "explicit" : "quality-policy",
    topModelProfile: Boolean(profile.topModel),
    topModelSelected: Boolean(!explicitModel && profile.topModel && topModel?.model !== "opus"),
    fallbackModel: !explicitModel && profile.topModel ? topModel?.fallbackModel || "" : "",
    score: quality === "auto" ? scoreQuality(command, args, signals) : null,
    signals: {
      changedFiles: Number(signals.changedFiles ?? 0),
      diffLines: Number(signals.diffLines ?? 0)
    }
  };
}

export function applyQualityPolicy(command, args = {}, env = process.env, signals = {}, capabilities = {}) {
  const policy = resolveQualityPolicy(command, args, env, signals, capabilities);
  args.qualityPolicy = policy;
  args.model = policy.model;
  args.effort = policy.effort;
  return policy;
}
