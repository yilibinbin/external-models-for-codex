# Claude for Codex 中文文档

Claude for Codex 是一个 Codex 插件，用于让 Codex 调用本地 Claude Code CLI，获得独立的第二模型审阅和规划能力。

Gemini for Codex 是同仓库的姊妹 Codex 插件，用于让 Codex 调用本地 Gemini CLI 做独立只读审阅和规划。它使用 Gemini plan mode 和有界 inline git context。

## 安装

从 GitHub 远端安装：

```bash
codex plugin marketplace add yilibinbin/claude-for-codex --ref main
codex plugin add claude-for-codex@external-models-for-codex-local
codex plugin add gemini-for-codex@external-models-for-codex-local
```

升级已有安装：

```bash
codex plugin marketplace upgrade external-models-for-codex-local
codex plugin remove claude-for-codex
codex plugin add claude-for-codex@external-models-for-codex-local
```

从 `0.4.0` 回滚：先用 `setup --disable-review-gate` 关闭审阅门禁，再移除或降级插件。如果 Codex Settings > Hooks 仍然保留指向缺失文件的 `SessionStart`、`SessionEnd`、`UserPromptSubmit` 或 `Stop` 信任项，请手动移除或禁用。

从本地仓库安装：

```bash
codex plugin marketplace add .
codex plugin add claude-for-codex@external-models-for-codex-local
codex plugin add gemini-for-codex@external-models-for-codex-local
```

## 依赖

- 支持插件的 Codex CLI
- 本地可执行的 Claude Code CLI：`claude`、`CLAUDE_CODE_PATH` 指向的可执行文件，或 `~/.local/bin/claude`
- Gemini for Codex 需要本地可执行的 Gemini CLI：`gemini`，或通过 `GEMINI_CLI_PATH` 指定
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

Gemini CLI 查找顺序：

1. `GEMINI_CLI_PATH` 指向的可执行文件。
2. 当前 `PATH` 中的 `gemini`。
3. 常见用户级和 JavaScript 工具链路径，包括 `~/.local/bin`、`~/bin`、npm global prefix、pnpm、Volta、asdf、bun、deno、nvm 和 fnm 路径。
4. 常见包管理器或系统路径，例如已配置的 Homebrew prefix、`/opt/homebrew/bin`、`/usr/local/bin` 和 `/usr/bin`。

## 能力

- `claude-review`：对当前 git 变更或分支 diff 执行只读 Claude 审阅。
- `claude-adversarial-review`：让 Claude 挑战方案假设、权衡、回滚路径和隐藏失败模式。
- `claude-plan`：让 Claude 给出独立实施计划，供 Codex 在编辑前对照和吸收。
- `claude-multi-review`：并行运行 correctness、security、tests、release、adversarial 多角色审阅。
- `claude-rescue`：让 Claude 做只读故障诊断，或在显式 `--write` 时执行修复。
- `claude-status`、`claude-result`、`claude-cancel`：跟踪后台 Claude job。
- `claude-review-gate`：配置可选 Stop Hook 审阅门禁。
- `claude-collaboration-loop`：执行规划、对齐、实现、审阅、报告的 Codex-Claude 协作流程。
- `gemini-review`、`gemini-adversarial-review`、`gemini-plan`、`gemini-multi-review`、`gemini-rescue`：对应的 Gemini CLI 复审/规划能力；Gemini rescue 保持只读。`gemini-review --structured` 会验证 schema-backed findings，`gemini-multi-review` 默认并行运行角色 fan-out，也支持 `--native-agents`，Gemini 原生 session flags 会按当前 CLI 能力探测启用。

## Gemini for Codex

从同一个本地 marketplace 安装：

```bash
codex plugin marketplace add .
codex plugin add gemini-for-codex@external-models-for-codex-local
```

Gemini 审阅使用 `gemini --approval-mode=plan --output-format=json --prompt`。它使用有界 inline git context，不依赖 Gemini MCP 或 Gemini extension。

`gemini-multi-review` 有两种多代理模式。默认模式会为每个选择的角色并行启动一个 Gemini CLI 审阅进程并汇总输出。使用 `--native-agents` 时，运行时会创建临时 Gemini subagent 定义，并要求 Gemini CLI 通过 `@gfc_<role>` 原生 subagents 执行对应角色审阅。

Gemini for Codex 现在也注册 SessionStart、SessionEnd、UserPromptSubmit 和 Stop hooks。Session hooks 会记录当前 Codex session、写入 turn baseline、提醒未读 Gemini job 结果，并且只清理明确同 session id 的 queued/running jobs。

需要结构化审阅时使用 `gemini-review --structured`。需要非交互式判断前台/后台时使用 `recommend-execution-mode`。`setup` 会报告当前 Gemini CLI 是否支持 `--resume`、`--session-id`、`--session-file`、`--list-sessions` 和 `--worktree`；不支持的显式请求会在调用 Gemini 前失败。

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

`--background` 支持 Codex host-forwarded 路径：skill 先通过 `reserve-job` 预留任务，然后 Codex 只派发一个转发子代理执行返回的 `workerCommand`。子代理只运行 `run-reserved-job`，不重新审阅、不解释仓库、不改写上下文。旧的 runtime detached 后台任务保留为兼容 fallback。

## MCP 支撑的只读 Git 审阅

只读 Claude 审阅会获得严格 MCP 配置。插件内置只读 Git MCP server，提供 status、diff、cached diff、log、show、blame、grep、ls-files 等只读能力，并在进入 Git 前校验 path/ref。`Bash`、`Edit`、`Write`、`MultiEdit` 仍然被禁用。

`multi-review` 默认并行运行角色 reviewer。`adversarial-review --parallel` 会将 skeptic、architect、minimalist 等 lens 作为独立 Claude CLI reviewer 并行执行并聚合输出。需要逐个运行排查问题或降低速率压力时使用 `--sequential`。

Claude 审阅输出是审阅材料，不是自动实施授权。报告时要保留文件路径、行号、role 名、uncertainty 标记和 residual risk；除非用户明确要求采纳并修复哪些发现，否则不要在同一步自动修复审阅发现。

## 正确调用方式

Claude for Codex 是 skills-and-hook 插件，不是 MCP/app tool 插件。因此 `tool_search` 查不到 `claude-for-codex` callable tool 是正常现象，不代表安装失败。

正确路线是通过 Codex skill 调用：

- `claude-for-codex:claude-review`
- `claude-for-codex:claude-adversarial-review`
- `claude-for-codex:claude-multi-review`
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

- 审阅流程以只读权限调用 Claude。
- 后台 job 状态保存在仓库外的插件数据目录。
- Codex 负责决定是否采纳 Claude 的发现。
- Stop gate 只在 Claude 明确返回 `BLOCK:` 时阻断。
- Claude 未安装、认证失败、限流、超时、输出无效或运行失败时默认 fail open，只输出警告，不阻断 Codex。

## 直接运行命令

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs review --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs adversarial-review --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs multi-review --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs review --background --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs jobs
node plugins/claude-for-codex/scripts/claude-companion.mjs result <job-id>
node plugins/claude-for-codex/scripts/claude-companion.mjs plan "implement the feature and include tests"
node plugins/claude-for-codex/scripts/claude-companion.mjs status
```
