# SummarizationMiddleware 上下文压缩中间件

对话太长时，把旧消息压缩成摘要，但保护 skill 内容和日期提醒不被压掉，压缩前顺便把旧消息存到记忆系统。

---

## 痛点：没有它会怎样

```
场景：用户和 Agent 聊了 50 轮，总共 50000 token，LLM 窗口 128K

没有压缩时：
  → 第 1 轮：1000 token，正常
  → 第 30 轮：40000 token，开始变慢
  → 第 50 轮：50000 token，还在撑
  → 第 100 轮：120000 token
  → 最终结果：LLM 报错 "context too long"，整个对话崩了 💥

有了压缩但没有 Skill Rescue：
  → 第 50 轮触发压缩 → 旧消息全压成摘要
  → Agent 之前加载的 research skill 内容被压掉了
  → Agent "忘了"怎么调研 → 后续回复质量暴跌
  → 用户：刚才还好好的，怎么突然变蠢了？

有了压缩但没有 Hook：
  → 前 88 条消息被压缩，用户偏好、关键决策全丢了
  → 下次对话，Agent 完全不记得用户喜欢什么
  → 就像每天失忆一次

这就是为什么需要 SummarizationMiddleware + Skill Rescue + BeforeSummarization Hook。
```

## 心智模型

**SummarizationMiddleware = 笔记本的"每晚总结"。**

- 写了一天笔记（对话消息）→ 晚上花 10 分钟写总结（摘要）→ 明天只看总结
- 但有张特别重要的操作手册（skill 文件）→ 总结时单独抽出来贴在新一页
- 总结前，让同事把今天的重点先拍照存档（memory_flush_hook）

预测：
- 如果对话很短 → 不需要总结 → 实际：`_should_summarize` 返回 False，跳过（吻合）
- 如果 skill 文件太大 → 操作手册贴不下 → 实际：超过 5000 token 的 skill 不保护（吻合）
- 如果记忆系统关了 → 同事不在，不拍照 → 实际：`memory_flush_hook` 直接 return（吻合）

---

## 一段话总结（1 分钟版）

这个中间件每次 LLM 调用前检查对话长度，超阈值就压缩。核心流程：**分区 → 保护 skill → 保护日期提醒 → 触发钩子 → 生成摘要**。基类（LangChain 的 `SummarizationMiddleware`）提供 token 计数、分区、摘要生成等基础能力，DeerFlow 在基类上增加三层保护：Skill Rescue（保护 skill 文件内容）、动态上下文保护（保护日期提醒）、BeforeSummarization Hook（压缩前存记忆）。

---

## 分层归属图

```
┌─────────────────────────────────────────────────────────┐
│ LangChain SDK 提供（你不需要写）                          │
│   ① token 计数 + 阈值判断                                │
│      → .venv/.../langchain/agents/middleware/            │
│         summarization.py:268-387                         │
│   ② 切割点确定                                           │
│      → summarization.py:415-525                          │
│   ③ 按切割点分区                                         │
│      → summarization.py:527 (_partition_messages)        │
│   ④ 用 LLM 生成摘要                                     │
│      → summarization.py:588 (_create_summary)            │
│   ⑤ 构建摘要消息（无 name 标记）                          │
│      → summarization.py:511 (_build_new_messages)        │
├─────────────────────────────────────────────────────────┤
│ DeerFlow 自己增加（本文件）                               │
│   ⑥ Skill Rescue — 保护 skill 内容不被压掉               │
│      → summarization_middleware.py:331-399               │
│   ⑦ 动态上下文保护 — 保护日期/记忆提醒                    │
│      → summarization_middleware.py:307-329               │
│   ⑧ BeforeSummarization Hook — 压缩前存记忆              │
│      → summarization_middleware.py:530-560               │
│   ⑨ name="summary" 标记 — 前端不展示摘要                 │
│      → summarization_middleware.py:297-305               │
├─────────────────────────────────────────────────────────┤
│ agent.py 注册（把钩子和中间件串起来）                      │
│   ⑩ 创建中间件实例，注册 memory_flush_hook               │
│      → lead_agent/agent.py:98-170                        │
│   ⑪ memory_flush_hook 实现 — 把旧消息存到记忆队列         │
│      → memory/summarization_hook.py:16-35                │
└─────────────────────────────────────────────────────────┘
```

---

## 数据流图

