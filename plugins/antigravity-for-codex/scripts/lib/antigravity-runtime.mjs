import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const AGY_CLI_PATH_ENV = "AGY_CLI_PATH";
export const ANTIGRAVITY_CLI_PATH_ENV = "ANTIGRAVITY_CLI_PATH";
export const MODEL_PROVIDER_ENV = "ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER";
export const MODEL_ENV = "ANTIGRAVITY_FOR_CODEX_MODEL";
export const GEMINI_MODEL_ENV = "ANTIGRAVITY_FOR_CODEX_GEMINI_MODEL";
export const CLAUDE_MODEL_ENV = "ANTIGRAVITY_FOR_CODEX_CLAUDE_MODEL";

export const DEFAULT_GEMINI_MODEL = "Gemini 3.1 Pro (High)";
export const DEFAULT_CLAUDE_MODEL = "Claude Sonnet 4.6 (Thinking)";
export const DEFAULT_TIMEOUT_MS = 15 * 60 * 1000;

const MAX_BUFFER = 20 * 1024 * 1024;

function isExecutable(filePath) {
  try {
    fs.accessSync(filePath, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

function findOnPath(commandName, env = process.env) {
  for (const entry of String(env.PATH || "").split(path.delimiter)) {
    if (!entry) continue;
    const candidate = path.join(entry, commandName);
    if (isExecutable(candidate)) return candidate;
  }
  return "";
}

function expandExecutableCandidates(pattern) {
  const parts = path.resolve(pattern).split(path.sep);
  const results = [];

  function visit(index, current) {
    if (index >= parts.length) {
      results.push(current || path.sep);
      return;
    }
    const part = parts[index];
    if (!part) {
      visit(index + 1, path.sep);
      return;
    }
    if (part !== "*") {
      visit(index + 1, path.join(current || path.sep, part));
      return;
    }
    const dir = current || path.sep;
    let entries = [];
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      if (entry.isDirectory()) {
        visit(index + 1, path.join(dir, entry.name));
      }
    }
  }

  visit(0, "");
  return results;
}

function candidateCommands(env = process.env) {
  const names = process.platform === "win32" ? ["agy.cmd", "agy.exe", "agy"] : ["agy"];
  const home = env.HOME || os.homedir();
  const candidates = [];
  const add = (candidate) => {
    if (candidate) candidates.push(candidate);
  };
  const addBin = (dir) => {
    for (const name of names) {
      add(dir ? path.join(dir, name) : "");
    }
  };

  addBin(path.join(home, ".local", "bin"));
  addBin(path.join(home, "bin"));
  addBin(path.join(home, ".npm-global", "bin"));
  addBin(path.join(home, ".volta", "bin"));
  addBin(path.join(home, ".asdf", "shims"));
  addBin(path.join(home, ".bun", "bin"));
  addBin(path.join(home, ".deno", "bin"));
  addBin(env.PNPM_HOME);
  addBin(env.NPM_CONFIG_PREFIX ? path.join(env.NPM_CONFIG_PREFIX, "bin") : "");
  addBin(env.npm_config_prefix ? path.join(env.npm_config_prefix, "bin") : "");
  addBin(env.HOMEBREW_PREFIX ? path.join(env.HOMEBREW_PREFIX, "bin") : "");

  for (const pattern of [
    path.join(home, ".nvm", "versions", "node", "*", "bin", "agy"),
    path.join(home, ".fnm", "node-versions", "*", "installation", "bin", "agy"),
    path.join(home, ".asdf", "installs", "nodejs", "*", "bin", "agy")
  ]) {
    candidates.push(...expandExecutableCandidates(pattern));
  }

  for (const dir of ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"]) {
    addBin(dir);
  }
  return [...new Set(candidates)];
}

export function agyCommand(env = process.env) {
  for (const envName of [AGY_CLI_PATH_ENV, ANTIGRAVITY_CLI_PATH_ENV]) {
    if (env[envName] && isExecutable(env[envName])) return env[envName];
  }
  const fromPath = findOnPath("agy", env);
  if (fromPath) return fromPath;
  // TEST-ONLY: lets fake-CLI tests prove missing-candidate behavior without
  // accidentally discovering a real user-home Antigravity install.
  if (env.ANTIGRAVITY_FOR_CODEX_TEST_DISABLE_CANDIDATE_DISCOVERY === "1") return "agy";
  for (const candidate of candidateCommands(env)) {
    if (isExecutable(candidate)) return candidate;
  }
  return "agy";
}

export function runCommand(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd || process.cwd(),
    env: options.env || process.env,
    encoding: "utf8",
    input: options.input,
    maxBuffer: MAX_BUFFER,
    timeout: options.timeout || DEFAULT_TIMEOUT_MS,
    killSignal: options.killSignal || "SIGKILL"
  });
  return {
    status: result.status ?? 1,
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    error: result.error ? String(result.error.message || result.error) : "",
    errorCode: result.error?.code ? String(result.error.code) : ""
  };
}

