# DeerFlow 2.0 源码调试计划

> 三遍法的第二遍（日志调试）和第三遍（写测试）。
> 前提：已完成第一遍（画调用关系图），对整体架构有概念。

---

## 一、两种调试手段

### 手段 A：日志调试（看全链路）

在源码里临时加 `logger.info()`，通过 `make dev` 热重载验证。

```bash
# 启动服务
make dev

# 看日志（另一个终端）
tail -f logs/gateway.log
```

保存文件后等 2-3 秒（uvicorn --reload），刷新网页发消息，看日志输出。

**调试完删掉加的日志，不要提交到 Git。**

### 手段 B：VS Code 断点（看单个函数内部逻辑）

#### 配置（一次性）

1. 安装 debugpy：`cd backend && uv add --group dev debugpy`
2. `backend/.vscode/launch.json` 已有 "DeerFlow: 调试 Gateway (attach)" 配置

#### 使用

**终端 1**（手动启动 Gateway，带 debug 端口）：

```bash
cd backend && PYTHONPATH=. uv run python -m debugpy --listen 5678 \
  -m uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001 --reload
```

**终端 2**（启动 Frontend + nginx）：

```bash
cd frontend && pnpm dev > ../logs/frontend.log 2>&1 &
nginx -g 'daemon off;' -c "$PWD/docker/nginx/nginx.local.conf" -p "$PWD" > logs/nginx.log 2>&1 &
```

**VS Code**：打开 `.py` 文件 → 设断点 → F5 → 选 "DeerFlow: 调试 Gateway (attach)" → 打开 http://localhost:2026 发消息 → 断点命中

### 手段 C：读已有测试 + 跑测试（最推荐的学习方式）

项目已有 **130+ 个测试文件**，每个测试都是"给定输入 → 期望输出"的最佳文档。

```bash
# 跑单个测试（带 print 输出）
cd backend && PYTHONPATH=. uv run pytest tests/test_loop_detection_middleware.py -v -s

# 跑某个测试类
cd backend && PYTHONPATH=. uv run pytest tests/test_loop_detection_middleware.py::TestHashToolCalls -v

# 跑某个测试方法
cd backend && PYTHONPATH=. uv run pytest tests/test_loop_detection_middleware.py::TestHashToolCalls::test_same_calls_same_hash -v
```

---

## 二、调试路线：5 个阶段，由浅入深

### 阶段 A：验证工具组装（30分钟）

**目标**：亲眼看到 `get_available_tools()` 返回了哪些工具。

**手段**：读日志（不需要改代码，已有 logger.info）

```bash
make dev
# 发一条消息后：
grep "Total tools loaded" logs/gateway.log
```

**然后读已有测试**：

| 测试文件 | 行数 | 学什么 |
|---------|------|--------|
| `tests/test_tool_deduplication.py` | 106 | 工具去重逻辑（106 行，最简单，先读这个） |
| `tests/test_local_bash_tool_loading.py` | ~100 | bash 工具加载条件 |

跑测试验证：
```bash
cd backend && PYTHONPATH=. uv run pytest tests/test_tool_deduplication.py -v -s
```

### 阶段 B：验证中间件链组装（30分钟）

**目标**：看到完整的中间件链列表。

**手段 A**：日志调试

在 `backend/packages/harness/deerflow/agents/lead_agent/agent.py` 的 `_build_middlewares()` 函数 `return middlewares` 之前加：

```python
logger.info("=== Middleware chain ===\n" + "\n".join(f"  [{i}] {type(m).__name__}" for i, m in enumerate(middlewares)))
```

保存 → 等重载 → 发消息 → 看日志。

**手段 B**：读已有测试

| 测试文件 | 行数 | 学什么 |
|---------|------|--------|
| `tests/test_loop_detection_middleware.py` | 670 | 循环检测完整测试（先看 TestHashToolCalls 类，80行） |
| `tests/test_clarification_middleware.py` | 179 | 中间件拦截逻辑 |
| `tests/test_thread_data_middleware.py` | ~200 | before_agent 初始化 |
| `tests/test_dangling_tool_call_middleware.py` | ~150 | 中断处理 |

