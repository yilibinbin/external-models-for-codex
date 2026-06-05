export const PROGRESS_EVENT_PREFIX = "[gemini-for-codex progress]";

export function formatProgressEvent(payload) {
  return `${PROGRESS_EVENT_PREFIX} ${JSON.stringify(payload)}\n`;
}

export function progressEventsFromStderr(stderr) {
  const summary = { events: [], malformedCount: 0, malformedPrefixCount: 0 };
  for (const line of String(stderr ?? "").split(/\r?\n/)) {
    if (!line) continue;
    if (line.startsWith(`${PROGRESS_EVENT_PREFIX} `)) {
      try {
        summary.events.push(JSON.parse(line.slice(PROGRESS_EVENT_PREFIX.length + 1)));
      } catch {
        summary.malformedCount += 1;
      }
    } else if (line.startsWith(PROGRESS_EVENT_PREFIX.slice(0, -1))) {
      summary.malformedPrefixCount += 1;
    }
  }
  return summary;
}
