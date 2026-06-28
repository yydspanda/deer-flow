# Agent 架构全景：从 ReAct 到 Super Agent Harness

> 2026 年 AI 工程化核心概念整合
> 来源：LangChain、Anthropic、OpenAI、Martin Fowler、社区实践

---

## 目录

1. [Agent = Model + Harness](#一agent--model--harness)
2. [四大单 Agent 推理模式](#二四大单-agent-推理模式)
3. [深度思考 / Plan / Todo 的演进](#三深度思考--plan--todo-的演进)
4. [DeerFlow 的四种模式对照](#四deerflow-的四模式对照)
5. [Harness 的 Guides 和 Sensors](#五harness-的-guides-和-sensors)
6. [Harness 工程实战数据](#六harness-工程实战数据)
7. [七大设计抉择](#七七大设计抉择)
8. [DeerFlow 在格局中的位置](#八deerflow-在格局中的位置)

---

## 一、Agent = Model + Harness

### 核心公式

```
Agent = Model + Harness
```

LangChain 工程师 Vivek Trivedy 的原话已成社区共识：

> **"如果你不是模型，你就是 Harness。"**

### 什么是 Harness

Harness = **包裹在大模型外面的全部软件基础设施**：

| 层 | 做什么 |
|----|--------|
| 编排循环 | Thought-Action-Observation 循环 |
| 工具系统 | 定义、加载、执行、权限控制 |
| 记忆系统 | 短期 + 长期记忆 |
| 上下文管理 | 压缩、遮蔽、按需检索 |
| 错误处理 | 分类错误、重试、降级 |
| 验证循环 | 模型自我检查工作质量 |
| 安全护栏 | 输入/输出/工具三层防护 |
| 沙箱执行 | 隔离的代码执行环境 |
| 状态持久化 | 跨会话保持状态 |

Anthropic 的 Claude Code 文档直说：SDK 就是"驱动 Claude Code 的 Agent Harness"。OpenAI 的 Codex 团队用同样的说法。

### Martin Fowler 的定义（2026.02）

Martin Fowler（《重构》作者）的定义更精辟：

> Harness 由两部分组成：**Guides（前馈控制）**和 **Sensors（反馈控制）**。
> - Guides 在 Agent 行动之前引导它做对
> - Sensors 在 Agent 行动之后帮它自我纠正

原文：
> "Guides increase the probability that the agent creates good results in the first attempt. Sensors observe after the agent acts and help it self-correct. The most powerful combination uses both together."

### 类比：裸模型就是没有操作系统的 CPU

Beren Millidge 2023 年的论文里的精确类比：

```
CPU 裸芯片       →  裸 LLM（只有推理能力）
RAM（内存）      →  上下文窗口（快但小）
硬盘             →  外部数据库（大但慢）
设备驱动         →  工具集成（bash、文件、搜索...）
操作系统         →  Harness（管理以上一切）

"我们重新发明了冯·诺依曼架构。"
```

### 三层工程（不是只有"提示词"）

```
第 1 层：提示工程（Prompt Engineering）
  → 设计模型接收到的指令

第 2 层：上下文工程（Context Engineering）
  → 管理模型看到什么、什么时候看到

第 3 层：Harness 工程（Harness Engineering）
  → 包含前两者，加上整个应用基础设施：
    工具编排、状态持久化、错误恢复、验证循环、
    安全执行、生命周期管理
```

> 很多人把"做智能体"等同于"写好提示词"，这就像把"做操作系统"等同于"写好启动脚本"。

---

## 二、四大单 Agent 推理模式

这是 Agent 架构的核心选型。来源：[The AI Engineer](https://theaiengineer.substack.com/p/the-4-single-agent-patterns)。

### 模式总览

| 模式 | 核心思路 | LLM 调用次数 | 何时用 |
|------|---------|-------------|--------|
| **ReAct** | 每步推理+行动 | N（每步 1 次） | 探索性任务、步骤间强依赖 |
| **Plan-and-Execute** | 先规划再执行 | 1(规划) + N(执行) | 多步骤结构化任务 |
| **ReWOO** | 一次规划 + 并行执行 | 2（规划+综合） | 工具调用独立的标准化流程 |
| **Reflexion** | 执行后自我批评+重试 | N × M（N步×M轮） | 有明确成功标准的任务 |

### 2.1 ReAct（Reasoning + Acting）

**一句话**：想一步、做一步、看结果、再想一步。

```
用户输入 → Thought → Action → Observation → Thought → Action → ... → Final Answer
```

**优点**：
- 自然适应性强，工具失败后自动换策略
- 可调试性强，每一步的推理过程都有日志
- 所有框架都支持，是默认起点

**缺点**：
- Token 成本随任务复杂度线性增长（10步 = 10次 LLM 调用）
- 容易陷入推理循环（反复调同一个工具）
- 天生短视——只看一步，不做全局优化

**适用**：探索性任务（搜索+阅读+综合）、客服对话、步骤间强依赖的场景

**天花板**：任务超过 5-7 步时，逐步 LLM 调用的成本和延迟就成为问题

### 2.2 Plan-and-Execute

**一句话**：先让强模型做完整规划，再用便宜模型逐步执行。

```
用户输入 → Planner(强模型) → 生成计划 DAG
         → Executor(弱模型) → 执行 Step 1 → Step 2 → ...
         → 失败时 Replanner 修正剩余计划
```

**优点**：
- 大幅减少 LLM 调用次数（10 步任务只需 1-2 次调用）
- 可以用不同模型（贵模型规划，便宜模型执行）
- 计划可审查——执行前就知道 agent 要做什么

**缺点**：
- 计划刚性——现实和预期不符时容易脱轨
- 规划器只能预见它能预见的，新奇的边界情况会产生坏计划
- 架构复杂度比 ReAct 高

**适用**：多步骤结构化工作流（保险理赔、数据处理管道、报告生成）

### 2.3 ReWOO（Reasoning Without Observation）

**一句话**：一次规划完所有步骤（用占位符替代未知结果），工具并行执行，最后综合。

```
用户输入 → Planner 生成带占位符的计划
         → #E1 = Search['query1']
         → #E2 = Search['query2']
         → Worker 并行执行所有工具
         → Solver 综合结果 → Final Answer
```

**优点**：
- Token 效率极高（原论文报告比 ReAct 高 5 倍）
- 独立工具调用可并行执行
- 只需 2 次 LLM 调用

**缺点**：
- 零中期调整——规划一旦锁定就不能改，工具返回意外结果也无法触发重新规划
- 占位符依赖链可能断裂
- 要求任务结构高度可预测

### 2.4 Reflexion

**一句话**：执行完一轮后自我批评，把批评存入记忆，重试。

```
用户输入 → Agent 执行 → 输出
         → Evaluator 评分（跑测试、校验格式...）
         → 评分不达标 → Self-Reflection 生成文字批评
         → 批评存入 Episodic Memory
         → 带着批评重试 → 循环直到达标或耗尽次数
```

**关键数据**：
- HumanEval 编码任务：GPT-4 pass rate 从 80% → 91%（Reflexion）
- AlfWorld 决策任务：ReAct + Reflexion 完成 134 个任务中的 130 个

**优点**：
- 真正从失败中学习——不是简单重跑，而是带着反思重试
- 可以叠加在任何底层模式上（ReAct + Reflexion 是最常见组合）
- 目前唯一一个能在同一任务上越做越好的模式

**缺点**：
- 昂贵——每次重试都是完整执行（3 次重试 = 3 倍成本）
- 自评质量取决于评估器（评估器打分不准，反思就没用）
- 延迟随重试次数倍增

### 2.5 模式选择决策树

```
每个步骤是否依赖上一步的结果？
├── 是 → 需要中期适应，排除 ReWOO
│   ├── 任务有明确的对/错标准吗？
│   │   ├── 是 → Reflexion（重试+自我批评）
│   │   └── 否 → ReAct（逐步适应就够了）
│   └── Token 成本是首要考虑吗？
│       ├── 是 → Plan-and-Execute（用便宜模型执行）
│       └── 否 → ReAct
└── 否 → 工具调用独立
    ├── Token 成本优先？→ ReWOO（2次调用+并行执行）
    └── 需要可审查计划？→ Plan-and-Execute
```

### 2.6 混合策略（生产实践）

生产环境中，纯模式很少见。常见组合：

| 组合 | 怎么做 | 解决什么 |
|------|--------|---------|
| ReAct + Reflexion | ReAct 跑一轮，结果失败验证后进入 Reflexion 重试 | 日常用 ReAct 的灵活性，难题用 Reflexion 的自我纠错 |
| Plan-and-Execute + ReAct 兜底 | 先规划，某步异常时降级到 ReAct | 结构化任务的效率 + 异常时的适应能力 |
| ReWOO 快路径 + Plan-and-Execute 兜底 | 先尝试 ReWOO，工具返回异常时重新用 Plan-and-Execute | 80% 标准任务的低延迟 + 20% 异常任务的可靠处理 |

---

## 三、深度思考 / Plan / Todo 的演进

这一节解释为什么 DeerFlow 有 Flash / Thinking / Pro / Ultra 四个模式，以及它们对应什么架构模式。

### 3.1 深度思考（Extended Thinking）

**本质**：让模型在输出前先"想"很久，把推理链展开。

| 特性 | 普通推理 | 深度思考 |
|------|---------|---------|
| 推理过程 | 隐式（模型内部） | 显式（thinking tokens） |
| Token 消耗 | 低 | 高（可达 10x） |
| 准确性 | 一般 | 显著提升（复杂推理任务） |
| 代表模型 | GPT-4o, Claude 3.5 | Claude 3.7 (extended thinking), DeepSeek-R1, o1/o3 |

**和 ReAct 的区别**：
- ReAct 的思考在**行动之间**，每步想一点
- 深度思考的思考在**行动之前**，一次性想透
- 两者可以叠加：深度思考 + ReAct = 每步推理更深

### 3.2 Plan Mode（规划模式）

**本质**：在执行任何工具调用之前，先生成一个结构化的任务列表。

```
普通 ReAct：
  用户 → [想] → [做] → [想] → [做] → [想] → [做] → 结果

Plan Mode：
  用户 → [深度思考] → 生成 Todo List → [逐步执行 Todo] → 结果
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^
         这一步不调用任何工具，纯推理
```

**实现要点**：
1. LLM 先生成一个 JSON 格式的 Todo 列表
2. 每个 Todo 项有标题、描述、状态（pending/in_progress/completed）
3. 后续每步 ReAct 循环围绕当前 Todo 项执行
4. 完成一项勾掉一项，自动推进到下一项

**为什么有效**：
- 强制模型"先想后做"而不是"边做边想"
- 用户可以看到计划，提前发现模型理解偏差
- 提供了自然的进度反馈（30% → 60% → 100%）

### 3.3 Todo / Task Management（任务管理）

**本质**：让 Agent 在执行过程中维护和更新任务列表。

DeerFlow 的实现（`TodoMiddleware`）：

```python
# 伪代码
class TodoMiddleware:
    def after_model(self, state):
        # 1. 检查 state 中是否有 todo 列表
        # 2. 如果有，注入 reminder 到下一条消息
        #    "当前进度：2/5 完成。下一个任务是：xxx"
        # 3. 如果模型标记某个 todo 完成，更新状态
        pass
```

**和 Plan Mode 的区别**：
- Plan Mode 是一次性的（开始时规划）
- Todo Management 是持续性的（执行过程中动态更新）

### 3.4 四层能力叠加图

```
Layer 0: 基础 ReAct Loop
  Thought → Action → Observation → 循环

Layer 1: + Extended Thinking（深度思考）
  在每次 Thought 阶段投入更多推理计算
  → 提升每步推理质量

Layer 2: + Plan Mode（规划）
  在 Layer 0/1 之前增加全局规划阶段
  → 解决"短视"问题

Layer 3: + Sub-Agent（子智能体）
  Lead Agent 把 Todo 项委派给独立 Sub-Agent 并行执行
  → 解决"串行瓶颈"问题

每一层都是叠加在上一层之上，不是替代。
```

---

## 四、DeerFlow 的四模式对照

| DeerFlow 模式 | ReAct | Thinking | Plan | Sub-Agent | 对应架构模式 |
|---------------|-------|----------|------|-----------|------------|
| **Flash** | ✅ | ❌ | ❌ | ❌ | 纯 ReAct |
| **Thinking** | ✅ | ✅ | ❌ | ❌ | ReAct + 深度思考 |
| **Pro** | ✅ | ✅ | ✅ | ❌ | ReAct + 深度思考 + Plan-and-Execute |
| **Ultra** | ✅ | ✅ | ✅ | ✅ | 全部叠加 + 多 Agent 编排 |

### 数据流对比

**Flash 模式**：
```
用户消息 → ReAct Loop (Thought→Action→Observation × N) → 回复
```

**Thinking 模式**：
```
用户消息 → [Thinking Tokens 展开] → ReAct Loop → 回复
            ^^^^^^^^^^^^^^^^^^^^^^^
            每步推理前先深度思考
```

**Pro 模式**：
```
用户消息 → [深度思考 + 生成 Todo List] → 逐步执行每个 Todo → 回复
```

**Ultra 模式**：
```
用户消息 → [深度思考 + 生成 Todo List] → Lead Agent 分发
                                        ├── Sub-Agent 1 (Todo 1)
                                        ├── Sub-Agent 2 (Todo 2)  ← 并行
                                        └── Sub-Agent 3 (Todo 3)
                                        → 汇总结果 → 回复
```

### 关键区别

| 维度 | Flash | Thinking | Pro | Ultra |
|------|-------|----------|-----|-------|
| 规划能力 | 无 | 隐式 | 显式 Todo List | 显式 + 分发 |
| 并行能力 | 无 | 无 | 无 | Sub-Agent 并行 |
| Token 成本 | 低 | 中 | 高 | 最高 |
| 适合任务 | 简单查询 | 需要推理的问题 | 多步骤复杂任务 | 研究型/探索型任务 |
| 中间件数量 | 7 基础 | 7 基础 | 7 基础 + Todo | 7 基础 + Todo + SubAgent |
| Sub-Agent 中间件 | N/A | N/A | N/A | 4 个精简版 |

---

## 五、Harness 的 Guides 和 Sensors

### 5.1 Martin Fowler 的框架（2026.02）

Martin Fowler 把 Harness 里所有组件分为两大类：

```
Guide（前馈控制）—— 行动之前引导 Agent 做对
  "你应该这样写代码，用这些工具，遵循这些规范"

Sensor（反馈控制）—— 行动之后帮 Agent 自我纠正
  "你写的代码跑不过测试，你调了同一个工具 5 次了"
```

每一类还可以细分为两种执行方式：

|  | Computational（确定性） | Inferential（推断性） |
|--|------------------------|---------------------|
| **Guide** | 脚手架脚本、代码模板、CLI 工具 | AGENTS.md、Skill 提示、编码规范 |
| **Sensor** | 静态分析、测试、类型检查、Linter | LLM 代码审查、"LLM as judge" |

**Computational**：确定性、快、毫秒到秒级、结果可靠。CPU 跑。
**Inferential**：语义分析、需要 GPU/NPU、更慢更贵、结果有不确定性。

### 5.2 为什么不能只有一种

```
只用 Guides（只有前馈）：
  Agent 编码了规则，但永远不知道规则有没有生效
  → 像蒙着眼开车——方向对不对全凭猜

只用 Sensors（只有反馈）：
  Agent 不断犯错再纠正，反复循环浪费 Token
  → 像没有方向盘的车——只能撞墙了才知道偏了

两者结合：
  Guide 告诉 Agent 正确方向，Sensor 帮它发现偏差
  → 这才是 Harness 的最佳实践
```

### 5.3 DeerFlow 12 个组件的 Guides/Sensors 分类

| # | 组件 | 类型 | 做什么 | DeerFlow 对应 | Guide/Sensor 原因 |
|---|------|------|--------|-------------|-------------------|
| 1 | 编排循环 | Guide | 定义 Agent 的行动框架 | `create_agent()` 的 while 循环 | 在 Agent 行动之前就确定了"想→做→看"的循环结构 |
| 2 | 工具系统 | Guide | 限定 Agent 能用什么工具 | `get_available_tools()` + Tool Groups | 在 Agent 行动之前就定义了可用工具的边界 |
| 3 | 记忆系统 | Sensor | 观察 Agent 的对话，提取记忆 | `MemoryMiddleware` → `memory.json` | 在 Agent 对话**之后**提取信息，下次再作为 Guide 注入 |
| 4 | 上下文管理 | Guide | 注入 prompt、压缩、按需检索 | `TodoMiddleware` 的 reminder | 在 Agent 行动**之前**注入上下文，引导它按计划走 |
| 5 | 错误处理 | Sensor | 观察 Agent 的错误，分类处理 | `ToolErrorHandlingMiddleware` | 在 Agent 执行**失败后**介入，帮它降级或重试 |
| 6 | 循环检测 | Sensor | 观察 Agent 是否在重复调工具 | `LoopDetectionMiddleware` | 在 Agent 行动**之后**检测异常模式（warn/hard_stop） |
| 7 | 安全护栏 | Guide + Sensor | 执行前约束 + 执行后审计 | `SandboxAuditMiddleware` | Guide: 工具分组权限限制能用什么；Sensor: 命令执行前审计 block/warn/pass |
| 8 | 沙箱执行 | Guide | 隔离执行环境 + 虚拟路径 | `SandboxMiddleware`（Local/Docker） | 在 Agent 行动**之前**就划定安全边界（能访问哪些路径） |
| 9 | 状态持久化 | Sensor | 记录 Agent 每步状态 | LangGraph checkpoint | 在每步**之后**保存快照，用于恢复和审计 |
| 10 | 子智能体 | Guide | 拆分任务、委派执行 | `SubagentExecutor` + 线程池 | 在执行**之前**就决定了任务如何分发给子智能体 |
| 11 | 配置管理 | Guide | 定义所有参数和约束 | `config.yaml` + mtime 热加载 | 在 Agent 启动**之前**就设定了模型、工具、策略 |
| 12 | 渠道集成 | Guide | 接入外部消息源 | `ChannelManager`（飞书/Telegram/Slack） | 在 Agent 行动**之前**预处理外部输入 |

### 5.4 关键洞察：记忆系统是先 Sensor 后 Guide

记忆系统是最有意思的——它是**跨轮次的 Guide-Sensor 循环**：

```
第 N 轮对话
  → MemoryMiddleware（Sensor）: 观察对话内容，提取 facts
  → 写入 memory.json

第 N+1 轮对话
  → System Prompt 注入 <memory>（Guide）: 把上次提取的 facts 作为上下文
  → Agent 带着记忆开始工作
  → MemoryMiddleware（Sensor）: 再提取新 facts
  → 循环...
```

这就是 Martin Fowler 说的"steering loop"——人类通过迭代 Harness 来改进 Agent 表现，而记忆系统把这个循环自动化了。

### 5.5 社区是不是都这么做？

**是的，但每个团队叫法不同、侧重点不同**：

| 团队/产品 | 叫法 | 核心做法 | 和 Martin Fowler 框架的关系 |
|----------|------|---------|---------------------------|
| **Anthropic (Claude Code)** | SDK = Harness | System Prompt + 工具选择 + 执行流控制 | SDK 内置了 Guides（prompt、工具边界）和 Sensors（self-verification） |
| **OpenAI (Codex)** | Harness | 编排层 + 模型后训练适配 | 同上，且模型和 Harness 一起训练 |
| **LangChain** | Harness Engineering | 中间件（Middleware）= Guides + Sensors | 明确用 Middleware 实现，每个中间件要么是 Guide 要么是 Sensor |
| **Stripe** | Minions | Pre-push hooks + Blueprints | Hooks = Computational Sensors，Blueprints = Guides |
| **Cursor** | Rules + AI Review | .cursorrules（Guide）+ 内置审查（Sensor） | 完美对应 Guides/Sensors |
| **Windsurf/Cline** | .windsurfrules + 反馈循环 | 规则文件（Guide）+ 错误检测（Sensor） | 同上 |

**共同模式**：
1. **所有 Agent 产品都有 Guide 机制**：system prompt、规则文件、AGENTS.md、工具白名单——名字不同，本质都是在行动前引导
2. **成熟的 Agent 产品都有 Sensor 机制**：测试运行、Linter、循环检测、自我验证——本质都是在行动后检测和纠正
3. **进阶团队在用两层循环**：内层是 Agent 自己的 Guide→Act→Sensor→Correct；外层是人类根据 Agent 失败模式迭代改进 Harness

**Martin Fowler 的贡献**：他给这些已经存在的实践提供了一个**统一的概念框架**。以前大家在分别做 prompt engineering、tool design、middleware、guardrails——现在知道这些都是 Guides 和 Sensors 的不同实现。

---

## 六、Harness 工程实战数据

### 数据 1：LangChain TerminalBench 2.0

LangChain 只改 Harness（模型不变，GPT-5.2-Codex）：

| 指标 | 改前 | 改后 | 变化 |
|------|------|------|------|
| 得分 | 52.8% | 66.5% | +13.7 分 |
| 排名 | Top 30+ | Top 5 | 跳 25 位 |

关键改进措施（全部是 Harness 改动）：
1. **Self-Verification Loop**：加了 `PreCompletionChecklistMiddleware`，强制 agent 退出前自我验证
2. **Context Onboarding**：`LocalContextMiddleware` 在 agent 启动时注入目录结构和可用工具
3. **Loop Detection**：追踪每个文件的编辑次数，超过 N 次注入"重新考虑方案"提示
4. **Reasoning Sandwich**：规划阶段用 xhigh 推理，执行阶段用 high，验证阶段用 xhigh
5. **Time Budgeting**：注入时间预算警告，防止 agent 超时

来源：[LangChain Blog - Improving Deep Agents with Harness Engineering](https://www.langchain.com/blog/improving-deep-agents-with-harness-engineering)

### 数据 2：MindStudio 基准测试

| 模型 | 换 Harness 前 | 换 Harness 后 | 提升 |
|------|-------------|-------------|------|
| GPT-5.5 | 61.5% | 87.2% | +25.7 分 |

来源：[MindStudio - Agent Harnesses Beat Model Upgrades](https://www.mindstudio.ai/blog/agent-harnesses-beat-model-upgrades-5-benchmarks/)

### 数据 3：Can Bölük 的 hashline 工具

把编辑工具从 `str_replace` 换成自己发明的 `hashline`：
- **模型没换**：Grok Code Fast 1
- 成功率：6.7% → **68.3%**
- 翻 **10 倍**

教训：**工具设计是 Harness 工程中杠杆率最高的环节**。

### 数据 4：错误复合效应

一个 10 步流程，每步独立，端到端成功率 = 各步成功率的乘积：

- 每步成功率 99% → 端到端成功率 ≈ 90.4%
- 每步成功率 95% → 端到端成功率 ≈ 59.9%
- 每步成功率 90% → 端到端成功率 ≈ 34.9%

注意：每步只差 4%（95% vs 99%），但端到端差了 **30 个百分点**。

**Harness 的价值不是让每步好 1%，而是阻止错误的复合。**

Harness 真正的价值是**消除致命失败模式**——不是在每步优化 1%，而是在某些步骤把 0% 变成接近 100%：

| 没有 Harness | 有 Harness | 效果 |
|-------------|-----------|------|
| Agent 死循环调同一个工具 20 次 | LoopDetection 在第 5 次强制停止 | 0% → ~100% |
| Agent 执行 `rm -rf /` | SandboxAudit 直接 block | 0% → 100% |
| Agent 不知道文件在哪，瞎搜 10 轮 | Context 注入目录结构 | 几步从 10% → 90% |
| Agent 写完代码就提交，不跑测试 | PreCompletionChecklist 强制验证 | 整体成功率翻倍 |

这些场景的共同特点：**没有 Harness 时 Agent 靠自己永远走不出来**。死循环、危险操作、不验证就提交——这些都是结构性死路，不是"做得不够好"。Harness 的价值在于打断这些死路，把 0% 变成接近 100%。

---

## 七、七大设计抉择

每个 Harness 架构师都要面对的问题：

### 7.1 单智能体 vs 多智能体

Anthropic 和 OpenAI 的建议一致：**先把单智能体做到极致**。

多智能体的额外成本：
- 路由开销（谁来决定分给谁？）
- 上下文丢失（交接时信息衰减）
- 调试复杂度指数级增长

何时拆分：
- 工具超过 10 个重叠
- 任务领域确实互不相干
- 需要并行处理

### 7.2 工具范围策略

**反直觉：工具越多，表现往往越差。**

Vercel 把 v0 的 80% 工具都砍掉了，结果反而更好。

原则：只暴露当前步骤需要的最小工具集。

### 7.3 Harness 厚度——拐杖 vs 放大器

多少逻辑放 Harness，多少留给模型？

Anthropic 押注**薄 Harness + 模型改进**——他们会定期从 Claude Code 的 Harness 里删除规划步骤，因为新版模型已经内化了这个能力。

> Harness 设计的"未来验证测试"：如果换上更强的模型后，性能自动提升而不需要增加 Harness 复杂度——那你的设计就是好的。

**拐杖型 Harness（应该避免）**：

```
模型弱 → 加了 10 层补丁：
  - 复杂 prompt 模板，手把手教模型每一步怎么想
  - 大量硬编码规则（"如果用户说 X，你就做 Y"）
  - 多个兜底中间件，专门处理模型犯的特定错误

模型升级了 → 发现：
  - 新模型不需要那些手把手指导了，但旧 prompt 反而干扰它
  - 硬编码规则和新模型能力冲突
  - 兜底中间件在处理不该处理的东西

结果：性能反而下降，必须重写 Harness 适配新模型
```

**放大器型 Harness（应该追求）**：

```
模型弱 → Harness 提供基础设施：
  - 工具系统（定义能用什么工具）
  - 沙箱（定义安全边界）
  - 错误恢复（出了事怎么兜底）
  - 状态持久化（跨会话记忆）

模型升级了 → Harness 不需要改：
  - 新模型自然会用更好的策略调用工具
  - 新模型自然会在安全边界内做更聪明的操作
  - 错误恢复触发频率降低（但不需要删掉它）
  - 记忆系统照常工作

结果：性能自动提升，Harness 不需要动
```

**核心区别**：拐杖定义策略（"你应该先做 A 再做 B"），放大器只定义约束（"你有这些工具，不能超出这个边界"）。策略是模型的活，约束是 Harness 的活。

**实操原则**：

```
放心加（放大器，永久保留）：
  ✅ 安全护栏（SandboxAudit）— 任何模型都需要
  ✅ 错误恢复（ToolErrorHandling）— 任何模型都可能出错
  ✅ 沙箱隔离 — 和模型能力无关
  ✅ 状态持久化 — 基础设施
  ✅ 工具权限白名单 — 安全边界

尽量少加（可能变成拐杖，最终要删）：
  ⚠️ 复杂 prompt 模板，手把手教推理步骤
  ⚠️ 针对特定模型弱点的 workaround
  ⚠️ 过多的硬编码决策规则
```

**放大器型 Harness 的前提**：模型本身要足够强，且 Harness 只管边界不管思维。Anthropic 和 OpenAI 都在后训练阶段教会模型"怎么当 Agent"——所以 Anthropic 敢删 prompt 里的规划指导。OpenAI Codex 更激进：模型是和特定 Harness 一起后训练的。OpenAI Codex 团队原话："Codex 模型在 Codex 的界面上感觉比在通用聊天窗口好用。"

**判断你的 Harness 是哪种**：

| 问题 | 拐杖型 | 放大器型 |
|------|--------|---------|
| 换更强的模型后，Harness 需要改吗？ | 需要改 prompt 和规则 | 不需要改 |
| Harness 里的逻辑，是在教模型"怎么想"还是在管"边界"？ | 教模型怎么想 | 管边界 |
| 模型升级后，某些中间件可以删掉吗？ | 不敢删，删了就崩 | 可以删，删了性能更好 |

### 7.4 上下文腐烂

关键内容落在上下文窗口中间时，模型性能下降超过 **30%**（斯坦福"Lost in the Middle"论文）。

应对手段：
- 压缩（Summarization）
- 观察遮蔽（Observation Masking）
- 按需检索（Retrieval）
- 子智能体委派（减少单次上下文长度）

### 7.5 验证循环

Anthropic 的 Boris Cherny（Claude Code 创造者）：

> 给模型一种验证自己工作的方式，可以将质量提升 **2 到 3 倍**。

LangChain 的实践：加了 `PreCompletionChecklistMiddleware` 后，Agent 不再"写完代码看一眼就说完成了"，而是会跑测试、对照需求验证。

### 7.6 脚手架隐喻

Harness 像建筑工地的脚手架。**关键洞察：脚手架在建筑完工后会被拆除。**

模型每升级一代，Harness 就应该瘦身一轮。今天必须的 LoopDetection，明天模型可能自带这个能力。

### 7.7 模型与 Harness 共同进化

Codex 团队："Codex 模型在 Codex 的界面上感觉比在通用聊天窗口好用。"因为模型是和特定 Harness 一起后训练的。

**换 Harness 等于换了模型的运行环境**。Tailor Harnesses to Models——不同模型需要不同的提示策略。

### 7.8 小厂家用开源模型的 Harness 策略

开源模型（DeepSeek、Qwen 等）没有 Claude/GPT 那种后训练，"模型自己就会规划"不能指望。但仍然可以追求放大器设计，只是需要多做一步。

**策略：Harness 放大器 + SFT/RFT 补课**

```
大厂路线：
  强模型（后训练过）+ 薄 Harness = 好效果

小厂路线：
  开源模型 + Harness 放大器（管边界）+ 针对性微调（补模型短板）= 好效果
```

**四步走**：

**第一步：先写放大器型 Harness（管边界）**

这部分和用 Claude 没区别——工具系统、沙箱、错误恢复、状态持久化、记忆系统。

**第二步：跑一遍，收集失败案例**

用 Harness 跑 100 个真实任务，记录每次失败的原因：
- 模型不会规划，上来就乱调工具
- 模型调了不存在的工具
- 模型不会自我验证
- 模型输出格式不对

**第三步：对高频失败做 SFT/RFT**

针对失败做微调，**不是改 Harness，而是教模型**：

| 失败原因 | 补课方法 |
|---------|---------|
| 不会规划 | RFT（Reinforcement Fine-Tuning）教模型规划 |
| 不遵循工具 schema | SFT 教模型遵循格式 |
| 不验证就提交 | RFT 教模型跑测试验证 |
| 输出格式不对 | SFT 教模型输出指定格式 |

DeepSeek、Qwen 做做 SFT/RFT 成本很低——几万条数据、几张 A100 就行。

**第四步：模型升级后重跑**

```
DeepSeek-V3 → Harness 跑 100 任务 → 60% 成功率
             → 分析失败 → SFT 补课 → 80%

DeepSeek-V4 出了 → 直接换模型，Harness 不改 → 75%
             → 分析新失败 → SFT 补课 → 90%
```

**小厂家的隐藏优势**：大厂的后训练是通用的（教模型当好通用 Agent），你的 SFT 是特化的（教模型当好你的领域的 Agent）。在你的细分领域，你可能比大厂效果更好。

### 7.9 细分领域的特化流程怎么做

有些场景必须教模型按特定流程走。关键是区分：**这条规则存在是因为模型弱（拐杖），还是因为业务需要（合理约束）？**

```
拐杖（应该避免）：
  "你推理能力不行，所以我手把手教你怎么想"
  → 补模型短板，模型强了就该删

业务流程（必须保留）：
  "我们公司的理赔流程就是：先验证保单 → 再核实事故 → 再计算赔付"
  → 业务规则，换什么模型都得遵守
```

**解决方案：把流程拆成"状态机 + 每步 ReAct"**

```
错误做法（全塞 prompt 里）：
  System Prompt:
    "你是理赔助手。第一步先调 verify_policy，
     第二步调 check_accident，第三步调 calculate_payout..."
  → 1000 行 prompt，模型记不住，换了模型效果更差

正确做法（Harness 编排层）：

  Harness 定义状态机：

  State: VERIFY_POLICY
    → 只暴露 verify_policy 工具给模型
    → 模型调用完 → Harness 检查结果 → 自动推进

  State: CHECK_ACCIDENT
    → 只暴露 check_accident 工具
    → 模型调用完 → Harness 检查结果 → 自动推进

  State: CALCULATE_PAYOUT
    → 只暴露 calculate_payout 工具
    → 模型调用完 → 输出结果
```

**核心设计**：

```
                 状态机（Harness 管流程，硬约束）
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
    Step 1         Step 2         Step 3
    验证保单        核实事故        计算赔付
        │             │             │
        ▼             ▼             ▼
    ReAct 循环     ReAct 循环     ReAct 循环
    （模型自由     （模型自由     （模型自由
     决策怎么做）   决策怎么做）   决策怎么做）
```

流程是硬的（必须先验证保单再核实事故），但每步内部怎么执行是软的（模型自己决定调什么工具、怎么分析）。

**这在 DeerFlow 里的对应**：Pro 模式的 Plan 阶段（Todo List = 硬约束流程）+ 每个 Todo 内部的 ReAct 循环（模型自由决策）= 就是这个设计。

**总结**：

| 场景 | 做法 | 性质 |
|------|------|------|
| 模型推理弱 | SFT/RFT 补课，不改 Harness | 拐杖→最终会删 |
| 业务有固定流程 | 状态机编排，每步内部 ReAct | 放大器→永久保留 |
| 安全约束 | 沙箱 + 审计 | 放大器→永久保留 |
| 错误恢复 | 重试 + 降级 | 放大器→永久保留 |

原则不变：**Harness 管边界和流程，模型管决策**。只是"边界"在细分领域里包括了业务流程约束，这是合理的。

---

## 八、DeerFlow 在格局中的位置

### 架构定位

```
Model:  你自己选（OpenAI/Claude/DeepSeek/任何兼容模型）
Harness: DeerFlow 2.0 提供 12 个组件中的全部
App:    你只写业务逻辑（SOUL.md + config.yaml）

= 开箱即用的 Super Agent Harness
```

### DeerFlow 的 Harness 完整度

| Harness 组件 | DeerFlow 实现 | 行业对标 |
|-------------|-------------|---------|
| 编排循环 | `create_react_agent()` | LangGraph 标准 |
| 工具系统 | 动态加载 + Tool Groups + MCP | 超越大多数框架 |
| 记忆系统 | LLM 提取 + 去抖动 + 原子写入 | 企业级 |
| 上下文管理 | TodoMiddleware reminder | 行业标准 |
| 错误处理 | ToolErrorHandlingMiddleware | 标准实践 |
| 验证循环 | LoopDetectionMiddleware | Anthropic 推荐 |
| 安全护栏 | SandboxAudit (block/warn/pass) | **超出行业标准** |
| 沙箱执行 | Local + Docker 双模式 | 企业级 |
| 状态持久化 | LangGraph checkpoint | 标准实践 |
| 子智能体 | 线程池 + 信号量 + 超时 | 行业领先 |
| 配置管理 | mtime 热加载 + $VAR 解析 | 超越大多数框架 |
| 渠道集成 | 飞书/Telegram/Slack | 企业必需 |

### 分层设计

```
deerflow.* (harness 包)    →  可发布框架，被 CI 保护
app.*     (应用层)          →  你的业务代码
单向依赖：app → deerflow（反向引用被 CI 拦截）
```

### DeerFlow 模式的架构映射

| DeerFlow 模式 | 学术模式 | 创新点 |
|---------------|---------|--------|
| Flash | ReAct | 标准 ReAct + 7 层中间件 |
| Thinking | ReAct + Chain-of-Thought | Thinking tokens 前置注入 |
| Pro | Plan-and-Execute + ReAct | Todo 列表 + 逐步执行 + 可视化进度 |
| Ultra | 多 Agent 编排 | Lead-Worker 模式 + 线程池 + 信号量控制 |

---

## 参考来源

| 来源 | 链接 | 关键内容 |
|------|------|---------|
| Martin Fowler | [Harness Engineering](https://martinfowler.com/articles/harness-engineering.html) | Guides + Sensors 框架 |
| LangChain Blog | [Improving Deep Agents](https://www.langchain.com/blog/improving-deep-agents-with-harness-engineering) | TerminalBench 实战数据 |
| The AI Engineer | [4 Single Agent Patterns](https://theaiengineer.substack.com/p/the-4-single-agent-patterns) | ReAct/Plan/ReWOO/Reflexion 对比 |
| MindStudio | [Harnesses Beat Model Upgrades](https://www.mindstudio.ai/blog/agent-harnesses-beat-model-upgrades-5-benchmarks/) | 5 个基准测试数据 |
| explainx.ai | [Agent Harness Engineering](https://explainx.ai/blog/agent-harness-engineering-terminal-bench-langchain-2026) | Harness 定义和演进 |
| ReAct 原论文 | Yao et al. 2022 | ReAct 模式原始定义 |
| Reflexion 原论文 | Shinn et al. 2023 | 自我批评+重试模式 |
| ReWOO 原论文 | Xu et al. 2023 | 无观察推理模式 |
| Stanford "Lost in the Middle" | Liu et al. 2023 | 上下文中间位置性能下降 |
