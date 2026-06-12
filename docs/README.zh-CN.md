# External Models for Codex 中文文档

External Models for Codex 是一个面向 Codex 的外部模型插件市场。它把不同模型提供商的本地 CLI 工作流放在同一个可安装 marketplace 中，同时保留每个 provider 独立的 plugin id、skills、hooks 和安全边界。

当前包含：

- Claude for Codex：让 Codex 调用本地 Claude Code CLI，获得独立审阅、规划、多角色复审、原生 SDK subagent 团队、救援诊断、结构化审阅输出、需要费用确认的 ultrareview 和可选 Stop Hook 门禁；同时包含 dynamic model alias registry、request-local outcome classification、低成本 `doctor --json` 诊断和 fork-safe CI dogfood。
- Gemini for Codex：让 Codex 调用 legacy Gemini CLI（`gemini`）做 Gemini-only 只读审阅、规划、救援诊断、结构化审阅输出和 Gemini CLI 原生 session 能力探测。
- Antigravity for Codex：让 Codex 调用 Google Antigravity CLI（`agy`）提供成熟的插件托管审阅工作流：只读审阅、对抗性审阅、规划、救援诊断、多角色审阅、结构化报告、角色包、后台任务、mailbox/leases、生命周期 hooks、GitHub Actions 工作流渲染、release checks、可选真实 smoke 和可选 Stop Hook 门禁，并显式选择 Gemini 或 Claude model provider。

Claude 自然语言路由规则：用户只需要正常表达“用 Claude 严格多角色发布审阅”或“用最强本地 Claude 审阅”这类意图，Codex 负责映射到 Claude for Codex 的 quality、model、effort、backend、role 和后台任务参数。普通的严格或强审阅表达不会自动选择 ultrareview；ultrareview 仍然必须显式请求，因为它可能使用远端/云端执行并产生 usage-credit 费用。

## 安装

从 GitHub 远端安装：

```bash
codex plugin marketplace add yilibinbin/external-models-for-codex --ref claude-for-codex-v0.18.2
codex plugin add claude-for-codex@external-models-for-codex

codex plugin marketplace add yilibinbin/external-models-for-codex --ref gemini-for-codex-v0.11.2
codex plugin add gemini-for-codex@external-models-for-codex

codex plugin marketplace add yilibinbin/external-models-for-codex --ref antigravity-for-codex-v0.5.4
codex plugin add antigravity-for-codex@external-models-for-codex
```

请为要安装的插件使用对应 provider 的不可变 release ref。仅在开发快照场景使用 `main`。

升级已有安装：

```bash
codex plugin marketplace upgrade external-models-for-codex
codex plugin remove claude-for-codex
codex plugin add claude-for-codex@external-models-for-codex
codex plugin remove gemini-for-codex
codex plugin add gemini-for-codex@external-models-for-codex
codex plugin remove antigravity-for-codex
codex plugin add antigravity-for-codex@external-models-for-codex
```

从 `0.4.0` 回滚：先用 `setup --disable-review-gate` 关闭审阅门禁，再移除或降级插件。如果 Codex Settings > Hooks 仍然保留指向缺失文件的 `SessionStart`、`SessionEnd`、`UserPromptSubmit` 或 `Stop` 信任项，请手动移除或禁用。

从本地仓库安装：

```bash
codex plugin marketplace add .
codex plugin add claude-for-codex@external-models-for-codex
codex plugin add gemini-for-codex@external-models-for-codex
codex plugin add antigravity-for-codex@external-models-for-codex
```

## 依赖

- 支持插件的 Codex CLI
- 本地可执行的 Claude Code CLI：`claude`、`CLAUDE_CODE_PATH` 指向的可执行文件，或 `~/.local/bin/claude`
- 可选的 `@anthropic-ai/claude-agent-sdk` 包，用于 Claude SDK 原生 subagent 模式；`@anthropic-ai/claude-code` 仍作为兼容 fallback
- Gemini for Codex 需要本地可执行的 Gemini CLI：`gemini`，可通过 `GEMINI_CLI_PATH` 指定
- Antigravity for Codex 需要本地可执行的 Google Antigravity CLI：`agy`，可通过 `AGY_CLI_PATH` 或 `ANTIGRAVITY_CLI_PATH` 指定
- Node.js 20 或更新版本
- 用于收集审阅上下文的 Git 仓库

