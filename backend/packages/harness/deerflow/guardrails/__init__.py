"""yyds: Guardrails 安全护栏 — 工具调用前的授权检查，决定"这个工具能不能跑"。

【大白话讲清楚】
  Agent 可以调很多工具（bash、write_file、发邮件...）。
  有些工具在某些场景下不应该被调用（比如 sub-agent 不该发邮件，
  或者用户权限不够不该执行 bash）。

  Guardrails 在工具执行**之前**拦截，问一个问题："这个调用允许吗？"
    允许 → 正常执行
    拒绝 → 返回错误 ToolMessage，告诉 Agent "被拦了，换个方式"

  关键设计：**拒绝不是报错，而是引导 Agent 自适应**。
  Agent 收到 "Guardrail denied: tool 'bash' was blocked" 后，
  会自己换一种方式完成任务（比如用 read_file 代替 bash）。

【具体例子】
  AllowlistProvider 配置：allowed_tools=["read_file", "ls"]

  Agent 想调 bash("rm -rf /")：
    → GuardrailMiddleware 拦截
    → AllowlistProvider.evaluate() → allow=False
    → 返回 ToolMessage("Guardrail denied: tool 'bash' was blocked")
    → Agent 看到"被拦了"，改用 read_file 完成任务

  Agent 想调 read_file("config.yaml")：
    → GuardrailMiddleware 拦截
    → AllowlistProvider.evaluate() → allow=True
    → 正常执行，返回文件内容

  Provider 抛异常时：
    fail_closed=True（默认）→ 拒绝调用（安全优先）
    fail_closed=False → 放行（可用性优先）

【代码结构】
  guardrails/
  ├── __init__.py          模块索引 + 公共 API 导出
  ├── provider.py          ★ Protocol 接口 + 数据结构（GuardrailRequest/Decision/Reason）
  ├── builtin.py           内置实现：AllowlistProvider（白名单/黑名单）
  └── middleware.py         ★ GuardrailMiddleware（拦截 wrap_tool_call）

  建议阅读顺序：
    1. provider.py    — 先看接口和数据结构（61 行）
    2. builtin.py     — 最简单的实现（27 行）
    3. middleware.py  — 核心拦截逻辑（104 行）

【在链中的位置】
  build_subagent_runtime_middlewares() 里注册：
    GuardrailMiddleware(AllowlistProvider(allowed_tools=..., denied_tools=...))
  → 挂在 Agent 的中间件链上
  → 每次工具调用前自动触发

---
Pre-tool-call authorization middleware.
"""

from deerflow.guardrails.builtin import AllowlistProvider
from deerflow.guardrails.middleware import GuardrailMiddleware
from deerflow.guardrails.provider import GuardrailDecision, GuardrailProvider, GuardrailReason, GuardrailRequest

__all__ = [
    "AllowlistProvider",
    "GuardrailDecision",
    "GuardrailMiddleware",
    "GuardrailProvider",
    "GuardrailReason",
    "GuardrailRequest",
]
