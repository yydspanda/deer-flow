# AI Agent 协议全景报告

> 调研时间：2026 年 5 月 | 版本：v1.0

---

## 目录

1. [概述](#1-概述)
2. [协议分层架构](#2-协议分层架构)
3. [核心协议详解](#3-核心协议详解)
   - 3.1 [MCP — Agent 与工具的桥梁](#31-mcp--agent-与工具的桥梁)
   - 3.2 [A2A — Agent 与 Agent 的协作](#32-a2a--agent-与-agent-的协作)
   - 3.3 [AG-UI — Agent 与用户的界面](#33-ag-ui--agent-与用户的界面)
   - 3.4 [ANP — 去中心化 Agent 网络](#34-anp--去中心化-agent-网络)
   - 3.5 [ACP — 多框架编排层](#35-acp--多框架编排层)
   - 3.6 [AP2 — Agent 支付协议](#36-ap2--agent-支付协议)
4. [编辑器专用协议](#4-编辑器专用协议)
   - 4.1 [LSP — 语言服务器协议](#41-lsp--语言服务器协议)
   - 4.2 [ACP (Zed) — Agent Client Protocol](#42-acp-zed--agent-client-protocol)
5. [全景对比矩阵](#5-全景对比矩阵)
6. [厂商采用矩阵](#6-厂商采用矩阵)
7. [如何选择协议栈](#7-如何选择协议栈)
8. [趋势与展望](#8-趋势与展望)
9. [参考资源](#9-参考资源)

---

## 1. 概述

AI Agent 协议是标准化 AI 智能体之间、智能体与外部系统之间、智能体与用户之间交互规则的开放规范。在 2024 年之前，每个 AI 框架都有自己的工具调用约定、Agent 协调机制和通信方式，导致大量重复造轮子和互操作性问题。

2024-2026 年间，一系列开放协议快速涌现并形成共识，构成了当前 AI Agent 生态的"协议栈"：

```
┌─────────────────────────────────────────────────────┐
│                  用户 / 前端应用                       │
│                    AG-UI 层                          │
├─────────────────────────────────────────────────────┤
│              Agent ↔ Agent 协作层                     │
│              A2A / ACP / ANP                        │
├─────────────────────────────────────────────────────┤
│              Agent ↔ 工具/数据层                       │
│                  MCP                                │
├─────────────────────────────────────────────────────┤
│              商业交易层                               │
│            AP2 / ACP(Commerce)                      │
└─────────────────────────────────────────────────────┘
```

**核心原则**：这些协议不是竞争关系，而是**互补组合**。生产级 Agent 系统通常同时使用 2-3 个协议。

---

## 2. 协议分层架构

| 层次 | 解决的问题 | 对应协议 | 类比 |
|------|-----------|---------|------|
| **Agent ↔ 工具** | Agent 如何调用外部 API、数据库、文件系统 | MCP | USB-C 接口 |
| **Agent ↔ Agent** | 多个 Agent 之间如何发现、协调、委派任务 | A2A, ACP, ANP | HTTP/TCP |
| **Agent ↔ 用户** | Agent 如何向用户展示状态、接收输入 | AG-UI | WebSocket/REST API |
| **Agent ↔ 商业** | Agent 如何自主完成购买、支付等交易 | AP2, UCP | 支付网关 |

---

## 3. 核心协议详解

### 3.1 MCP — Agent 与工具的桥梁

| 属性 | 详情 |
|------|------|
| **全称** | Model Context Protocol（模型上下文协议） |
| **创建者** | Anthropic |
| **发布时间** | 2024 年 11 月 |
| **治理** | Anthropic 主导的开放规范 |
| **下载量** | 9700 万+（截至 2026 年 Q1） |
| **传输方式** | JSON-RPC 2.0 over stdio / HTTP+SSE |

**架构角色**：

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   MCP Host   │────►│  MCP Client  │────►│  MCP Server  │
│ (Claude/IDE) │     │  (连接器)     │     │  (工具暴露)   │
└──────────────┘     └──────────────┘     └──────────────┘
                                                │
                                    ┌───────────┼───────────┐
                                    ▼           ▼           ▼
                                数据库       API 服务     文件系统
```

- **Host**：AI 应用（Claude Desktop、VS Code Copilot、Cursor 等）
- **Client**：Host 内部的连接器，与 Server 保持 1:1 链接
- **Server**：轻量进程，暴露 Tools（工具）、Resources（资源）、Prompts（提示词）

**核心能力**：
- 能力发现：Agent 动态了解可用工具
- 工具抽象：将复杂数据库/API 包装为标准化接口
- 结构化上下文：标准化的上下文格式
- 解耦扩展：新增数据源无需修改 Agent 逻辑

**为什么 MCP 成为事实标准**：
- 规范极简，一个下午可读完
- 官方 SDK（TypeScript/Python），50 行代码可构建简单 Server
- 从 Anthropic 专属快速扩展为跨厂商标准

---

### 3.2 A2A — Agent 与 Agent 的协作

| 属性 | 详情 |
|------|------|
| **全称** | Agent-to-Agent Protocol（Agent 间协议） |
| **创建者** | Google |
| **发布时间** | 2025 年 4 月 |
| **合作伙伴** | 50+（Salesforce、SAP、ServiceNow、Atlassian 等） |
| **传输方式** | HTTPS + JSON，异步任务生命周期 |
| **发现机制** | Agent Card（`/.well-known/agent.json`） |

**架构模型 — Task-Based Actor**：

```
┌──────────┐     ┌──────────────┐     ┌──────────────┐
│   User   │────►│ Client-Agent │────►│ Remote-Agent │
│  (用户)   │     │  (编排者)     │     │  (执行者)     │
└──────────┘     └──────────────┘     └──────────────┘
                       │                     │
                       │    Agent Card       │
                       │    (能力描述)        │
                       └─────────────────────┘
```

**三步流程**：
1. **发现**：Client-Agent 查询 Remote-Agent 的 Agent Card，选择最合适的
2. **授权**：验证权限，授予必要的控制范围
3. **通信**：通过 HTTPS + JSON-RPC 分发任务，SSE 流式返回结果

**任务生命周期**：`submitted → working → input-required → completed / canceled / failed`

**核心能力**：
- 点对点委派，无中心瓶颈
- 动态协商（propose/accept/counter-offer）
- 实时状态同步（SSE 流式）
- 标准化身份认证

**与 MCP 的关系**：互补而非竞争。A2A 处理 Agent 间通信，每个 Agent 内部用 MCP 访问自己的工具。

---

### 3.3 AG-UI — Agent 与用户的界面

| 属性 | 详情 |
|------|------|
| **全称** | Agent-User Interaction Protocol（Agent-用户交互协议） |
| **创建者** | CopilotKit + 社区 |
| **发布时间** | 2025 年末 |
| **传输方式** | HTTP / WebSocket，事件驱动 |
| **事件类型** | ~16 种（文本流、工具调用、状态更新等） |

**解决的问题**：

传统 REST/GraphQL 是请求-响应模型，但 Agent 是**长期运行、流式输出、非确定性**的。AG-UI 为此提供了标准化的事件协议。

```
┌────────────────┐     事件流     ┌────────────────┐
│   前端应用      │◄────────────►│   Agent 后端    │
│  (React/Vue)   │  AG-UI 事件   │  (LangGraph等)  │
└────────────────┘               └────────────────┘
```

**核心特性**：
- 事件驱动：文本流、工具调用、状态更新等 ~16 种标准事件
- 双向通信：前端 ↔ Agent 双向实时交互
- 多模态：支持文本、语音、结构化数据混合
- Agent 组合：支持子 Agent 调用（递归）

**框架支持**（2026 年 Q1）：
- **一等支持**：LangGraph、CrewAI、Microsoft Agent Framework、Google ADK、AWS Strands、Pydantic AI、LlamaIndex
- **进行中**：OpenAI Agent SDK、Cloudflare Agents

---

### 3.4 ANP — 去中心化 Agent 网络

| 属性 | 详情 |
|------|------|
| **全称** | Agent Network Protocol（Agent 网络协议） |
| **创建者** | 开源社区 |
| **传输方式** | JSON-LD over HTTPS |
| **身份模型** | W3C 去中心化标识符（DID） |

**三层架构**：

| 层次 | 技术 | 功能 |
|------|------|------|
| 身份层 | W3C DID | 加密身份验证 |
| 传输层 | JSON-LD | 语义化消息格式 |
| 协议协商层 | 动态协商 | 运行时协商消息 schema |

**核心特性**：
- 完全去中心化，无中心注册表
- DID 加密身份验证，无需信任第三方
- 端到端加密
- AI 原生交互（机器可读接口，非爬虫）
- 语义发现：Agent 发布元数据供其他 Agent 发现

**适用场景**：跨组织 Agent 协作、Agent 市场、无中心信任的商业网络

---

### 3.5 ACP — 多框架编排层

> 注意：有两个 ACP。此处描述的是 IBM/Linux Foundation 的 Agent Communication Protocol。

| 属性 | 详情 |
|------|------|
| **全称** | Agent Communication Protocol（Agent 通信协议） |
| **创建者** | IBM Research → Linux Foundation (BeeAI) |
| **传输方式** | REST / HTTPS |
| **架构** | Router-Agent 拓扑 |

**核心定位**：多框架编排。当一个企业同时运行 LangChain、AutoGen、CrewAI 等多个 Agent 框架时，ACP 提供统一入口。

```
┌──────────────────────────────────┐
│          ACP Router              │
│       (统一入口 + 路由)           │
└───────┬─────────┬───────────────┘
        │         │         │
   ┌────▼───┐ ┌──▼────┐ ┌──▼─────┐
   │LangChain│ │AutoGen│ │ CrewAI │
   │  Agent  │ │Agent  │ │ Agent  │
   └────────┘ └───────┘ └────────┘
```

**核心特性**：
- 有状态消息路由（基于历史上下文）
- REST 接口，可用 cURL/Postman 测试
- 被动发现：Agent 以 YAML 发布元数据
- 多模态：支持 MIME 类型多部分消息
- Linux Foundation 治理，变更需社区审批

**商业扩展**：ACP 还定义了 Agent 商业交易词汇表（询价、报价、谈判、确认），使其也覆盖商业协议层。

---

### 3.6 AP2 — Agent 支付协议

| 属性 | 详情 |
|------|------|
| **全称** | Agent Payment Protocol（Agent 支付协议） |
| **创建者** | Google |
| **传输方式** | HTTPS |
| **核心** | 加密签名授权委托（Mandate） |

**三种凭证类型**：

| 凭证 | 场景 | 示例 |
|------|------|------|
| **Cart Mandate** | 用户在场时 | "帮我找个 $100 以下的剃须刀" → Agent 填购物车，用户确认后支付 |
| **Intent Mandate** | 用户不在场时预授权 | "演唱会票一开售就买" → 预先签授权，Agent 自主执行 |
| **Payment Mandate** | 向银行证明授权 | 证明交易经合法 Agent 执行且符合前两种 Mandate |

**职责分离架构**：购物 Agent、商家端点、凭证提供者、支付处理方、银行网络——各司其职，无单点权力集中。

---

## 4. 编辑器专用协议

### 4.1 LSP — 语言服务器协议

| 属性 | 详情 |
|------|------|
| **全称** | Language Server Protocol（语言服务器协议） |
| **创建者** | Microsoft |
| **发布时间** | 2016 年 |
| **传输方式** | JSON-RPC over stdio/TCP/WebSocket |
| **用途** | 编辑器与语言服务器之间的通信 |

**解决的问题**：在 LSP 之前，每个编辑器需要为每种语言单独实现代码补全、跳转定义、重构等功能。LSP 将语言智能抽象为独立服务。

```
┌──────────┐     JSON-RPC     ┌──────────────┐
│  编辑器   │◄───────────────►│  Language     │
│ (VS Code) │                 │  Server       │
│           │                 │ (Go/Python/…) │
└──────────┘                 └──────────────┘
```

**关键点**：LSP 不是 AI 协议，它是**代码智能**协议。但在 AI Agent 架构中常被拿来对比，因为 ACP (Zed) 借鉴了 LSP 的设计理念。

### 4.2 ACP (Zed) — Agent Client Protocol

| 属性 | 详情 |
|------|------|
| **全称** | Agent Client Protocol（Agent 客户端协议） |
| **创建者** | Zed Industries |
| **传输方式** | JSON-RPC over stdio |
| **定位** | 编辑器/应用与 AI Agent 之间的通信 |

**与 LSP 的关系**：

| 对比 | LSP | ACP (Zed) |
|------|-----|-----------|
| 解决 | 编辑器 ↔ 语言服务器 | 编辑器 ↔ AI Agent |
| 功能 | 代码补全/跳转/重构 | Agent 多步推理/工具调用/文件操作 |
| 传输 | JSON-RPC over stdio | JSON-RPC over stdio |

**在 Obsidian 中的应用**：Obsidian Agent Client 插件通过 ACP 连接 Claude Code CLI，实现 AI 直接读写笔记库。

---

## 5. 全景对比矩阵

| 维度 | MCP | A2A | AG-UI | ANP | ACP (IBM) | AP2 | ACP (Zed) | LSP |
|------|-----|-----|-------|-----|-----------|-----|-----------|-----|
| **通信主体** | Agent↔工具 | Agent↔Agent | Agent↔用户 | Agent↔Agent | Agent↔Agent | Agent↔支付 | 编辑器↔Agent | 编辑器↔语言服务 |
| **治理** | Anthropic | Google | 社区 | 开源社区 | Linux Foundation | Google | Zed | Microsoft |
| **传输** | JSON-RPC/stdio | HTTPS+JSON | HTTP/WS | JSON-LD/HTTPS | REST/HTTPS | HTTPS | JSON-RPC/stdio | JSON-RPC/stdio |
| **发现** | 静态配置 | Agent Card | — | DID 解析 | Router 注册 | — | — | — |
| **身份** | 绑定 Host | Agent Card | — | W3C DID | API Key/OAuth | Mandate | — | — |
| **成熟度** | 高（16 个月+） | 中（~12 个月） | 中 | 低 | 低 | 低 | 低 | 高（10 年+） |
| **生态规模** | 9700 万下载 | 50+ 合作伙伴 | 10+ 框架 | 早期 | 早期 | 早期 | 小众 | 极广泛 |

---

## 6. 厂商采用矩阵

| 厂商/平台 | MCP | A2A | ACP(IBM) | AG-UI |
|-----------|-----|-----|----------|-------|
| Anthropic (Claude) | **创建者** | 客户端 | — | — |
| Google (Gemini) | 完整 | **创建者** | — | 支持 |
| OpenAI (GPT) | 完整 | 合作伙伴 | — | 进行中 |
| Microsoft (Copilot) | 完整 | 合作伙伴 | — | 支持 |
| Amazon (Bedrock) | 完整 | 合作伙伴 | — | 支持 |
| IBM (watsonx) | 完整 | 合作伙伴 | **创建者** | — |
| Salesforce (Einstein) | 完整 | 合作伙伴 | — | — |
| LangChain | 完整 | 完整 | 计划中 | 支持 |
| AutoGen | 完整 | 完整 | — | — |
| CrewAI | 完整 | 完整 | 计划中 | 支持 |
| Pydantic AI | — | — | — | 支持 |
| LlamaIndex | — | — | — | 支持 |

**关键观察**：MCP 的跨厂商采用最为完整，从 Anthropic 专属到行业标准的转变速度前所未有。

---

## 7. 如何选择协议栈

### 场景 1：单 Agent + 工具调用（最常见）

```
协议：仅 MCP
适用：90% 的 Agent 应用场景
示例：ChatBot 调用数据库/API/文件系统
```

### 场景 2：多 Agent 协作

```
协议：MCP + A2A
适用：问题确实无法被单 Agent + 多工具解决
架构：每个 Agent 用 MCP 访问自己的工具，Agent 间用 A2A 委派任务
```

### 场景 3：Agent 面向前端用户

```
协议：MCP + A2A + AG-UI
适用：需要实时交互的 Agent 应用
架构：MCP 管工具，A2A 管协作，AG-UI 管前端交互
```

### 场景 4：Agent 自主交易

```
协议：MCP + A2A + AP2/ACP(Commerce)
适用：自主采购、B2B 交易、Agent 市场
注意：先做好 MCP 和 A2A，再考虑商业层
```

### 场景 5：编辑器集成 AI Agent

```
协议：ACP (Zed) + MCP
适用：Obsidian/编辑器 中嵌入 AI Agent
架构：ACP 连接 Agent 到编辑器，Agent 内部用 MCP 调工具
```

### 实施路线图建议

```
第一阶段（2-6 周）：构建 MCP Server 覆盖现有工具/API/数据源
第二阶段（4-12 周）：设计多 Agent 拓扑，实现 A2A Agent Card 和委派
第三阶段（6-24 月）：评估 AG-UI 前端集成和 AP2/ACP 商业交易
```

---

## 8. 趋势与展望

### 8.1 协议融合而非统一

不会有单一协议统一所有场景。协议栈模式（MCP + A2A + AG-UI）将持续发展，各层保持独立演进。

### 8.2 MCP 成为新的"HTTP"

MCP 的采用速度和跨厂商支持使其正在成为 AI 工具调用的基础设施标准，类比 HTTP 对 Web 的意义。

### 8.3 安全成为核心议题

所有协议都面临安全挑战：
- **MCP**：工具可执行任意代码，工具返回结果可能包含 prompt 注入
- **A2A**：跨组织任务交接时的 PII 泄露
- **ACP**：Router 成为单点策略执行点
- **ANP**：信任完全依赖 DID 验证
- **AP2**：自主交易的授权边界

跨协议的安全执行层将成为刚需。

### 8.4 Agent Skills 生态

协议解决"如何通信"，Skills 解决"如何做事"。垂直领域 Skills（如 Obsidian Skills）将成为 Agent 与专业工具深度融合的标准模式。

### 8.5 去中心化 vs 中心化

ANP 的去中心化 DID 模型与 A2A/ACP 的中心化发现模型代表了两种哲学，短期内中心化方案因简单易用将占主导，长期看去中心化在跨组织场景中有不可替代的价值。

---

## 9. 参考资源

- [MCP 官方规范](https://spec.modelcontextprotocol.io/)
- [A2A 官方文档](https://google.github.io/A2A/)
- [AG-UI 官方文档](https://docs.ag-ui.com/)
- [ANP GitHub](https://github.com/agent-network-protocol)
- [ACP (IBM/BeeAI)](https://github.com/i-am-bee/acp)
- [AP2 规范](https://github.com/google/ap2)
- [ACP (Zed)](https://agentclientprotocol.com/)
- [AI Agent Protocol Ecosystem Map 2026](https://www.digitalapplied.com/blog/ai-agent-protocol-ecosystem-map-2026-mcp-a2a-acp-ucp)
- [MCP vs A2A vs ACP vs ANP 完整指南](https://data443.com/blog/ai-agent-protocols-explained-mcp-vs-a2a-vs-acp-vs-anp/)
- [GetStream: AI Agent Protocols Guide](https://getstream.io/blog/ai-agent-protocols/)

---

> 本报告基于 2026 年 5 月的公开信息编写。协议生态快速演进，具体能力和厂商支持可能已发生变化。
