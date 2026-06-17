# 新时代 AI SOC Agent 构建方案 — 向领导汇报

> 日期：2026-05-14
> 核心问题：应该怎样构建自己的 AI SOC Agent？直接用开源框架，还是自研？

---

## 一、行业现状：AI SOC 已进入"Agent 时代"

### 1.1 市场格局（2026 Q1）

Gartner 将 AI SOC Agent 列入 **"Technology Trigger" 阶段**（1-5% 市场渗透率），意味着：
- 技术成熟度足够支撑生产部署，但市场还在早期
- **先动手的人有 2-3 年的护城河窗口期**

当前 Top 10 AI SOC 平台：

| 平台 | 类型 | 核心能力 | 定价 |
|------|------|----------|------|
| D3 Morpheus | 全自研 LLM + SOAR | 800+ 集成，自愈 API，95% 告警 2 分钟内研判 | 固定费率 |
| CrowdStrike Charlotte | Falcon 生态绑定 | 98% 决策准确率，但仅限 Falcon 告警 | 按 endpoint |
| Palo Alto XSIAM | 数据湖绑定 | 99% 降噪（Forrester 验证），257% ROI | 按 GB 摄入 |
| Torq HyperSOC | AI-Native SOAR | 多 Agent 系统，48 小时部署 | 订阅制 |
| Microsoft Copilot + Sentinel | M365 生态绑定 | 免费送 E5 用户，但采用率低、幻觉风险 | 捆绑 |
| Google SecOps | Gemini + 数据湖 | 300+ 集成，AI Triage Agent | 按 GB + Gemini |
| Splunk ES | SIEM + AI Agent | 500+ 集成，但 AI 功能多在 beta | 按 GB |
| Dropzone AI | 纯 AI SOC Analyst | $0.18/alert，50 秒研判 | 按调查量 |
| Exaforce | 多模型 Exabots | 全生命周期覆盖 | 按调查量 |
| Prophet Security | 多 Agent（Analyst/Hunter/Advisor） | 10x 响应提速 | 按 environment |

**关键洞察**：所有成熟产品都是**闭源商业产品**，没有可用的开源 AI SOC 平台。

### 1.2 开源生态现状

| 开源项目 | 定位 | 能力 | 局限 |
|----------|------|------|------|
| **SamiGPT (Blackhat 2025)** | MCP Server + SOC 工作流 | SOC1/SOC2 分层 runbook，TheHive/ELK 集成 | 单机 MCP 工具，无 Gateway/多用户/审批 |
| **Splunk MCP + LangGraph (Omar Santos)** | LangGraph + Splunk MCP 演示 | 自然语言查 Splunk，5 步调查流程 | 演示级，无去重/审批/记忆 |
| **LangGraph** | 通用 Agent 框架 | 状态图编排，human-in-the-loop | 通用框架，安全领域逻辑需全部自建 |
| **CrewAI / AutoGen** | 多 Agent 协作框架 | Agent 角色定义 + 任务分配 | 无安全工具、无审批流、无告警管道 |
| **A2A Protocol (Google, 23.8k stars)** | Agent 互联协议 | JSON-RPC 2.0，Agent Card 发现，流式 | 仅通信协议，不含 Agent 运行时 |

**结论**：没有开箱即用的开源 AI SOC 平台。有零散的安全 MCP 工具和通用 Agent 框架，但缺少：
- 告警去重与合并引擎
- 多级审批流
- 场景记忆 / 专家偏好
- 7×24 事件驱动 + 人工协同双模式
- A2A 企业 Agent 互联

---

## 二、构建策略：不是"开源 vs 自研"，而是"三层架构"

### 2.1 三层架构模型

