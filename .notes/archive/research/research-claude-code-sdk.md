# Claude Code SDK 调研报告

> 调研时间：2026-05-12
> 调研目的：评估 Claude Code SDK 作为安全 Agent 平台基础的可行性

---

## 一、SDK 本质架构

**核心结论：SDK 是闭源 CLI 二进制的 Python 包装器，不是纯 Python 实现的 Agent 框架。**

```
你的 Python 代码
  │
  ├── claude-agent-sdk（Python 包，开放源码）
  │     ├── query() / ClaudeSDKClient → 启动 CLI 子进程
  │     ├── Transport 层（stdin/stdout JSON 协议通信）
  │     ├── @tool 装饰器（自定义 MCP 工具）
  │     └── Hooks（10 种事件回调）
  │
  └── Claude Code CLI（闭源 Node.js 二进制）  ← 真正的 Agent 运行时
        ├── Agent Loop（规划→执行→观察→重复）
        ├── 上下文压缩（compaction，PreCompact Hook 可介入）
        ├── 子代理编排（SubagentStart/Stop 生命周期管理）
        ├── 工具系统（Bash/Read/Write/Edit/Glob/Grep/WebFetch/WebSearch/TodoWrite/MCP...）
        └── 沙箱执行
```

### 关键证据

1. **必须安装 Claude Code CLI**：SDK 有 `CLINotFoundError` 错误类型，`ClaudeAgentOptions` 有 `cli_path` 参数
2. **通过子进程通信**：`Transport` 抽象基类描述 subprocess 模式（write JSON + 换行，read 解析后的 JSON）
3. **错误类 `ProcessError`** 有 `exit_code` 和 `stderr`，证实管理的是外部进程
4. **SDK 本身不实现 LLM 调用、上下文压缩、工具执行**，所有智能逻辑在 CLI 进程中运行

### 安装

```bash
# 安装 CLI（闭源二进制）
npm install -g @anthropic-ai/claude-code

# 安装 SDK（Python 包）
pip install claude-agent-sdk

# 认证
claude login
```

前置条件：Python 3.12+，Claude Code CLI 已安装

---

## 二、模型配置：可以用第三方模型

**结论：可以。** 通过 `ANTHROPIC_BASE_URL` 环境变量指向任何兼容 Anthropic Messages API 格式的端点。

### 原理

Claude Code CLI 通过 HTTP 调用模型 API，默认指向 `api.anthropic.com`，但可以通过环境变量替换为任何兼容端点。智谱（Z.ai）、MiniMax、Kimi K2 等国产模型都提供了兼容 Anthropic Messages API 格式（`/v1/messages`）的端点。

### 已验证可用的第三方模型

| 模型 | Base URL | 说明 |
|------|----------|------|
| 智谱 GLM 4.7/5.1 | `https://api.z.ai/api/anthropic` | 推荐使用，性价比高 |
| MiniMax 2.1 | `https://api.minimax.io/anthropic` | 支持订阅和按量计费 |
| Kimi K2 | `https://api.moonshot.ai/anthropic` | Moonshot 出品 |
| LiteLLM 网关 | `https://litellm-server:4000` | 统一网关，可路由到任意模型 |

### 配置方式

**方式 1：SDK 代码中配置**

```python
from claude_agent_sdk import query, ClaudeAgentOptions

options = ClaudeAgentOptions(
    model="glm-5.1",
    env={
        "ANTHROPIC_AUTH_TOKEN": "你的智谱api_key",
        "ANTHROPIC_BASE_URL": "https://api.z.ai/api/anthropic",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.1",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.1",
    },
)

async for message in query(prompt="分析这段日志", options=options):
    print(message)
```

**方式 2：全局配置文件 `~/.claude/settings.json`**

```json
{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "你的智谱api_key",
    "ANTHROPIC_BASE_URL": "https://api.z.ai/api/anthropic",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.1",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.1"
  }
}
```

**方式 3：跳过 Anthropic 账号认证**

编辑 `~/.claude.json`，加入 `"hasCompletedOnboarding": true`，跳过 Pro+ 账号认证流程。

### 关键环境变量

