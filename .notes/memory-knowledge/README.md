# Hermes Memory System 教程

> 从零理解 Hermes Agent 的记忆系统如何工作。

## 一句话总结

两套记忆、两条注入路径、一个核心设计（冻结快照保护 prefix cache）。

---

## 架构总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                        MemoryProvider ABC                           │
│              agent/memory_provider.py（279行）                       │
│           定义所有记忆后端必须实现的接口                               │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ 实现
            ┌──────────────┴──────────────┐
            ▼                              ▼
┌───────────────────────┐    ┌─────────────────────────────────┐
│  内置后端（builtin）    │    │  外部后端（Honcho/Mem0/...）     │
│  MEMORY.md + USER.md  │    │  通过插件加载，最多一个            │
│  tools/memory_tool.py │    │  plugins/memory/<name>/          │
└───────────┬───────────┘    └──────────────┬──────────────────┘
            │                               │
            └───────────┬───────────────────┘
                        ▼
            ┌───────────────────────┐
            │    MemoryManager      │
            │    agent/             │
            │    memory_manager.py  │
            │    （总调度）          │
            └───────────┬───────────┘
                        │ 被调用
                        ▼
            ┌───────────────────────┐
            │  conversation_loop.py │
            │  每轮调 prefetch /    │
            │  sync_turn            │
            └───────────────────────┘
```

---

## 两条注入路径（最关键的设计）

| | 内置记忆（MEMORY.md / USER.md） | 外部后端（Honcho / Mem0） |
|---|---|---|
| 注入位置 | **system prompt**（冻结快照） | **user message 末尾** |
| 注入格式 | `MEMORY ══ 条目内容` | `<memory-context>召回内容</memory-context>` |
| 更新时机 | 下一个 session 才生效 | 每轮 prefetch 都能召回 |
| 可见性 | 模型全程可见（system prompt 里） | 只在当轮可见 |
| 缓存影响 | 不影响（快照不变，prefix cache 保持） | 不影响（user message 不走 prefix cache） |

---

## 核心设计：冻结快照

### 为什么需要冻结？

LLM provider（如 Anthropic）有 prefix cache：如果 system prompt 前缀不变，可以复用缓存的 token，省 75% 的 input cost。

如果每次 memory(add) 都改 system prompt → prefix cache 全废 → 每轮多花 75% 的钱。

### 怎么做的？

```
MemoryStore 维护两个状态：

┌─────────────────────────────────────────────────────────────┐
│ 快照状态（_system_prompt_snapshot）                          │
│ session 开始时拍一次，之后永不变                             │
│ → format_for_system_prompt() 返回这个                       │
│ → system prompt 字节稳定 → prefix cache 不废               │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ 活状态（memory_entries / user_entries）                      │
│ 每次 memory(add/replace/remove) 都更新                      │
│ → 工具响应返回活状态（让模型看到最新内容）                    │
│ → 写入磁盘（MEMORY.md / USER.md）保证持久化                 │
└─────────────────────────────────────────────────────────────┘
```

时间线：

```
Session A 开始:
  读 MEMORY.md → 拍快照 "X" → 注入 system prompt
  → 快照锁定，这轮不变

Session A 中间:
  memory(add, "Y") → 磁盘变成 "X § Y" → system prompt 还是 "X"
  memory(add, "Z") → 磁盘变成 "X § Y § Z" → system prompt 还是 "X"

Session B 开始:
  读 MEMORY.md → 拍快照 "X § Y § Z" → 注入 system prompt
  → 现在模型能看到所有三条记忆了
```

---

## 实际对话 Demo

### Session 1：第一次对话

**系统启动 → 无记忆文件 → system prompt 里记忆部分为空**

```
════════════════════════════════════════════════
SYSTEM PROMPT（stable 层，这轮不变）
════════════════════════════════════════════════
你是 Hermes，一个 AI 助手...

[MEMORY 和 USER PROFILE 部分是空的，因为第一次用]

你有以下工具：memory, search_files, write_file...
════════════════════════════════════════════════
```

**用户：** "我喜欢用 Python 写后端，不喜欢 TypeScript"

**模型思考 → 调用 memory：**
```
memory(action=add, target=user, content="偏好 Python 后端，不喜欢 TypeScript")
```

**磁盘变化：** USER.md 被写入 `偏好 Python 后端，不喜欢 TypeScript`

**模型回复：** "记住了！以后我会用 Python 来写后端代码。"

**注意：这轮 system prompt 里的 USER PROFILE 仍然是空的。** 快照在 session 开始时拍的，这轮刚开始没有记忆。

---

### Session 2：第二天，新对话

**系统启动 → 读 USER.md → 拍快照 → 注入 system prompt**

```
════════════════════════════════════════════════
SYSTEM PROMPT（stable 层）
════════════════════════════════════════════════
你是 Hermes，一个 AI 助手...