```
┌─────────────────────────────────────────────────────────┐
│  第一层：安全业务层（必须自研，核心竞争力）                  │
│                                                         │
│  ├── 告警去重 & 合并引擎（5min 窗口，同 IP + 同 IOC）      │
│  ├── 场景记忆 & 专家偏好（模拟平安集团分析师习惯）           │
│  ├── A2A Agent 联邦（IP Agent / UM Agent / AD Agent /    │
│  │   邮件 Agent → A2A 协议互联）                          │
│  ├── 传统安全设备状态监控（配置漂移 / 规则失效检测）         │
│  ├── Hook Engine（6 个安全生命周期事件）                   │
│  ├── Steering（中途注入，真正 Human-in-the-Loop）          │
│  └── 审批流（三级：自动 / 通知可干预 / 必须人工）           │
├─────────────────────────────────────────────────────────┤
│  第二层：Agent 运行时（复用 DeerFlow Harness，不重复造轮子）│
│                                                         │
│  ├── DeerFlowClient（step-by-step 流式 Agent Loop）       │
│  ├── 17 个 Middleware（循环检测 / Token 追踪 / 记忆...）   │
│  ├── MCP 工具加载（标准化安全工具接口）                     │
│  ├── Docker 沙箱（恶意样本隔离）                           │
│  ├── 记忆系统（防抖 + 批量提取 + 持久化）                  │
│  ├── IM 渠道（飞书/钉钉/企微等 7 个渠道）                  │
│  └── Gateway + Frontend（已有，可直接用）                  │
├─────────────────────────────────────────────────────────┤
│  第三层：基础设施层（全部开源，不自研）                      │
│                                                         │
│  ├── Kafka（告警流输入）                                  │
│  ├── PostgreSQL（结果存储 + Session）                      │
│  ├── Qdrant / Milvus（RAG 向量库）                        │
│  ├── 大模型（DeepSeek-V4 / 智谱 GLM-5.1）                 │
│  └── Prometheus + Grafana（监控）                         │
└─────────────────────────────────────────────────────────┘
```

### 2.2 为什么是这个三层结构

| 方案 | 评估 | 结论 |
|------|------|------|
| **全买商业产品** | CrowdStrike/Palo Alto 生态绑定，数据锁死，无法接平安内部系统（UM/AD/邮件），年费百万级 | ❌ 不适用 |
| **全自研** | 需要 6-8 周重建中间件链、沙箱、记忆系统、流式输出、IM 渠道 | ❌ 浪费 |
| **用通用框架（LangGraph/CrewAI）+ 自研安全层** | 通用框架不含安全特有逻辑，中间件能力弱，无审批/Steering/Hook | ⚠️ 可行但大量自研 |
| **DeerFlow Fork + 自研安全层（推荐）** | 复用已有 17 个中间件 + 沙箱 + 记忆 + IM + MCP + Gateway，专注安全业务 | ✅ 最优 |

---

## 三、核心能力详解

### 3.1 告警去重 & 合并

**问题**：1000 条告警/小时 → 1000 个并发 Agent → API 费用爆了

**方案**：
```
5 分钟窗口 + 同 IP + 同 IOC → dedup_key → 合并为 1 个 Task
效果：1000 条 → 去重后 200 条 → 费用降 80%
```

去重发生在 `PreSession` Hook，在进入 Agent 之前就拦截。合并后的告警作为附加上下文注入已有 Session。

### 3.2 场景记忆 & 专家偏好

**问题**：每个分析师研判风格不同，Agent 应该学习并模拟

**方案**：
```
记忆类型：
  ├── Short-term Memory：当前 session 的告警上下文（DeerFlow 内置）
  ├── Long-term Memory：分析师偏好（DeerFlow MemoryMiddleware）
  │     - "张三偏好：先查 CMDB 再查 SIEM"
  │     - "李四偏好：高优告警直接跳 VT，走内部情报库"
  │     - "王五偏好：钓鱼邮件先看 SPF/DKIM/DMARC"
  ├── Playbook Memory：处置手册（RAG 向量检索）
  │     - 按告警类型的标准化处置流程
  │     - 历史相似告警的处置结果
  └── Organization Memory：组织级知识
        - 内部资产白名单
        - 已知蜜罐 IP
        - 关键业务系统列表
```

