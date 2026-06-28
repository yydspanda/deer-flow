# TodoMiddleware — 让 AI 按计划干活，不许忘事也不许偷懒

TodoMiddleware 解决两个问题：AI 干着干着忘了待办列表（因为历史被压缩了），和 AI 偷懒没做完就交差。基类 LangChain 提供工具和 prompt，DeerFlow 加上"防忘"和"防偷懒"两个守护。

---

## 痛点：没有它会怎样

### 问题 A — AI 忘了待办列表

```
场景：用户说"调研 LangGraph 和 CrewAI 的架构对比"（Pro 模式）

第 5 轮：AI 创建待办列表
  [pending] 搜索 LangGraph
  [pending] 搜索 CrewAI
  [pending] 对比差异
  [pending] 写报告

第 6-14 轮：AI 正常工作，搜索、分析...

第 15 轮：摘要中间件把第 1-10 轮消息压缩了
  → 包含 write_todos 调用的那条 AIMessage 被删了
  → AI 的上下文窗口里看不见待办列表了
  → state["todos"] 还有数据，但 AI 不知道

第 16 轮：AI 开始重复搜索 LangGraph（忘了自己已经搜过了）
  → 用户看到重复工作，浪费时间
  → 4 个待办项可能漏做 2 个

这就是为什么需要 before_model 上下文丢失检测。
```

### 问题 B — AI 偷懒提前交差

```
场景：待办列表有 4 项，AI 完成了 3 项

待办状态：
  [completed] 搜索 LangGraph
  [completed] 搜索 CrewAI
  [completed] 对比差异
  [pending]   写报告          ← 还没做！

AI 输出："好的，调研完成，主要差异如下..."
  → 没有调用任何工具，想直接结束对话
  → 用户收到的报告不完整，没有经过"写报告"步骤
  → 可能遗漏格式化、总结、引用等

这就是为什么需要 after_model 过早退出预防。
```

---

## 心智模型

**write_todos = 白板，每次擦掉重写。**

预测：
- 如果模型同时调 2 次 write_todos → 白板上两个人同时擦写 → 冲突 → 实际：基类拦截报错（吻合）
- 如果历史被压缩，白板被搬走了 → AI 看不见白板但白板还在 → 实际：before_model 重新摆一块白板（吻合）
- 如果 AI 想走人但白板还有没打勾的 → 拉回来继续 → 实际：after_model + jump_to="model"（吻合）

---

## 一段话总结

LangChain SDK 内置了 `TodoListMiddleware`，它做三件事：注册 `write_todos` 工具（让模型能创建/更新待办列表，数据存在 `state["todos"]` 里）、注入 system prompt（教模型何时用这个工具）、检测并行调用（`write_todos` 每次调用替换整个列表，不能两个人同时擦白板）。DeerFlow 的 `TodoMiddleware` 继承基类，增加了两个守护：`before_model` 在模型调用前检测待办列表是否被摘要截断了（截断就注入一条提醒），`after_model` 在模型响应后检测是否还有未完成待办（有就强制跳回模型继续干活，最多催 2 次后放行防死循环）。

---

## 分层归属图

```
┌──────────────────────────────────────────────────────────────┐
│ LangChain SDK 提供（你不需要写，也不需要改）                    │
│                                                                │
│   ① write_todos 工具定义                                       │
│      → langchain/agents/middleware/todo.py:127-137            │
│      功能：返回 Command(update={"todos": [...]}) 更新 state     │
│                                                                │
│   ② 工具注册到 self.tools                                      │
│      → langchain/agents/middleware/todo.py:208-217            │
│      功能：StructuredTool 包装，模型自动获得调用能力             │
│                                                                │
│   ③ system prompt 注入                                         │
│      → langchain/agents/middleware/todo.py:219-244            │
│      功能：wrap_model_call 在 system message 末尾追加           │
│            "你有 write_todos 工具，复杂任务用它追踪进度..."       │
│                                                                │
│   ④ 并行调用检测                                                │
│      → langchain/agents/middleware/todo.py:273-323            │
│      功能：after_model 检查最后一次 AI 消息里                   │
│            有没有 >1 个 write_todos 调用，有的话返回错误         │
├──────────────────────────────────────────────────────────────┤
│ DeerFlow 增加（todo_middleware.py）                             │
│                                                                │
│   ⑤ 上下文丢失检测                                              │
│      → todo_middleware.py:151-199                              │
│      功能：before_model — 检测 write_todos 被摘要截断后         │
│            注入 HumanMessage(name="todo_reminder")             │
│                                                                │
│   ⑥ 过早退出预防                                                │
│      → todo_middleware.py:212-274                              │
│      功能：after_model — 检测未完成待办 + AI 想退出             │
│            注入 HumanMessage(name="todo_completion_reminder")  │
│            + jump_to="model" 强制继续                           │
│                                                                │
│   ⑦ 中间件注册                                                  │
│      → agents/lead_agent/agent.py:173-292                     │
│      功能：只在 Pro/Ultra 模式加载（plan_mode=True）            │
│            Flash/Thinking 模式不加这个中间件                    │
└──────────────────────────────────────────────────────────────┘

你不需要关心 ①②③④，LangChain SDK 全部搞定。
你只需要理解 ⑤⑥ 的守护逻辑，以及 ⑦ 的加载条件。
```

