"""yyds: Memory 子系统 — 跨会话记住用户是谁、喜欢什么、在做什么。

═════════════════════════════════════════════════════════════════════
【大白话：解决什么问题】
═════════════════════════════════════════════════════════════════════
  Agent 每次对话都是"失忆"的 — 不知道用户是谁、之前聊过什么。
  Memory 子系统解决这个问题：
    对话结束 → LLM 提取关键信息 → 存到 memory.json
    下次对话 → 从 memory.json 读取 → 注入 system prompt → Agent 有了"记忆"

═════════════════════════════════════════════════════════════════════
【memory.json 存了什么？三块内容】
═════════════════════════════════════════════════════════════════════
  ① user（你是谁）— 三个 summary 段落，LLM 覆盖式更新
    - workContext:     "AI Agent 开发工程师，技术栈 Python + LangGraph"
    - personalContext: "中文用户，偏好简洁回答"
    - topOfMind:       "正在调研 SOC Agent 方案；学 DeerFlow 中..."

  ② history（你之前做了什么）— 三个 summary 段落，按时间分层
    - recentMonths:       "最近 1-3 个月：学了 DeerFlow Agent 构建..."
    - earlierContext:     "3-12 个月前：做 AI 安全方向..."
    - longTermBackground: "长期：5 年安全行业经验"

  ③ facts（一个个具体知识点）— 列表，每条有 id/content/category/confidence
    - {content: "技术栈 LangGraph", category: "knowledge", confidence: 0.9}
    - {content: "喜欢中文回复",     category: "preference", confidence: 0.95}
    - facts 有 6 种 category: preference / knowledge / context / behavior / goal / correction
    - 超过 max_facts 时按 confidence 排序只保留 top N

═════════════════════════════════════════════════════════════════════
【全流程：数据进去 → 存起来 → 拿出来用】
═════════════════════════════════════════════════════════════════════

  ┌─────────────────────────────────────────────────────────────┐
  │ 阶段一：数据怎么进去（两条入口）                              │
  │                                                             │
  │ 入口 A：正常路径（MemoryMiddleware，不急）                    │
  │   每轮对话结束                                               │
  │     → queue.add(messages)  ← queue.py                      │
  │     → 重置 30 秒倒计时（去抖动）                              │
  │     → 用户继续说话 → 再 add() → 倒计时重新开始               │
  │     → 用户停了 30 秒 → 倒计时到期 → _process_queue()         │
  │     → 同一 thread_id 去重（新 context 替换旧的）              │
  │     → 逐个调 MemoryUpdater.update_memory()                   │
  │                                                             │
  │ 入口 B：抢救路径（memory_flush_hook，很急）                   │
  │   SummarizationMiddleware 即将删除旧消息                     │
  │     → memory_flush_hook(event)  ← summarization_hook.py    │
  │     → event.messages_to_summarize = 即将被删的消息           │
  │     → filter_messages_for_memory()  过滤掉工具调用、纯上传   │
  │     → detect_correction()  检测"不对""你理解错了"等信号      │
  │     → detect_reinforcement()  检测"对就是这样""完全正确"     │
  │     → queue.add_nowait(0s)  不能等 30 秒，消息马上被删       │
  └─────────────────────────────────────────────────────────────┘
                              │
                              ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ 阶段二：LLM 提取 + 合并 + 存储（updater.py ★核心）           │
  │                                                             │
  │ Step 1: 准备 prompt  ← _prepare_update_prompt()             │
  │   current_memory = storage.load()  读当前 memory.json       │
  │   conversation_text = format_conversation_for_update(msgs)  │
  │   correction_hint = 如果有纠正信号，提示 LLM 用高置信度      │
  │   prompt = MEMORY_UPDATE_PROMPT.format(...)                 │
  │                                                             │
  │ Step 2: 调 LLM  ← _do_update_memory_sync()                  │
  │   model.invoke(prompt)  用 sync 不用 async                  │
  │   （避免跨 event loop 的 httpx 连接池冲突）                  │
  │   → LLM 返回 JSON: {user: {shouldUpdate, summary},          │
  │                     newFacts: [...], factsToRemove: [...]}   │
  │                                                             │
  │ Step 3: 合并  ← _apply_updates() ★★★ 记忆系统最核心的函数   │
  │   user 三段:  shouldUpdate=True → 直接覆盖 summary           │
  │   history 三段: shouldUpdate=True → 直接覆盖 summary         │
  │   删 facts:   factsToRemove 里的 id → 移除                   │
  │   加 facts:   newFacts → 去重（content casefold）+           │
  │               confidence >= 阈值 才加入                      │
  │   上限:       超过 max_facts → 按 confidence 排序保留 top N  │
  │                                                             │
  │ Step 4: 清理 + 存储                                         │
  │   _strip_upload_mentions_from_memory()  清除上传文件描述     │
  │   storage.save()  原子写入（.tmp → rename）                  │
  └─────────────────────────────────────────────────────────────┘
                              │
                              ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ 阶段三：怎么拿出来用（一条出口）                              │
  │                                                             │
  │ format_memory_for_injection()  ← prompt.py                 │
  │   1. 格式化 user context:  Work / Personal / Current Focus  │
  │   2. 格式化 history:       Recent / Earlier / Background    │
  │   3. 格式化 facts:         按 confidence 从高到低排序        │
  │      逐条加入，tiktoken 计算 token 数                        │
  │      总量不超过 max_tokens（默认 2000）                      │
  │   → 输出文本注入 system prompt 的 <memory> 标签              │
  │   → Agent 下次对话就"记住"了                                │
  └─────────────────────────────────────────────────────────────┘

═════════════════════════════════════════════════════════════════════
【纠正机制：用户说"不对"时怎么处理】
═════════════════════════════════════════════════════════════════════
  用户说"不对，我说的是 CrewAI 不是 LangGraph"
    → message_processing.detect_correction() 正则匹配到纠正信号
    → correction_detected = True 传入 MemoryUpdater
    → _build_correction_hint() 生成提示：
      "请用 confidence >= 0.95 记录为 category=correction"
    → LLM 输出 newFact: {category: "correction", confidence: 0.95, ...}
    → 同时 factsToRemove 旧的那条
    → _apply_updates 里：删旧的，加新的

  reinforcement 同理：用户说"对，就是这样"
    → detect_reinforcement() 匹配
    → 提示 LLM 用 confidence >= 0.9 记录为 preference/behavior

═════════════════════════════════════════════════════════════════════
【具体例子：从第 1 轮到第 10 轮】
═════════════════════════════════════════════════════════════════════
  第 1-3 轮：用户说"我在做一个 AI Agent 框架，用 LangGraph"
    → 30s 后 LLM 提取：
      user.workContext = "开发 AI Agent 框架，技术栈 LangGraph"
      facts += [{content: "技术栈 LangGraph", category: "knowledge", confidence: 0.9}]
    → 原子写入 memory.json

  第 4 轮：system prompt 里自动注入：
    <memory>
    User Context:
    - Work: 开发 AI Agent 框架，技术栈 LangGraph
    Facts:
    - [knowledge | 0.90] 技术栈 LangGraph
    </memory>
    → Agent 知道用户在做什么了，不用重复问

  第 10 轮：用户说"不对，我说的是 CrewAI 不是 LangGraph"
    → correction_detected=True
    → LLM 用 confidence>=0.95 覆盖旧记忆
    → 旧 fact 被 factsToRemove 删掉，新 fact 加入
    → facts 更新为 "技术栈 CrewAI"

  第 50 轮：对话太长，SummarizationMiddleware 触发压缩
    → 即将删除前 88 条消息
    → memory_flush_hook 抢救：过滤 + 检测信号 → add_nowait(0s)
    → LLM 从即将被删的消息中提取剩余关键信息
    → 信息被安全保存到 memory.json，然后旧消息才被删除

═════════════════════════════════════════════════════════════════════
【文件结构与阅读顺序】
═════════════════════════════════════════════════════════════════════
  agents/memory/
  ├── storage.py              存储层：memory.json 读写 + mtime 缓存 + 原子写入
  ├── queue.py                去抖动队列：30s 攒一批再处理，避免每轮都调 LLM
  ├── updater.py              ★ 核心：用 LLM 从对话中提取用户画像，_apply_updates 合并更新
  ├── prompt.py               LLM prompt 模板 + format_memory_for_injection 注入函数
  ├── summarization_hook.py   压缩前钩子：消息被删前"抢救"到记忆队列
  ├── message_processing.py   消息过滤 + correction/reinforcement 信号检测
  └── __init__.py

  阅读顺序：
    1. storage.py           存储层，先搞清楚 memory.json 长什么样、怎么读写
    2. queue.py             去抖动队列，理解为什么不是每轮都更新
    3. updater.py           ★ 核心，三步走：准备 prompt → 调 LLM → _apply_updates 合并
    4. prompt.py            prompt 模板（MEMORY_UPDATE_PROMPT）+ 注入函数（format_memory_for_injection）
    5. summarization_hook.py  压缩前抢救（和 SummarizationMiddleware 配合）
    6. message_processing.py  信号检测（correction/reinforcement 正则匹配）

═════════════════════════════════════════════════════════════════════
【关键设计决策】
═════════════════════════════════════════════════════════════════════
  - 去抖动：queue.add() 每次重置 30s 定时器，连续对话只触发一次 LLM（省钱）
  - 抢救路径：queue.add_nowait(0s)，消息即将被删，等不了 30s
  - sync model.invoke()：不用 async，避免跨 event loop 的 httpx 连接池冲突
  - 原子写入：.tmp → rename，防止写一半断电丢数据
  - mtime 缓存：load 时检查文件修改时间，没变则返回缓存，避免重复读磁盘
  - 上传文件清除：_strip_upload_mentions_from_memory，上传是 session 级的，不该存到 memory
  - facts 去重：content.casefold() 比较，忽略大小写
  - confidence 阈值：低于 config.fact_confidence_threshold 的新 fact 不加入
  - max_facts 上限：超过时按 confidence 排序只保留 top N

---
Memory module for DeerFlow.

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

# TODO：memory模块只是对每块有个粗略理解，后续需要回过头来反复看！ 这个比较重要，因为在特定任务场景下，我要考虑在我的场景下存哪些记忆，怎么存，怎么用。
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