运行检查：

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs setup
```

Claude CLI 查找顺序：

1. `CLAUDE_CODE_PATH` 指向的可执行文件。
2. 当前 `PATH` 中的 `claude`。
3. `~/.local/bin/claude`，用于覆盖 Codex Desktop `PATH` 未包含 `~/.local/bin` 的情况。

如果 `setup` 显示 `claudeAvailable: false`，但本机已安装 Claude，请将 `CLAUDE_CODE_PATH` 设置为 Claude 可执行文件的绝对路径。

Antigravity for Codex 模型选择：

1. `ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER=gemini` 是默认值。
2. `ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER=claude` 会显式选择 Antigravity 暴露的 Claude 模型。
3. `ANTIGRAVITY_FOR_CODEX_MODEL`、`ANTIGRAVITY_FOR_CODEX_GEMINI_MODEL`、`ANTIGRAVITY_FOR_CODEX_CLAUDE_MODEL` 和 `--model` 会按所选 provider 校验。GPT/OpenAI 标签会被拒绝。
4. Claude-through-Antigravity 是显式 Antigravity provider 选择，并且和 `claude-for-codex` 分离；它仍然调用 `agy`，不调用 Claude Code CLI 或 Claude SDK。

自然语言路由规则：用户只需要正常表达“用 Antigravity 严格多角色审阅”这类意图，Codex 负责映射到内部 provider/model 参数。默认使用 Gemini provider；只有用户明确要求“Antigravity 使用 Claude”或“Claude through Antigravity”时，才切换到 Claude provider。

工作流生成说明：`github-actions init` 会把所选 provider 持久化写入生成的 workflow。只有在你明确希望把该 provider 提交给团队共用时，才在生成 workflow 时使用 provider 环境变量；provider-specific model 默认值仍归运行时管理，除非显式设置 `ANTIGRAVITY_FOR_CODEX_MODEL`。

Legacy Gemini CLI 查找顺序：

1. `GEMINI_CLI_PATH` 指向的可执行文件。
2. 当前 `PATH` 中的 `gemini`。
3. 常见用户级和 JavaScript 工具链路径，包括 `~/.local/bin`、`~/bin`、npm global prefix、pnpm、Volta、asdf、bun、deno、nvm 和 fnm 路径。
4. 常见包管理器或系统路径，例如已配置的 Homebrew prefix、`/opt/homebrew/bin`、`/usr/local/bin` 和 `/usr/bin`。

## 能力

- `claude-review`：对当前 git 变更或分支 diff 执行只读 Claude 审阅。
- `claude-adversarial-review`：让 Claude 挑战方案假设、权衡、回滚路径和隐藏失败模式。
- `claude-plan`：让 Claude 给出独立实施计划，供 Codex 在编辑前对照和吸收。
- `claude-multi-review`：并行运行 correctness、security、tests、release、adversarial 多角色审阅。
- `claude-multi-review --backend sdk --agent-team sdk-subagents`：使用 Claude 原生 SDK subagents 运行多角色审阅团队。
- `claude-ultrareview`：只有在用户用 `--confirm-cost` 明确同意可能产生 usage-credit 费用后，才运行 Claude cloud ultrareview。
- `claude-rescue`：让 Claude 做只读故障诊断，或在显式 `--write` 时执行修复。
- `claude-status`、`claude-result`、`claude-cancel`：跟踪后台 Claude job。
- `claude-review-gate`：配置可选 Stop Hook 审阅门禁。
- `claude-github-actions-review`：生成或校验 fork-safe GitHub Actions PR 审阅工作流。
- `claude-collaboration-loop`：执行规划、对齐、实现、审阅、报告的 Codex-Claude 协作流程。
- `gemini-review`、`gemini-adversarial-review`、`gemini-plan`、`gemini-multi-review`、`gemini-rescue`：Gemini CLI 后端复审/规划能力；Gemini rescue 保持只读。`gemini-review --structured` 会验证 schema-backed findings，`gemini-multi-review` 默认并行运行角色 fan-out，Gemini CLI 专属 native agent/session flags 会按当前 CLI 能力探测启用。
- `gemini-mailbox`、`gemini-leases`：查看脱敏 Gemini 协作摘要和 advisory 路径关注 leases。
- `antigravity-review`、`antigravity-adversarial-review`、`antigravity-plan`、`antigravity-multi-review`、`antigravity-rescue`、`antigravity-review-gate`、`antigravity-github-actions-review`：Antigravity 后端的成熟插件托管审阅、规划、救援、Stop 门禁和 workflow 风险审阅，并显式选择 Gemini 或 Claude model provider。Antigravity for Codex 只调用 `agy`，不宣称 Claude SDK、Gemini native-agent 或 ultrareview 对等能力，并且 Claude-through-Antigravity 与 `claude-for-codex` 保持分离。

## Gemini for Codex

从同一个本地 marketplace 安装：

```bash
codex plugin marketplace add .
codex plugin add gemini-for-codex@external-models-for-codex
```

Gemini 审阅是 Gemini CLI-only，使用 legacy Gemini CLI headless JSON mode（`gemini --approval-mode=plan --output-format=json --prompt`）。它使用有界 inline git context，不依赖 Antigravity、Gemini MCP 或 Gemini extension。

`gemini-multi-review` 有两种多代理模式。默认模式会为每个选择的角色并行启动一个 Gemini CLI 审阅进程并汇总输出。使用 `--native-agents` 时，运行时会创建临时 Gemini subagent 定义，并要求 Gemini CLI 通过 `@gfc_<role>` 原生 subagents 执行对应角色审阅。

Gemini for Codex 现在也注册 SessionStart、SessionEnd、UserPromptSubmit 和 Stop hooks。Session hooks 会记录当前 Codex session、写入 turn baseline、提醒未读 Gemini job 结果，并且只清理明确同 session id 的 queued/running jobs。

需要结构化审阅时使用 `gemini-review --structured`。需要非交互式判断前台/后台时使用 `recommend-execution-mode`。`setup` 会报告本地 Gemini CLI 是否支持 `--resume`、`--session-id`、`--session-file`、`--list-sessions` 和 `--worktree`；不支持的显式请求会在调用 Gemini 前失败。

## 增强对抗性审阅

`claude-adversarial-review` 会要求 Claude 先识别作者意图，再用三个 lens 审阅：

- `skeptic`：挑战正确性、完整性、未证明假设和可破坏状态。
- `architect`：挑战结构适配性、边界、耦合和责任泄漏。
- `minimalist`：挑战必要性、复杂度、过早抽象和可删除工作。

输出必须包含：

- `## Intent`
- `## Verdict: PASS | CONTESTED | REJECT`
- `## Findings`
- `## What Went Well`
- `## Lead Judgment`