**技术实现**：DeerFlow 已有 `MemoryMiddleware`（防抖 + 批量提取 + 持久化），在此基础上：
1. 为每个分析师建 memory profile
2. Agent 启动时加载对应分析师的偏好作为 bootstrap
3. CLI 命令：`secops config set preference "先查内部情报再查 VT"`

### 3.3 A2A Agent 联邦 — 平安集团企业 Agent 互联

**问题**：IP 归属查询、UM 统一身份、AD 域控、邮件系统是分散的不同系统

**方案**：每个系统封装为一个独立 Agent，通过 A2A 协议互联

```
┌──────────────────────────────────────────────────────┐
│                 AI SOC Gateway（编排中心）              │
│                                                      │
│  A2A Client → 发现 & 调用以下 Agent                   │
│                                                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐│
│  │ IP Agent │ │ UM Agent │ │ AD Agent │ │ 邮件     ││
│  │          │ │          │ │          │ │ Agent    ││
│  │ IP 归属  │ │ 统一身份 │ │ 域控查询 │ │ 邮件追踪 ││
│  │ 资产关联 │ │ 权限验证 │ │ 异常登录 │ │ SPF 检查 ││
│  │ 子网信息 │ │ 人员信息 │ │ 组策略   │ │ 附件分析 ││
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘│
│        ↑            ↑            ↑           ↑       │
│        └────────────┴────────────┴───────────┘       │
│                    A2A Protocol                       │
│              (JSON-RPC 2.0 over HTTP)                 │
└──────────────────────────────────────────────────────┘
```

**A2A 协议关键特性**：
- **Agent Card**：每个 Agent 注册自己的能力描述（JSON），Gateway 通过 Agent Card 自动发现
- **Opaque 协作**：Agent 之间不暴露内部状态/记忆/工具，只交换结果
- **多模态**：支持文本、文件、结构化 JSON 数据交换
- **流式**：支持 SSE 流式响应
- **企业级**：支持认证、授权、可观测性

**实现步骤**：
1. 每个企业系统（IP/UM/AD/邮件）封装为 MCP Server（暴露工具）
2. 每个系统独立部署为 A2A Agent（暴露 Agent Card + Task 接口）
3. SOC Gateway 作为 A2A Client，按需调用
4. 未来可扩展：威胁情报 Agent、EDR Agent、云安全 Agent

### 3.4 传统安全设备状态监控

**问题**：防火墙规则、WAF 配置、IDS/IPS 签名更新后，是否还有效？是否被意外修改？

**方案**：Agent 定期巡检 + 变更检测

```
巡检方式：
  1. 定时任务（cron）触发 Agent → 查询当前配置 → 与基线对比
  2. 配置变更 Webhook → 实时触发 Agent → 验证变更是否合规
  3. 人工触发：secops audit-config --device firewall-01

检测内容：
  - 防火墙规则是否被意外放宽
  - WAF 规则是否过期（CVE 已修复但规则还在）
  - IDS/IPS 签名是否最新
  - 安全策略是否与 CMDB 资产匹配
  - 白名单/黑名单是否有重复/冲突

实现：
  通过 MCP 工具封装各设备 API
  Agent 对比配置基线 → 发现异常 → 自动创建工单或告警
```

### 3.5 CLI 模拟专家习惯

**核心理念**：分析师的主界面是终端，不是浏览器

```bash
# 一条命令完成研判（2 秒）
$ secops triage ALERT-1234

# 模拟专家张三的研判风格
$ secops triage ALERT-1234 --expert zhangsan

# 后台并行处理多个告警
$ secops triage ALERT-0043 --bg
$ secops triage ALERT-0044 --bg
$ secops sessions   # 查看所有活跃 session

# 中途纠偏（Steering）
$ secops steer SESS-abc123 "这个 IP 是蜜罐，别封"

# 审批处置
$ secops approve TASK-001

# 主动威胁狩猎
$ secops hunt --query "过去 7 天来自 10.0.0.0/8 的异常登录"

# 设备配置巡检
$ secops audit-config --device firewall-01
```

