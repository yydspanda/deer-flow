# 跨项目参考工作流

> 目标：在需要借鉴其他 Agent 项目时，快速找到可复用设计点，并把结论沉淀到 `reference-index/`。
> 默认工具链：**`rg` + 最小源码读取 + 必要的测试/运行轨迹**。Understand Anything 不进入日常流程；只有用户明确要求时临时使用。

## 流程总览

```text
① 定义具体问题 → ② 查已有 reference-index → ③ `rg` 定位代码 → ④ 读取最小源码并验证动态链路 → ⑤ 写参考索引
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

## ③ `rg` 定位代码

先列出候选文件，再查询符号、注册点、调用点、配置和测试：

```bash
rg --files /home/yydspei/projects/claude-code-sourcemap
rg -n "permission|approval|tool" /home/yydspei/projects/claude-code-sourcemap
rg -n "AgentLifecycle|event_stream|run_with" /home/yydspei/projects/hermes-agent
rg -n "class MemoryManager|MemoryManager\(" /home/yydspei/projects/claude-mem
```

规则：

- 先用文件名、符号和稳定术语缩小范围，再读代码；不要无目标遍历整个项目。
- 只读取和问题直接相关的文件/函数。
- 参考项目只读不改。
- 静态搜索不能证明动态注册、依赖注入、MCP/Skill 加载或运行时路由；这些关系必须继续核对配置、测试或运行轨迹。

常用查询：

| 命令 | 用途 |
|---|---|
| `rg --files <path>` | 按文件清单和路径快速定位模块 |
| `rg -n "class X|def x|function x" <path>` | 查定义和接口 |
| `rg -n "X\(|\.x\(" <path>` | 查显式调用点 |
| `rg -n "register|registry|provider|middleware" <path>` | 查注册和装配边界 |
| `rg -n "X|x" <path-to-tests>` | 查行为契约和回归覆盖 |
| `git diff -- <paths>` | 检查当前切片的真实影响面 |

## ④ 读取最小源码

`rg` 返回候选文件/符号后，再读取最小必要代码片段确认：

- public interface / 类型签名
- 权限和错误语义
- 状态流转
- 审计/日志/恢复机制
- 测试如何覆盖

遇到插件发现、配置驱动路由、MCP/Skill 加载等动态关系时，再通过聚焦测试、配置解析结果或 Runtime trace 验证；不要从一次文本命中推断完整调用链。

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
- 临时使用后的结论仍必须经过源码以及必要的测试/运行轨迹确认，不能直接把图谱摘要当代码事实。

旧的 Understand-heavy 流程已归档：

```text
.notes/archive/reference/cross-project-workflow-understand-heavy.md
```

## 三条铁律

1. **问题驱动**：先定义问题，再决定是否查参考项目。
2. **源码优先**：先用 `rg` 定位候选，再读最小源码；动态关系必须额外验证。
3. **查完写索引**：采用点和拒绝点必须进入 `reference-index/`。

## 参考项目

| 项目 | 路径 | 用途 |
|---|---|---|
| claude-code-sourcemap | `/home/yydspei/projects/claude-code-sourcemap` | Claude Code 源码，Agent 架构设计模式 |
| claude-mem | `/home/yydspei/projects/claude-mem` | 记忆系统实现参考 |
| hermes-agent | `/home/yydspei/projects/hermes-agent` | Hermes Agent 框架，语言模型交互模式 |
| openclaw | `/home/yydspei/projects/openclaw` | Personal AI Assistant，多平台 agent 参考 |
