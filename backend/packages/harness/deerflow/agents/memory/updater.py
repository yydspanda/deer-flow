"""yyds: Memory 更新器 — 用 LLM 从对话中提取用户画像，合并写入 memory.json。

【大白话讲清楚】
  这个文件是 memory 系统的"大脑"。它决定：
    ① 拿对话给 LLM 看，让它分析用户是谁、在做什么、喜欢什么
    ② 把 LLM 返回的分析结果合并到现有 memory 里
    ③ 持久化到文件

  核心三步走（MemoryUpdater.update_memory）：
    1. _prepare_update_prompt: 加载当前 memory + 格式化对话 → 构造 prompt
    2. model.invoke(prompt): LLM 返回 JSON 更新指令
    3. _finalize_update: 解析 JSON → _apply_updates 合并 → storage.save 持久化

  为什么用 sync model.invoke() 不用 async？
    memory 更新在后台线程跑（queue 的 _process_queue 在 Timer 线程里）。
    如果用 async httpx，会跟主 event loop 的连接池冲突（跨 loop 复用连接导致 bug）。
    所以走 sync HTTP，完全独立的连接池，互不干扰。

【具体例子】
  用户说了 5 轮对话：
    "我在做一个 AI Agent 框架" "用 LangGraph" "不对，是 CrewAI" "帮我设计架构" "用中文回复"

  三步走：
    Step 1: 准备 prompt
      当前 memory: {user: {workContext: ""}, facts: []}  （空的新用户）
      对话文本: "User: 我在做一个 AI Agent 框架\nAssistant: 好的...\n..."
      correction_hint: "用户纠正了你，用 confidence>=0.95 记录"

    Step 2: LLM 返回 JSON
      {
        "user": {
          "workContext": {"summary": "开发 AI Agent 框架，技术栈 CrewAI", "shouldUpdate": true},
          "topOfMind": {"summary": "正在设计架构", "shouldUpdate": true}
        },
        "newFacts": [
          {"content": "技术栈 CrewAI", "category": "knowledge", "confidence": 0.95},
          {"content": "偏好中文回复", "category": "preference", "confidence": 0.9}
        ]
      }

    Step 3: _apply_updates 合并
      user.workContext = "开发 AI Agent 框架，技术栈 CrewAI" （覆盖空字符串）
      facts += [两条新 fact]  （去重检查通过 → 加入）
      → storage.save() → 写入 memory.json

---
Memory updater for reading, writing, and updating memory data.
"""

import asyncio
import atexit
import concurrent.futures
import copy
import json
import logging
import math
import re
import uuid
from typing import Any

from deerflow.agents.memory.prompt import (
    MEMORY_UPDATE_PROMPT,
    format_conversation_for_update,
)
from deerflow.agents.memory.storage import (
    create_empty_memory,
    get_memory_storage,
    utc_now_iso_z,
)
from deerflow.config.memory_config import get_memory_config
from deerflow.models import create_chat_model

logger = logging.getLogger(__name__)


_SYNC_MEMORY_UPDATER_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix="memory-updater-sync",
)
atexit.register(lambda: _SYNC_MEMORY_UPDATER_EXECUTOR.shutdown(wait=False))


def _create_empty_memory() -> dict[str, Any]:
    """Backward-compatible wrapper around the storage-layer empty-memory factory."""
    return create_empty_memory()


def _save_memory_to_file(memory_data: dict[str, Any], agent_name: str | None = None, *, user_id: str | None = None) -> bool:
    """Backward-compatible wrapper around the configured memory storage save path."""
    return get_memory_storage().save(memory_data, agent_name, user_id=user_id)


def get_memory_data(agent_name: str | None = None, *, user_id: str | None = None) -> dict[str, Any]:
    """yyds: 获取当前 memory — 通过 storage provider（带缓存）。

    谁调的？
      - updater._prepare_update_prompt(): 准备 prompt 时读当前 memory
      - prompt.format_memory_for_injection(): 注入 prompt 时读 memory
      - 外部 API: GET /memory 接口
    """
    return get_memory_storage().load(agent_name, user_id=user_id)


def reload_memory_data(agent_name: str | None = None, *, user_id: str | None = None) -> dict[str, Any]:
    """yyds: 强制重载 — 绕过缓存，从文件重新读取。外部 API 修改后用。"""
    return get_memory_storage().reload(agent_name, user_id=user_id)


