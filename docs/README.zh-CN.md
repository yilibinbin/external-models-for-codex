# Claude for Codex 中文文档

Claude for Codex 是一个 Codex 插件，用于让 Codex 调用本地 Claude Code CLI，获得独立的第二模型审阅和规划能力。

## 安装

从 GitHub 远端安装：

```bash
codex plugin marketplace add yilibinbin/claude-for-codex --ref main
codex plugin add claude-for-codex@claude-for-codex-local
```

升级已有安装：

```bash
codex plugin marketplace upgrade claude-for-codex-local
codex plugin remove claude-for-codex
codex plugin add claude-for-codex@claude-for-codex-local
```

从本地仓库安装：

```bash
codex plugin marketplace add .
codex plugin add claude-for-codex@claude-for-codex-local
```

## 依赖

- 支持插件的 Codex CLI
- 本地可执行的 Claude Code CLI：`claude`
- Node.js 18 或更新版本
- 用于收集审阅上下文的 Git 仓库

运行检查：

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs setup
```

## 能力

- `claude-review`：对当前 git 变更或分支 diff 执行只读 Claude 审阅。
- `claude-adversarial-review`：让 Claude 挑战方案假设、权衡、回滚路径和隐藏失败模式。
- `claude-plan`：让 Claude 给出独立实施计划，供 Codex 在编辑前对照和吸收。
- `claude-multi-review`：按 correctness、security、tests、release、adversarial 多角色顺序审阅。
- `claude-review-gate`：配置可选 Stop Hook 审阅门禁。
- `claude-collaboration-loop`：执行规划、对齐、实现、审阅、报告的 Codex-Claude 协作流程。

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

## 正确调用方式

Claude for Codex 是 skills-and-hook 插件，不是 MCP/app tool 插件。因此 `tool_search` 查不到 `claude-for-codex` callable tool 是正常现象，不代表安装失败。

正确路线是通过 Codex skill 调用：

- `claude-for-codex:claude-review`
- `claude-for-codex:claude-adversarial-review`
- `claude-for-codex:claude-multi-review`
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

安装或升级后，请在 Codex Settings > Hooks 中信任或启用 `Claude for Codex` Stop hook。

## 安全边界

- 审阅流程以只读权限调用 Claude。
- Codex 负责决定是否采纳 Claude 的发现。
- Stop gate 只在 Claude 明确返回 `BLOCK:` 时阻断。
- Claude 未安装、认证失败、限流、超时、输出无效或运行失败时默认 fail open，只输出警告，不阻断 Codex。

## 直接运行命令

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs review --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs adversarial-review --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs multi-review --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs plan "implement the feature and include tests"
node plugins/claude-for-codex/scripts/claude-companion.mjs status
```