---

## 数据流图

场景：用户在 Pro 模式下发"调研 LangGraph 和 CrewAI 的架构对比"

```
用户发消息
  │
  ├─ [agent.py:292] _create_todo_list_middleware(is_plan_mode=True)
  │                  → 为什么在这：组装 Agent 时创建中间件，传入自定义 prompt
  │                  → is_plan_mode=False 时返回 None（Flash/Thinking 不加载）
  │
  ├─ [todo.py:219] wrap_model_call：在 system message 末尾追加 todo 使用指南
  │                  → 为什么在这：必须让模型知道有 write_todos 工具可用
  │
  ├─ [LLM 调用] 模型看到 system prompt，决定创建待办列表
  │              生成 AIMessage(tool_calls=[{name:"write_todos", args:{todos:[...]}}])
  │
  ├─ [todo.py:133] write_todos 工具执行
  │                  → 返回 Command(update={"todos": [...], "messages": [ToolMessage(...)]})
  │                  → 每次调用替换整个列表
  │
  ├─ [todo.py:274] after_model 基类检查：只有 1 个 write_todos → 放行
  │
  └─ Agent 循环：更新待办 → 执行工具 → 更新待办 → ... → 全部 completed → 输出最终答案
```

---

## 核心机制

这一节讲清楚"靠什么做到的"——理解了这 5 个机制，案例追踪里的每一步你都能举一反三。

### 机制 1：Command(update={...}) — 工具怎么直接改 state

LangGraph 的状态更新原语。工具不靠 return 值传递数据，而是返回 `Command(update={...})`，LangGraph 拦截后直接把 update 里的字段写入 state。

怎么工作：
```
write_todos([{status:"pending", content:"搜索"}])
  → 返回 Command(update={"todos": [...], "messages": [ToolMessage(...)]})
  → LangGraph 拦截这个返回值
  → state["todos"] = [...]（直接赋值，不是字符串）
  → state["messages"] 追加 ToolMessage
```

为什么用它：
→ 如果用普通 return 值，todos 会变成 ToolMessage 的文本（"Updated todo list to [...]"），是字符串不是结构化数据
→ Command 让 todos 保持 `list[dict]` 格式，后续中间件（before_model 读 state["todos"]）能直接用
→ 相当于 React 的 setState——直接改 state，不走 props 传递

### 机制 2：PlanningState + Todo — state 里存什么

PlanningState 是 LangChain 定义的状态 schema，在普通 AgentState 基础上多了一个 `todos` 字段：

```python
class Todo(TypedDict):
    content: str                                          # 待办内容
    status: Literal["pending", "in_progress", "completed"] # 三种状态

class PlanningState(AgentState):
    todos: Annotated[list[Todo], OmitFromInput]  # 每轮开始时不从输入复制，保留上一轮的值
```

关键：`OmitFromInput` 意味着 `todos` 不会被每轮的输入覆盖，而是持久保存在 state 里。只有 `write_todos` 工具的 `Command(update={"todos": [...]})` 才能更新它。

### 机制 3：jump_to="model" + @hook_config — 怎么强制 AI 回去重做

LangGraph 的节点跳转机制。中间件在 after_model 里返回 `{"jump_to": "model"}`，LangGraph 就不会进入下一个阶段（可能结束对话），而是跳回模型节点重新调用 LLM。