```
场景：第 50 轮对话，100 条消息、50000 token，阈值 40000

  │
  ├─ [summarization_middleware.py:225-226]
  │   before_model 被调 → 委托给 _maybe_summarize
  │   → 为什么在这：中间件链在每次 LLM 调用前执行 before_model
  │
  ├─ [summarization_middleware.py:245-246]
  │   _ensure_message_ids(messages) → 给每条消息补 id
  │   → 为什么在这：LangGraph 用 id 做消息替换/追加判断，没 id 就不知道怎么处理
  │
  ├─ [summarization_middleware.py:248-250]
  │   token_counter(messages) → 50000 → _should_summarize → True
  │   → 为什么在这：大部分时候不触发（对话没那么长），这里是第一次过滤
  │
  ├─ [summarization_middleware.py:252-254]
  │   _determine_cutoff_index → cutoff = 88（保留最后 12 条）
  │   → 为什么在这：基类根据 token 预算算出切割点
  │
  ├─ [summarization_middleware.py:256]
  │   _partition_with_skill_rescue(messages, 88)
  │   → 为什么在这：基类分区后，DeerFlow 需要把 skill 消息从"要压缩"区拉回来
  │   │
  │   ├─ [summarization_middleware.py:359]
  │   │   _partition_messages(messages, 88) → to_summarize=[0-87], preserved=[88-99]
  │   │
  │   ├─ [summarization_middleware.py:365]
  │   │   _find_skill_bundles → 找到消息 [5,6] 是 research skill bundle
  │   │
  │   ├─ [summarization_middleware.py:373]
  │   │   _select_bundles_to_rescue → 4000 token，不超预算，入选
  │   │
  │   └─ [summarization_middleware.py:381-399]
  │       遍历消息，把 [5,6] 移到 preserved
  │       → 最终 to_summarize=[0-4,7-87], preserved=[5,6,88-99]
  │
  ├─ [summarization_middleware.py:257]
  │   _preserve_dynamic_context_reminders → 把日期提醒从 to_summarize 移到 preserved
  │   → 为什么在这：日期提醒被压掉会导致下一轮重复注入
  │
  ├─ [summarization_middleware.py:258]
  │   _fire_hooks → 创建 SummarizationEvent → 调 memory_flush_hook(event)
  │   → 为什么在这：消息即将被压缩，这是"抢救"到记忆系统的最后机会
  │   │
  │   └─ [summarization_hook.py:29-35]
  │       queue.add_nowait(thread_id, messages, ...) → 入队记忆队列
  │       → 用 add_nowait 而非 add：消息马上要被删了，不能等 30 秒
  │
  ├─ [summarization_middleware.py:259]
  │   _create_summary(to_summarize) → "用户想调研 LangGraph，已完成初步搜索..."
  │   → 为什么在这：基类把旧消息发给 LLM 生成摘要
  │
  ├─ [summarization_middleware.py:260]
  │   _build_new_messages(summary) → HumanMessage(name="summary")
  │   → 为什么在这：覆写基类方法，加 name="summary" 让前端不展示
  │
  └─ [summarization_middleware.py:262-268]
      返回 {"messages": [RemoveAll, 摘要, skill消息, 日期提醒, 最近消息]}
      → 最终结果：100 条 → ~15 条，约 8000 token
```

---

## 核心机制

### 1. 分区（Partition）

把消息列表按切割点切成两半：前半压缩、后半保留。

基类 `_partition_messages` 按索引切一刀。DeerFlow 在此基础上把特殊消息（skill、日期提醒）从"压缩区"拉回"保留区"。

为什么用它：一刀切太粗暴，有些消息不能压（skill 是工作指南，压掉了 Agent "失忆"）。

例子：100 条消息，cutoff=88 → to_summarize=[0-87], preserved=[88-99]。

### 2. Skill Bundle 追踪

把"AIMessage(调用 read_file) + ToolMessage(skill 内容)"绑成一组（`_SkillBundle`），方便整体移动。

扫描消息列表，找到 `tool_call.name` 是 read_file 且路径以 `/mnt/skills/` 开头的调用，然后往后找匹配 `tool_call_id` 的 ToolMessage。

为什么用它：skill 相关的消息必须成对移动（AIMessage + ToolMessage），单独移动会导致孤儿消息 → LLM 报 400。

例子：AIMessage 调了 `read_file("/mnt/skills/research/SKILL.md")` → ToolMessage 返回 skill 内容 → 这两条绑成一个 `_SkillBundle`。