def import_memory_data(memory_data: dict[str, Any], agent_name: str | None = None, *, user_id: str | None = None) -> dict[str, Any]:
    """Persist imported memory data via storage provider.

    Args:
        memory_data: Full memory payload to persist.
        agent_name: If provided, imports into per-agent memory.
        user_id: If provided, scopes memory to a specific user.

    Returns:
        The saved memory data after storage normalization.

    Raises:
        OSError: If persisting the imported memory fails.
    """
    storage = get_memory_storage()
    if not storage.save(memory_data, agent_name, user_id=user_id):
        raise OSError("Failed to save imported memory data")
    return storage.load(agent_name, user_id=user_id)


def clear_memory_data(agent_name: str | None = None, *, user_id: str | None = None) -> dict[str, Any]:
    """yyds: 清空 memory — 重置为空结构。API 的 DELETE /memory 用。"""
    cleared_memory = create_empty_memory()
    if not _save_memory_to_file(cleared_memory, agent_name, user_id=user_id):
        raise OSError("Failed to save cleared memory data")
    return cleared_memory


def _validate_confidence(confidence: float) -> float:
    """yyds: confidence 必须在 [0, 1] 且有限（排除 NaN/inf）。"""
    if not math.isfinite(confidence) or confidence < 0 or confidence > 1:
        raise ValueError("confidence")
    return confidence


def create_memory_fact(
    content: str,
    category: str = "context",
    confidence: float = 0.5,
    agent_name: str | None = None,
    *,
    user_id: str | None = None,
) -> dict[str, Any]:
    """yyds: 手动创建一条 fact 并持久化 — API 的 POST /memory/facts 用。

    和 LLM 提取的区别：LLM 提取是自动的，这个是人工指定。
    """
    normalized_content = content.strip()
    if not normalized_content:
        raise ValueError("content")

    normalized_category = category.strip() or "context"
    validated_confidence = _validate_confidence(confidence)
    now = utc_now_iso_z()
    memory_data = get_memory_data(agent_name, user_id=user_id)
    updated_memory = dict(memory_data)
    facts = list(memory_data.get("facts", []))
    facts.append(
        {
            "id": f"fact_{uuid.uuid4().hex[:8]}",
            "content": normalized_content,
            "category": normalized_category,
            "confidence": validated_confidence,
            "createdAt": now,
            "source": "manual",
        }
    )
    updated_memory["facts"] = facts

    if not _save_memory_to_file(updated_memory, agent_name, user_id=user_id):
        raise OSError("Failed to save memory data after creating fact")

    return updated_memory


def delete_memory_fact(fact_id: str, agent_name: str | None = None, *, user_id: str | None = None) -> dict[str, Any]:
    """yyds: 删除指定 fact — API 的 DELETE /memory/facts/{id} 用。"""
    memory_data = get_memory_data(agent_name, user_id=user_id)
    facts = memory_data.get("facts", [])
    updated_facts = [fact for fact in facts if fact.get("id") != fact_id]
    if len(updated_facts) == len(facts):
        raise KeyError(fact_id)

    updated_memory = dict(memory_data)
    updated_memory["facts"] = updated_facts

    if not _save_memory_to_file(updated_memory, agent_name, user_id=user_id):
        raise OSError(f"Failed to save memory data after deleting fact '{fact_id}'")

    return updated_memory


