import {
  MODEL_ENV,
  MODEL_PROVIDER_ENV,
  agyCommand,
  runCommand
} from "./antigravity-runtime.mjs";
import {
  parseAgyHelp,
  parseAgyModels,
  selectAgyModel
} from "./agy-capabilities.mjs";

function safeSelect(selection) {
  try {
    const selected = selectAgyModel(selection);
    return { ok: true, ...selected };
  } catch (error) {
    return {
      ok: false,
      modelProvider: selection.provider || "",
      model: selection.explicitModel || "",
      source: "error",
      error: error.message || String(error)
    };
  }
}

function envWithoutGenericModel(env = process.env) {
  const clone = { ...env };
  delete clone[MODEL_ENV];
  return clone;
}

function runDoctorCommand(command, args, options) {
  const result = runCommand(command, args, options);
  if (!result || typeof result.then === "function" || typeof result.status !== "number") {
    throw new Error("Antigravity doctor requires a synchronous process runner; use the current spawnSync-backed runCommand or convert antigravityDoctor to async with awaited calls.");
  }
  return result;
}

function probeError(result) {
  return result.status !== 0 ? (result.error || result.stderr || result.stdout || "") : "";
}

export function antigravityDoctor(env = process.env, options = {}) {
  const command = agyCommand(env);
  const help = runDoctorCommand(command, ["--help"], { env, timeout: options.timeout || 10_000 });
  const capabilities = parseAgyHelp(help.stdout || help.stderr);
  const modelsResult = capabilities.modelsCommand
    ? runDoctorCommand(command, ["models"], { env, timeout: options.timeout || 10_000 })
    : { status: 1, stdout: "", stderr: "agy models is unavailable", error: "", errorCode: "" };
  const models = parseAgyModels(modelsResult.stdout);
  const selected = {
    current: safeSelect({ provider: options.modelProvider || env[MODEL_PROVIDER_ENV] || "gemini", explicitModel: options.model || "", models, env }),
    providers: {
      gemini: models.gemini.length ? safeSelect({ provider: "gemini", models, env: envWithoutGenericModel(env) }) : { ok: false, error: "no Gemini models listed" },
      claude: models.claude.length ? safeSelect({ provider: "claude", models, env: envWithoutGenericModel(env) }) : { ok: false, error: "no Claude models listed" }
    }
  };
  const ready = help.status === 0
    && modelsResult.status === 0
    && Boolean(capabilities.prompt && capabilities.model && capabilities.printTimeout)
    && selected.current.ok;
  return {
    ok: ready,
    agy: {
      command,
      available: help.status === 0,
      capabilities,
      helpStatus: help.status,
      helpError: probeError(help)
    },
    models: {
      available: modelsResult.status === 0,
      count: models.all.length,
      gemini: models.gemini,
      claude: models.claude,
      unsupported: models.unsupported,
      status: modelsResult.status,
      error: probeError(modelsResult)
    },
    selected
  };
}
