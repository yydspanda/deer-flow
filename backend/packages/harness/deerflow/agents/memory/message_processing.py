"""yyds: 消息预处理 — 过滤 + 信号检测（correction / reinforcement）。

【大白话讲清楚】
  这个文件做两件事：
    ① 过滤消息：从对话中挑出"值得记住的"消息（用户输入 + AI 最终回复）
    ② 检测信号：从用户消息中检测两种特殊信号

  为什么需要过滤？
    一轮对话里可能有 20 条消息：用户提问 → AI 调工具 → 工具返回 → AI 再调工具 → ...
    但只有"用户说了什么"和"AI 最终回答了什么"值得进 memory。
    中间的工具调用过程是"思考过程"，不是"记忆素材"。

  两种信号：
    correction（纠正）：用户说"不对""你理解错了""重试"
      → 记忆系统会用 confidence>=0.95 记录，覆盖旧记忆
    reinforcement（肯定）：用户说"对，就是这样""完全正确""继续保持"
      → 记忆系统会用 confidence>=0.9 记录，加强当前做法
    correction 优先级高于 reinforcement（同时出现以纠正为准）

  信号检测只看最近 6 条消息（[-6:]），不扫描全部历史：
    用户纠正后可能又聊了别的，之前的纠正信号已经没意义了。
    6 条是一个经验值：覆盖最近 2-3 轮对话。

【具体例子】
  过滤例子：
    输入 [
      HumanMsg("帮我查天气"),           ← 保留
      AIMsg(tool_calls=[search("天气")]),  ← 跳过（有 tool_calls）
      ToolMsg("北京 25°C 晴"),          ← 跳过（非 human/ai）
      AIMsg("北京今天 25°C 晴"),         ← 保留（无 tool_calls）
      HumanMsg("<uploaded_files>...</uploaded_files>"), ← 跳过（纯上传）
      AIMsg("收到文件"),                 ← 跳过（skip_next_ai，紧跟纯上传）
    ]
    输出 [HumanMsg("帮我查天气"), AIMsg("北京今天 25°C 晴")]

  信号检测例子：
    消息 [-6:] 中有 HumanMsg("不对，我说的是上海不是北京")
    → detect_correction() 匹配"不对" → return True
    → 记忆系统提示 LLM："用 confidence>=0.95 记录为 correction"

【谁调的？】
    filter_messages_for_memory:  summarization_hook.py（压缩前抢救）
                                 MemoryMiddleware（每次对话结束时）
    detect_correction / detect_reinforcement:  summarization_hook.py + MemoryMiddleware

---
Shared helpers for turning conversations into memory update inputs.
"""

from __future__ import annotations

import re
from copy import copy
from typing import Any

_UPLOAD_BLOCK_RE = re.compile(r"<uploaded_files>[\s\S]*?</uploaded_files>\n*", re.IGNORECASE)

_CORRECTION_PATTERNS = (
    re.compile(r"\bthat(?:'s| is) (?:wrong|incorrect)\b", re.IGNORECASE),
    re.compile(r"\byou misunderstood\b", re.IGNORECASE),
    re.compile(r"\btry again\b", re.IGNORECASE),
    re.compile(r"\bredo\b", re.IGNORECASE),
    re.compile(r"不对"),
    re.compile(r"你理解错了"),
    re.compile(r"你理解有误"),
    re.compile(r"重试"),
    re.compile(r"重新来"),
    re.compile(r"换一种"),
    re.compile(r"改用"),
)

_REINFORCEMENT_PATTERNS = (
    re.compile(r"\byes[,.]?\s+(?:exactly|perfect|that(?:'s| is) (?:right|correct|it))\b", re.IGNORECASE),
    re.compile(r"\bperfect(?:[.!?]|$)", re.IGNORECASE),
    re.compile(r"\bexactly\s+(?:right|correct)\b", re.IGNORECASE),
    re.compile(r"\bthat(?:'s| is)\s+(?:exactly\s+)?(?:right|correct|what i (?:wanted|needed|meant))\b", re.IGNORECASE),
    re.compile(r"\bkeep\s+(?:doing\s+)?that\b", re.IGNORECASE),
    re.compile(r"\bjust\s+(?:like\s+)?(?:that|this)\b", re.IGNORECASE),
    re.compile(r"\bthis is (?:great|helpful)\b(?:[.!?]|$)", re.IGNORECASE),
    re.compile(r"\bthis is what i wanted\b(?:[.!?]|$)", re.IGNORECASE),
    re.compile(r"对[，,]?\s*就是这样(?:[。！？!?.]|$)"),
    re.compile(r"完全正确(?:[。！？!?.]|$)"),
    re.compile(r"(?:对[，,]?\s*)?就是这个意思(?:[。！？!?.]|$)"),
    re.compile(r"正是我想要的(?:[。！？!?.]|$)"),
    re.compile(r"继续保持(?:[。！？!?.]|$)"),
)