```python
@hook_config(can_jump_to=["model"])   # ← 必须声明，否则 jump_to 被忽略
def after_model(self, state, runtime):
    return {"jump_to": "model", "messages": [reminder]}
    #         ↑ 跳回模型节点                ↑ 顺带给模型看一条催促消息
```

为什么用它：
→ 相当于对 AI 说"回去重做"——不是结束对话，而是让模型重新思考
→ 模型看到 HumanMessage("你还有 1 个任务没做完！")，知道不能结束，继续干活
→ 必须加 `@hook_config(can_jump_to=["model"])` 声明，这是 LangGraph 的安全机制——不声明的中间件不能随意跳转

### 机制 4：add_messages reducer — 注入的消息怎么合并

中间件返回 `{"messages": [HumanMessage(...)]}` 时，LangGraph 用 `add_messages` reducer 把新消息合并到 state["messages"] 里。

合并规则：
- 新消息没有 id → 追加到末尾（最常见的情况）
- 新消息有 id 且和已有消息 id 相同 → 原地替换
- RemoveMessage(id="xxx") → 删除指定消息（摘要中间件用的就是这个）

为什么用 HumanMessage 不用 SystemMessage：
→ before_model / after_model 钩子只能返回 `{"messages": [...]}`，追加到消息列表
→ HumanMessage 会被所有 Provider 正确处理（SystemMessage 在某些 Provider 行为不一致）
→ 用 `name` 属性标记消息身份（如 `name="todo_reminder"`），不干扰普通用户消息

### 机制 5：HumanMessage(name="xxx") — 怎么做幂等检测和计数

注入的提醒消息都用 `name` 属性标记身份，后续靠这个标记做幂等检测（不重复注入）和计数（催了几次）。

```
todo_reminder           → name="todo_reminder"            → 用 _reminder_in_messages() 检测"是否已注入"
todo_completion_reminder → name="todo_completion_reminder" → 用 _completion_reminder_count() 计数"催了几次"
普通用户消息              → name=None                       → 不会被误识别
```

为什么用 name 不用其他方式：
→ HumanMessage.name 是 LangChain 内置属性，不需要自定义字段
→ 遍历 messages 检查 `getattr(msg, "name", None) == "xxx"` 就行，简单可靠
→ 如果用消息内容做幂等检测，内容变化就会失效（name 不会变）

---

## 案例追踪

### Happy Path：正常完成所有待办

```
初始状态：state = {messages: [HumanMessage("调研 LG 和 CA")], todos: []}

步骤 1: LLM 决定创建待办列表 → AIMessage(tool_calls=[write_todos])
        ↑ 靠的机制：wrap_model_call 注入的 system prompt 教会了模型用 write_todos

步骤 2: write_todos 工具执行 → Command(update={"todos": [4 个 pending 项]})
        ↑ 靠的机制：Command(update={...}) 直接写入 state["todos"]
        → state.todos = [{pending, "搜索LG"}, {pending, "搜索CA"}, ...]

步骤 3-7: LLM 逐个执行任务，每次调 write_todos 更新状态
        ↑ 靠的机制：Command 每次替换整个列表（completed 的也带上）
        → state.todos = [全部 completed]

步骤 8: LLM 输出 "调研完成，对比结果如下..."
        → after_model: 无 tool_calls（AI 想退出）
        → after_model: 检查 todos → 全部 completed → 允许退出 ✅
```

### Failure Path A：摘要截断导致失忆

```
第 5 轮：AI 创建待办 [搜索, 分析, 报告]
第 15 轮：摘要中间件把第 1-10 轮压缩 → write_todos 调用被删

步骤 15: before_model 被调用
        state.todos = [{completed, "搜索"}, {completed, "分析"}, {pending, "报告"}]
        messages 里找不到 write_todos（被摘要删了）
        没有已注入的 todo_reminder
        → 注入 HumanMessage(name="todo_reminder", "你的待办列表还在！...")
        ↑ 靠的机制：add_messages reducer 把提醒追加到 messages 末尾
        ↑ 靠的机制：name="todo_reminder" 让 _reminder_in_messages() 下次检测到，不重复注入
        → AI 重新看到待办列表，继续工作 ✅
```

### Failure Path B：AI 偷懒提前交差

