#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import readline from "node:readline";
import process from "node:process";
import { fileURLToPath } from "node:url";

const MAX_BUFFER = 5 * 1024 * 1024;
const GIT_TIMEOUT_MS = 30 * 1000;
const SAFE_PATH = /^[A-Za-z0-9._/@:+-]+$/;
const SAFE_REF = /^[A-Za-z0-9._/@:+~^-]+$/;

const TOOLS = {
  git_status: { args: ["status", "--short", "--untracked-files=all"] },
  git_diff: { args: ["diff"], acceptsPaths: true },
  git_diff_cached: { args: ["diff", "--cached"], acceptsPaths: true },
  git_log: { args: ["log", "--oneline", "--decorate", "-n"], acceptsLimit: true },
  git_show: { args: ["show"], acceptsRef: true, acceptsPaths: true },
  git_blame: { args: ["blame"], acceptsPaths: true, requiresOnePath: true },
  git_grep: { args: ["grep", "-n"], acceptsPattern: true, acceptsPaths: true },
  git_ls_files: { args: ["ls-files"], acceptsPaths: true }
};

const CLI_ALIASES = new Map([
  ["status", "git_status"],
  ["diff", "git_diff"],
  ["diff-cached", "git_diff_cached"],
  ["ls-files", "git_ls_files"]
]);

class ValidationError extends Error {
  constructor(message) {
    super(message);
    this.name = "ValidationError";
  }
}

export function isSafeGitPath(value) {
  if (typeof value !== "string" || value.length === 0) return false;
  if (value.startsWith("-") || value.includes("\n") || value.includes("\r")) return false;
  if (!SAFE_PATH.test(value)) return false;
  return !value.split("/").some((part) => part === "..");
}

export function isSafeGitRef(value) {
  if (typeof value !== "string" || value.length === 0) return false;
  if (value.startsWith("-") || value.includes("\n") || value.includes("\r")) return false;
  return SAFE_REF.test(value);
}

function checkedPaths(paths = []) {
  if (!Array.isArray(paths)) throw new ValidationError("paths must be an array");
  for (const item of paths) {
    if (!isSafeGitPath(item)) throw new ValidationError(`Unsafe git path: ${item}`);
  }
  return paths;
}

function checkedRef(ref) {
  if (ref === undefined || ref === "") return "";
  if (!isSafeGitRef(ref)) throw new ValidationError(`Unsafe git ref: ${ref}`);
  return ref;
}

function checkedLimit(limit) {
  if (limit === undefined) return "20";
  const numeric = Number(limit);
  if (!Number.isInteger(numeric) || numeric < 1 || numeric > 100) {
    throw new ValidationError("limit must be an integer from 1 to 100");
  }
  return String(numeric);
}

function checkedPattern(pattern) {
  if (typeof pattern !== "string" || pattern.length === 0 || pattern.includes("\n") || pattern.includes("\r")) {
    throw new ValidationError("pattern must be a non-empty single-line string");
  }
  if (pattern.startsWith("-")) {
    throw new ValidationError("pattern must not start with '-'");
  }
  return pattern;
}

export function runGitTool(name, input = {}, cwd = process.cwd()) {
  const tool = TOOLS[name];
  if (!tool) throw new ValidationError(`Unknown git MCP tool: ${name}`);
  const args = [...tool.args];

  if (tool.acceptsLimit) args.push(checkedLimit(input.limit));
  if (tool.acceptsRef) {
    const ref = checkedRef(input.ref || "HEAD");
    if (ref) args.push(ref);
  }
  if (tool.acceptsPattern) {
    args.push(checkedPattern(input.pattern));
  }

  const paths = checkedPaths(input.paths || []);
  if (paths.length && !tool.acceptsPaths) throw new ValidationError(`${name} does not accept paths`);
  if (tool.requiresOnePath && paths.length !== 1) throw new ValidationError(`${name} requires exactly one path`);
  if (paths.length) args.push("--", ...paths);

  const result = spawnSync("git", args, {
    cwd,
    encoding: "utf8",
    maxBuffer: MAX_BUFFER,
    timeout: GIT_TIMEOUT_MS,
    killSignal: "SIGKILL"
  });
  return {
    status: result.status ?? 1,
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    error: result.error ? String(result.error.message ?? result.error) : ""
  };
}

