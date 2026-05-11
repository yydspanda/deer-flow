"""Stage 2 Day 2 — 第三遍：写测试，亲手调 API。

每个测试对应一个学习目标：
  test_tool_assembly          → 工具组装了哪些
  test_middleware_chain_order  → 中间件链顺序
  test_loop_detection_hash    → 循环检测 hash 逻辑
  test_prompt_contains_sections → system prompt 结构
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Test 1: 工具组装
# ---------------------------------------------------------------------------


def test_tool_assembly():
    """验证 get_available_tools 组装了哪些工具"""
    config = MagicMock()
    config.tools = []
    config.models = []
    config.tool_search.enabled = False
    config.sandbox = MagicMock()

    with patch("deerflow.tools.tools.get_app_config", return_value=config), patch("deerflow.tools.tools.is_host_bash_allowed", return_value=True), patch("deerflow.tools.tools.reset_deferred_registry"):
        from deerflow.tools.tools import get_available_tools

        tools = get_available_tools(include_mcp=False)

    tool_names = [t.name for t in tools]
    print(f"\n=== Tools loaded ({len(tools)}) ===")
    for name in tool_names:
        print(f"  - {name}")

    assert "ask_clarification" in tool_names
    assert "skill_manage" in tool_names


# ---------------------------------------------------------------------------
# Test 2: 中间件链顺序
# ---------------------------------------------------------------------------


def test_middleware_chain_order():
    """验证基础中间件链的组装顺序"""
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


# ---------------------------------------------------------------------------
# Test 3: 循环检测 hash
# ---------------------------------------------------------------------------


def test_loop_detection_hash():
    """亲手验证循环检测的 hash 逻辑"""
    from deerflow.agents.middlewares.loop_detection_middleware import _hash_tool_calls

    call_a = [{"name": "bash", "args": {"command": "ls"}}]
    call_b = [{"name": "bash", "args": {"command": "ls"}}]
    call_c = [{"name": "bash", "args": {"command": "pwd"}}]

    print("\n=== Loop detection hash ===")
    print(f"  same calls hash equal:  {_hash_tool_calls(call_a) == _hash_tool_calls(call_b)}")
    print(f"  diff calls hash differ: {_hash_tool_calls(call_a) != _hash_tool_calls(call_c)}")
    print(f"  hash_a={_hash_tool_calls(call_a)}")
    print(f"  hash_c={_hash_tool_calls(call_c)}")

    assert _hash_tool_calls(call_a) == _hash_tool_calls(call_b)
    assert _hash_tool_calls(call_a) != _hash_tool_calls(call_c)


# ---------------------------------------------------------------------------
# Test 4: system prompt 结构
# ---------------------------------------------------------------------------


def test_prompt_contains_sections():
    """验证 system prompt 包含关键段"""
    with (
        patch("deerflow.agents.lead_agent.prompt.get_enabled_skills_for_config", return_value=[]),
        patch("deerflow.agents.lead_agent.prompt._get_memory_context", return_value=""),
        patch("deerflow.agents.lead_agent.prompt.get_deferred_tools_prompt_section", return_value=""),
        patch("deerflow.agents.lead_agent.prompt._build_custom_mounts_section", return_value=""),
        patch("deerflow.agents.lead_agent.prompt._build_acp_section", return_value=""),
        patch("deerflow.agents.lead_agent.prompt._build_subagent_section", return_value=""),
        patch("deerflow.agents.lead_agent.prompt._build_self_update_section", return_value=""),
        patch("deerflow.config.get_app_config"),
    ):
        from deerflow.agents.lead_agent.prompt import apply_prompt_template

        prompt = apply_prompt_template()

    print(f"\n=== Prompt length: {len(prompt)} chars ===")
    sections = ["<skill_system>", "<memory>", "<todo_list_system>", "<deferred_tools>"]
    for s in sections:
        print(f"  has {s}: {s in prompt}")