def update_memory_fact(
    fact_id: str,
    content: str | None = None,
    category: str | None = None,
    confidence: float | None = None,
    agent_name: str | None = None,
    *,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Update an existing fact and persist the updated memory data."""
    memory_data = get_memory_data(agent_name, user_id=user_id)
    updated_memory = dict(memory_data)
    updated_facts: list[dict[str, Any]] = []
    found = False

    for fact in memory_data.get("facts", []):
        if fact.get("id") == fact_id:
            found = True
            updated_fact = dict(fact)
            if content is not None:
                normalized_content = content.strip()
                if not normalized_content:
                    raise ValueError("content")
                updated_fact["content"] = normalized_content
            if category is not None:
                updated_fact["category"] = category.strip() or "context"
            if confidence is not None:
                updated_fact["confidence"] = _validate_confidence(confidence)
            updated_facts.append(updated_fact)
        else:
            updated_facts.append(fact)

    if not found:
        raise KeyError(fact_id)

    updated_memory["facts"] = updated_facts

    if not _save_memory_to_file(updated_memory, agent_name, user_id=user_id):
        raise OSError(f"Failed to save memory data after updating fact '{fact_id}'")

    return updated_memory


def _extract_text(content: Any) -> str:
    """yyds: 从 LLM 响应中提取纯文本。

    LLM 返回的内容可能是三种格式：
      str              → 直接返回
      [{"type":"text","text":"..."}] → 拼接所有 text 字段
      ["chunk1","chunk2"]           → 拼接字符串块

    为什么不直接 str()？
      str([{"type":"text","text":"hello"}])
      → "[{'type': 'text', 'text': 'hello'}]"  ← Python repr，不是文本内容
      而我们需要的是 "hello"。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        pending_str_parts: list[str] = []

        def flush_pending_str_parts() -> None:
            if pending_str_parts:
                pieces.append("".join(pending_str_parts))
                pending_str_parts.clear()

        for block in content:
            if isinstance(block, str):
                pending_str_parts.append(block)
            elif isinstance(block, dict):
                flush_pending_str_parts()
                text_val = block.get("text")
                if isinstance(text_val, str):
                    pieces.append(text_val)

        flush_pending_str_parts()
        return "\n".join(pieces)
    return str(content)


_REQUIRED_MEMORY_UPDATE_TOP_LEVEL_KEYS = frozenset({"user", "history", "newFacts", "factsToRemove"})


def _normalize_memory_update_fact(fact: Any) -> dict[str, Any] | None:
    """Normalize a single fact entry from a model-produced memory update."""
    if not isinstance(fact, dict):
        return None

    raw_content = fact.get("content")
    if not isinstance(raw_content, str):
        return None
    content = raw_content.strip()
    if not content:
        return None

    raw_category = fact.get("category")
    category = raw_category.strip() if isinstance(raw_category, str) and raw_category.strip() else "context"

    raw_confidence = fact.get("confidence", 0.5)
    if isinstance(raw_confidence, bool):
        return None
    if isinstance(raw_confidence, str):
        raw_confidence = raw_confidence.strip()
        if not raw_confidence:
            return None
        try:
            raw_confidence = float(raw_confidence)
        except ValueError:
            return None
    elif isinstance(raw_confidence, (int, float)):
        raw_confidence = float(raw_confidence)
    else:
        return None

    if not math.isfinite(raw_confidence):
        return None

    normalized_fact = {
        "content": content,
        "category": category,
        "confidence": raw_confidence,
    }
    source_error = fact.get("sourceError")
    if isinstance(source_error, str):
        normalized_source_error = source_error.strip()
        if normalized_source_error:
            normalized_fact["sourceError"] = normalized_source_error

    return normalized_fact


def _normalize_memory_update_data(update_data: dict[str, Any]) -> dict[str, Any]:
    """Coerce parsed memory update data into the shape consumed by _apply_updates."""
    user = update_data.get("user")
    history = update_data.get("history")
    new_facts = update_data.get("newFacts")
    facts_to_remove = update_data.get("factsToRemove")
    normalized_facts_to_remove = [fact_id for fact_id in facts_to_remove if isinstance(fact_id, str)] if isinstance(facts_to_remove, list) else []
    normalized_new_facts = []
    dropped_new_fact = not isinstance(new_facts, list)
    if isinstance(new_facts, list):
        for fact in new_facts:
            normalized_fact = _normalize_memory_update_fact(fact)
            if normalized_fact is not None:
                normalized_new_facts.append(normalized_fact)
            else:
                dropped_new_fact = True

    if normalized_facts_to_remove and dropped_new_fact:
        raise json.JSONDecodeError(
            "Unsafe partial memory update: factsToRemove with malformed newFacts",
            json.dumps(update_data, ensure_ascii=False),
            0,
        )

    return {
        "user": user if isinstance(user, dict) else {},
        "history": history if isinstance(history, dict) else {},
        "newFacts": normalized_new_facts,
        "factsToRemove": normalized_facts_to_remove,
    }


def _parse_memory_update_response(response_content: Any) -> dict[str, Any]:
    """Parse the first valid memory-update JSON object from an LLM response.

    Some providers may wrap JSON in thinking traces, prose, or markdown fences
    even when prompted to return JSON only. This parser accepts safely
    extractable JSON objects but does not repair truncated or malformed JSON.
    """
    response_text = _extract_text(response_content).strip()
    decoder = json.JSONDecoder()

    for match in re.finditer(r"\{", response_text):
        try:
            parsed, _end = decoder.raw_decode(response_text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and _REQUIRED_MEMORY_UPDATE_TOP_LEVEL_KEYS.issubset(parsed):
            return _normalize_memory_update_data(parsed)

    raise json.JSONDecodeError("No valid memory update JSON object found", response_text, 0)


# yyds: 匹配文件上传相关句子的正则 —— 用于从 memory 中清除临时上传事件
# yyds: 上传文件是 session 级别的，记录到 memory 会导致下次对话找不到文件
# Matches sentences that describe a file-upload *event* rather than general
# file-related work.  Deliberately narrow to avoid removing legitimate facts
# such as "User works with CSV files" or "prefers PDF export".
_UPLOAD_SENTENCE_RE = re.compile(
    r"[^.!?]*\b(?:"
    r"upload(?:ed|ing)?(?:\s+\w+){0,3}\s+(?:file|files?|document|documents?|attachment|attachments?)"
    r"|file\s+upload"
    r"|/mnt/user-data/uploads/"
    r"|<uploaded_files>"
    r")[^.!?]*[.!?]?\s*",
    re.IGNORECASE,
)


def _strip_upload_mentions_from_memory(memory_data: dict[str, Any]) -> dict[str, Any]:
    """yyds: 清除 memory 中所有"上传了文件"的描述。

    为什么？
      文件上传是 session 级别的 — 这次对话传的文件，下次对话就找不到了。
      如果 memory 记了"用户上传了 report.pdf"，下次对话 AI 会以为文件还在，去引用它 → 找不到 → 报错。

    正则很窄：
      只匹配"上传了某个文件"这种事件描述，
      不匹配"用户喜欢 CSV 格式"这种一般性提及。
    """
    for section in ("user", "history"):
        section_data = memory_data.get(section, {})
        for _key, val in section_data.items():
            if isinstance(val, dict) and "summary" in val:
                cleaned = _UPLOAD_SENTENCE_RE.sub("", val["summary"]).strip()
                cleaned = re.sub(r"  +", " ", cleaned)
                val["summary"] = cleaned

    facts = memory_data.get("facts", [])
    if facts:
        memory_data["facts"] = [f for f in facts if not _UPLOAD_SENTENCE_RE.search(f.get("content", ""))]

    return memory_data


def _fact_content_key(content: Any) -> str | None:
    """yyds: fact 去重键 — content 去空白后转小写比较。

    为什么用 casefold 不用 lower？
      casefold 处理更多 Unicode 情况（比如德语 ß → ss）。
      虽然 memory 里主要是中英文，但用 casefold 更安全。
    """
    if not isinstance(content, str):
        return None
    stripped = content.strip()
    if not stripped:
        return None
    return stripped.casefold()


class MemoryUpdater:
    """yyds: 记忆更新器 — 用 LLM 分析对话并更新 memory。

    完整生命周期：

    update_memory() 被调用（queue._process_queue 里逐个调）
      │
      在 event loop 内吗？
      ├─ 不在 → 直接调 _do_update_memory_sync()
      └─ 在 → offload 到线程池（避免阻塞 event loop）
              → _do_update_memory_sync()
      │
      ▼
    三步走：
      Step 1: _prepare_update_prompt()
        ├─ memory 系统关了？→ return None，不处理
        ├─ 没有消息？→ return None
        ├─ 读当前 memory + 格式化对话 + 纠正提示
        └─ 返回 (current_memory, prompt)
      │
      Step 2: model.invoke(prompt)
        ├─ LLM 返回 JSON 更新指令
        └─ 解析失败？→ 记警告，return False
      │
      Step 3: _finalize_update()
        ├─ _apply_updates(): 合并 user/history + 增删 facts
        ├─ _strip_upload_mentions_from_memory(): 清除上传文件描述
        └─ storage.save(): 原子写入 memory.json
    """

    def __init__(self, model_name: str | None = None):
        self._model_name = model_name

    def _get_model(self):
        """Get the model for memory updates."""
        config = get_memory_config()
        model_name = self._model_name or config.model_name
        return create_chat_model(name=model_name, thinking_enabled=False)  # yyds: memory 提取不需要 thinking，省 token

    def _build_correction_hint(
        self,
        correction_detected: bool,
        reinforcement_detected: bool,
    ) -> str:
        """yyds: 构建 correction/reinforcement 提示 — 告诉 LLM 用多高的置信度记录。

        correction（用户说"不对"）：
          → 提示 LLM："用 confidence >= 0.95 记录为 category=correction"
          → 高置信度确保纠正后的记忆不会被低置信度的新信息覆盖

        reinforcement（用户说"对就是这样"）：
          → 提示 LLM："用 confidence >= 0.9 记录为 preference/behavior"
          → 用户明确肯定了 AI 的做法，值得记住

        correction 优先级高于 reinforcement：
          如果同时检测到纠正和肯定，以纠正为准。
        """
        correction_hint = ""
        if correction_detected:
            correction_hint = (
                "IMPORTANT: Explicit correction signals were detected in this conversation. "
                "Pay special attention to what the agent got wrong, what the user corrected, "
                "and record the correct approach as a fact with category "
                '"correction" and confidence >= 0.95 when appropriate.'
            )
        if reinforcement_detected:
            reinforcement_hint = (
                "IMPORTANT: Positive reinforcement signals were detected in this conversation. "
                "The user explicitly confirmed the agent's approach was correct or helpful. "
                "Record the confirmed approach, style, or preference as a fact with category "
                '"preference" or "behavior" and confidence >= 0.9 when appropriate.'
            )
            correction_hint = (correction_hint + "\n" + reinforcement_hint).strip() if correction_hint else reinforcement_hint

        return correction_hint

    def _prepare_update_prompt(
        self,
        messages: list[Any],
        agent_name: str | None,
        correction_detected: bool,
        reinforcement_detected: bool,
        user_id: str | None = None,
    ) -> tuple[dict[str, Any], str] | None:
        """yyds: 准备给 LLM 的 prompt — 加载当前 memory + 格式化对话 + 纠正提示。

        返回 None 的条件（任一满足就不处理）：
          - memory 系统关了（config.enabled=False）
          - 消息列表空
          - 对话文本过滤后没内容（全是工具调用，没有人类对话）
        """
        config = get_memory_config()
        if not config.enabled or not messages:
            return None

        current_memory = get_memory_data(agent_name, user_id=user_id)
        conversation_text = format_conversation_for_update(messages)
        if not conversation_text.strip():
            return None

        correction_hint = self._build_correction_hint(
            correction_detected=correction_detected,
            reinforcement_detected=reinforcement_detected,
        )
        prompt = MEMORY_UPDATE_PROMPT.format(
            current_memory=json.dumps(current_memory, indent=2, ensure_ascii=False),
            conversation=conversation_text,
            correction_hint=correction_hint,
        )
        return current_memory, prompt

    def _finalize_update(
        self,
        current_memory: dict[str, Any],
        response_content: Any,
        thread_id: str | None,
        agent_name: str | None,
        user_id: str | None = None,
    ) -> bool:
        # yyds: 解析 LLM 响应 → 合并 → 清理 → 存储。
        # 步骤：① 提取纯文本 ② JSON 解析 ③ 深拷贝+合并 ④ 清除上传文件描述 ⑤ 持久化
        # 为什么要深拷贝？_apply_updates 会原地修改，如果 save() 失败缓存不会被改坏。
        """Parse the model response, apply updates, and persist memory."""
        update_data = _parse_memory_update_response(response_content)
        # Deep-copy before in-place mutation so a subsequent save() failure
        # cannot corrupt the still-cached original object reference.
        updated_memory = self._apply_updates(copy.deepcopy(current_memory), update_data, thread_id)
        updated_memory = _strip_upload_mentions_from_memory(updated_memory)
        return get_memory_storage().save(updated_memory, agent_name, user_id=user_id)

    async def aupdate_memory(
        self,
        messages: list[Any],
        thread_id: str | None = None,
        agent_name: str | None = None,
        correction_detected: bool = False,
        reinforcement_detected: bool = False,
        user_id: str | None = None,
    ) -> bool:
        """yyds: 异步更新入口 — asyncio.to_thread 调用 sync 路径。

        为什么不直接用 async model.ainvoke()？
          因为 async httpx 的连接池是全局缓存的（@lru_cache），
          和主 event loop 共享。在后台线程创建新 loop 调 ainvoke
          会导致跨 loop 复用连接 → bug。

          用 to_thread + sync invoke = 新线程 + sync HTTP = 独立连接池 = 安全。
        """
        return await asyncio.to_thread(
            self._do_update_memory_sync,
            messages=messages,
            thread_id=thread_id,
            agent_name=agent_name,
            correction_detected=correction_detected,
            reinforcement_detected=reinforcement_detected,
            user_id=user_id,
        )

    def _do_update_memory_sync(
        self,
        messages: list[Any],
        thread_id: str | None = None,
        agent_name: str | None = None,
        correction_detected: bool = False,
        reinforcement_detected: bool = False,
        user_id: str | None = None,
    ) -> bool:
        """yyds: 纯同步更新 — model.invoke() 走 sync HTTP，不触碰 async 连接池。

        三步走的核心实现：
          ① _prepare_update_prompt: 准备 prompt
          ② model.invoke(prompt): LLM 返回 JSON
          ③ _finalize_update: 解析 + 合并 + 存储
        """
        try:
            prepared = self._prepare_update_prompt(
                messages=messages,
                agent_name=agent_name,
                correction_detected=correction_detected,
                reinforcement_detected=reinforcement_detected,
                user_id=user_id,
            )
            if prepared is None:
                return False

            current_memory, prompt = prepared
            model = self._get_model()
            response = model.invoke(prompt, config={"run_name": "memory_agent"})
            return self._finalize_update(
                current_memory=current_memory,
                response_content=response.content,
                thread_id=thread_id,
                agent_name=agent_name,
                user_id=user_id,
            )
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse LLM response for memory update: %s", e)
            return False
        except Exception as e:
            logger.exception("Memory update failed: %s", e)
            return False

    def update_memory(
        self,
        messages: list[Any],
        thread_id: str | None = None,
        agent_name: str | None = None,
        correction_detected: bool = False,
        reinforcement_detected: bool = False,
        user_id: str | None = None,
    ) -> bool:
        """yyds: 同步更新入口 — 自动检测是否在 event loop 内。

        在 event loop 内？
          → offload 到线程池（ThreadPoolExecutor），不阻塞 loop
        不在 event loop 内？
          → 直接调 _do_update_memory_sync()

        谁调的？
          queue._process_queue() — 在 Timer 线程里调（不在 event loop 内）→ 直接 sync
          外部同步 API — 可能在 event loop 内 → offload
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            try:
                future = _SYNC_MEMORY_UPDATER_EXECUTOR.submit(
                    self._do_update_memory_sync,
                    messages=messages,
                    thread_id=thread_id,
                    agent_name=agent_name,
                    correction_detected=correction_detected,
                    reinforcement_detected=reinforcement_detected,
                    user_id=user_id,
                )
                return future.result()
            except Exception:
                logger.exception("Failed to offload memory update to executor")
                return False

        return self._do_update_memory_sync(
            messages=messages,
            thread_id=thread_id,
            agent_name=agent_name,
            correction_detected=correction_detected,
            reinforcement_detected=reinforcement_detected,
            user_id=user_id,
        )

    def _apply_updates(
        self,
        current_memory: dict[str, Any],
        update_data: dict[str, Any],
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """yyds: ★★★ 记忆系统最核心的函数 — 把 LLM 返回的更新指令合并到现有 memory。

        LLM 返回的 update_data 长这样：
          {
            "user": {
              "workContext": {"summary": "...", "shouldUpdate": true},
              "personalContext": {"summary": "...", "shouldUpdate": false},
              ...
            },
            "history": {
              "recentMonths": {"summary": "...", "shouldUpdate": true},
              ...
            },
            "newFacts": [
              {"content": "技术栈 CrewAI", "category": "knowledge", "confidence": 0.95}
            ],
            "factsToRemove": ["fact_abc123"]
          }

        合并规则：
          user/history 的 6 个 section：
            shouldUpdate=true + summary 非空 → 覆盖
            shouldUpdate=false → 不动

          facts 删除：
            factsToRemove 里的 id → 移除
            （LLM 判断某些旧 fact 已过时或被纠正了）

          facts 添加：
            新 fact 的 confidence >= 阈值（默认 0.5）→ 加入
            content 去重（casefold 比较）→ 已存在则跳过
            超过 max_facts → 按 confidence 排序只保留 top N

        例子：
          当前 facts: [
            {id: "f1", content: "技术栈 LangGraph", confidence: 0.9}
          ]
          LLM 返回:
            factsToRemove: ["f1"]  （旧的 LangGraph 被纠正了）
            newFacts: [{content: "技术栈 CrewAI", confidence: 0.95}]
          结果:
            facts: [{id: "fact_xxx", content: "技术栈 CrewAI", confidence: 0.95}]
        """
        config = get_memory_config()
        now = utc_now_iso_z()

        # ① 更新 user 三段 — shouldUpdate=true 才覆盖
        user_updates = update_data.get("user", {})
        for section in ["workContext", "personalContext", "topOfMind"]:
            section_data = user_updates.get(section, {})
            if section_data.get("shouldUpdate") and section_data.get("summary"):
                current_memory["user"][section] = {
                    "summary": section_data["summary"],
                    "updatedAt": now,
                }

        # ② 更新 history 三段 — 同理
        history_updates = update_data.get("history", {})
        for section in ["recentMonths", "earlierContext", "longTermBackground"]:
            section_data = history_updates.get(section, {})
            if section_data.get("shouldUpdate") and section_data.get("summary"):
                current_memory["history"][section] = {
                    "summary": section_data["summary"],
                    "updatedAt": now,
                }

        # ③ 删除 facts — LLM 说哪些 id 过时了
        facts_to_remove = set(update_data.get("factsToRemove", []))
        if facts_to_remove:
            current_memory["facts"] = [f for f in current_memory.get("facts", []) if f.get("id") not in facts_to_remove]

        # ④ 添加新 facts — 去重 + confidence 阈值过滤
        existing_fact_keys = {fact_key for fact_key in (_fact_content_key(fact.get("content")) for fact in current_memory.get("facts", [])) if fact_key is not None}
        new_facts = update_data.get("newFacts", [])
        for fact in new_facts:
            confidence = fact.get("confidence", 0.5)
            if confidence >= config.fact_confidence_threshold:  # yyds: 低置信度的不要，避免垃圾信息污染 memory
                raw_content = fact.get("content", "")
                if not isinstance(raw_content, str):
                    continue
                normalized_content = raw_content.strip()
                fact_key = _fact_content_key(normalized_content)
                if fact_key is not None and fact_key in existing_fact_keys:  # yyds: 去重，已有的不重复加
                    continue

                fact_entry = {
                    "id": f"fact_{uuid.uuid4().hex[:8]}",
                    "content": normalized_content,
                    "category": fact.get("category", "context"),
                    "confidence": confidence,
                    "createdAt": now,
                    "source": thread_id or "unknown",
                }
                source_error = fact.get("sourceError")  # yyds: correction 类型的 fact 可以附带"之前的错误是什么"
                if isinstance(source_error, str):
                    normalized_source_error = source_error.strip()
                    if normalized_source_error:
                        fact_entry["sourceError"] = normalized_source_error
                current_memory["facts"].append(fact_entry)
                if fact_key is not None:
                    existing_fact_keys.add(fact_key)

        # ⑤ 超过上限 → 按 confidence 排序只保留 top N
        if len(current_memory["facts"]) > config.max_facts:
            current_memory["facts"] = sorted(
                current_memory["facts"],
                key=lambda f: f.get("confidence", 0),
                reverse=True,
            )[: config.max_facts]

        return current_memory


def update_memory_from_conversation(
    messages: list[Any],
    thread_id: str | None = None,
    agent_name: str | None = None,
    correction_detected: bool = False,
    reinforcement_detected: bool = False,
    user_id: str | None = None,
) -> bool:
    """yyds: 便捷函数 — 创建 MemoryUpdater 并调用 update_memory。

    谁调的？queue._process_queue() 里逐个处理时。
    """
    updater = MemoryUpdater()
    return updater.update_memory(messages, thread_id, agent_name, correction_detected, reinforcement_detected, user_id=user_id)