可以只选择部分 lens：

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs adversarial-review --adversarial-lenses skeptic,minimalist --base main
```

需要机器可解析输出时使用 `--json`，会验证 `{verdict, summary, findings, next_steps}`。长耗时任务可以在 `review`、`adversarial-review`、`multi-review`、`rescue` 上加 `--background`，之后用 `claude-result` 取回结果。`rescue --write` 是显式 opt-in，并会记录 git 指纹前后变化。

`review --json` 返回普通审阅的规范化对象，verdict 使用 `approve|needs-attention`。`multi-review --json` 返回一个聚合对象，保留 role 标记的 findings 和每个 role 的结果。`adversarial-review --json` 保留专用的 `PASS|CONTESTED|REJECT` verdict 语义。

在 `--json` 模式下，退出码表示命令和 JSON 解析是否成功；是否需要处理发现要读取返回体里的 `verdict`。

## Codex 转发式后台任务

`--background` 支持 Codex host-forwarded 路径：skill 先通过 `reserve-job` 预留任务，然后 Codex 只派发一个转发子代理执行返回的 `workerCommand`。子代理只运行 `run-reserved-job`，不重新审阅、不解释仓库、不改写上下文。返回的命令会携带显式 `--cwd`，因此即使转发 shell 从其他目录启动，也会 claim 正确 workspace 的任务状态。旧的 runtime detached 后台任务保留为兼容 fallback。

未被 claim 的 host-forwarded reservation 使用独立于直接 worker 启动清理的 claim deadline：默认 10 分钟，可用 `CLAUDE_FOR_CODEX_RESERVATION_CLAIM_MS=<毫秒>` 调整。等待中的 reservation 会占用 active job cap；高并发派发场景也可以调节 `CLAUDE_FOR_CODEX_MAX_ACTIVE_JOBS`。如果 `jobs` 显示 phase 为 `unsafe-child-identity`，表示插件发现一个没有保存身份信息的 live child PID，会保留容量直到你 inspect/cancel，避免误杀 PID 复用进程。如果 `jobs` 显示 `leaderless-liveness-inconclusive`，表示有界 `ps` 探测无法确认进程组是否还有 live member；负载较高的 CI runner 可用 `CLAUDE_FOR_CODEX_PS_TIMEOUT_MS=<毫秒>` 调整探测窗口。SDK 后端的后台和 reserved 任务会默认启用已脱敏的 stream progress，用于 `jobs`/`result` 的进度预览。

## MCP 支撑的只读 Git 审阅

只读 Claude 审阅会获得严格 MCP 配置。插件内置只读 Git MCP server，提供 status、diff、cached diff、log、show、blame、grep、ls-files 等只读能力，并在进入 Git 前校验 path/ref。`Bash`、`Edit`、`Write`、`MultiEdit` 仍然被禁用。CLI 只读审阅还会禁用 Claude Code slash commands、settings sources 和 session persistence，同时保留正常 Claude 认证路径。

`multi-review` 默认并行运行角色 reviewer。`adversarial-review --parallel` 会将 skeptic、architect、minimalist 等 lens 作为独立 Claude CLI reviewer 并行执行并聚合输出。需要逐个运行排查问题或降低速率压力时使用 `--sequential`。

Claude 原生 SDK 模式必须显式开启，并且在真实 SDK subagent smoke 稳定前保持实验性；默认仍是插件管理的 CLI `multi-review`。使用 `multi-review --backend sdk --agent-team sdk-subagents` 时，运行时会为所选审阅角色创建 Claude SDK native subagents。SDK 包优先解析 `@anthropic-ai/claude-agent-sdk`，同时保留 `@anthropic-ai/claude-code` 作为兼容 fallback。SDK subagents 是 fresh isolated context reviewers，会保留规范化 Claude model aliases 和长上下文 alias，并且不能调用嵌套 `Agent`、shell、workflow 或写工具。SDK 只读审阅会禁用 settings sources、skills、hooks、plugins 和 session persistence，同时保留正常 Claude 认证路径。组合使用 `--json --native-structured` 时，会请求 SDK schema-backed 聚合输出，其中 `role_results[].result.review` 必须是完整的逐角色审阅 JSON 对象，并由插件在本地校验。报告不会保存原始角色文本或原始 SDK `structured_output`。加上 `--stream-progress` 会输出脱敏后的流式进度，但不会打印原始 SDK chunk，也不会把原始 SDK message 写入报告。

SDK native subagent 结构化审阅使用嵌套的逐角色 review 对象，并且仍然是显式启用路径。默认审阅后端不变。

Claude for Codex 支持 `--quality auto|fast|standard|strong|max`。`auto` 是默认策略，会根据命令类型、JSON 输出、角色数量、风险角色、backend、SDK subagent team、语义上下文和 diff 大小评分。`fast` 映射到 `sonnet` 加 low effort，`standard` 映射到 `sonnet` 加 high effort，`strong` 映射到 `opus` 加 xhigh effort，`max` 映射到当前 Claude Code 暴露的最强本地 alias 加 max effort，优先 `best`，其次 `fable`，否则使用 `opus`。显式传入的 `--model` 和 `--effort` 永远优先。策略使用 Claude Code alias，而不是 `claude-opus-4-8` 这类具体模型 id；不会把 `ultracode` 作为 `--effort` 传出，`ultrareview` 仍然是单独的显式费用确认命令。共享 model alias registry 覆盖 `best`、`fable`、`opus`、`sonnet`、`haiku`、`opusplan`、`opus[1m]`、`sonnet[1m]` 和 `inherit` 等 alias，避免重复硬编码 allowlist。`review-gate` 默认最高保持 `standard`，除非用户手动显式运行 `review-gate --quality strong|max`。

Claude for Codex 诊断会报告运行中插件 manifest 与 Codex 已启用 registry entry 的安装一致性。review、multi-review、adversarial review、plan 和 rescue prompt 会自动包含来自 `CLAUDE.md`、`REVIEW.md`、`.claude/review.md`、`.claude/CLAUDE.md` 的有界 advisory 项目规则；symlink 和工作区外文件会被忽略。`capabilities --json` 会解释自适应 quality 路由为什么选择对应 model 和 effort。

### Fable / 顶级模型路由

Claude for Codex 将 `--quality max` 视为最强本地 Claude 档位。若当前 Claude Code 暴露顶级模型别名，运行时优先选择 `best`，其次选择 `fable`，否则回退到 `opus`。当 CLI backend 选择顶级模型且支持 `--fallback-model` 时，插件会自动追加 Claude Code 原生 fallback，除非用户已经显式提供 fallback。只有当本机 CLI help 明确支持 comma-separated fallback list 时才使用 `--fallback-model opus,sonnet`；否则使用 `--fallback-model opus`。这个 fallback 只覆盖 Claude Code 支持的模型不可用、过载或服务端错误场景，不覆盖认证、额度、账单、限流、网络或请求过大问题。

显式模型选择永远优先：

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs review \
  --model fable --effort max --scope branch --base origin/main --json
```

