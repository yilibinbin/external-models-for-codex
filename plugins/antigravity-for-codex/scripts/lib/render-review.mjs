export function renderPullRequestComment({ command, provider, model, body } = {}) {
  return [
    "## Antigravity for Codex Review",
    "",
    `- Command: ${command || "review"}`,
    `- Provider: ${provider || "gemini"}`,
    `- Model: ${model || ""}`,
    "",
    String(body || "").trim()
  ].join("\n");
}

function normalizeLine(value) {
  const line = Number(value);
  return Number.isInteger(line) && line > 0 ? line : 1;
}

function annotationLevel(severity) {
  const value = String(severity || "").trim().toLowerCase();
  return value === "high" || value === "critical" || value === "blocker" ? "failure" : "warning";
}

export function renderGithubAnnotations(findings) {
  return (Array.isArray(findings) ? findings : [])
    .map((finding) => {
      const startLine = normalizeLine(finding.line || finding.start_line);
      const endLine = Math.max(startLine, normalizeLine(finding.end_line || finding.line || finding.start_line));
      return {
        path: finding.path || finding.file || "",
        start_line: startLine,
        end_line: endLine,
        annotation_level: annotationLevel(finding.severity),
        message: finding.message || finding.summary || "Antigravity finding"
      };
    })
    .filter((item) => item.path);
}
