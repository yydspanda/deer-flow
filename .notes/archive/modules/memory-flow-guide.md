# Memory 模块全景图

> 这篇文档只解决一个问题：**数据在 7 个文件之间怎么流转的？每个函数为什么这么设计？**

---

## 1. 总流程图

```mermaid
flowchart TD
    subgraph 入口["阶段一：两条入口"]
        direction TB
        A["💬 用户说话 + Agent 回复<br/>每轮对话结束"] --> B["MemoryMiddleware<br/>on_update_cycle_complete()"]
        B --> C["message_processing.py<br/>filter_messages_for_memory()"]
        C --> D["message_processing.py<br/>detect_correction() / detect_reinforcement()"]
        D --> E["queue.add()<br/>30 秒去抖动"]

        A2["🗜️ SummarizationMiddleware<br/>压缩对话，即将删除旧消息"] --> B2["summarization_hook.py<br/>memory_flush_hook()"]
        B2 --> C2["message_processing.py<br/>filter_messages_for_memory()"]
        C2 --> D2["message_processing.py<br/>detect_correction() / detect_reinforcement()"]
        D2 --> E2["queue.add_nowait()<br/>0 秒，立刻入队"]
    end

    subgraph 处理["阶段二：LLM 提取 + 合并（updater.py）"]
        direction TB
        E & E2 --> F["queue._process_queue()<br/>Timer 线程，30 秒到期后执行"]
        F --> G["Step 1: _prepare_update_prompt()"]
        G --> G1["storage.load() → 读当前 memory.json"]
        G --> G2["format_conversation_for_update() → 格式化对话"]
        G --> G3["拼成 prompt（含 correction_hint）"]
        G1 & G2 & G3 --> H["Step 2: model.invoke(prompt)<br/>sync HTTP，不走 async"]
        H --> I["LLM 返回 JSON<br/>{user, history, newFacts, factsToRemove}"]
        I --> J["Step 3: _finalize_update()"]
        J --> J1["_apply_updates() → 合并"]
        J --> J2["_strip_upload_mentions() → 清除上传描述"]
        J --> J3["storage.save() → 原子写入 memory.json"]
        J1 & J2 & J3 --> K["memory.json 更新完成 ✓"]
    end

    subgraph 使用["阶段三：一条出口"]
        direction TB
        K --> L["下次对话开始"]
        L --> M["MemoryMiddleware.attach()"]
        M --> N["storage.load() → 读 memory.json"]
        N --> O["format_memory_for_injection()<br/>按 confidence 排序，2000 token 预算"]
        O --> P["注入 system prompt 的 &lt;memory&gt; 标签"]
        P --> Q["Agent '记住' 用户了 🧠"]
    end

    style 入口 fill:#e8f5e9,stroke:#4caf50
    style 处理 fill:#fff3e0,stroke:#ff9800
    style 使用 fill:#e3f2fd,stroke:#2196f3
```

---

## 2. 两条入口的区别

```mermaid
flowchart LR
    subgraph 正常路径["入口 A：正常路径（不急）"]
        A1["MemoryMiddleware"] --> A2["queue.add()"]
        A2 --> A3["30 秒去抖动"]
        A3 --> A4["同 thread_id<br/>新替换旧"]
    end

    subgraph 抢救路径["入口 B：抢救路径（很急）"]
        B1["summarization_hook"] --> B2["queue.add_nowait()"]
        B2 --> B3["0 秒，立刻"]
        B3 --> B4["同 thread_id<br/>追加到已有列表"]
    end

    A4 & B4 --> C["queue._process_queue()"]
```

| | add() 正常路径 | add_nowait() 抢救路径 |
|---|---|---|
| **延迟** | 30 秒去抖动 | 0 秒，立刻入队 |
| **去重策略** | 同 thread_id **新替换旧** | 同 thread_id **追加到已有** |
| **谁用** | MemoryMiddleware | summarization_hook |
| **为什么** | 不急，等用户说完整 | 消息马上被删，等不了 |

---

## 3. user_id 为什么入队时捕获？

```mermaid
sequenceDiagram
    participant EL as Event Loop 线程
    participant CV as ContextVar<br/>(user_id)
    participant Q as Queue
    participant Timer as Timer 线程

    Note over EL,CV: t=0 用户发消息
    EL->>CV: 读取 user_id = "user_xxx" ✓
    EL->>Q: queue.add(user_id="user_xxx")<br/>★ 存在 ConversationContext 对象里
    Q->>Q: 存到 _pending_contexts
    Q->>Timer: 启动 30 秒倒计时

    Note over Timer: t=30 倒计时到期
    Timer->>CV: 读取 user_id = ??? ✗<br/>ContextVar 不跨线程传播！
    Timer->>Q: _process_queue()
    Q-->>Timer: context.user_id = "user_xxx"<br/>★ 从对象读，不依赖 ContextVar ✓
```

**一句话：** ContextVar 是线程局部变量，Timer 线程读不到 event loop 线程的值。所以入队时取出来存在对象里，带过去。

---

## 4. _apply_updates 合并逻辑