建议的阅读顺序：
1. `test_thread_data_middleware.py`（before_agent，最简单）
2. `test_clarification_middleware.py`（拦截+中断）
3. `test_loop_detection_middleware.py`（最复杂，但最有价值）

### 阶段 C：验证 LLM 调用和工具执行（45分钟）

**目标**：看到"用户消息 → LLM 返回 tool_calls → 工具执行 → 结果返回"的全过程。

**手段 A**：日志调试（3 个位置同时加）

位置 1 — LLM 返回后看 tool_calls：
```python
# 文件：backend/packages/harness/deerflow/agents/middlewares/loop_detection_middleware.py
# 在 after_model 方法开头加：
last_msg = state["messages"][-1]
tool_names = [tc["name"] for tc in getattr(last_msg, "tool_calls", [])]
logger.info(f"[after_model] LLM returned tool_calls: {tool_names}, content: {str(last_msg.content)[:100]}")
```

位置 2 — 工具被调用：
```python
# 文件：backend/packages/harness/deerflow/sandbox/tools.py
# 在 bash_tool 函数开头加：
logger.info(f"[tool] bash_tool called: {command[:200]}")
```

位置 3 — read_file 被调用（Skill 加载时会触发）：
```python
# 文件：backend/packages/harness/deerflow/sandbox/tools.py
# 在 read_file_tool 函数开头加：
logger.info(f"[tool] read_file called: path={filepath}")
```

**手段 B**：读已有测试

| 测试文件 | 行数 | 学什么 |
|---------|------|--------|
| `tests/test_tool_error_handling_middleware.py` | ~150 | wrap_tool_call 洋葱包裹 |
| `tests/test_sandbox_audit_middleware.py` | ~100 | 审计日志 |
| `tests/test_sandbox_tools_security.py` | ~200 | 工具安全检查 |

**手段 C**：VS Code 断点

在 `sandbox/tools.py` 的 `bash_tool` 函数第一行打断点，发消息触发工具调用（如"在当前目录创建一个 hello.txt"），单步跟踪。

### 阶段 D：验证 Prompt 组装 + Memory（45分钟）

**目标**：看到 system prompt 里注入了什么内容（skills、memory、subagent 指令）。

**手段 A**：日志调试

位置 — 看 prompt 的完整内容：
```python
# 文件：backend/packages/harness/deerflow/agents/lead_agent/agent.py
# 在 _make_lead_agent 函数里，apply_prompt_template() 调用后加：
prompt = apply_prompt_template(...)
logger.info(f"=== System prompt length: {len(prompt)} chars ===")
logger.info(f"=== Prompt sections: { [s for s in ['skill_system', 'memory', 'todo_list_system', 'subagent'] if f'<{s}>' in prompt] }")
```

**手段 B**：读已有测试

| 测试文件 | 行数 | 学什么 |
|---------|------|--------|
| `tests/test_lead_agent_prompt.py` | 319 | prompt 各段注入逻辑 |
| `tests/test_memory_updater.py` | 1048 | Memory 提取（大文件，挑几个看） |
| `tests/test_memory_queue.py` | ~200 | 去抖动队列 |

建议的阅读顺序：
1. `test_lead_agent_prompt.py`（看 skills/memory 怎么注入 prompt）
2. `test_memory_queue.py`（看 30s 去抖动）
3. `test_memory_updater.py`（挑 `test_extract_facts_*` 开头的几个看）

### 阶段 E：验证模型创建 + Sub-Agent（45分钟）

**目标**：理解模型怎么创建的、sub-agent 怎么执行的。

**手段 A**：读已有测试（这阶段读测试比日志更有价值）

| 测试文件 | 行数 | 学什么 |
|---------|------|--------|
| `tests/test_model_factory.py` | 1036 | 模型工厂（先看前 100 行的 FakeChatModel + _patch_factory） |
| `tests/test_subagent_executor.py` | 1305 | Sub-Agent 执行（大文件，搜索 `test__create_agent` 开头的看） |
| `tests/test_subagent_limit_middleware.py` | ~150 | 并发限制 |
| `tests/test_create_deerflow_agent.py` | ~300 | SDK 版 agent 创建 |

