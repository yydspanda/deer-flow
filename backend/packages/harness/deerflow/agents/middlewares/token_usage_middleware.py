"""yyds: Token 用量追踪中间件 — 每次 LLM 调用后，记录花了多少 token + 标注这步在干什么。

【做什么】两件事：
   1. 从 LLM 响应中提取 token 用量（input/output/total），写入日志
   2. 分析 AI 消息内容，推断"这步在干什么"（归因），写入 AIMessage.additional_kwargs
【为什么存在】
   - Token 是 LLM 应用的核心成本。不知道每次调用花了多少 token，就无法优化成本。
   - 前端需要知道"Agent 这步在做什么"——是在思考、在调工具、在派子 Agent、还是在回答用户？
     归因信息（attribution）就是告诉前端这些信息的。前端根据 kind 字段决定展示什么图标和文案。
【在链中的位置】after_model 阶段（模型返回响应后），在 agent.py 的 _build_middlewares 第 ④ 步追加。
【关键设计】
   - 归因信息存储在 AIMessage.additional_kwargs["token_usage_attribution"] 中，前端直接读
   - 步骤类型推断（kind）：
     有工具调用 → tool_batch（批量调工具）
     唯一工具是 task → subagent_dispatch（派子 Agent）
     唯一操作是待办变化 → todo_update（更新待办）
     无工具调用但有文本 → final_answer（最终答案）
     什么都没有 → thinking（纯思考）
   - 特别处理 write_todos：对比前后待办列表差异，精确识别"新建/开始/完成/删除"
   - 特别处理 task/web_search/ask_clarification 等工具，提取描述信息给前端展示
   - model_copy 更新 AIMessage（不修改原对象，创建新实例替换）

归因信息长什么样（前端拿到的数据）：
  {
    "version": 1,
    "kind": "tool_batch",                    // 这步在做什么
    "shared_attribution": true,              // 多个工具共享这次 token 消耗
    "tool_call_ids": ["call_abc", "call_def"], // 涉及的工具调用 ID
    "actions": [                             // 具体操作列表
      {"kind": "search", "tool_name": "web_search", "query": "北京天气", "tool_call_id": "call_abc"},
      {"kind": "tool", "tool_name": "bash", "description": "curl wttr.in", "tool_call_id": "call_def"}
    ]
  }
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.todo import Todo
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

TOKEN_USAGE_ATTRIBUTION_KEY = "token_usage_attribution"  # yyds: 归因信息在 additional_kwargs 中的键名


def _string_arg(value: Any) -> str | None:
    """yyds: 安全提取字符串参数 — 去空白，空字符串返回 None。"""
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def _normalize_todos(value: Any) -> list[Todo]:
    """yyds: 标准化待办列表 — 过滤非法条目，只保留有 content + 合法 status 的。

    yyds 执行顺序：
      ① 非 list → 返回空列表
      ② 遍历每个元素，非 dict → 跳过
      ③ 提取 content（非空字符串）和 status（pending/in_progress/completed 之一）
      ④ 返回标准化后的列表
    """
    if not isinstance(value, list):
        return []

    normalized: list[Todo] = []
    for item in value:
        if not isinstance(item, dict):
            continue

        todo: Todo = {}
        content = _string_arg(item.get("content"))
        status = item.get("status")

        if content is not None:
            todo["content"] = content
        if status in {"pending", "in_progress", "completed"}:
            todo["status"] = status

        normalized.append(todo)

    return normalized


def _todo_action_kind(previous: Todo | None, current: Todo) -> str:
    """yyds: 判断单条待办的操作类型 — 新建/开始/完成/更新。

    yyds 执行顺序：
      ① 之前不存在 → 新建的：
         completed → todo_complete（直接完成）
         in_progress → todo_start（开始做）
         其他 → todo_update（新建但未开始）
      ② 之前存在，content 变了 → todo_update（内容更新）
      ③ 之前存在，content 没变：
         status → completed → todo_complete
         status → in_progress → todo_start
         其他 → todo_update
    """
    status = current.get("status")
    previous_content = previous.get("content") if previous else None
    current_content = current.get("content")

    # yyds: ① 之前不存在（新建）
    if previous is None:
        if status == "completed":
            return "todo_complete"
        if status == "in_progress":
            return "todo_start"
        return "todo_update"

    # yyds: ② content 变了（内容更新）
    if previous_content != current_content:
        return "todo_update"

    # yyds: ③ content 没变，看 status 变化
    if status == "completed":
        return "todo_complete"
    if status == "in_progress":
        return "todo_start"
    return "todo_update"


def _build_todo_actions(previous_todos: list[Todo], next_todos: list[Todo]) -> list[dict[str, Any]]:
    """yyds: 对比前后待办列表差异，生成精确的操作列表 — 前端展示待办变化的唯一数据源。

    yyds 执行顺序：
      ① 建立 previous_by_content 索引：按 content 分组，方便快速匹配
      ② 遍历 next_todos（新列表），为每条找 previous 中的匹配项：
         - 优先按 content 精确匹配
         - 没匹配到 → 按位置（index）匹配
         - 匹配到但 content 和 status 都一样 → 无变化，跳过
      ③ 为有变化的条目生成 action（kind + content）
      ④ 遍历 previous_todos，没被匹配到的 → todo_remove（删除）

    举例：
      之前：[{"content": "调研", "status": "pending"}, {"content": "开发", "status": "pending"}]
      之后：[{"content": "调研", "status": "completed"}, {"content": "测试", "status": "pending"}]

      结果：
        [{"kind": "todo_complete", "content": "调研"},   ← 调研完成了
         {"kind": "todo_update", "content": "测试"},     ← "开发" 变成了 "测试"（content 变了）
         {"kind": "todo_remove", "content": "开发"}]     ← "开发" 没被匹配，删除
    """
    # yyds: ① 建索引 — 按 content 分组
    previous_by_content: dict[str, list[tuple[int, Todo]]] = defaultdict(list)
    matched_previous_indices: set[int] = set()

    for index, todo in enumerate(previous_todos):
        content = todo.get("content")
        if isinstance(content, str) and content:
            previous_by_content[content].append((index, todo))

    actions: list[dict[str, Any]] = []

    # yyds: ②③ 遍历新列表，找匹配 + 生成 action
    for index, todo in enumerate(next_todos):
        content = todo.get("content")
        if not isinstance(content, str) or not content:
            continue

        # yyds: ②a 优先按 content 精确匹配
        previous_match: Todo | None = None
        content_matches = previous_by_content.get(content)
        if content_matches:
            while content_matches and content_matches[0][0] in matched_previous_indices:
                content_matches.pop(0)
            if content_matches:
                previous_index, previous_match = content_matches.pop(0)
                matched_previous_indices.add(previous_index)

        # yyds: ②b content 没匹配到 → 按位置匹配（兜底）
        if previous_match is None and index < len(previous_todos) and index not in matched_previous_indices:
            previous_match = previous_todos[index]
            matched_previous_indices.add(index)

        # yyds: ③ 无变化 → 跳过；有变化 → 生成 action
        if previous_match is not None:
            previous_content = previous_match.get("content")
            previous_status = previous_match.get("status")
            if previous_content == content and previous_status == todo.get("status"):
                continue

        actions.append(
            {
                "kind": _todo_action_kind(previous_match, todo),
                "content": content,
            }
        )

    # yyds: ④ 遍历旧列表，没被匹配的 → todo_remove
    for index, todo in enumerate(previous_todos):
        if index in matched_previous_indices:
            continue

        content = todo.get("content")
        if not isinstance(content, str) or not content:
            continue

        actions.append(
            {
                "kind": "todo_remove",
                "content": content,
            }
        )

    return actions


def _describe_tool_call(tool_call: dict[str, Any], todos: list[Todo]) -> list[dict[str, Any]]:
    """yyds: 描述单个工具调用 — 返回结构化操作信息，不同工具有不同字段。

    yyds 执行顺序（按工具名分支）：
      ① write_todos → 对比前后待办差异，返回 todo_start/todo_complete/todo_update/todo_remove
      ② task → 子 Agent 派发，返回 kind="subagent" + description + subagent_type
      ③ web_search / image_search → 搜索，返回 kind="search" + query
      ④ present_files → 文件展示，返回 kind="present_files"
      ⑤ ask_clarification → 提问确认，返回 kind="clarification"
      ⑥ 其他工具 → 通用，返回 kind="tool" + tool_name + description
    """
    name = _string_arg(tool_call.get("name")) or "unknown"
    args = tool_call.get("args") if isinstance(tool_call.get("args"), dict) else {}
    tool_call_id = _string_arg(tool_call.get("id"))

    # yyds: ① write_todos — 对比前后待办差异
    if name == "write_todos":
        next_todos = _normalize_todos(args.get("todos"))
        actions = _build_todo_actions(todos, next_todos)
        if not actions:
            return [
                {
                    "kind": "tool",
                    "tool_name": name,
                    "tool_call_id": tool_call_id,
                }
            ]
        return [
            {
                **action,
                "tool_call_id": tool_call_id,
            }
            for action in actions
        ]

    # yyds: ② task — 子 Agent 派发
    if name == "task":
        return [
            {
                "kind": "subagent",
                "description": _string_arg(args.get("description")),
                "subagent_type": _string_arg(args.get("subagent_type")),
                "tool_call_id": tool_call_id,
            }
        ]

    # yyds: ③ web_search / image_search — 搜索
    if name in {"web_search", "image_search"}:
        query = _string_arg(args.get("query"))
        return [
            {
                "kind": "search",
                "tool_name": name,
                "query": query,
                "tool_call_id": tool_call_id,
            }
        ]

    # yyds: ④ present_files — 文件展示
    if name == "present_files":
        return [
            {
                "kind": "present_files",
                "tool_call_id": tool_call_id,
            }
        ]

    # yyds: ⑤ ask_clarification — 提问确认
    if name == "ask_clarification":
        return [
            {
                "kind": "clarification",
                "tool_call_id": tool_call_id,
            }
        ]

    # yyds: ⑥ 其他工具 — 通用描述
    return [
        {
            "kind": "tool",
            "tool_name": name,
            "description": _string_arg(args.get("description")),
            "tool_call_id": tool_call_id,
        }
    ]


def _infer_step_kind(message: AIMessage, actions: list[dict[str, Any]]) -> str:
    """yyds: 推断步骤类型 — 从工具调用列表推断模型这次在做什么。

    yyds 执行顺序：
      ① 有操作（actions 非空）→ 看具体类型：
         唯一操作是待办相关 → "todo_update"
         唯一操作是 subagent → "subagent_dispatch"
         其他 → "tool_batch"
      ② 无操作但有文本内容 → "final_answer"（最终答案）
      ③ 无操作也无内容 → "thinking"（纯思考，推理模型可能只输出 thinking block）
    """
    if actions:
        first_kind = actions[0].get("kind")
        if len(actions) == 1 and first_kind in {"todo_start", "todo_complete", "todo_update", "todo_remove"}:
            return "todo_update"
        if len(actions) == 1 and first_kind == "subagent":
            return "subagent_dispatch"
        return "tool_batch"

    if message.content:
        return "final_answer"
    return "thinking"


def _has_tool_call(message: AIMessage, tool_call_id: str) -> bool:
    """Return True if the AIMessage contains a tool_call with the given id."""
    for tc in message.tool_calls or []:
        if isinstance(tc, dict):
            if tc.get("id") == tool_call_id:
                return True
        elif hasattr(tc, "id") and tc.id == tool_call_id:
            return True
    return False


def _build_attribution(message: AIMessage, todos: list[Todo]) -> dict[str, Any]:
    """yyds: 构建完整的步骤归因信息 — 这步在做什么、涉及哪些工具、具体操作列表。

    yyds 执行顺序：
      ① 遍历 AI 消息的所有 tool_calls，逐个调用 _describe_tool_call 生成操作描述
      ② 如果中间有 write_todos → 更新 current_todos（后续工具调用可能基于新的待办列表）
      ③ 收集所有 tool_call_ids
      ④ 调用 _infer_step_kind 推断步骤类型
      ⑤ 返回完整归因字典

    返回值结构：
      {
        "version": 1,                           // 版本号（前端兼容用）
        "kind": "tool_batch",                   // 步骤类型
        "shared_attribution": true,             // 多个工具共享这次 token 消耗
        "tool_call_ids": ["call_abc", ...],     // 工具调用 ID 列表
        "actions": [...]                        // 具体操作列表
      }
    """
    tool_calls = getattr(message, "tool_calls", None) or []
    actions: list[dict[str, Any]] = []
    current_todos = list(todos)

    # yyds: ①② 遍历工具调用，生成操作描述
    for raw_tool_call in tool_calls:
        if not isinstance(raw_tool_call, dict):
            continue

        described_actions = _describe_tool_call(raw_tool_call, current_todos)
        actions.extend(described_actions)

        # yyds: ② write_todos 会更新待办列表，后续工具需要基于新列表
        if raw_tool_call.get("name") == "write_todos":
            args = raw_tool_call.get("args") if isinstance(raw_tool_call.get("args"), dict) else {}
            current_todos = _normalize_todos(args.get("todos"))

    # yyds: ③ 收集 tool_call_ids
    tool_call_ids: list[str] = []
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue

        tool_call_id = _string_arg(tool_call.get("id"))
        if tool_call_id is not None:
            tool_call_ids.append(tool_call_id)

    # yyds: ④⑤ 推断类型 + 返回
    return {
        "version": 1,
        "kind": _infer_step_kind(message, actions),
        "shared_attribution": len(actions) > 1,
        "tool_call_ids": tool_call_ids,
        "actions": actions,
    }


class TokenUsageMiddleware(AgentMiddleware):
    """yyds: Token 用量追踪中间件 — 记录 token 消耗 + 标注步骤归因信息。

    执行时机：after_model（模型返回响应后）。
    两件事：
      1. 从 usage_metadata 提取 token 用量并记录日志
      2. 生成步骤归因信息（这次调用做了什么）写入 additional_kwargs

    数据流：
      after_model / aafter_model
        └─ _apply(state)
             ├─ 取最后一条消息（必须是 AIMessage）
             ├─ 提取 usage_metadata → 记录日志
             ├─ _build_attribution() → 归因信息
             │    ├─ 遍历 tool_calls → _describe_tool_call() → actions
             │    ├─ _infer_step_kind() → kind（步骤类型）
             │    └─ 返回 {version, kind, shared_attribution, tool_call_ids, actions}
             ├─ 归因信息和已有的一样？→ 不更新（幂等性）
             └─ model_copy 更新 AIMessage.additional_kwargs → 返回 {"messages": [updated_msg]}
    """

    def _apply(self, state: AgentState) -> dict | None:
        """yyds: 主逻辑 — 提取 token 用量 + 生成归因 + 更新 AIMessage。

        yyds 执行顺序：
          ① 取最后一条消息，不是 AIMessage → 跳过
          ② 提取 usage_metadata（input_tokens/output_tokens/total_tokens）→ 记录日志
          ③ 从 state["todos"] 获取当前待办列表
          ④ _build_attribution() 构建归因信息
          ⑤ 归因信息和已有的一样 → 不更新（幂等性）
          ⑥ model_copy 创建新 AIMessage（additional_kwargs 含归因信息）
          ⑦ 返回 {"messages": [updated_msg]}（替换原始 AIMessage）
        """
        # yyds: ① 取最后一条消息
        messages = state.get("messages", [])
        if not messages:
            return None

        # Annotate subagent token usage onto the AIMessage that dispatched it.
        # When a task tool completes, its usage is cached by tool_call_id.  Detect
        # the ToolMessage → search backward for the corresponding AIMessage → merge.
        # Walk backward through consecutive ToolMessages before the new AIMessage
        # so that multiple concurrent task tool calls all get their subagent tokens
        # written back to the same dispatch message (merging into one update).
        state_updates: dict[int, AIMessage] = {}
        if len(messages) >= 2:
            from deerflow.tools.builtins.task_tool import pop_cached_subagent_usage

            idx = len(messages) - 2
            while idx >= 0:
                tool_msg = messages[idx]
                if not isinstance(tool_msg, ToolMessage) or not tool_msg.tool_call_id:
                    break

                subagent_usage = pop_cached_subagent_usage(tool_msg.tool_call_id)
                if subagent_usage:
                    # Search backward from the ToolMessage to find the AIMessage
                    # that dispatched it.  A single model response can dispatch
                    # multiple task tool calls, so we can't assume a fixed offset.
                    dispatch_idx = idx - 1
                    while dispatch_idx >= 0:
                        candidate = messages[dispatch_idx]
                        if isinstance(candidate, AIMessage) and _has_tool_call(candidate, tool_msg.tool_call_id):
                            # Accumulate into an existing update for the same
                            # AIMessage (multiple task calls in one response),
                            # or merge fresh from the original message.
                            existing_update = state_updates.get(dispatch_idx)
                            prev = existing_update.usage_metadata if existing_update else (getattr(candidate, "usage_metadata", None) or {})
                            merged = {
                                **prev,
                                "input_tokens": prev.get("input_tokens", 0) + subagent_usage["input_tokens"],
                                "output_tokens": prev.get("output_tokens", 0) + subagent_usage["output_tokens"],
                                "total_tokens": prev.get("total_tokens", 0) + subagent_usage["total_tokens"],
                            }
                            state_updates[dispatch_idx] = candidate.model_copy(update={"usage_metadata": merged})
                            break
                        dispatch_idx -= 1
                idx -= 1

        last = messages[-1]
        if not isinstance(last, AIMessage):
            if state_updates:
                return {"messages": [state_updates[idx] for idx in sorted(state_updates)]}
            return None

        # yyds: ② 提取 token 用量并记录日志
        usage = getattr(last, "usage_metadata", None)
        if usage:
            input_token_details = usage.get("input_token_details") or {}
            output_token_details = usage.get("output_token_details") or {}
            detail_parts = []
            if input_token_details:
                detail_parts.append(f"input_token_details={input_token_details}")
            if output_token_details:
                detail_parts.append(f"output_token_details={output_token_details}")
            detail_suffix = f" {' '.join(detail_parts)}" if detail_parts else ""
            logger.info(
                "LLM token usage: input=%s output=%s total=%s%s",
                usage.get("input_tokens", "?"),
                usage.get("output_tokens", "?"),
                usage.get("total_tokens", "?"),
                detail_suffix,
            )

        # yyds: ③④ 构建归因信息
        todos = state.get("todos") or []
        attribution = _build_attribution(last, todos if isinstance(todos, list) else [])

        # yyds: ⑤ 幂等性检查 — 归因信息没变则不更新
        additional_kwargs = dict(getattr(last, "additional_kwargs", {}) or {})
        if additional_kwargs.get(TOKEN_USAGE_ATTRIBUTION_KEY) == attribution:
            return {"messages": [state_updates[idx] for idx in sorted(state_updates)]} if state_updates else None

        # yyds: ⑥⑦ 更新 AIMessage
        additional_kwargs[TOKEN_USAGE_ATTRIBUTION_KEY] = attribution
        updated_msg = last.model_copy(update={"additional_kwargs": additional_kwargs})
        state_updates[len(messages) - 1] = updated_msg
        return {"messages": [state_updates[idx] for idx in sorted(state_updates)]}

    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        """yyds: 同步版。"""
        return self._apply(state)

    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        """yyds: 异步版 — 逻辑和同步版完全相同。"""
        return self._apply(state)
