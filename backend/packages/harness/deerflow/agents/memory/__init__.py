# yyds: ═══════════════════════════════════════════════════════════════════
# yyds: Memory 子系统 —— 跨会话的用户画像持久化
# yyds: ═══════════════════════════════════════════════════════════════════
# yyds:
# yyds: 核心数据流：
# yyds:
# yyds:   MemoryMiddleware(after_agent)
# yyds:     │
# yyds:     ├─ queue.add(thread_id, messages)  ← 去抖动入队
# yyds:     │     │
# yyds:     │     └─ 30s 后 _process_queue()
# yyds:     │           │
# yyds:     │           └─ MemoryUpdater.update_memory()
# yyds:     │                 ├─ format_conversation_for_update() → 文本
# yyds:     │                 ├─ MEMORY_UPDATE_PROMPT + current_memory + conversation
# yyds:     │                 ├─ model.invoke(prompt) → JSON 更新指令
# yyds:     │                 ├─ _apply_updates() → 合并 user/history/facts
# yyds:     │                 └─ storage.save() → 原子写入 memory.json
# yyds:     │
# yyds:   下一轮对话：
# yyds:     prompt.py:format_memory_for_injection(memory_data)
# yyds:       → 注入 <memory> 标签到 system prompt → top 15 facts by confidence
# yyds:
# yyds: 另一条路径（summarization_hook）：
# yyds:   SummarizationMiddleware 删除旧消息前 → memory_flush_hook()
# yyds:     → queue.add_nowait() → 立即处理（不等 30s）
# yyds: ═══════════════════════════════════════════════════════════════════
"""Memory module for DeerFlow.

This module provides a global memory mechanism that:
- Stores user context and conversation history in memory.json
- Uses LLM to summarize and extract facts from conversations
- Injects relevant memory into system prompts for personalized responses
"""

from deerflow.agents.memory.prompt import (
    FACT_EXTRACTION_PROMPT,
    MEMORY_UPDATE_PROMPT,
    format_conversation_for_update,
    format_memory_for_injection,
)
from deerflow.agents.memory.queue import (
    ConversationContext,
    MemoryUpdateQueue,
    get_memory_queue,
    reset_memory_queue,
)
from deerflow.agents.memory.storage import (
    FileMemoryStorage,
    MemoryStorage,
    get_memory_storage,
)
from deerflow.agents.memory.updater import (
    MemoryUpdater,
    clear_memory_data,
    delete_memory_fact,
    get_memory_data,
    reload_memory_data,
    update_memory_from_conversation,
)

__all__ = [
    # Prompt utilities
    "MEMORY_UPDATE_PROMPT",
    "FACT_EXTRACTION_PROMPT",
    "format_memory_for_injection",
    "format_conversation_for_update",
    # Queue
    "ConversationContext",
    "MemoryUpdateQueue",
    "get_memory_queue",
    "reset_memory_queue",
    # Storage
    "MemoryStorage",
    "FileMemoryStorage",
    "get_memory_storage",
    # Updater
    "MemoryUpdater",
    "clear_memory_data",
    "delete_memory_fact",
    "get_memory_data",
    "reload_memory_data",
    "update_memory_from_conversation",
]