export function normalizedModelProvider(value = process.env[MODEL_PROVIDER_ENV]) {
  const provider = String(value || "gemini").trim().toLowerCase();
  if (provider === "gemini" || provider === "claude") return provider;
  throw new Error(`Invalid ${MODEL_PROVIDER_ENV} "${value}". Valid values: gemini, claude.`);
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

export function validateModelForProvider(model, provider) {
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
  if (provider === "claude") {
    if (/\bgemini\b/i.test(value)) {
      throw new Error(`Antigravity Claude provider requires a Claude model; rejected model "${value}".`);
    }
    if (!/\b(claude|sonnet|opus)\b/i.test(value)) {
      throw new Error(`Antigravity Claude provider requires a Claude/Sonnet/Opus model; rejected model "${value}".`);
    }
    return value;
  }
  throw new Error(`Unsupported model provider "${provider}".`);
}

export function selectedModel(env = process.env, options = {}) {
  const provider = normalizedModelProvider(options.modelProvider || env[MODEL_PROVIDER_ENV]);
  const explicit = options.model || env[MODEL_ENV];
  if (explicit) {
    return { modelProvider: provider, model: validateModelForProvider(explicit, provider) };
  }
  if (provider === "claude") {
    return {
      modelProvider: provider,
      model: validateModelForProvider(env[CLAUDE_MODEL_ENV] || DEFAULT_CLAUDE_MODEL, provider)
    };
  }
  return {
    modelProvider: provider,
    model: validateModelForProvider(env[GEMINI_MODEL_ENV] || DEFAULT_GEMINI_MODEL, provider)
  };
}

function capabilitiesFromHelp(help) {
  const text = String(help || "");
  return {
    prompt: text.includes("--prompt"),
    model: text.includes("--model"),
    print: text.includes("--print"),
    printTimeout: text.includes("--print-timeout"),
    sandbox: text.includes("--sandbox"),
    addDir: text.includes("--add-dir"),
    modelsCommand: /\bmodels\b/.test(text),
    pluginCommand: /\bplugin\b/.test(text)
  };
}

export function antigravityPreflight(env = process.env, options = {}) {
  const command = agyCommand(env);
  const result = runCommand(command, ["--help"], { env });
  const help = result.stdout || result.stderr || "";
  const capabilities = capabilitiesFromHelp(help);
  let selected = { modelProvider: "", model: "" };
  let modelError = "";
  try {
    selected = selectedModel(env, options);
  } catch (error) {
    modelError = error.message || String(error);
  }
  const requiredFlags = ["--prompt", "--model", "--print-timeout"];
  const missing = requiredFlags.filter((flag) => !help.includes(flag));
  return {
    backend: "antigravity",
    command,
    checked: result.status === 0,
    ok: result.status === 0 && missing.length === 0 && !modelError,
    missing,
    requiredFlags,
    capabilities,
    modelProvider: selected.modelProvider,
    model: selected.model,
    modelPolicyError: Boolean(modelError),
    error: modelError || (result.status === 0 ? "" : (result.stderr || result.error || "agy --help failed").trim())
  };
}

export function antigravityModelCatalog(env = process.env) {
  const command = agyCommand(env);
  const result = runCommand(command, ["models"], { env, timeout: 30 * 1000 });
  const models = result.status === 0
    ? result.stdout.split(/\r?\n/).map((line) => line.trim()).filter(Boolean)
    : [];
  return {
    available: result.status === 0,
    models,
    error: result.status === 0 ? "" : (result.stderr || result.error || "").trim()
  };
}

export function antigravityModelDiagnostics(env = process.env, options = {}) {
  const preflight = antigravityPreflight(env, options);
  const capabilities = preflight.capabilities || {};
  const catalog = capabilities.modelsCommand
    ? antigravityModelCatalog(env)
    : { available: false, models: [], error: "agy models command not reported" };
  return {
    ok: true,
    provider: {
      modelProvider: preflight.modelProvider || "",
      model: preflight.model || ""
    },
    modelCatalog: {
      available: catalog.available,
      selectedModelListed: catalog.models.includes(preflight.model),
      count: catalog.models.length,
      error: catalog.error
    }
  };
}

function timeoutMsToAgyDuration(timeoutMs) {
  const seconds = Math.max(1, Math.ceil((timeoutMs || DEFAULT_TIMEOUT_MS) / 1000));
  return `${seconds}s`;
}

export function antigravityPrintArgs(prompt, options = {}, env = process.env) {
  const preflight = options.preflight?.ok ? options.preflight : antigravityPreflight(env, options);
  if (!preflight.ok) {
    throw new Error(preflight.error || `Antigravity CLI is unavailable; missing ${preflight.missing.join(", ")}.`);
  }
  const args = [
    "--model",
    preflight.model,
    "--print-timeout",
    options.printTimeout || timeoutMsToAgyDuration(options.timeout)
  ];
  if (env.ANTIGRAVITY_FOR_CODEX_SANDBOX === "on" && preflight.capabilities.sandbox) {
    args.push("--sandbox");
  }
  if (options.includeDirectories?.length) {
    if (!preflight.capabilities.addDir) {
      throw new Error("Antigravity CLI does not report --add-dir support.");
    }
    for (const includeDirectory of options.includeDirectories) {
      args.push("--add-dir", includeDirectory);
    }
  }
  // Current agy treats --prompt as the noninteractive print prompt; combining
  // --print with --prompt can ignore the supplied prompt and produce a greeting.
  args.push("--prompt", String(prompt));
  if (args.includes("--dangerously-skip-permissions")) {
    throw new Error("Internal error: unsafe Antigravity permission flag is forbidden.");
  }
  return { command: preflight.command, args, preflight };
}

export function antigravityPrint(prompt, options = {}, env = process.env) {
  let invocation;
  try {
    invocation = antigravityPrintArgs(prompt, options, env);
  } catch (error) {
    return { status: 2, stdout: "", stderr: error.message || String(error), error: "", errorCode: "" };
  }
  const result = runCommand(invocation.command, invocation.args, {
    cwd: options.cwd,
    env,
    timeout: options.timeout
  });
  const output = String(result.stdout || "").trim();
  if (result.status === 0 && !output) {
    return {
      ...result,
      status: 1,
      stderr: "Antigravity CLI returned empty output.",
      provider: invocation.preflight
    };
  }
  return { ...result, stdout: output, provider: invocation.preflight };
}

export function antigravityPrintAsync(prompt, options = {}, env = process.env) {
  let invocation;
  try {
    invocation = antigravityPrintArgs(prompt, options, env);
  } catch (error) {
    return Promise.resolve({
      status: 2,
      stdout: "",
      stderr: error.message || String(error),
      error: "",
      errorCode: ""
    });
  }
  return new Promise((resolve) => {
    const detached = process.platform !== "win32";
    const child = spawn(invocation.command, invocation.args, {
      cwd: options.cwd || process.cwd(),
      env,
      detached,
      stdio: ["pipe", "pipe", "pipe"]
    });
    let stdout = "";
    let stderr = "";
    let settled = false;
    let timedOut = false;
    let timeoutTimer;
    let killTimer;
    let forceResolveTimer;

    const clearTimers = () => {
      clearTimeout(timeoutTimer);
      clearTimeout(killTimer);
      clearTimeout(forceResolveTimer);
    };

    const killChild = (signal) => {
      if (detached && child.pid) {
        try {
          process.kill(-child.pid, signal);
          return;
        } catch {
          // Fall through to direct child kill for platforms/runtimes without process-group support.
        }
      }
      try {
        child.kill(signal);
      } catch {
        // The close/error handlers, or the forced timeout resolver, will finish the result.
      }
    };

    const settle = (payload) => {
      if (settled) return;
      settled = true;
      clearTimers();
      resolve(payload);
    };

    const timeoutMs = options.timeout || DEFAULT_TIMEOUT_MS;
    const killGraceMs = options.timeoutKillGraceMs ?? 1000;
    const forceResolveGraceMs = options.timeoutForceResolveGraceMs ?? 250;

    timeoutTimer = setTimeout(() => {
      if (settled) return;
      timedOut = true;
      killChild("SIGTERM");
      killTimer = setTimeout(() => {
        if (settled) return;
        killChild("SIGKILL");
        forceResolveTimer = setTimeout(() => {
          const output = String(stdout || "").trim();
          settle({
            status: 1,
            stdout: output,
            stderr: `${stderr}${stderr ? "\n" : ""}terminated by SIGKILL after timeout`,
            error: "ETIMEDOUT",
            errorCode: "ETIMEDOUT",
            timedOut: true,
            provider: invocation.preflight
          });
        }, forceResolveGraceMs);
      }, killGraceMs);
    }, timeoutMs);
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.stdin.end();
    child.on("error", (error) => {
      settle({
        status: 1,
        stdout,
        stderr,
        error: timedOut ? "ETIMEDOUT" : String(error.message || error),
        errorCode: timedOut ? "ETIMEDOUT" : String(error.code || ""),
        timedOut,
        provider: invocation.preflight
      });
    });
    child.on("close", (status, signal) => {
      const output = String(stdout || "").trim();
      settle({
        status: status ?? 1,
        stdout: output,
        stderr: signal ? `${stderr}${stderr ? "\n" : ""}terminated by ${signal}` : stderr,
        error: timedOut ? "ETIMEDOUT" : "",
        errorCode: timedOut ? "ETIMEDOUT" : "",
        timedOut,
        provider: invocation.preflight
      });
    });
  });
}
