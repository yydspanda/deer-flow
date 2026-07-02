# 跨项目开发工作流

> 目标是参考其他开源 Agent 项目来增强 deer-flow SOC Agent 的设计。
> 核心原则：**问题驱动，不项目驱动**。查完立刻写索引。

## 流程总览

```
① 定义具体问题 → ② Understand Anything 导航定位符号 → ③ CodeGraph 拉代码 → ④ 写参考索引
```

## ① 定义问题

不要想「我要去翻 claude-code-sourcemap」，而是想「我要解决什么问题」。

```
问题: 如何设计 Agent 的 Tool 生命周期管理？
候选项目: claude-code-sourcemap, hermes-agent
```

## ② 层一：Understand Anything — 全景导航

对候选项目生成知识图谱（一次性的，结果持久化在 `.understand-anything/knowledge-graph.json`）。Understand Anything 在 Codex 中是 slash/skill 工作流，不是普通 shell 命令：

```text
/understand /home/yydspei/projects/claude-code-sourcemap --language zh
/understand /home/yydspei/projects/hermes-agent --language zh
```

SOC Agent 本项目建议使用 scoped graph，而不是在 root graph 过期时强行增量：

```text
$understand-anything:understand /home/yydspei/projects/deer-flow/backend/soc_agent --full --language zh
```

使用规则：

- `understand-chat` / `understand-explain` 前先看对应 `.understand-anything/meta.json`，确认图谱覆盖当前问题涉及的代码。
- 如果 root graph 早于 SOC Agent 新增代码，不能用它回答 SOC 代码落点；先跑 SOC scoped rebuild，或明确记录“图谱过期，改用 CodeGraph”。
- 如果变更集中在 `backend/soc_agent/**`，优先刷新 SOC scoped graph；只有跨 DeerFlow core/frontend/Gateway 架构变化时才考虑 root graph full rebuild。
- 不为了一个局部函数改动启动 full Understand；局部落点继续用 CodeGraph。

启动 Dashboard 交互浏览：

```text
/understand-dashboard /home/yydspei/projects/claude-code-sourcemap
```

功能选择：

| 功能 | 什么时候用 | 示例 |
|---|---|---|
| `/understand <path>` | 第一次理解项目、生成/更新架构知识图谱 | `/understand /home/yydspei/projects/hermes-agent --language zh` |
| `/understand-dashboard <path>` | 用浏览器可视化架构层级、依赖、tour、diff/domain 图 | `/understand-dashboard /home/yydspei/projects/hermes-agent` |
| `/understand-chat <问题>` | 基于已有图谱问架构问题，不想重新扫代码 | `/understand-chat Hermes Agent 的 memory 生命周期是什么？` |
| `/understand-explain <文件或符号>` | 深入解释某个文件、函数、模块及上下游关系 | `/understand-explain restored-src/src/Tool.ts:buildTool` |
| `/understand-diff` | 分析当前 git diff/PR 影响面、风险、受影响组件 | `/understand-diff` |
| `/understand-domain [--full]` | 抽取业务域、业务流程、步骤，生成 domain flow graph | `/understand-domain --full` |
| `/understand-onboard` | 基于图谱生成新人 onboarding 文档 | `/understand-onboard` |
| `/understand-knowledge <wiki-dir>` | 分析 Karpathy-style LLM wiki 知识库，生成知识图谱 | `/understand-knowledge .notes` |

常用参数：

| 参数 | 用途 |
|---|---|
| `--full` | 忽略已有图谱，完整重建 |
| `--language zh` | 生成中文摘要、标题、标签 |
| `--auto-update` / `--no-auto-update` | 开启/关闭提交后的自动更新配置 |
| `--review` | 对已有图谱运行 LLM reviewer |

目标：看架构分层图 → 找到目标模块的**具体文件和符号名**。例如：

- 找到 Tool 系统在 `restored-src/src/Tool.ts`，关键函数是 `buildTool()`、类型是 `ToolDef`

## ③ 层二：CodeGraph — 精确读代码

用上一步拿到的符号名，优先通过 Codex 已配置的 CodeGraph MCP 查询。需要手动跑 shell 命令时，使用新版 CodeGraph CLI 的 `-p/--path` 指定跨项目路径：