自然语言只有在用户明确提到 Fable、顶级、最强、max、不要省成本，或自动评分达到极高风险时才进入 `--quality max` 档；CLI runtime 再根据本机能力选择 `best`、`fable` 或 `opus`。普通“深度/严格”审阅仍保持 `--quality strong`，也就是 Opus。安装后的 Stop 钩子保持保守，不会自动使用 Fable。SDK backend 不会根据 CLI help 推断顶级模型可用性；如果希望 SDK subagents 使用顶级 alias，请显式传 `--model` 或设置 `CLAUDE_FOR_CODEX_TOP_MODEL`。

`ultrareview` 会转发到 Claude 原生 cloud ultrareview 命令。它不会被 hooks 或默认审阅路径自动调用；因为可能使用远端/cloud 执行并消耗 usage credits，所以必须传 `--confirm-cost`，或设置 `CLAUDE_FOR_CODEX_ALLOW_ULTRAREVIEW=1`，否则运行时会拒绝执行。

Claude reviewer role packs 是 `multi-review` 的内置审阅预设。可以用 `roles list`、`roles inspect <pack>` 和 `multi-review --role-pack <pack>` 选择 `minimal`、`release`、`security`、`default` 等预设。用户自定义 JSON pack 可以用 `roles validate <file>` 校验，但它们仅支持 validate/inspect，不能由审阅命令执行。Role packs 是插件管理的 reviewer 预设，不是 Claude 原生 subagents，也不能授予工具、shell、hooks、MCP servers、环境变量、backend mode 或写权限。

