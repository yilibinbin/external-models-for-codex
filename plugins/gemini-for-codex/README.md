# Gemini for Codex

Codex plugin that invokes the local Gemini CLI for independent read-only review, adversarial review, implementation planning, rescue diagnosis, tracked background jobs, and an opt-in Stop hook gate.

## Requirements

- Codex with plugin support
- Gemini CLI available as `gemini`, or set `GEMINI_CLI_PATH`
- Node.js 18 or newer
- Git repository for review context collection

## Install

```bash
codex plugin marketplace add .
codex plugin add gemini-for-codex@external-models-for-codex-local
```

`external-models-for-codex-local` is the local marketplace for this repository's Codex plugins that connect to external model CLIs. It currently publishes both Claude for Codex and Gemini for Codex.

## Runtime Safety

Gemini review runs in headless JSON mode with:

```bash
gemini --approval-mode=plan --output-format=json --prompt
```

v0.1.0 sends bounded inline git context and does not depend on Gemini MCP or a Gemini extension. Gemini extension and MCP support are deferred until their current CLI configuration path is validated.

## Commands

- `setup`: report Gemini, git, hook, and review-gate status; supports `--enable-review-gate` and `--disable-review-gate`.
- `review`: read-only review of current git changes or a branch diff.
- `adversarial-review`: skeptical multi-lens review.
- `multi-review`: role fan-out across correctness, security, tests, release, and adversarial review.
- `plan`: independent implementation plan for Codex to reconcile.
- `rescue`: read-only diagnosis for stuck implementation work.
- `jobs`, `result`, `cancel`: tracked job lifecycle.
- `review-gate`: internal Stop hook runner.

## Background Jobs

Use `--background` on long reviews. The skill reserves a job and dispatches exactly one forwarding child. Retrieve the result with:

```bash
gemini-result <job-id>
```

## Stop Hook

The Stop hook is installed but disabled by default. Enable it per repository with:

```bash
node plugins/gemini-for-codex/scripts/gemini-companion.mjs setup --enable-review-gate
```

Only explicit `BLOCK:` verdicts from Gemini emit Codex hook block JSON. Gemini CLI failures, auth failures, rate limits, timeouts, parse errors, and invalid gate output fail open with stderr diagnostics.

## Verification

```bash
python3 -m pytest tests/test_gemini_for_codex_plugin.py -q
node --check plugins/gemini-for-codex/scripts/gemini-companion.mjs
node --check plugins/gemini-for-codex/hooks/gemini-review-gate.mjs
```
