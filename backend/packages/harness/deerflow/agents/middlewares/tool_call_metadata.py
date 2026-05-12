# yyds: 工具调用元数据同步工具 — 克隆 AIMessage 时保持 tool_calls 与底层 provider 的原始元数据（additional_kwargs、response_metadata）一致
"""Helpers for keeping AIMessage tool-call metadata consistent."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage


# yyds: 从原始 provider 工具调用字典中安全提取 id 字段
def _raw_tool_call_id(raw_tool_call: Any) -> str | None:
    """yyds: 从 provider 原始格式的 tool_call 字典里安全提取 id。

    Provider 原始格式可能是 dict，也可能不是（防御性检查）。
    返回 str 类型的 id，拿不到就返回 None。
    """
    if not isinstance(raw_tool_call, dict):
        return None

    raw_id = raw_tool_call.get("id")
    return raw_id if isinstance(raw_id, str) and raw_id else None


# yyds: 克隆 AIMessage 并同步过滤 tool_calls — 仅保留指定 tool_calls 对应的原始元数据，清理无效的 function_call 和 finish_reason
def clone_ai_message_with_tool_calls(
    message: AIMessage,
    tool_calls: list[dict[str, Any]],
    *,
    content: Any | None = None,
) -> AIMessage:
    """yyds: 克隆一条 AIMessage，用新的 tool_calls 列表替换原来的，同时同步三处关联数据。

    核心作用：当你修改了 msg.tool_calls（比如截断、过滤），你不能只改这一个字段，
    因为 AIMessage 里跟 tool_calls 关联的还有另外两处数据：

    1. msg.additional_kwargs["tool_calls"] — Provider 原始格式的工具调用列表
       格式和 msg.tool_calls 不同（例如 OpenAI 是 {"type":"function","function":{...}}），
       但 id 是一样的。这里必须按 id 同步过滤，否则会出现"标准格式说只有2个调用，
       但原始格式还存着4个"的不一致。

    2. msg.response_metadata["finish_reason"] — Provider 返回的结束原因
       如果 tool_calls 被全部清空了，finish_reason 还是 "tool_calls" 就不对了，
       应该改成 "stop"（表示模型正常结束输出，不再调用工具）。
       同时 additional_kwargs 里的 "function_call"（单工具调用的旧格式）也要清理。

    3. msg.content — 可选，如果调用方想顺便改内容（比如注入警告文本）

    最后用 model_copy(update=...) 创建新消息。model_copy 是 Pydantic v2 的方法，
    保持原消息的 id 和其他字段不变，只覆盖 update 里指定的字段。
    因为 id 不变，LangGraph 的 add_messages reducer 会识别为"替换"而不是"追加"。

    参数:
        message: 原 AIMessage
        tool_calls: 新的 tool_calls 列表（已经截断/过滤后的）
        content: 可选的新 content，None 表示不修改

    返回: 克隆的新 AIMessage（id 相同，tool_calls + additional_kwargs + response_metadata 已同步）
    """
    # yyds: 第一步：收集要保留的 tool_call ID 集合
    # 例如 tool_calls=[{id:"t1"},{id:"t2"}] → kept_ids={"t1","t2"}
    kept_ids = {tc["id"] for tc in tool_calls if isinstance(tc.get("id"), str) and tc["id"]}

    # yyds: 第二步：构建 update 字典，model_copy 会用这个字典覆盖原消息的字段
    update: dict[str, Any] = {"tool_calls": tool_calls}
    if content is not None:
        update["content"] = content

    # yyds: 第三步：同步 additional_kwargs["tool_calls"]（Provider 原始格式）
    # 按 id 过滤，只保留 kept_ids 里的条目，确保和标准格式 tool_calls 一致
    additional_kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
    raw_tool_calls = additional_kwargs.get("tool_calls")
    if isinstance(raw_tool_calls, list):
        synced_raw_tool_calls = [raw_tc for raw_tc in raw_tool_calls if _raw_tool_call_id(raw_tc) in kept_ids]
        if synced_raw_tool_calls:
            additional_kwargs["tool_calls"] = synced_raw_tool_calls
        else:
            # yyds: 如果过滤后为空，直接删掉这个字段（比留着空列表更干净）
            additional_kwargs.pop("tool_calls", None)

    # yyds: 第四步：如果 tool_calls 全部被清空，清理 function_call（单工具调用的旧格式）
    if not tool_calls:
        additional_kwargs.pop("function_call", None)

    update["additional_kwargs"] = additional_kwargs

    # yyds: 第五步：同步 response_metadata["finish_reason"]
    # tool_calls 全空但 finish_reason 还是 "tool_calls" → 改成 "stop"
    response_metadata = dict(getattr(message, "response_metadata", {}) or {})
    if not tool_calls and response_metadata.get("finish_reason") == "tool_calls":
        response_metadata["finish_reason"] = "stop"
    update["response_metadata"] = response_metadata

    # yyds: 第六步：用 model_copy 克隆消息，只更新改动的字段，id 保持不变
    return message.model_copy(update=update)