Mailbox 和 advisory leases 是可选协作元数据。`mailbox list|show|post` 只保存脱敏摘要，不保存 transcript。`leases list|claim|release` 只声明路径关注范围，不锁文件。Lease conflict 只是警告，不改变 review verdict，也不影响 `review-gate` 行为。

Claude 审阅输出是审阅材料，不是自动实施授权。报告时要保留文件路径、行号、role 名、uncertainty 标记和 residual risk；除非用户明确要求采纳并修复哪些发现，否则不要在同一步自动修复审阅发现。

## 正确调用方式

Claude for Codex 是 skills-and-hook 插件，不是 MCP/app tool 插件。因此 `tool_search` 查不到 `claude-for-codex` callable tool 是正常现象，不代表安装失败。

正确路线是通过 Codex skill 调用：

- `claude-for-codex:claude-review`
- `claude-for-codex:claude-adversarial-review`
- `claude-for-codex:claude-multi-review`
- `claude-for-codex:claude-ultrareview`
- `claude-for-codex:claude-rescue`
- `claude-for-codex:claude-status`
- `claude-for-codex:claude-result`
- `claude-for-codex:claude-cancel`
- `claude-for-codex:claude-plan`
- `claude-for-codex:claude-review-gate`
- `claude-for-codex:claude-collaboration-loop`

## Stop Hook 审阅门禁

Hook 文件随插件安装，但默认不启用。需要在受保护仓库中显式开启：

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs setup --enable-review-gate --review-gate-mode multi-role
```

关闭：

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs setup --disable-review-gate
```

