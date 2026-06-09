import { sanitizeSummary } from "./sanitize.mjs";

export const CLAUDE_PROGRESS_EVENT_PREFIX = "[claude-for-codex progress]";
export const PROGRESS_PREFIX = `${CLAUDE_PROGRESS_EVENT_PREFIX} `;

export function formatProgressEvent(event, options = {}) {
  const cwd = options.cwd ?? process.cwd();
  const payload = {
    phase: sanitizeSummary(event?.phase ?? "", { cwd, maxBytes: 80 }),
    message: sanitizeSummary(event?.message ?? "", { cwd, maxBytes: 512 }),
    role: sanitizeSummary(event?.role ?? "", { cwd, maxBytes: 80 }),
    at: event?.at ?? new Date().toISOString()
  };
  return `${PROGRESS_PREFIX}${JSON.stringify(payload)}\n`;
}

export function progressEventsFromLines(lines) {
  const events = [];
  let malformedCount = 0;
  let malformedPrefixCount = 0;
  for (const line of lines) {
    const text = String(line ?? "");
    if (!text.startsWith(PROGRESS_PREFIX)) {
      if (text.startsWith("[claude-for-codex progress")) {
        malformedPrefixCount += 1;
      }
      continue;
    }
    const raw = text.slice(PROGRESS_PREFIX.length);
    try {
      events.push(JSON.parse(raw));
    } catch {
      malformedCount += 1;
    }
  }
  return { events, malformedCount, malformedPrefixCount };
}
