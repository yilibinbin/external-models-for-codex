import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const THIS_DIR = path.dirname(fileURLToPath(import.meta.url));

function tmpRoot(env = process.env) {
  if (env.CLAUDE_PLUGIN_DATA) {
    return path.join(env.CLAUDE_PLUGIN_DATA, "tmp");
  }
  return path.join(os.tmpdir(), "claude-for-codex");
}

export function createGitMcpConfig(cwd = process.cwd(), env = process.env) {
  const root = tmpRoot(env);
  fs.mkdirSync(root, { recursive: true, mode: 0o700 });
  const configPath = path.join(root, `mcp-${process.pid}-${Date.now()}.json`);
  const serverPath = path.join(THIS_DIR, "mcp-git.mjs");
  const config = {
    mcpServers: {
      "claude-for-codex-git": {
        command: process.execPath,
        args: [serverPath, "server"],
        cwd,
        env: {
          PWD: cwd
        }
      }
    }
  };

  fs.writeFileSync(configPath, `${JSON.stringify(config, null, 2)}\n`, {
    encoding: "utf8",
    mode: 0o600
  });

  return {
    configPath,
    cleanup() {
      try {
        fs.unlinkSync(configPath);
      } catch {
        // Best-effort cleanup: tests may intentionally keep the config for inspection.
      }
    }
  };
}