def extract_message_text(message: Any) -> str:
    """yyds: 从消息中提取纯文本 — 处理 LLM 的三种 content 格式。

    LLM 消息的 content 可能是：
      str: "你好"                           → 直接返回
      list[str]: ["你好", "世界"]            → 拼接 "你好 世界"
      list[dict]: [{"type":"text","text":"你好"}] → 提取 text 字段拼接

    为什么不直接 str(content)？
      list[dict] 的 str() 会变成 "[{'type': 'text', 'text': '你好'}]" ← Python repr
      而我们需要的是 "你好"。
    """
    content = getattr(message, "content", "")
    if isinstance(content, list):
        text_parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                text_parts.append(part)
            elif isinstance(part, dict):
                text_val = part.get("text")
                if isinstance(text_val, str):
                    text_parts.append(text_val)
        return " ".join(text_parts)
    return str(content)


def filter_messages_for_memory(messages: list[Any]) -> list[Any]:
    """yyds: 过滤消息 — 只保留 human + 无 tool_calls 的 ai，跳过纯上传。

    三条过滤规则：
      ① human 消息：保留，但去掉 <uploaded_files> 标签
         - 去掉标签后内容为空？→ 跳过，并标记 skip_next_ai（紧跟的 AI 回复也不要）
         - 去掉标签后还有内容？→ 保留（用户在上传的同时还说了话）
      ② ai 消息：无 tool_calls 才保留
         - 有 tool_calls？→ 跳过（这是中间过程，不是最终回复）
         - 被标记 skip_next_ai？→ 跳过（前一条是纯上传，AI 的"收到文件"没记忆价值）
      ③ 其他消息（tool/system）：全部跳过

    skip_next_ai 的作用：
      用户发了一条纯上传消息 → AI 回复"收到文件" → 这一对对话对记忆没用。
      所以标记 skip_next_ai=True，把 AI 的这条回复也跳过。
    """
    filtered = []
    skip_next_ai = False
    for msg in messages:
        msg_type = getattr(msg, "type", None)

        if msg_type == "human":
            content_str = extract_message_text(msg)
            if "<uploaded_files>" in content_str:
                stripped = _UPLOAD_BLOCK_RE.sub("", content_str).strip()
                if not stripped:  # yyds: 纯上传，没有任何文字 → 跳过，并标记下一条 AI 也跳过
                    skip_next_ai = True
                    continue
                clean_msg = copy(msg)
                clean_msg.content = stripped  # yyds: 上传 + 文字 → 去掉上传标签，保留文字
                filtered.append(clean_msg)
                skip_next_ai = False
            else:
                filtered.append(msg)
                skip_next_ai = False
        elif msg_type == "ai":
            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:  # yyds: 无 tool_calls = AI 的最终回复，保留
                if skip_next_ai:  # yyds: 前一条是纯上传 → AI 的"收到文件"没记忆价值
                    skip_next_ai = False
                    continue
                filtered.append(msg)

    return filtered


def detect_correction(messages: list[Any]) -> bool:
    """yyds: 检测纠正信号 — 最近 6 条消息中有没有用户说"不对"。

    匹配中英文模式：
      英文：that's wrong, you misunderstood, try again, redo
      中文：不对, 你理解错了, 你理解有误, 重试, 重新来, 换一种, 改用

    检测到 correction 的后果：
      updater._build_correction_hint() → 告诉 LLM "用 confidence>=0.95 记录"
      → _apply_updates() 里高置信度的 fact 会覆盖低置信度的旧 fact
    """
    recent_user_msgs = [msg for msg in messages[-6:] if getattr(msg, "type", None) == "human"]

    for msg in recent_user_msgs:
        content = extract_message_text(msg).strip()
        if content and any(pattern.search(content) for pattern in _CORRECTION_PATTERNS):
            return True

    return False


def detect_reinforcement(messages: list[Any]) -> bool:
    """yyds: 检测正面强化信号 — 最近 6 条消息中有没有用户说"对，就是这样"。

    匹配中英文模式：
      英文：yes exactly, perfect, that's right, keep doing that, this is great
      中文：对就是这样, 完全正确, 就是这个意思, 正是我想要的, 继续保持

    检测到 reinforcement 的后果：
      updater._build_correction_hint() → 告诉 LLM "用 confidence>=0.9 记录"
      → 用户明确肯定的做法会被加强记录

    注意：summarization_hook 里 reinforcement 只在 correction=False 时才检测
      （correction 优先级高于 reinforcement，同时出现以纠正为准）
    """
    recent_user_msgs = [msg for msg in messages[-6:] if getattr(msg, "type", None) == "human"]

    for msg in recent_user_msgs:
        content = extract_message_text(msg).strip()
        if content and any(pattern.search(content) for pattern in _REINFORCEMENT_PATTERNS):
            return True

    return False
