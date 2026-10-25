import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { spawn, spawnSync } from "node:child_process";
import { test } from "node:test";
import { normalizeWindowsGeminiInvocation } from "../plugins/gemini-for-codex/scripts/lib/gemini-invocation.mjs";

function fixture(t, bin = "bundle/gemini.js") {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "gemini launcher 空间 "));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const prefix = path.join(root, "npm global 路径");
  const pkg = path.join(prefix, "node_modules", "@google", "gemini-cli");
  const cwd = path.join(root, "reviewed repo");
  fs.mkdirSync(path.dirname(path.join(pkg, bin)), { recursive: true });
  fs.mkdirSync(cwd);
  fs.writeFileSync(path.join(pkg, "package.json"), JSON.stringify({ name: "@google/gemini-cli", type: "module", bin: { gemini: bin } }));
  const entry = path.join(pkg, bin);
  fs.writeFileSync(entry, `if (process.argv.includes("--hang")) { setInterval(() => {}, 100); }
else { console.log(JSON.stringify({args:process.argv.slice(2),cwd:process.cwd(),env:process.env.GEMINI_LAUNCH_TEST})); process.exitCode=Number(process.env.GEMINI_TEST_EXIT || 0); }`);
  return { prefix, pkg, cwd, entry: fs.realpathSync(entry) };
}

for (const shim of ["gemini.cmd", "gemini"]) {
  for (const bin of ["dist/index.js", "bundle/gemini.js"]) {
    test(`Windows ${shim} resolves package-declared ${bin} without changing argv`, (t) => {
      const f = fixture(t, bin);
      const command = path.join(f.prefix, shim);
      fs.writeFileSync(command, "shim text is never executed");
      const args = Object.freeze(["--prompt", "空格 and spaces \"quotes\" & | % ! ^ $() `text`\nnext", "", "--model", "gemini-3.8-flash"]);
      const saved = [...args];
      const invocation = normalizeWindowsGeminiInvocation(command, args, { platform: "win32", cwd: f.cwd });
      assert.equal(invocation.command, process.execPath);
      assert.deepEqual(invocation.args, [f.entry, ...saved]);
      assert.deepEqual(args, saved);
      const result = spawnSync(invocation.command, invocation.args, { cwd: f.cwd, env: {...process.env, GEMINI_LAUNCH_TEST:"环境 value"}, encoding: "utf8", timeout: 5000 });
      assert.equal(result.status, 0, result.stderr);
      const received = JSON.parse(result.stdout);
      assert.deepEqual(received.args, saved);
      assert.equal(received.cwd, f.cwd);
      assert.equal(received.env, "环境 value");
    });
  }
}

test("asynchronous spawning preserves argv, cwd, environment and exit status", async (t) => {
  const f = fixture(t);
  const args = Object.freeze(["--prompt", "unicode 中文 ; & spaces"]);
  const invocation = normalizeWindowsGeminiInvocation(path.join(f.prefix, "gemini.cmd"), args, {platform:"win32", cwd:f.cwd});
  const child = spawn(invocation.command, invocation.args, {cwd:f.cwd, env:{...process.env, GEMINI_LAUNCH_TEST:"async", GEMINI_TEST_EXIT:"7"}});
  let output = "";
  child.stdout.on("data", x => output += x);
  const status = await new Promise((resolve, reject) => { child.on("error", reject); child.on("close", resolve); });
  assert.equal(status, 7);
  assert.deepEqual(JSON.parse(output).args, [...args]);
  assert.equal(JSON.parse(output).cwd, f.cwd);
  assert.equal(JSON.parse(output).env, "async");
});

test("synchronous timeouts remain effective", (t) => {
  const f = fixture(t);
  const invocation = normalizeWindowsGeminiInvocation(path.join(f.prefix, "gemini"), ["--hang"], {platform:"win32", cwd:f.cwd});
  const result = spawnSync(invocation.command, invocation.args, {cwd:f.cwd, timeout:150});
  assert.equal(result.error?.code, "ETIMEDOUT");
});

test("native executables and non-Windows invocations are unchanged", () => {
  const args = Object.freeze(["--version"]);
  for (const platform of ["linux", "darwin"]) {
    const result = normalizeWindowsGeminiInvocation("/usr/bin/gemini", args, {platform});
    assert.equal(result.command, "/usr/bin/gemini");
    assert.equal(result.args, args);
  }
  const native = path.resolve("gemini.exe");
  assert.deepEqual(normalizeWindowsGeminiInvocation(native, args, {platform:"win32"}), {command:native,args});
});

test("wrong package identity, path traversal and workspace packages are rejected", (t) => {
  const f = fixture(t);
  const command = path.join(f.prefix, "gemini.cmd");
  const manifest = path.join(f.pkg, "package.json");
  fs.writeFileSync(manifest, JSON.stringify({name:"wrong-package", bin:{gemini:"bundle/gemini.js"}}));
  assert.throws(() => normalizeWindowsGeminiInvocation(command, [], {platform:"win32",cwd:f.cwd}), /@google\/gemini-cli/);
  fs.writeFileSync(manifest, JSON.stringify({name:"@google/gemini-cli", bin:{gemini:"../../escape.js"}}));
  assert.throws(() => normalizeWindowsGeminiInvocation(command, [], {platform:"win32",cwd:f.cwd}), /escapes/);
  assert.throws(() => normalizeWindowsGeminiInvocation(command, [], {platform:"win32",cwd:f.prefix}), /inside the reviewed workspace/);
});

test("both installed runtime implementations route sync/async through the shared resolver", () => {
  const companion = fs.readFileSync(new URL("../plugins/gemini-for-codex/scripts/gemini-companion.mjs", import.meta.url), "utf8");
  const runtime = fs.readFileSync(new URL("../plugins/gemini-for-codex/scripts/lib/gemini-runtime.mjs", import.meta.url), "utf8");
  assert.match(companion, /return normalizeWindowsGeminiInvocation\(command, args\)/);
  assert.match(runtime, /return normalizeWindowsGeminiInvocation\(command, args\)/);
  assert.match(companion, /function runGemini\(args[\s\S]*?normalizePosixScriptInvocation\(geminiCommand\(\), args\)/);
  assert.match(companion, /function runGeminiAsync\(args[\s\S]*?normalizePosixScriptInvocation\(geminiCommand\(\), args\)/);
});
