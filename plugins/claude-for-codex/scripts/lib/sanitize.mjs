import os from "node:os";
import path from "node:path";
import { canonicalWorkspaceRoot } from "./workspace.mjs";

export const SECRET_PATTERNS = Object.freeze([
  { name: "private-key", pattern: /BEGIN (RSA|OPENSSH|EC|DSA)? ?PRIVATE KEY/g },
  { name: "github-token", pattern: /gh[pousr]_[A-Za-z0-9_]{20,}/g },
  { name: "openai-key", pattern: /sk-[A-Za-z0-9_-]{20,}/g },
  { name: "aws-access-key", pattern: /AKIA[0-9A-Z]{16}/g }
]);

export const SECRET_ASSIGNMENT_PATTERN = /\b(api[_-]?key|secret|token|password|passwd)\b\s*[:=]\s*["']([A-Za-z0-9_./+=:-]{16,})["']/ig;

const CONTROL_PATTERN = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g;
const ABSOLUTE_PATH_PATTERNS = [
  /\/Users\/[^\s'"<>]+/g,
  /\/home\/[^\s'"<>]+/g,
  /\/private\/tmp\/[^\s'"<>]+/g,
  /\/tmp\/[^\s'"<>]+/g,
  /[A-Za-z]:\\[^\s'"<>]+/g,
  /\\\\[^\s'"<>]+\\[^\s'"<>]+/g
];

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function redactLiteral(text, literal, replacement) {
  if (!literal) {
    return text;
  }
  return text.replace(new RegExp(escapeRegExp(literal), "g"), replacement);
}

function redactSecrets(text) {
  let redacted = text.replace(SECRET_ASSIGNMENT_PATTERN, "$1=<redacted-secret>");
  for (const { pattern } of SECRET_PATTERNS) {
    redacted = redacted.replace(pattern, "<redacted-secret>");
  }
  return redacted;
}

function redactPaths(text, cwd) {
  let redacted = text;
  redacted = redactLiteral(redacted, os.homedir(), "<redacted-home>");
  try {
    redacted = redactLiteral(redacted, canonicalWorkspaceRoot(cwd), "<redacted-workspace>");
  } catch {
    // Keep generic path redaction below if workspace detection fails.
  }
  for (const pattern of ABSOLUTE_PATH_PATTERNS) {
    redacted = redacted.replace(pattern, "<redacted-path>");
  }
  return redacted;
}

function capUtf8(text, maxBytes) {
  if (Buffer.byteLength(text, "utf8") <= maxBytes) {
    return text;
  }
  const marker = "...<truncated>";
  const budget = Math.max(0, maxBytes - Buffer.byteLength(marker, "utf8"));
  let output = "";
  for (const char of text) {
    const candidate = output + char;
    if (Buffer.byteLength(candidate, "utf8") > budget) {
      break;
    }
    output = candidate;
  }
  return output + marker;
}

export function stripControls(text) {
  return String(text ?? "").replace(CONTROL_PATTERN, "");
}

export function sanitizeSummary(text, options = {}) {
  const cwd = options.cwd ?? process.cwd();
  const maxBytes = options.maxBytes ?? 2048;
  const redacted = redactPaths(redactSecrets(String(text ?? "")), cwd);
  return capUtf8(stripControls(redacted), maxBytes);
}
