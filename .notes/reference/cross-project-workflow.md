# 跨项目参考工作流

> 目标：在需要借鉴其他 Agent 项目时，快速找到可复用设计点，并把结论沉淀到 `reference-index/`。
> 默认工具链：**CodeGraph + 源码读取**。Understand Anything 不进入日常流程；只有用户明确要求时临时使用。

## 流程总览

```text
① 定义具体问题 → ② 查已有 reference-index → ③ CodeGraph 定位代码 → ④ 读取最小源码 → ⑤ 写参考索引
```

## ① 定义问题

不要从“我要翻某个项目”开始，而是先写清楚当前设计问题。

```text
问题: SOC Agent 的高风险动作审批后如何进入执行层？
候选项目: claude-code-sourcemap, hermes-agent
预期产出: 权限/审批/执行边界的可复用设计点
```

只有当前方案还没定型，或者本仓库缺少足够参考时，才查外部项目。

常见触发点：

| 问题类型 | 优先参考 |
|---|---|
| tool/action permission、approval、HITL | `claude-code-sourcemap`、DeerFlow ACP permission |
| memory lifecycle、fact/lesson storage、回滚 | `claude-mem` |
| multi-agent orchestration、agent lifecycle、event stream | `hermes-agent`、`openclaw` |
| context compaction、long-running session | `claude-code-sourcemap`、`openclaw` |

## ② 先查已有索引

先看 `.notes/reference-index/` 有没有同类问题的结论。已有索引能回答的，不重新扫项目。

```text
.notes/reference-index/
├── soc-agent-engineering-contracts.md
├── memory-system-architecture.md
├── permission-decision-pipeline.md
└── context-compaction-strategy.md
```

## ③ CodeGraph 定位代码

优先用 Codex 已配置的 CodeGraph MCP。手动 CLI 只在 MCP 不方便时使用：

```bash
codegraph query -p /home/yydspei/projects/claude-code-sourcemap "permission approval tool"
codegraph context -p /home/yydspei/projects/hermes-agent "agent lifecycle event stream"
codegraph callers -p /home/yydspei/projects/claude-mem "MemoryManager"
```

规则：

- 先搜符号/模块，再读代码；不要从项目根目录盲扫。
- 只读取和问题直接相关的文件/函数。
- 参考项目只读不改。
- 如果 CodeGraph 报 `not initialized`，再考虑 `codegraph init <path>`。
- 本仓库 SOC 代码改动后继续执行 `codegraph sync .`，保证下一刀能查到新符号。

常用命令：

| 命令 | 用途 |
|---|---|
| `codegraph status -p <path>` | 查看索引状态 |
| `codegraph sync -p <path>` | 同步索引变化 |
| `codegraph query -p <path> "keyword"` | 搜索符号/类/函数 |
| `codegraph context -p <path> "task"` | 为某个问题生成相关上下文 |
| `codegraph callers -p <path> "Symbol"` | 查谁调用某个符号 |
| `codegraph callees -p <path> "Symbol"` | 查某个符号调用谁 |
| `codegraph impact -p <path> "Symbol"` | 分析修改影响面 |

## ④ 读取最小源码

CodeGraph 返回候选文件/符号后，再读取最小必要代码片段确认：

- public interface / 类型签名
- 权限和错误语义
- 状态流转
- 审计/日志/恢复机制
- 测试如何覆盖

不要复制参考项目代码；只复用设计思想，在本仓库按 SOC Agent 契约重写。

## ⑤ 写参考索引

查完必须写入 `.notes/reference-index/`。没有沉淀的跨项目发现，不作为长期决策依据。

模板：

```markdown
# permission-decision-pipeline.md

| 问题 | 参考项目/位置 | 采用点 | 未采用点 |
|---|---|---|---|
| 高风险动作审批 | claude-code-sourcemap: path/to/file.ts:123 | 审批和执行 token 分离 | 不采用其 UI 交互细节 |
```

## Understand Anything

默认不使用 Understand Anything。原因：它对长流程和图谱更新消耗较高，且 scoped 增量存在路径作用域问题。

保留规则：

- 项目顶层 `.understand-anything` 是 DeerFlow 全仓静态快照，可作为人工追溯参考，但不再更新。
- 参考项目的 `.understand-anything` 也按静态快照看待，不再更新。
- 只有用户明确要求“使用 Understand”时，才临时使用相关 skill。
- 临时使用后的结论仍必须经过 CodeGraph/源码确认，不能直接把图谱摘要当代码事实。

旧的 Understand-heavy 流程已归档：

```text
.notes/archive/reference/cross-project-workflow-understand-heavy.md
```

## 三条铁律

1. **问题驱动**：先定义问题，再决定是否查参考项目。
2. **CodeGraph 优先**：先定位符号和调用关系，再读最小源码。
3. **查完写索引**：采用点和拒绝点必须进入 `reference-index/`。

## 参考项目

| 项目 | 路径 | 用途 |
|---|---|---|
| claude-code-sourcemap | `/home/yydspei/projects/claude-code-sourcemap` | Claude Code 源码，Agent 架构设计模式 |
| claude-mem | `/home/yydspei/projects/claude-mem` | 记忆系统实现参考 |
| hermes-agent | `/home/yydspei/projects/hermes-agent` | Hermes Agent 框架，语言模型交互模式 |
| openclaw | `/home/yydspei/projects/openclaw` | Personal AI Assistant，多平台 agent 参考 |
