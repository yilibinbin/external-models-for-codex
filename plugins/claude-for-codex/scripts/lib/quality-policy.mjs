export const QUALITY_ENV = "CLAUDE_FOR_CODEX_QUALITY";

export const VALID_QUALITIES = Object.freeze(["auto", "fast", "standard", "strong", "max"]);
export const VALID_EFFORTS = Object.freeze(["low", "medium", "high", "xhigh", "max"]);
export const VALID_MODEL_ALIASES = Object.freeze(["haiku", "sonnet", "opus", "inherit"]);

const QUALITY_PROFILES = Object.freeze({
  fast: Object.freeze({ quality: "fast", model: "sonnet", effort: "low" }),
  standard: Object.freeze({ quality: "standard", model: "sonnet", effort: "high" }),
  strong: Object.freeze({ quality: "strong", model: "opus", effort: "xhigh" }),
  max: Object.freeze({ quality: "max", model: "opus", effort: "max" })
});

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

export function resolveQualityPolicy(command, args = {}, env = process.env, signals = {}) {
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
  const model = explicitModel || profile.model;
  const effort = explicitEffort || profile.effort;
  return {
    requestedQuality: quality,
    resolvedQuality: profile.quality,
    model,
    effort,
    modelSource: explicitModel ? "explicit" : "quality-policy",
    effortSource: explicitEffort ? "explicit" : "quality-policy",
    score: quality === "auto" ? scoreQuality(command, args, signals) : null,
    signals: {
      changedFiles: Number(signals.changedFiles ?? 0),
      diffLines: Number(signals.diffLines ?? 0)
    }
  };
}

export function applyQualityPolicy(command, args = {}, env = process.env, signals = {}) {
  const policy = resolveQualityPolicy(command, args, env, signals);
  args.qualityPolicy = policy;
  args.model = policy.model;
  args.effort = policy.effort;
  return policy;
}
