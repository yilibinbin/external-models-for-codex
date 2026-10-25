import fs from "node:fs";
import path from "node:path";
import process from "node:process";

function isInside(root, target) {
  const relative = path.relative(root, target);
  return relative === "" || (!path.isAbsolute(relative) && relative !== ".." && !relative.startsWith(`..${path.sep}`));
}

// npm's Windows shims are scripts, not CreateProcess executables. Resolve only
// their adjacent, identified Gemini package; never evaluate the shim as a shell.
export function normalizeWindowsGeminiInvocation(command, args = [], options = {}) {
  if ((options.platform ?? process.platform) !== "win32"
      || !path.isAbsolute(command)
      || !/^gemini(?:\.cmd|\.bat|\.ps1)?$/i.test(path.basename(command))) {
    return { command, args };
  }
  const packageRoot = fs.realpathSync(path.join(path.dirname(command), "node_modules", "@google", "gemini-cli"));
  const cwd = fs.realpathSync(options.cwd ?? process.cwd());
  if (isInside(cwd, packageRoot)) {
    throw new Error("Refusing to launch a Gemini CLI package inside the reviewed workspace.");
  }
  const manifest = JSON.parse(fs.readFileSync(path.join(packageRoot, "package.json"), "utf8"));
  const bin = typeof manifest.bin === "string" ? manifest.bin : manifest.bin?.gemini;
  if (manifest.name !== "@google/gemini-cli" || typeof bin !== "string" || path.isAbsolute(bin)) {
    throw new Error("Windows Gemini launcher must resolve to the @google/gemini-cli package bin.");
  }
  const candidate = path.resolve(packageRoot, bin);
  if (!isInside(packageRoot, candidate)) {
    throw new Error("Gemini CLI bin escapes its package directory.");
  }
  const entryPoint = fs.realpathSync(candidate);
  if (!isInside(packageRoot, entryPoint) || !/\.(?:js|mjs|cjs)$/i.test(entryPoint)
      || !fs.statSync(entryPoint).isFile()) {
    throw new Error("Gemini CLI bin must be a JavaScript file inside its package directory.");
  }
  return { command: options.execPath ?? process.execPath, args: [entryPoint, ...args] };
}