```
codegraph context -p /home/yydspei/projects/claude-code-sourcemap "Tool buildTool ToolDef checkPermissions isEnabled call"
codegraph query -p /home/yydspei/projects/hermes-agent "tool register execute lifecycle"
codegraph callers -p /home/yydspei/projects/claude-mem "MemoryManager"
```

关键：

- 只看返回的代码，不直接 `ls`/`cat` 参考项目文件
- 不打开参考项目的编辑器（防止 IDE 索引混淆 + 手滑改错）
- 如果报 `not initialized`，去那个项目跑一次 `codegraph init`
- 已验证命令格式：`codegraph query -p /home/yydspei/projects/deer-flow "memory" --limit 3`

常用命令：

| 命令 | 用途 |
|---|---|
| `codegraph init <path>` | 初始化并索引项目，生成 `.codegraph/` |
| `codegraph status -p <path>` | 查看索引状态 |
| `codegraph sync -p <path>` | 手动同步索引变化 |
| `codegraph query -p <path> "keyword"` | 搜索符号/类/函数 |
| `codegraph context -p <path> "task"` | 为某个问题生成相关上下文 |
| `codegraph callers -p <path> "Symbol"` | 查谁调用了某个符号 |
| `codegraph callees -p <path> "Symbol"` | 查某个符号调用了谁 |
| `codegraph impact -p <path> "Symbol"` | 分析修改某个符号的影响范围 |
| `codegraph affected -p <path> <files...>` | 根据变更文件找可能受影响测试 |

## ④ 写参考索引

每次查完记录到 `.notes/reference-index/` 下。格式：

```markdown
# tool-lifecycle-design.md

| 问题 | 最佳参考位置 | 要点 |
|---|---|---|
| Tool 生命周期管理 | claude-code-sourcemap: restored-src/src/Tool.ts:721-792 | `buildTool()` 合并默认值，`ToolDef` 支持按需覆写 |
| Tool 权限检查 | claude-code-sourcemap: restored-src/src/hooks/useCanUseTool.tsx:28-191 | 多层决策链：rule→mode→classifier→hook→dialog |
```

下次同类问题直接翻索引，不用重新查。

## 多项目参考索引示例

```
.notes/reference-index/
├── tool-lifecycle-design.md
├── memory-system-architecture.md
├── agent-execution-loop.md
├── permission-decision-pipeline.md
└── context-compaction-strategy.md
```

每个文件一个主题，记录各项目的**最佳实现位置**（文件+行号+要点），不追求全面对比。

## ⑤ 什么时候参考其他项目

参考项目不是每个切片都要查。只有当前问题需要跨项目设计判断时才查，例如：

| 问题类型 | 优先参考 |
|---|---|
| tool/action permission、approval、HITL | `claude-code-sourcemap`、DeerFlow ACP permission |
| memory lifecycle、fact/lesson storage、回滚 | `claude-mem` |
| multi-agent orchestration、agent lifecycle、event stream | `hermes-agent`、`openclaw` |
| context compaction、long-running session | `claude-code-sourcemap`、`openclaw` |

查完必须写入 `.notes/reference-index/`，至少包含：

```markdown
| 问题 | 参考项目/位置 | 采用点 | 未采用点 |
|---|---|---|---|
```

没有记录到 reference-index 的跨项目发现，不能作为长期决策依据。

## 三条铁律

1. **先定义问题，再选项目查** — 不要打开项目从头扫
2. **Understand Anything 看全局，CodeGraph 看细节** — 不要反过来
3. **查完立刻写索引** — 过两天你会忘

## 参考项目

| 项目 | 路径 | 用途 |
|---|---|---|
| claude-code-sourcemap | `/home/yydspei/projects/claude-code-sourcemap` | Claude Code 源码，Agent 架构设计模式 |
| claude-mem | `/home/yydspei/projects/claude-mem` | 记忆系统实现参考 |
| hermes-agent | `/home/yydspei/projects/hermes-agent` | Hermes Agent 框架，语言模型交互模式 |
| openclaw | `/home/yydspei/projects/openclaw` | Personal AI Assistant，多平台 agent 参考 |