---

## 四、技术路线：DeerFlow Fork + 安全层

### 4.1 为什么 Fork DeerFlow

| 维度 | LangGraph | CrewAI | Claude Code SDK | **DeerFlow** |
|------|-----------|--------|-----------------|-------------|
| Agent 运行时 | 开源 Python | 开源 Python | **闭源 CLI 二进制** | **开源 Python** |
| 自定义 Middleware | 有限 | 无 | 仅 10 种 Hook | **17 个 + 可无限扩展** |
| 工具执行管道 | 基础 | 基础 | 闭源 | **洋葱模型，完全可控** |
| 消息级操作 | 有限 | 无 | 无 | **替换/插入/复制** |
| 多用户并发 | 需自建 | 需自建 | 单用户 | **Gateway + ThreadData 隔离** |
| API 网关 | 无 | 无 | 无 | **内置 FastAPI Gateway** |
| 记忆系统 | 无 | 无 | 无 | **MemoryMiddleware** |
| 沙箱 | 无 | 无 | 内置 | **Docker + Local 双模式** |
| IM 渠道 | 无 | 无 | 无 | **7 个渠道** |
| 前端 | 无 | 无 | 无 | **Next.js + React** |
| MCP 支持 | ✅ | ❌ | ✅ | **✅** |
| 安全治理层 | 无 | 无 | 有限 | **需自建 → 本项目核心** |

**DeerFlow 省掉的工作量**：中间件链 2 周 + 沙箱 1 周 + 记忆 1 周 + 流式 3 天 + 工具加载 3 天 + IM 渠道 1 周 = **约 6-8 周**

### 4.2 需要自建的安全层（核心竞争力）

| 模块 | 工作量 | 价值 |
|------|--------|------|
| Security Gateway daemon | 2 周 | 统一控制面，所有入口汇聚 |
| Hook Engine（6 事件） | 1 周 | 安全治理核心 |
| Dedup Engine | 3 天 | 费用降 80% |
| Steering Queue | 1 周 | 真正 Human-in-the-Loop |
| Agent Router + 5 个专用 Agent | 2 周 | 场景化 AI |
| secops CLI | 2 周 | 分析师一等公民界面 |
| 审批流（三级） | 1 周 | 安全合规 |
| 安全工具集（MCP） | 3 周 | 20+ 工具对接平安内部系统 |
| A2A Agent 封装 | 2 周 | IP/UM/AD/邮件互联 |
| 场景记忆 & 专家偏好 | 1 周 | 个性化研判 |
| Plugin 系统 | 1 周 | 可扩展 |
| **总计** | **约 16 周（4 人月）** | |

### 4.3 开发路线图

```
Phase 1（3 周）：CLI + Gateway + Triage Agent
  → secops triage ALERT-1234 跑通
  → Kafka → Gateway → DeerFlowClient → 结果存 PostgreSQL
  → 基础去重 + 白名单过滤 + 报告生成

Phase 2（3 周）：Steering + Hook Engine + 审批
  → secops steer / approve / reject
  → 6 个 Hook 事件全部就绪
  → 三级审批模型上线
  → Response Agent（所有操作必须审批）

Phase 3（4 周）：A2A + 多 Agent + 记忆 + 巡检
  → IP/UM/AD/邮件 4 个 A2A Agent
  → Investigate + Malware + ThreatIntel 3 个 Agent
  → 场景记忆 & 专家偏好
  → 安全设备配置巡检
  → Plugin 系统

Phase 4（持续）：智能升级
  → RAG 知识库（内部安全文档 + 处置手册）
  → 多模型路由（简单告警用便宜模型，复杂用强模型）
  → 告警关联分析（攻击链路还原）
  → SFT 微调（安全研判场景）
```

---

## 五、成本分析

### 5.1 开发成本