export function runReadOnlyGitCommand(name) {
  const toolName = CLI_ALIASES.get(name) || name;
  if (!TOOLS[toolName]) {
    return {
      status: 2,
      stdout: "",
      stderr: `Unsupported read-only git command "${name}".`,
      error: ""
    };
  }
  return runGitTool(toolName);
}

function toolList() {
  return Object.keys(TOOLS).map((name) => ({
    name,
    description: `Read-only ${name.replaceAll("_", " ")} inspection for the current Git repository.`,
    inputSchema: {
      type: "object",
      properties: {
        paths: { type: "array", items: { type: "string" } },
        ref: { type: "string" },
        pattern: { type: "string" },
        limit: { type: "integer", minimum: 1, maximum: 100 }
      },
      additionalProperties: false
    }
  }));
}

function send(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

function requestId(request) {
  if (request && typeof request === "object" && Object.hasOwn(request, "id")) return request.id;
  return null;
}

function isJsonRpcRequest(request) {
  return request !== null && typeof request === "object" && !Array.isArray(request) && typeof request.method === "string";
}

async function runServer() {
  const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  for await (const line of rl) {
    if (!line.trim()) continue;
    let request;
    try {
      try {
        request = JSON.parse(line);
      } catch {
        send({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } });
        continue;
      }
      if (!isJsonRpcRequest(request)) {
        send({ jsonrpc: "2.0", id: requestId(request), error: { code: -32600, message: "Invalid Request" } });
        continue;
      }
      if (request.method === "initialize") {
        send({
          jsonrpc: "2.0",
          id: request.id,
          result: {
            protocolVersion: "2024-11-05",
            capabilities: { tools: {} },
            serverInfo: { name: "claude-for-codex-git", version: "1.0.0" }
          }
        });
      } else if (request.method === "tools/list") {
        send({ jsonrpc: "2.0", id: request.id, result: { tools: toolList() } });
      } else if (request.method === "tools/call") {
        const name = request.params?.name;
        const args = request.params?.arguments || {};
        const result = runGitTool(name, args);
        send({
          jsonrpc: "2.0",
          id: request.id,
          result: { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] }
        });
      } else {
        send({ jsonrpc: "2.0", id: request.id, error: { code: -32601, message: `Unknown method ${request.method}` } });
      }
    } catch (error) {
      const code = error instanceof ValidationError ? -32602 : -32000;
      send({ jsonrpc: "2.0", id: requestId(request), error: { code, message: error.message || String(error) } });
    }
  }
}

function selftest() {
  return {
    safePath: {
      "file.txt": isSafeGitPath("file.txt"),
      "../secret": isSafeGitPath("../secret"),
      "-p": isSafeGitPath("-p"),
      "a;b": isSafeGitPath("a;b"),
      "a$(touch x)": isSafeGitPath("a$(touch x)"),
      "a\nb": isSafeGitPath("a\nb")
    },
    safeRef: {
      HEAD: isSafeGitRef("HEAD"),
      "main~1": isSafeGitRef("main~1"),
      "main;rm -rf /": isSafeGitRef("main;rm -rf /"),
      "--help": isSafeGitRef("--help")
    }
  };
}

if (fileURLToPath(import.meta.url) === process.argv[1]) {
  const mode = process.argv[2] || "server";
  if (mode === "selftest") {
    process.stdout.write(`${JSON.stringify(selftest(), null, 2)}\n`);
  } else if (mode === "server") {
    runServer().catch((error) => {
      process.stderr.write(`${error.stack || error.message || String(error)}\n`);
      process.exit(1);
    });
  } else {
    const result = runReadOnlyGitCommand(mode);
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
    process.exit(result.status === 0 ? 0 : 1);
  }
}