### 3. AIMessage 拆分

一条 AIMessage 可能同时调了 skill 工具和非 skill 工具。拆分时克隆成两条：一条只保留 skill tool_calls（被救），一条保留非 skill tool_calls（继续压缩）。

用 `_clone_ai_message` 克隆 AIMessage 并替换 `tool_calls` 列表，同步三处关联数据（tool_calls、additional_kwargs、response_metadata）。

为什么用它：不能整条 AIMessage 都救回来（非 skill 调用的消息应该被压缩），也不能整条都压缩（skill 调用要保留）。

例子：AIMessage 的 `tool_calls=[read_file(skill), search(普通)]` → 拆成两条：`AIMessage(tool_calls=[read_file])` 被救，`AIMessage(tool_calls=[search])` 继续压缩。

### 4. 预算选择（Budget Selection）

从最新的 skill bundle 开始挑，三重预算限制：最多 5 个、总共 25000 token、单个 5000 token、同名去重。

为什么用它：不加限制的话，如果 Agent 加载了 20 个 skill，全救回来等于没压缩。

例子：coding skill 6000 token → 单件超 5000 → 跳过。research skill 出现两次 → 只保护最新一次。

### 5. Hook 机制

主流程在"压缩前"这个时机暂停，遍历调用注册的钩子函数。钩子失败用 try/except 兜底。

```python
# 本质就三步：
self._hooks = [memory_flush_hook]  # 注册
for hook in self._hooks:            # 触发
    try: hook(event)                # 防御
    except: pass
```

为什么用它：把"压缩前要做什么"的决定权交给外部。DeerFlow 当前只有一个钩子（存记忆），但框架允许注册多个。

例子：`memory_flush_hook` 把即将被压掉的 [0-87] 条消息入队到记忆系统，用 `add_nowait`（0s 延迟）因为消息马上要被删了。

### 6. RemoveAll + 重建

返回 `[RemoveMessage(id=REMOVE_ALL_MESSAGES), 摘要, preserved...]`。先删掉所有旧消息，再按新顺序加回来。

为什么用它：LangGraph 的 `add_messages` 机制用 id 做替换/追加。`REMOVE_ALL_MESSAGES` 是哨兵值，告诉 LangGraph "全删了重新来"。

例子：100 条旧消息 → RemoveAll 清空 → 加上摘要 + 15 条保留消息 = 最终 16 条。

---

## 案例追踪

```
初始状态：
  消息列表 = 100 条，50000 token
  中间件配置：阈值 40000 token，保留最近 12 条，skill 保护开，memory 开

步骤 1: before_model 被调 → _maybe_summarize
  → _ensure_message_ids：给每条消息补 id
  → token_counter：50000 token
  → _should_summarize：50000 > 40000 → True，需要压缩
  状态：确定要压缩
  ↑ 靠的机制：分区（还没执行，正在判断）

步骤 2: 确定切割点
  → _determine_cutoff_index：cutoff = 88
  → 基类根据 token 预算算出：保留最后 12 条，压缩前 88 条
  状态：cutoff = 88
  ↑ 靠的机制：分区

步骤 3: 基类分区 + Skill Rescue
  → _partition_messages：to_summarize=[0-87], preserved=[88-99]
  → _find_skill_bundles：找到消息 [5,6] 是 research skill bundle（4000 token）
  → _select_bundles_to_rescue：4000 < 5000（单件上限），不超总预算，入选
  → 遍历消息：消息 [5] AIMessage 拆分（只有 skill 调用，不拆），
              消息 [6] ToolMessage 移到 rescued
  状态：to_summarize=[0-4,7-87], preserved=[5,6,88-99]
  ↑ 靠的机制：Skill Bundle 追踪 + AIMessage 拆分 + 预算选择

步骤 4: 保护动态上下文提醒
  → 消息 [0] 是日期提醒 → 从 to_summarize 移到 preserved
  状态：to_summarize=[1-4,7-87], preserved=[0(日期),5,6,88-99]
  ↑ 靠的机制：分区

步骤 5: 触发 Hook
  → _fire_hooks：创建 SummarizationEvent，调 memory_flush_hook
  → memory_flush_hook：过滤消息 → 检测 correction/reinforcement → 入队
  → 用 add_nowait（不能等，消息马上要被删）
  ↳ 成功：消息入队，等待记忆系统处理
  ↳ 失败：try/except 兜底，只记日志，压缩继续
  状态：记忆队列有新任务
  ↑ 靠的机制：Hook 机制

步骤 6: 生成摘要 + 返回新消息列表
  → _create_summary([1-4,7-87]) → "用户想调研 LangGraph，已完成初步搜索..."
  → _build_new_messages → HumanMessage(name="summary")
  → 返回 {"messages": [RemoveAll, 摘要, 日期提醒, skill消息5-6, 最近消息88-99]}

最终状态：
  消息列表从 100 条(50000 token) → ~16 条(~8000 token)
  - 摘要保留了旧对话的核心信息
  - skill 内容完整保留
  - 日期提醒保留（不会重复注入）
  - 旧消息的关键信息已存入记忆系统
```