```mermaid
flowchart TD
    LLM["LLM 返回的 JSON"] --> U["user 三段<br/>workContext / personalContext / topOfMind"]
    LLM --> H["history 三段<br/>recentMonths / earlierContext / longTermBackground"]
    LLM --> FR["factsToRemove<br/>要删的 fact id 列表"]
    LLM --> FA["newFacts<br/>要加的新 fact 列表"]

    U --> UR{"shouldUpdate?"}
    UR -->|"True + summary 非空"| UY["覆盖旧 summary"]
    UR -->|"False"| UN["不动"]
    UY & UN --> DONE

    H --> HR{"shouldUpdate?"}
    HR -->|"True + summary 非空"| HY["覆盖旧 summary"]
    HR -->|"False"| HN["不动"]
    HY & HN --> DONE

    FR --> FRD["移除匹配 id 的 fact"]
    FRD --> DONE

    FA --> FAC{"confidence >= 阈值?"}
    FAC -->|"否"| FAS["丢弃"]
    FAC -->|"是"| FAD{"content 去重?"}
    FAD -->|"已存在"| FAS
    FAD -->|"不存在"| FAA["加入新 fact（新 id）"]
    FAA --> FAM{"超过 max_facts?"}
    FAM -->|"否"| DONE
    FAM -->|"是"| FAT["按 confidence 排序<br/>只保留 top N"]

    FAT --> DONE["合并完成 → storage.save()"]

    style UY fill:#c8e6c9
    style HY fill:#c8e6c9
    style FRD fill:#ffcdd2
    style FAA fill:#c8e6c9
    style FAT fill:#fff9c4
```

**为什么 user/history 用覆盖，facts 用增删？**

- user/history 是"状态总结"：用户技术栈变了 → 旧总结没意义 → 直接覆盖
- facts 是"独立知识点"：删一条"技术栈 LangGraph"，加一条"技术栈 CrewAI"，不影响"偏好中文"

---

## 5. token 预算分配

```mermaid
flowchart LR
    BUDGET["总预算<br/>2000 tokens"] --> UH["User Context + History<br/>约 800 tokens"]
    BUDGET --> REST["剩余约 1200 tokens"]
    REST --> F1["facts 按 confidence ↓ 排序"]

    F1 --> F2["fact[0] +50 ✓"]
    F2 --> F3["fact[1] +60 ✓"]
    F3 --> F4["..."]
    F4 --> F5["fact[29] +40 ✓<br/>累计 1190"]
    F5 --> F6{"fact[30] +150?"}
    F6 -->|"1190+150=1340 > 1200"| STOP["停止 ✗"]
    F6 -->|"否则"| F5

    style STOP fill:#ffcdd2
```

---

## 6. 为什么用 sync model.invoke() 不用 async？

```mermaid
flowchart LR
    subgraph 错误方案["❌ 用 async ainvoke()"]
        A1["Timer 线程"] --> A2["新建 event loop"]
        A2 --> A3["model.ainvoke()"]
        A3 --> A4["httpx.AsyncClient<br/>全局 @lru_cache"]
        A4 --> A5["绑定在主线程 event loop"]
        A5 --> A6["跨 loop 复用 → bug 💥"]
    end

    subgraph 正确方案["✓ 用 sync invoke()"]
        B1["Timer 线程"] --> B2["model.invoke()"]
        B2 --> B3["httpx 同步客户端"]
        B3 --> B4["自己的连接池"]
        B4 --> B5["互不干扰 ✓"]
    end

    style 错误方案 fill:#ffebee
    style 正确方案 fill:#e8f5e9
```

---

## 7. 7 个文件一句话总结

| 文件 | 一句话 | 比喻 |
|---|---|---|
| `__init__.py` | 模块入口，导出公共 API | 目录 + 全流程串讲 |
| `message_processing.py` | 过滤消息 + 检测纠正/肯定信号 | 净化器 + 信号灯 |
| `queue.py` | 去抖动队列，攒消息到合适时机处理 | 30 秒缓冲区 |
| `prompt.py` | prompt 模板 + 格式化函数（memory↔文本） | 翻译官 |
| `updater.py` | 调 LLM 提取 + 合并更新 | 大脑（核心三步走） |
| `storage.py` | 读写 memory.json + 缓存 + 原子写入 | 仓库 |
| `summarization_hook.py` | 压缩前抢救即将被删的消息 | 救援队 |

---

## 8. _process_queue 为什么用 while 不用 if？

```mermaid
sequenceDiagram
    participant Q as _pending_contexts 字典
    participant PQ as _process_queue()
    participant SH as summarization_hook

    Note over Q: 有 1 个 context
    PQ->>Q: popitem() → 取出第 1 个
    PQ->>PQ: 正在处理第 1 个...
    SH->>Q: add_nowait() 塞入第 2 个！
    Note over Q: 又有 1 个 context
    PQ->>PQ: 第 1 个处理完
    PQ->>Q: while 检查：还有 → popitem() → 取出第 2 个
    PQ->>PQ: 正在处理第 2 个...
    PQ->>Q: while 检查：没了 → 退出
```

如果用 `if`，第 2 个 context 就丢了。`while` 保证处理期间新塞进来的也会被处理。