**手段 B**：VS Code 断点

在 `subagents/executor.py` 的 `_create_agent()` 函数第一行打断点，用 Ultra 模式发消息触发 sub-agent，看它怎么创建的。

---

## 三、调试节奏建议

```
第1天（2-3小时）：
  阶段 A：工具组装     ← 最简单，热身
  阶段 B：中间件链     ← 核心设计模式

第2天（2-3小时）：
  阶段 C：LLM调用+工具执行  ← 最有成就感，能看到完整流程
  阶段 D：Prompt+Memory     ← 理解大模型"看到"了什么

第3天（2-3小时）：
  阶段 E：模型创建+Sub-Agent  ← 进阶内容
  写自己的测试（见第四节）
```

---

## 四、第三遍：写自己的测试

### 测试 1：验证工具组装

```python
# tests/test_my_learning.py

def test_tool_assembly():
    """验证 get_available_tools 组装了哪些工具"""
    from unittest.mock import MagicMock, patch

    with patch("deerflow.tools.tools.get_app_config") as mock_cfg, \
         patch("deerflow.tools.tools.is_host_bash_allowed", return_value=True), \
         patch("deerflow.tools.tools.reset_deferred_registry"):
        config = MagicMock()
        config.tools = []
        config.models = []
        config.tool_search.enabled = False
        config.sandbox = MagicMock()
        mock_cfg.return_value = config

        from deerflow.tools.tools import get_available_tools
        tools = get_available_tools(include_mcp=False)

        tool_names = [t.name for t in tools]
        print(f"\n=== Tools loaded ({len(tools)}) ===")
        for name in tool_names:
            print(f"  - {name}")

        assert "present_files" in tool_names
        assert "skill_manage" in tool_names
```

### 测试 2：验证中间件链顺序

```python
def test_middleware_chain_order():
    """验证中间件链的组装顺序"""
    from types import SimpleNamespace
    from unittest.mock import patch
    from deerflow.agents.middlewares.tool_error_handling_middleware import (
        build_lead_runtime_middlewares,
    )

    config = SimpleNamespace(
        guardrails=SimpleNamespace(enabled=False),
        circuit_breaker=SimpleNamespace(failure_threshold=3, recovery_timeout_sec=60),
    )
    with patch("deerflow.config.get_app_config", return_value=config):
        middlewares = build_lead_runtime_middlewares(app_config=config)

    print(f"\n=== Base middlewares ({len(middlewares)}) ===")
    for i, m in enumerate(middlewares):
        print(f"  [{i}] {type(m).__name__}")

    assert len(middlewares) >= 3
    assert type(middlewares[-1]).__name__ == "ToolErrorHandlingMiddleware"
```

### 测试 3：验证 LoopDetection 的 hash 逻辑

```python
def test_loop_detection_hash():
    """亲手验证循环检测的 hash 逻辑"""
    from deerflow.agents.middlewares.loop_detection_middleware import _hash_tool_calls

    # 同样的调用 → 同样的 hash
    call_a = [{"name": "bash", "args": {"command": "ls"}}]
    call_b = [{"name": "bash", "args": {"command": "ls"}}]
    assert _hash_tool_calls(call_a) == _hash_tool_calls(call_b)

    # 不同调用 → 不同 hash
    call_c = [{"name": "bash", "args": {"command": "pwd"}}]
    assert _hash_tool_calls(call_a) != _hash_tool_calls(call_c)

    print("\n=== Loop detection hash ===")
    print(f"  same calls:  {_hash_tool_calls(call_a) == _hash_tool_calls(call_b)}")
    print(f"  diff calls:  {_hash_tool_calls(call_a) != _hash_tool_calls(call_c)}")
```

### 测试 4：验证 Prompt 包含 Skill 段

