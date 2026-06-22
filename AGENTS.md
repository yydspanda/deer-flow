# SOC Agent — DeerFlow + LangGraph

## 项目目标
基于 DeerFlow 框架 + LangGraph 构建 SOC 预警研判 Agent。

## 项目介绍

### 当前状态

**DeerFlow 2.0（基础框架）**：已完成。后端 Python + LangChain + LangGraph，前端 TypeScript。包含 19 个子系统、18 个中间件、8 个模型提供商、7 个 IM 渠道集成、子 Agent 并行调度、记忆系统等。

**SOC Agent（业务应用）**：设计方案 v4 已完成，代码开发尚未开始。详见 `.notes/ai_soc/soc-agent-solution.md`。

### SOC Agent 是什么

从 EDR/SIEM 接入安全告警（Kafka 或 CLI），通过 7 步分析流水线自动研判告警真伪：

- **主 Agent**：持久运行，维护近期告警窗口 + 模式索引，做去重和编排
- **子 Agent**：按告警临时创建，独立执行 7 步流水线（实体抽取 → 经验匹配 → 历史关联 → 漏斗关联 → LLM 分析 → 置信度决策 → 落库）
- **三层反馈**：CLI 人工修正 → Daemon 批量审核 → LLM 自主发现新规律

### 当前技术约定

- **数据库**：SOC Agent 使用 PostgreSQL，不再使用 SQLite/`alerts.db` 作为业务存储。开发库默认按 `soc_agent_dev` 规划；生产环境使用 PostgreSQL 同构 schema。
- **Kafka**：Phase 4 引入 daemon 消费；本地测试默认 Kafka/Redpanda `localhost:9092`。`9092` 是 Kafka broker 默认客户端监听端口，不是 HTTP 端口，不能用浏览器访问。
- **Codex MCP**：当前 Codex 已配置 Context7、Chrome DevTools、OpenAI Docs、CodeGraph、GitHub、Sentry、Postgres、Kafka MCP。Postgres/Kafka MCP 用于辅助开发和测试，不是业务运行依赖。
- **GitHub**：GitHub MCP 走本地 `github-mcp-server`，通过 `gh auth token` 动态取 token，不在配置中明文保存 token。

### 开发路线图（预计 8 周）

| 阶段 | 目标 | 周期 |
|---|---|---|
| Phase 1 | MVP：CLI 基础可用（纯代码提取 + LLM 分析 + 落库） | 2 周 |
| Phase 2 | 关联能力 + 主 Agent 去重 | 2 周 |
| Phase 3 | 学习能力 + 分类器预判 | 2 周 |
| Phase 4 | Daemon 模式 + 子 Agent 并行 | 2 周 |
| Phase 5 | 增强（威胁情报 / 审核面板 / MITRE ATT&CK / 知识老化等） | 按需 |

## 项目详情
详见 `.notes/project-overview.md`

## 参考项目

所有参考项目只读，不直接修改文件。使用下面两层工具按需查阅。

| 项目 | 路径 | 用途 |
|---|---|---|
| claude-code-sourcemap | `/home/yydspei/projects/claude-code-sourcemap` | Claude Code 源码，Agent 架构设计模式 |
| claude-mem | `/home/yydspei/projects/claude-mem` | 记忆系统实现参考 |
| hermes-agent | `/home/yydspei/projects/hermes-agent` | Hermes Agent 框架，语言模型交互模式 |
| openclaw | `/home/yydspei/projects/openclaw` | Personal AI Assistant，多平台 agent 参考 |

### 查阅方式

**层一：全景探索 — Understand Anything**

初次接触或需要理解架构全貌时，用 Codex 中的 Understand Anything skill 生成知识图谱，再用 Dashboard 交互浏览。它是 Codex slash/skill 工作流，不是普通 shell 命令。

常用入口：

```text
# 对参考项目生成知识图谱（一次性的，结果持久化）
# claude-code-sourcemap 的核心源码在 restored-src/src/，图谱已生成在此目录下
/understand /home/yydspei/projects/claude-code-sourcemap/restored-src/src --language zh
/understand /home/yydspei/projects/hermes-agent --language zh

# 当前项目全量重建
/understand /home/yydspei/projects/deer-flow --full --language zh

# 只更新已有图谱的增量变化（默认行为）
/understand /home/yydspei/projects/deer-flow --language zh

# 启动 Dashboard 交互探索
/understand-dashboard /home/yydspei/projects/claude-code-sourcemap/restored-src/src
```

功能选择：

| 功能 | 什么时候用 | 示例 |
|---|---|---|
| `/understand <path>` | 第一次理解项目、生成/更新架构知识图谱 | `/understand /home/yydspei/projects/deer-flow --language zh` |
| `/understand-dashboard <path>` | 用浏览器可视化架构层级、依赖、tour、diff/domain 图 | `/understand-dashboard /home/yydspei/projects/deer-flow` |
| `/understand-chat <问题>` | 基于已有图谱问项目架构问题，不想重新扫代码 | `/understand-chat SOC Agent 的 memory 和 db 怎么交互？` |
| `/understand-explain <文件或符号>` | 深入解释某个文件、函数、模块及其上下游关系 | `/understand-explain src/graph/builder.py` |
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

图形化导航：看模块分层 → 点节点看依赖 → 跟 tour 走学习路径。

> claude-code-sourcemap 知识图谱已生成：`restored-src/src/.understand-anything/knowledge-graph.json`（9061 节点, 12 层架构, 12 步导览）
>
> **openclaw 未生成 Understand Anything 知识图谱** — 项目太大，前面几个参考项目已足够，不需要对 openclaw 做 Understand Anything。

**层二：精确查询 — CodeGraph**

日常开发时，需要查某个具体函数/类的实现，优先使用 Codex 中已配置的 CodeGraph MCP。若需要在 shell 中手动查询，可使用 CodeGraph CLI 的 `-p/--path` 跨项目查。

已验证命令格式：

```
codegraph query -p /home/yydspei/projects/hermes-agent "memory"
codegraph context -p /home/yydspei/projects/claude-code-sourcemap/restored-src/src "Tool buildTool permission"
codegraph callers -p /home/yydspei/projects/claude-mem "MemoryManager"
```

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

> 如果 CodeGraph 报 `not initialized`，先去那个项目目录跑一次 `codegraph init`。

## 边界规则
- 所有代码修改仅限本仓库目录
- 参考项目通过 Understand Anything + CodeGraph 查阅，不直接 `ls`/`cat` 参考项目文件
- 参考项目的设计模式应理解后在本项目中重新实现，不直接复制代码

## 跨项目开发工作流
详见 `.notes/reference/cross-project-workflow.md` — 问题驱动四步法（定义问题 → Understand Anything 导航 → CodeGraph 查代码 → 写参考索引）

## 参考索引目录
`.notes/reference-index/` — 每个文件对应一个设计主题，记录各项目的最佳实现位置

## 相关研究文档
- `.notes/research/hermes-vs-deerflow-agent-patterns.md` — Claude Code 可借鉴设计模式（含代码位置）
- `.notes/ai_soc/soc-agent-solution.md` — SOC Agent 设计方案 v4
- `.notes/research/tech-selection-report.md` — 技术选型报告