| 项目 | 成本 |
|------|------|
| 4 人 × 16 周（Phase 1-3） | 约 4 人月研发投入 |
| 大模型 API（DeepSeek-V4） | ~¥0.18/alert × 200 alerts/h × 8h × 22d ≈ ¥6,300/月 |
| 基础设施（Kafka + PG + Qdrant） | 已有或 ¥5,000/月（云） |
| **对比：商业 AI SOC 平台** | **¥50-200 万/年** |

### 5.2 效果预期

| 指标 | 当前 | Phase 1 后 | Phase 3 后 |
|------|------|-----------|-----------|
| 告警自动研判率 | ~0% | 60%（误报+低优） | 90%+ |
| 单告警研判时间 | 30-60 min | 2-5 min | 50 sec |
| 分析师日处理量 | 20-30 条 | 100+ 条 | 500+ 条 |
| MTTR | 数小时 | 30 min | 5 min |
| API 费用/告警 | — | ¥0.27 | ¥0.18 |

---

## 六、风险与对策

| 风险 | 对策 |
|------|------|
| 大模型幻觉导致误判 | 置信度阈值 + 低于阈值自动升级人工 + JSONL 全量审计 |
| 敏感操作误执行 | Hook Engine 强制审批 + Steering 纠偏 + 回滚方案 |
| DeerFlow 框架升级断裂 | Fork 维护，不跟主仓库同步，安全层与 Harness 松耦合 |
| A2A Agent 内部系统改造 | MCP 封装层隔离，内部系统不改代码，只暴露 API |
| 单点故障 | Gateway 持久化 Session 到 PostgreSQL，重启自动恢复 |

---

## 七、总结与建议

### 核心结论

1. **不买商业产品**：闭源、生态绑定、无法接平安内部系统、年费百万级
2. **不全自研**：通用 Agent 能力（中间件/沙箱/记忆/IM）已有成熟开源实现
3. **Fork DeerFlow + 自研安全层**：复用通用 Agent 运行时，专注安全业务逻辑
4. **A2A 协议互联**：平安集团 IP/UM/AD/邮件 Agent 通过 A2A 联邦化
5. **CLI-first**：分析师的主界面是终端，不是浏览器

### 行动建议

```
第 1 步：Fork DeerFlow，搭建 Security Gateway 骨架（1 周）
第 2 步：实现 Triage Agent + 3 个基础工具（VT/SIEM/CMDB）（2 周）
第 3 步：跑通 Kafka → Gateway → Agent → 结果 链路（demo 给领导看）
第 4 步：按 Phase 1-3 路线图推进
```

### 对标产品

我们的方案本质上是 **"开源版的 Torq HyperSOC + D3 Morpheus"**，但：
- **可控**：100% 开源，可审计
- **可定制**：深度对接平安内部系统
- **低成本**：无年费，只有模型 API 费用
- **先进**：A2A 协议 + 场景记忆 + Steering 人工协同

---

## 附录：参考资料

- [Top 10 Agentic SOC Platforms (Stellar Cyber)](https://stellarcyber.ai/learn/top-10-agentic-soc-platforms/)
- [Best AI SOC Platforms 2026 (Torq)](https://torq.io/blog/ai-soc-platform/)
- [Best AI SOC Platforms 2026 (D3 Security)](https://d3security.com/blog/ai-soc-platforms-2026/)
- [A2A Protocol (Google, 23.8k stars)](https://github.com/a2aproject/A2A) — v1.0.0 released Mar 2026
- [SamiGPT: AI SOC Agent (Blackhat 2025)](https://github.com/M507/AI-SOC-Agent)
- [Building AI SOC Analyst with Splunk MCP + LangGraph (Omar Santos)](https://becomingahacker.org/building-an-ai-powered-soc-analyst-with-splunk-mcp-langchain-and-langgraph-22847005eaf1)
- [LangGraph Multi-Agent with A2A](https://github.com/5enxia/langgraph-multiagent-with-a2a)
- 内部设计文档：`.notes/security-agent-platform-design-v3.md`
- Claude Code SDK 调研：`.notes/research-claude-code-sdk.md`