```python
def test_prompt_contains_sections():
    """验证 system prompt 包含关键段"""
    from unittest.mock import patch
    with patch("deerflow.agents.lead_agent.prompt._get_enabled_skills_for_config", return_value=[]), \
         patch("deerflow.agents.lead_agent.prompt._get_memory_context", return_value=""), \
         patch("deerflow.agents.lead_agent.prompt.get_deferred_tools_prompt_section", return_value=""), \
         patch("deerflow.agents.lead_agent.prompt._build_custom_mounts_section", return_value=""), \
         patch("deerflow.agents.lead_agent.prompt._build_acp_section", return_value=""), \
         patch("deerflow.agents.lead_agent.prompt._build_subagent_section", return_value=""), \
         patch("deerflow.agents.lead_agent.prompt._build_self_update_section", return_value=""), \
         patch("deerflow.config.get_app_config"):
        from deerflow.agents.lead_agent.prompt import apply_prompt_template
        prompt = apply_prompt_template()

    print(f"\n=== Prompt length: {len(prompt)} chars ===")
    sections = ["<skill_system>", "<memory>", "<todo_list_system>", "<deferred_tools>"]
    for s in sections:
        print(f"  has {s}: {s in prompt}")
```

### 运行

```bash
cd backend && PYTHONPATH=. uv run pytest tests/test_my_learning.py -v -s
```

`-s` 让 print 输出到终端，这样你能看到工具列表、中间件顺序、prompt 内容。

---

## 五、已有测试文件索引（按学习优先级）

### 第一优先级：读这些（短、核心、易懂）

| 测试文件 | 行数 | 对应源码 | 学什么 |
|---------|------|---------|--------|
| `test_tool_deduplication.py` | 106 | `tools/tools.py` | 工具去重（最简单，10分钟能读完） |
| `test_clarification_middleware.py` | 179 | `middlewares/clarification_middleware.py` | 中间件拦截+中断 |
| `test_lead_agent_prompt.py` | 319 | `lead_agent/prompt.py` | Prompt 组装逻辑 |
| `test_thread_data_middleware.py` | ~200 | `middlewares/thread_data_middleware.py` | before_agent 初始化 |
| `test_reflection_resolvers.py` | ~80 | `reflection/__init__.py` | resolve_variable 字符串→代码 |

### 第二优先级：读这些（中等长度，重要）

| 测试文件 | 行数 | 对应源码 | 学什么 |
|---------|------|---------|--------|
| `test_loop_detection_middleware.py` | 670 | `middlewares/loop_detection_middleware.py` | 循环检测（企业必备） |
| `test_tool_error_handling_middleware.py` | ~150 | `middlewares/tool_error_handling_middleware.py` | wrap_tool_call 洋葱包裹 |
| `test_subagent_limit_middleware.py` | ~150 | `middlewares/subagent_limit_middleware.py` | 并发控制 |
| `test_memory_queue.py` | ~200 | `memory/queue.py` | 去抖动队列 |
| `test_create_deerflow_agent.py` | ~300 | `agents/factory.py` | SDK 版 agent |

### 第三优先级：挑着看（大文件，按需读）

| 测试文件 | 行数 | 对应源码 | 学什么 |
|---------|------|---------|--------|
| `test_model_factory.py` | 1036 | `models/factory.py` | 模型创建（看前200行即可） |
| `test_subagent_executor.py` | 1305 | `subagents/executor.py` | Sub-Agent 执行（搜 `test__create_agent` 开头的） |
| `test_client.py` | 3234 | `client.py` | 内嵌客户端（搜 `TestGatewayConformance` 看） |
| `test_memory_updater.py` | 1048 | `memory/updater.py` | Memory 提取（挑 `test_extract_facts_*` 看） |

---

## 六、调试中的注意事项

1. **加的日志是临时的**，调试完必须删掉。可以用 `git diff` 检查
2. **VS Code 断点调试时**，Gateway 要手动启动（不用 `make dev`），Frontend 和 nginx 需要另开终端
3. **跑已有测试时**，`PYTHONPATH=.` 不能少（tests 从项目根 import）
4. **测试里大量使用 mock**，看不懂的 mock 先跳过，关注"输入→输出"的断言部分
5. **每个测试文件的 fixture/helper 函数**（如 `_make_state`、`_make_runtime`）是理解测试的关键，先看这些
