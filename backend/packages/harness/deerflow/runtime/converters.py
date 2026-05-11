# yyds: LangChain消息转OpenAI格式的纯函数转换器，用于兼容OpenAI Chat Completions协议
"""Pure functions to convert LangChain message objects to OpenAI Chat Completions format.

Utility for translating LangChain message types to OpenAI-compatible dicts.
Not currently wired into RunJournal (which uses message.model_dump() directly),
but available for consumers that need the OpenAI wire format.
"""

from __future__ import annotations

import json
from typing import Any

_ROLE_MAP = {
    "human": "user",
    "ai": "assistant",
    "system": "system",
    "tool": "tool",
}


# yyds: 将单个LangChain消息转为OpenAI格式的dict，处理Human/AI/System/Tool等所有消息类型
def langchain_to_openai_message(message: Any) -> dict:
    """Convert a single LangChain BaseMessage to an OpenAI message dict.

    Handles:
    - HumanMessage → {"role": "user", "content": "..."}
    - AIMessage (text only) → {"role": "assistant", "content": "..."}
    - AIMessage (with tool_calls) → {"role": "assistant", "content": null, "tool_calls": [...]}
    - AIMessage (text + tool_calls) → both content and tool_calls present
    - AIMessage (list content / multimodal) → content preserved as list
    - SystemMessage → {"role": "system", "content": "..."}
    - ToolMessage → {"role": "tool", "tool_call_id": "...", "content": "..."}
    """
    msg_type = getattr(message, "type", "")
    role = _ROLE_MAP.get(msg_type, msg_type)
    content = getattr(message, "content", "")

    if role == "tool":
        return {
            "role": "tool",
            "tool_call_id": getattr(message, "tool_call_id", ""),
            "content": content,
        }

    if role == "assistant":
        tool_calls = getattr(message, "tool_calls", None) or []
        result: dict = {"role": "assistant"}

        if tool_calls:
            openai_tool_calls = []
            for tc in tool_calls:
                args = tc.get("args", {})
                openai_tool_calls.append(
                    {
                        "id": tc.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": tc.get("name", ""),
                            "arguments": json.dumps(args) if not isinstance(args, str) else args,
                        },
                    }
                )
            # If no text content, set content to null per OpenAI spec
            result["content"] = content if (isinstance(content, list) and content) or (isinstance(content, str) and content) else None
            result["tool_calls"] = openai_tool_calls
        else:
            result["content"] = content

        return result

    # user / system / unknown
    return {"role": role, "content": content}


# yyds: 从AIMessage推断OpenAI finish_reason，优先返回tool_calls，其次查response_metadata
def _infer_finish_reason(message: Any) -> str:
    """Infer OpenAI finish_reason from an AIMessage.

    Returns "tool_calls" if tool_calls present, else looks in
    response_metadata.finish_reason, else returns "stop".
    """
    tool_calls = getattr(message, "tool_calls", None) or []
    if tool_calls:
        return "tool_calls"
    resp_meta = getattr(message, "response_metadata", None) or {}
    if isinstance(resp_meta, dict):
        finish = resp_meta.get("finish_reason")
        if finish:
            return finish
    return "stop"


# yyds: 将AIMessage及其元数据转换为完整的OpenAI completion response格式，包含usage和choices
def langchain_to_openai_completion(message: Any) -> dict:
    """Convert an AIMessage and its metadata to an OpenAI completion response dict.

    Returns:
        {
            "id": message.id,
            "model": message.response_metadata.get("model_name"),
            "choices": [{"index": 0, "message": <openai_message>, "finish_reason": <inferred>}],
            "usage": {"prompt_tokens": ..., "completion_tokens": ..., "total_tokens": ...} or None,
        }
    """
    resp_meta = getattr(message, "response_metadata", None) or {}
    model_name = resp_meta.get("model_name") if isinstance(resp_meta, dict) else None

    openai_msg = langchain_to_openai_message(message)
    finish_reason = _infer_finish_reason(message)

    usage_metadata = getattr(message, "usage_metadata", None)
    if usage_metadata is not None:
        input_tokens = usage_metadata.get("input_tokens", 0) or 0
        output_tokens = usage_metadata.get("output_tokens", 0) or 0
        usage: dict | None = {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }
    else:
        usage = None

    return {
        "id": getattr(message, "id", None),
        "model": model_name,
        "choices": [
            {
                "index": 0,
                "message": openai_msg,
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage,
    }


# yyds: 批量将LangChain消息列表转换为OpenAI消息dict列表
def langchain_messages_to_openai(messages: list) -> list[dict]:
    """Convert a list of LangChain BaseMessages to OpenAI message dicts."""
    return [langchain_to_openai_message(m) for m in messages]