| 变量 | 说明 |
|------|------|
| `ANTHROPIC_BASE_URL` | API 端点地址（替换为第三方） |
| `ANTHROPIC_AUTH_TOKEN` | 认证 token（第三方 API key） |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | sonnet 别名映射到的模型 |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` | opus 别名映射到的模型 |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` | haiku 别名映射到的模型 |
| `CLAUDE_CODE_SUBAGENT_MODEL` | 子代理使用的模型 |
| `ANTHROPIC_CUSTOM_MODEL_OPTION` | 自定义模型选项（出现在 /model 选择器） |

---

## 三、SDK 功能详解

### 3.1 两种 API 风格

**单次查询（query）**

```python
async for message in query(prompt="创建一个 web server", options=options):
    print(message)
```

**持续会话（ClaudeSDKClient）**

```python
async with ClaudeSDKClient(options=options) as client:
    await client.query("创建 hello.py")
    async for message in client.receive_response():
        print(message)
    await client.query("文件里有什么？")  # 保持上下文
    async for message in client.receive_response():
        print(message)
```

### 3.2 子代理

```python
from claude_agent_sdk.types import AgentDefinition

options = ClaudeAgentOptions(
    agents={
        "researcher": AgentDefinition(
            description="研究代理",
            prompt="你是研究专家...",
            tools=["WebSearch", "WebFetch"],
            model="haiku",
            background=True,  # 非阻塞后台运行
        ),
        "coder": AgentDefinition(
            description="编码代理",
            prompt="你写高质量代码...",
            model="sonnet",
        ),
    }
)
```

- 子代理在隔离上下文中运行
- `background=True` 可并行执行
- Hook 事件：`SubagentStart`、`SubagentStop`

### 3.3 自定义工具（MCP）

```python
from claude_agent_sdk import tool, create_sdk_mcp_server, ClaudeAgentOptions

@tool("calculate", "执行计算", {"expression": str})
async def calculate(args):
    result = eval(args["expression"], {"__builtins__": {}})
    return {"content": [{"type": "text", "text": f"Result: {result}"}]}

server = create_sdk_mcp_server(name="utils", tools=[calculate])
options = ClaudeAgentOptions(
    mcp_servers={"utils": server},
    allowed_tools=["mcp__utils__calculate"],
)
```

也支持外部 MCP 服务器（stdio / SSE / HTTP）。

### 3.4 Hooks（10 种事件）

| Hook 事件 | 时机 |
|-----------|------|
| `PreToolUse` | 工具执行前（可拦截） |
| `PostToolUse` | 工具执行后 |
| `PostToolUseFailure` | 工具执行失败 |
| `UserPromptSubmit` | 用户提交 prompt 时 |
| `Stop` | Agent 停止时 |
| `SubagentStart` | 子代理启动 |
| `SubagentStop` | 子代理停止 |
| `PreCompact` | 上下文压缩前（可介入） |
| `Notification` | 通知 |
| `PermissionRequest` | 权限请求 |

Hook 能力：阻止执行、修改输入、添加上下文、处理权限决策。

### 3.5 上下文压缩

- CLI 内置自动 compaction（上下文窗口满时自动压缩旧消息）
- 通过 `PreCompact` Hook 可以介入压缩过程
- SDK 本身不实现压缩逻辑，只是通过 Hook 暴露了接口

### 3.6 权限控制

```python
options = ClaudeAgentOptions(
    permission_mode="acceptEdits",  # default / acceptEdits / plan / dontAsk / bypassPermissions
    allowed_tools=["Read", "Write", "Bash"],
    disallowed_tools=["Bash"],
    can_use_tool=lambda tool_name, args: tool_name != "Bash" or args.get("command", "").startswith("ls"),
)
```

---

## 四、与 DeerFlow 对比