---

## 常见坑

```
坑：拆分 AIMessage 后 ToolMessage 找不到对应的 tool_call → 孤儿 ToolMessage → LLM 报 400
 → 设计：_clone_ai_message 同步三处关联数据（tool_calls、additional_kwargs、response_metadata）
 → 但你踩过的 bug 说明这个同步可能有遗漏

坑：skill 文件太大（>5000 token）→ 不保护 → Agent "失忆"
 → 设计：_select_bundles_to_rescue 的单件预算限制 preserve_recent_skill_tokens_per_skill=5000
 → 新建 thread 可规避（还没触发压缩）

坑：memory_flush_hook 用 add() 而非 add_nowait() → 消息已被删了但记忆还没处理
 → 设计：summarization_hook.py 用 add_nowait（0s 延迟），因为消息马上被删

坑：压缩后 AI "失忆" — 看不见之前创建的待办列表
 → 设计：这不是本中间件的问题，是 TodoMiddleware 的 before_model 检测到不一致后注入提醒
 → 根因：write_todos 的 AIMessage 被压缩了，但 state["todos"] 数据还在
```

---

## 设计决策

```
Q: 为什么基类用 _create_summary（调 LLM 生成摘要）而不是直接截断？
→ 直接截断会丢失上下文（AI 不知道前面聊了什么）
→ 摘要保留了关键信息（用户目标、已完成的工作、待办事项）
→ 代价：多一次 LLM 调用（花 token、花时间）

Q: 为什么从最新的 skill 开始保护（reversed）而不是从最早的？
→ 最新的 skill 更可能是当前正在使用的
→ 最早的 skill 可能已经不用了（比如调研完了不再需要 research skill）
→ 代价：如果最早加载的 skill 是核心 skill，可能被跳过

Q: 为什么 Hook 用 Protocol 而不是 ABC？
→ Protocol 是鸭子类型的静态检查版：函数签名对了就行，不用继承
→ hook 就是个普通函数（memory_flush_hook），定义类继承 ABC 太重了
→ 代价：Protocol 只在类型检查时生效，运行时不强制

Q: 为什么用 RemoveAll + 重建而不是逐条 Remove？
→ 逐条 Remove 要知道每条消息的 id，还要处理顺序问题
→ RemoveAll 一把清空，再按新顺序加回来，简单可靠
→ 代价：如果中间过程出错，所有消息都没了（但 LangGraph 有状态持久化兜底）

Q: 为什么 memory_flush_hook 用 add_nowait 而非 add？
→ add 有 30 秒延迟（等更多消息一起批量处理）
→ 但消息马上就要被 RemoveAll 删掉了，等 30 秒就来不及了
→ add_nowait 立即入队，0 秒延迟
```

---

## 对比表

| 特性 | 基类 SummarizationMiddleware | DeerFlow 扩展 |
|------|------------------------------|---------------|
| token 计数 | ✅ 基类提供 | 不改 |
| 阈值判断 | ✅ 基类提供 | 不改 |
| 切割点确定 | ✅ 基类提供 | 不改 |
| 分区策略 | 按索引一刀切 | 一刀切 + skill 保护 + 日期保护 |
| 摘要生成 | ✅ 基类调 LLM | 不改 |
| 摘要消息格式 | HumanMessage（无 name） | HumanMessage(name="summary") |
| 压缩前钩子 | ❌ 没有 | ✅ BeforeSummarization Hook |
| Skill 保护 | ❌ 没有 | ✅ Skill Rescue（预算限制） |
| 记忆存储 | ❌ 没有 | ✅ memory_flush_hook |