```
待办状态：[completed 搜索, completed 分析, pending 报告]

步骤 N: LLM 输出 "分析完成，结论如下..."（无 tool_calls，想结束）
        → after_model: 不是全部 completed（"报告"还是 pending）
        → _completion_reminder_count = 0（第一次催）
        → 注入 HumanMessage(name="todo_completion_reminder", "你还有任务没做完！")
        → 返回 {"jump_to": "model", "messages": [reminder]}
        ↑ 靠的机制：jump_to="model" 强制跳回模型节点，AI 不能退出
        ↑ 靠的机制：name="todo_completion_reminder" 让 _completion_reminder_count() 计数 +1
        → AI 被迫继续，开始写报告 ✅
```

### Edge Case：催了 2 次仍然无法完成

```
待办状态：[completed 搜索, in_progress 报告]
AI 反复尝试写报告但一直失败

第 N 轮: 想退出 → 催第 1 次 → jump_to="model" → name="todo_completion_reminder" 计数=1
第 N+1 轮: 还是想退出 → 催第 2 次 → jump_to="model" → 计数=2
第 N+2 轮: 还是想退出
        → _completion_reminder_count = 2 >= _MAX_COMPLETION_REMINDERS
        ↑ 靠的机制：name="todo_completion_reminder" 累计计数，达上限放行
        → 放行，不再催了（至少不会死循环）
```

---

## 常见坑

```
坑：write_todos 被摘要截断 → AI "失忆"
  → 设计：before_model 检测 state 有 todos 但 messages 里找不到 write_todos → 注入提醒

坑：AI 没做完就想输出最终答案
  → 设计：after_model 检测"无 tool_calls + 有未完成 todos" → jump_to="model" 拉回

坑：AI 真的做不完，催了还在循环
  → 设计：_MAX_COMPLETION_REMINDERS=2，超过就放行

坑：模型并行调了 2 次 write_todos（两个人同时擦白板）
  → 设计：基类 after_model 检测 >1 个 write_todos 调用 → 返回错误 ToolMessage

坑：提醒消息本身也被截断了（极端长对话）
  → 设计：_reminder_in_messages 幂等检查 → 如果提醒也被删了，会重新注入（正确行为）
```

---

## 设计决策

**Q: 为什么 write_todos 每次调用替换整个列表，而不是增量更新（比如 add_todo / update_todo_status）？**
→ 增量更新需要 LLM 精确指定"改第几项的 status"——LLM 经常搞错索引或漏字段
→ 整体替换更简单可靠：LLM 只需要输出完整的新列表，不需要记住旧列表的结构
→ 代价：每次调用要传输完整列表（token 更多），且不能并行调用（因为后写的会覆盖先写的）

**Q: 为什么用 `Command(update={...})` 更新 state，而不是让工具返回值？**
→ LangGraph 的 `Command` 可以直接更新 `state["todos"]`，不走消息管道
→ 如果用返回值，todos 会变成 ToolMessage 的文本，不是结构化数据
→ `Command` 是 LangGraph 的官方模式，类似 React 的 setState

**Q: 为什么提醒消息用 `HumanMessage` 而不是 `SystemMessage`？**
→ 因为 `before_model` 钩子只能返回 `{"messages": [...]}` 来追加消息
→ 追加的消息会通过 `add_messages` reducer 合并到 state
→ HumanMessage 会被 LLM 当作用户输入处理（SystemMessage 在某些 Provider 不稳定）

**Q: 为什么催促上限是 2 次而不是 3 次或无限次？**
→ 2 次是经验值：给 AI 一次"重新尝试"的机会，但不会无限消耗 token
→ 如果 AI 做了 2 轮还是完成不了，说明任务本身有问题（比如缺少信息、工具不够）
→ 继续催只是浪费 token，不如让用户看到不完整的结果然后提供更多信息

---

## 对比表

| 特性 | LangChain 基类 TodoListMiddleware | DeerFlow TodoMiddleware |
|------|----------------------------------|------------------------|
| write_todos 工具注册 | ✅ 提供 | 继承，不自定义 |
| system prompt 注入 | ✅ 提供 | 继承，DeerFlow 自定义了 prompt 内容 |
| 并行调用检测 | ✅ 拦截 >1 个 write_todos | 继承，先执行基类逻辑 |
| 上下文丢失检测 | ❌ | ✅ before_model 注入提醒 |
| 过早退出预防 | ❌ | ✅ after_model + jump_to="model" |
| 催促上限（防死循环） | 不需要 | ✅ _MAX_COMPLETION_REMINDERS=2 |
| state_schema | PlanningState（含 todos 字段） | 继承 |