| 维度 | Claude Code SDK + 智谱 | DeerFlow + 智谱 |
|------|----------------------|----------------|
| **模型** | ✅ 可用智谱 GLM | ✅ 可用智谱 GLM |
| **Agent 运行时** | 闭源 CLI 二进制（不可改） | 开源 Python（完全可控） |
| **上下文压缩** | 内置（闭源，PreCompact Hook 可介入） | SummarizationMiddleware（可完全自定义） |
| **子代理** | 内置（闭源，SubagentStart/Stop Hook） | task 工具 + SubagentLimitMiddleware（可改） |
| **工具执行管道** | 内置（PreToolUse/PostToolUse Hook） | wrap_tool_call 洋葱模型（完全控制） |
| **循环检测** | 内置（不可自定义） | LoopDetectionMiddleware（两层检测，阈值可配） |
| **悬空调用修复** | 内置 | DanglingToolCallMiddleware（可自定义） |
| **Token 用量追踪** | 内置 | TokenUsageMiddleware（步骤归因） |
| **自定义 Middleware** | ❌ 只有 Hooks（10 种事件） | ✅ 可任意添加新 Middleware |
| **安全沙箱** | 内置 | Docker sandbox |
| **MCP 工具** | ✅ 内置 + 自定义 | ✅ 内置 + 自定义 |
| **Skills 系统** | ✅ Markdown 定义 | ✅ Markdown 定义 |
| **记忆系统** | 无独立记忆 | MemoryMiddleware（防抖 + 批量提取） |
| **多用户并发** | 单用户 CLI | Gateway + ThreadData 隔离 |
| **API 网关** | 无 | 内置 FastAPI Gateway |
| **前端** | 无 | Next.js + React |

### Hooks vs Middleware 对比

Claude Code SDK 的 Hooks 本质上是一个简化版的 Middleware：

| 能力 | Claude Code Hooks | DeerFlow Middleware |
|------|-------------------|-------------------|
| 拦截工具执行 | ✅ PreToolUse | ✅ wrap_tool_call |
| 修改请求参数 | 有限 | ✅ 完全控制 |
| 重试 | ❌ | ✅ 洋葱模型可重试 |
| 捕获异常降级 | ❌ | ✅ try/except handler |
| 精确插入消息 | ❌ | ✅ wrap_model_call |
| 替换消息内容 | ❌ | ✅ model_copy |
| 注入新消息 | ❌ | ✅ before/after_model |
| 线程安全状态 | ❌ | ✅ Lock + OrderedDict |
| 频率统计 | ❌ | ✅ LoopDetection Layer 2 |

---

## 五、对安全 Agent 平台的适用性评估

### Claude Code SDK 能做的

- ✅ 基本的 Agent 循环（规划→执行→观察→重复）
- ✅ 工具调用（文件操作、Shell、MCP）
- ✅ 子代理并行执行
- ✅ 上下文自动压缩
- ✅ PreToolUse Hook 做基本审批
- ✅ 使用国产模型（智谱 GLM）

### Claude Code SDK 做不到的（安全平台需要）

- ❌ **自定义 Middleware 管道**：只有 10 种 Hook 事件，无法像 DeerFlow 那样加自定义中间件
- ❌ **工具执行管道的深度控制**：无法在工具执行链中间插入审计、限流、权限检查等逻辑
- ❌ **消息级别的精确操作**：无法替换消息、精确插入、频率统计
- ❌ **多用户并发隔离**：CLI 是单用户设计，无 ThreadData 级别的线程隔离
- ❌ **API 网关**：没有 HTTP API 层，无法被 Kafka/IM/Web 等多渠道调用
- ❌ **自定义 Agent 状态机**：无法修改 Agent Loop 的执行流程（如加 Approval Gate、Steering）
- ❌ **完全闭源的运行时**：无法审计 CLI 二进制的行为，安全产品需要完全可控

### 结论

| 场景 | 推荐 |
|------|------|
| 个人编码工具 | Claude Code SDK + 智谱 ✅ |
| CI/CD 自动化 | Claude Code SDK + 智谱 ✅ |
| 轻量 Agent 自动化 | Claude Code SDK + 智谱 ✅ |
| **企业级安全 Agent 平台** | **DeerFlow Fork ✅**（深度定制需求超出 SDK Hooks 能力） |

---

## 六、参考来源

- Claude Code Agent SDK Python 文档：https://code.claude.com/docs/en/agent-sdk/python
- Claude Code 模型配置：https://code.claude.com/docs/en/model-config
- Claude Code LLM Gateway 配置：https://code.claude.com/docs/en/llm-gateway
- 第三方模型配置教程（GLM 4.7/MiniMax/Kimi K2）：https://guozheng-ge.medium.com/set-up-claude-code-using-third-party-coding-models-glm-4-7-minimax-2-1-kimi-k2-5a3cdf38c261
