export const DEFAULT_GEMINI_MODEL = "Gemini 3.1 Pro (High)";
export const DEFAULT_CLAUDE_MODEL = "Claude Sonnet 4.6 (Thinking)";
export const VALID_AGY_MODEL_PROVIDERS = Object.freeze(["gemini", "claude"]);

export function parseAgyHelp(helpText = "") {
  const text = String(helpText || "");
  return {
    prompt: text.includes("--prompt"),
    model: text.includes("--model"),
    print: text.includes("--print"),
    printTimeout: text.includes("--print-timeout"),
    sandbox: text.includes("--sandbox"),
    addDir: text.includes("--add-dir"),
    logFile: text.includes("--log-file"),
    modelsCommand: /\bmodels\b/.test(text),
    pluginCommand: /\bplugin\b/.test(text)
  };
}

export function normalizeAgyProvider(value = "gemini") {
  const provider = String(value || "gemini").trim().toLowerCase();
  if (provider === "gemini" || provider === "claude") return provider;
  throw new Error(`Invalid Antigravity model provider "${value}". Valid values: gemini, claude.`);
}

function assertSafeModelText(value) {
  const model = String(value || "").trim();
  if (!model) throw new Error("Missing Antigravity model.");
  if (model.startsWith("-") || /[\r\n\0$`]/.test(model)) {
    throw new Error("Invalid Antigravity model value.");
  }
  if (/\b(gpt|openai)\b/i.test(model)) {
    throw new Error(`Antigravity for Codex does not support GPT/OpenAI models; rejected model "${model}".`);
  }
  return model;
}

export function validateAgyModelForProvider(model, providerValue = "gemini") {
  const provider = normalizeAgyProvider(providerValue);
  const value = assertSafeModelText(model);
  if (provider === "gemini") {
    if (/\b(claude|sonnet|opus|anthropic)\b/i.test(value)) {
      throw new Error(`Antigravity Gemini provider requires a Gemini model; rejected model "${value}".`);
    }
    if (!/^gemini(?:[\s._-]|$)/i.test(value)) {
      throw new Error(`Antigravity Gemini provider requires a Gemini model label or id; rejected model "${value}".`);
    }
    return value;
  }
  if (/\bgemini\b/i.test(value)) {
    throw new Error(`Antigravity Claude provider requires a Claude model; rejected model "${value}".`);
  }
  if (!/\b(claude|sonnet|opus|haiku)\b/i.test(value)) {
    throw new Error(`Antigravity Claude provider requires a Claude/Sonnet/Opus/Haiku model; rejected model "${value}".`);
  }
  return value;
}

export function parseAgyModels(modelsText = "") {
  const rows = String(modelsText || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  return {
    all: rows,
    gemini: rows.filter((line) => /^gemini(?:[\s._-]|$)/i.test(line)),
    claude: rows.filter((line) => /\b(claude|sonnet|opus|haiku)\b/i.test(line) && !/\bgemini\b/i.test(line)),
    unsupported: rows.filter((line) => /\b(gpt|openai)\b/i.test(line))
  };
}

function validateOptionalEnvModelForProvider(model, provider) {
  if (!model) return null;
  try {
    return validateAgyModelForProvider(model, provider);
  } catch (error) {
    const otherProvider = provider === "gemini" ? "claude" : "gemini";
    try {
      validateAgyModelForProvider(model, otherProvider);
      return null;
    } catch {
      // Preserve the provider-specific error from the requested selection.
    }
    throw error;
  }
}

export function selectAgyModel({ provider = "gemini", explicitModel = "", env = {}, models = null } = {}) {
  const normalizedProvider = normalizeAgyProvider(provider);
  if (explicitModel) {
    return {
      modelProvider: normalizedProvider,
      model: validateAgyModelForProvider(explicitModel, normalizedProvider),
      source: "explicit"
    };
  }
  const generic = env.ANTIGRAVITY_FOR_CODEX_MODEL || "";
  const genericForProvider = validateOptionalEnvModelForProvider(generic, normalizedProvider);
  if (genericForProvider) {
    return {
      modelProvider: normalizedProvider,
      model: genericForProvider,
      source: "env-generic"
    };
  }
  const providerSpecific = normalizedProvider === "claude"
    ? env.ANTIGRAVITY_FOR_CODEX_CLAUDE_MODEL
    : env.ANTIGRAVITY_FOR_CODEX_GEMINI_MODEL;
  if (providerSpecific) {
    return {
      modelProvider: normalizedProvider,
      model: validateAgyModelForProvider(providerSpecific, normalizedProvider),
      source: "env-provider"
    };
  }
  const fallback = normalizedProvider === "claude" ? DEFAULT_CLAUDE_MODEL : DEFAULT_GEMINI_MODEL;
  const catalogModels = normalizedProvider === "claude" ? models?.claude : models?.gemini;
  const catalogModel = Array.isArray(catalogModels)
    ? (catalogModels.find((model) => model === fallback) || catalogModels[0])
    : "";
  return {
    modelProvider: normalizedProvider,
    model: validateAgyModelForProvider(catalogModel || fallback, normalizedProvider),
    source: catalogModel ? "catalog" : "default"
  };
}