安装或升级后，请在 Codex Settings > Hooks 中信任或启用 `Claude for Codex` hooks。若本地 Codex runtime 支持，`SessionStart`、`SessionEnd` 和 `UserPromptSubmit` 会用于记录会话、提示未读结果和保存 turn baseline。

Stop gate 使用 `UserPromptSubmit` 保存的 turn-baseline 指纹，避免当前轮没有改变工作区时反复审阅旧的脏变更。基于 Stop payload 区分 status/setup/report-only 轮次的逻辑会等到真实 Codex Stop payload 暴露可验证 edit/no-edit 信号后再启用。

## 安全边界

- 审阅流程以只读权限调用 Claude，并禁用 slash/settings/session 侧效应，同时显式拒绝写工具。
- 后台 job 状态保存在仓库外的插件数据目录。
- Codex 负责决定是否采纳 Claude 的发现。
- Stop gate 只在 Claude 明确返回 `BLOCK:` 时阻断。
- Claude 未安装、认证失败、限流、超时、输出无效或运行失败时默认 fail open，只输出警告，不阻断 Codex。

## 直接运行命令

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs review --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs adversarial-review --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs multi-review --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs multi-review --backend sdk --agent-team sdk-subagents --json --native-structured --stream-progress --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs ultrareview --confirm-cost --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs review --background --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs jobs
node plugins/claude-for-codex/scripts/claude-companion.mjs result <job-id>
node plugins/claude-for-codex/scripts/claude-companion.mjs capabilities
node plugins/claude-for-codex/scripts/claude-companion.mjs report --latest
node plugins/claude-for-codex/scripts/claude-companion.mjs release-check
node plugins/claude-for-codex/scripts/claude-companion.mjs release-check --ci-simulate
node plugins/claude-for-codex/scripts/claude-companion.mjs github-actions render
node plugins/claude-for-codex/scripts/claude-companion.mjs github-actions init --write
node plugins/claude-for-codex/scripts/claude-companion.mjs github-actions validate
node plugins/claude-for-codex/scripts/claude-companion.mjs plan "implement the feature and include tests"
node plugins/claude-for-codex/scripts/claude-companion.mjs status
```

`capabilities` 输出 Claude CLI flags、可选 SDK backend、native subagent 能力探测、Git/GitHub CLI、hooks、MCP 和可选语义 provider 诊断，但不会初始化 provider。`doctor --json` 是安装后优先使用的低成本健康检查，会报告 CLI、SDK、hook compatibility、review-gate state、state-file health 和 semantic-provider config，且不会运行 Claude prompt。CLI 仍是默认 backend；只有显式传 `--backend sdk` 或设置 `CLAUDE_FOR_CODEX_BACKEND=sdk` 时，才会在能保持只读工具和 Git MCP 边界的前提下使用 Claude SDK backend。SDK 原生 subagent 审阅还需要显式传 `--agent-team sdk-subagents`。语义上下文默认关闭；只有配置 repo 外 argv-array provider 后，才使用 `--semantic-context <provider>` 显式启用。语义上下文只是辅助信息，provider 失败会降低审阅可信度但不阻断普通审阅；如果 `review-gate` 的语义上下文失败，会记录 `DEGRADED_PASS` 等降级元数据。`report --latest` 读取 repo 外的脱敏报告；默认不保存 prompt、diff、模型原文、源码、环境变量、语义片段、SDK 原始消息、raw SDK events 或原始绝对工作区路径，但会保留 compact outcome classification metadata。`github-actions render|init|validate` 管理 GitHub Actions PR 审阅模板，默认使用不可变 release ref，不默认启用 `pull_request_target`，fork PR 会跳过 Claude、评论和 annotation，GitHub context 先映射到环境变量再进入 shell，评论会脱敏清洗，Checks annotations 需要显式开启。仓库自带 fork-safe CI dogfood workflow，会运行 syntax checks、focused pytest、`release-check --ci-simulate --json` 和 `git diff --check`。`release-check --ci-simulate` 离线验证这些 GitHub Actions 假设，不调用真实 GitHub API、不需要 secrets、不依赖用户 HOME 或本地 Codex cache。`release-check` 校验发布卫生，除非显式要求，否则跳过远端安装 smoke。