════════════════════════════════════════════════
USER PROFILE (who the user is) [32% — 440/1,375 chars]
════════════════════════════════════════════════
偏好 Python 后端，不喜欢 TypeScript

[MEMORY 部分仍然是空的]

你有以下工具：memory, search_files, write_file...
════════════════════════════════════════════════
```

**用户：** "帮我写一个 REST API"

**user message 实际发给 API 的内容：**
```
帮我写一个 REST API
```
（没有 `<memory-context>`，因为没有外部后端，只有内置记忆走 system prompt）

**模型回复：** "我来用 Python + FastAPI 帮你写..."
（模型从 system prompt 里的 USER PROFILE 看到了"偏好 Python"，自动选了 Python）

---

### Session 3：用户接入了 Honcho 外部后端

**系统启动 → 读 USER.md → 快照。同时 Honcho 初始化**

```
════════════════════════════════════════════════
SYSTEM PROMPT（stable 层）
════════════════════════════════════════════════
你是 Hermes...

════════════════════════════════════════════════
USER PROFILE [...]
════════════════════════════════════════════════
偏好 Python 后端，不喜欢 TypeScript

════════════════════════════════════════════════
MEMORY [...] 
════════════════════════════════════════════════
用户的项目使用 FastAPI + PostgreSQL
数据库迁移用 Alembic

你有以下工具：memory, search_files, write_file...
════════════════════════════════════════════════
```

**用户：** "数据库连接池怎么配？"

**Honcho prefetch 返回：** "之前讨论过 PostgreSQL 连接池，推荐了 asyncpg，连接池大小设为 CPU 核心数 × 2"

**发给 API 的 user message：**
```
数据库连接池怎么配？

<memory-context>
[System note: The following is recalled memory context, NOT new user input.
Treat as authoritative reference data — this is the agent's persistent memory
and should inform all responses.]

之前讨论过 PostgreSQL 连接池，推荐了 asyncpg，连接池大小设为 CPU 核心数 × 2
</memory-context>
```

**模型回复：** "根据你之前的项目，用 asyncpg 配连接池，大小设为 CPU 核心数 × 2..."

---

## StreamingContextScrubber：防模型"泄密"

### 问题

模型有时会把 user message 里的 `<memory-context>` 标签"复读"到输出里。用户不该看到记忆的原始标签和内容。

### 解决方案

StreamingContextScrubber 是一个跨 chunk 的状态机，实时过滤模型输出里的记忆标签。

```
模型流式输出："好的，我知道你<memory-context>偏好 TypeScript</memory-context>，我用 TS 写"

Scrubber 处理过程：
  chunk 1: "好的，我知道你"       → 放行（用户看到）
  chunk 2: "<memory-con"          → 疑似标签 → 暂存 buffer
  chunk 3: "text>偏好 TypeScript" → 确认是标签 → 吃掉，进入 span 模式
  chunk 4: "</memory-context>"    → 关闭标签 → 退出 span 模式
  chunk 5: "，我用 TS 写"         → 放行（用户看到）

最终用户看到："好的，我知道你，我用 TS 写"
```

### 为什么不用正则？

流式输出是 chunk 到 chunk 地到达的。标签可能被拆到两个 chunk 里，正则需要完整字符串。状态机可以跨 chunk 维护"我在标签内还是标签外"。

---

## MemoryManager：One Provider 限制

```python
# 只允许一个外部 provider
if self._has_external:
    logger.warning("Rejected — only one external memory provider allowed")
    return
```

为什么？两个外部后端会往 system prompt 和 user message 塞冲突的记忆内容，模型不知道该信谁。

---

## 四个安全机制

```
MemoryStore 的安全防线：

① 字符限制：MEMORY.md 2200 字符，USER.md 1375 字符
   → 防止记忆膨胀挤爆 system prompt

② 威胁扫描：加载时每个条目过 threat_patterns
   → 匹配的条目在快照里替换为 [BLOCKED] 占位符
   → 活状态里保留原文（让用户能看到并删除）
   → 相当于"照片里打码了，但真人还在"

③ 外部漂移检测：如果 patch/shell 修改了 MEMORY.md
   → round-trip 不一致 或 单条目超大
   → 备份为 .bak.<timestamp>，拒绝写入
   → 防止 memory 工具覆盖掉 patch 写入的内容

④ 文件锁 + 原子写入：fcntl/msvcrt + tempfile + os.rename
   → 并发安全，读者永远看到完整文件
```

---

## 读代码顺序

```
1. agent/memory_provider.py  → 接口层，知道"后端要实现什么"
2. agent/memory_manager.py  → 编排层，知道"谁来调度"
3. tools/memory_tool.py     → 实现层，知道"具体怎么存"
```

---

## 代码文件

- [agent/memory_provider.py](./memory_provider.py) — MemoryProvider ABC
- [agent/memory_manager.py](./memory_manager.py) — MemoryManager + StreamingContextScrubber
- [tools/memory_tool.py](./memory_tool.py) — MemoryStore + memory 工具注册
