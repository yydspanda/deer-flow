# LangChain 中间件笔记

## 版本

langchain 1.2+ / langchain_core 1.3+ / langgraph 1.0+

## 两种钩子模型

### 管道模型（before / after）

钩子：`before_agent`、`before_model`、`after_model`、`after_agent`

执行顺序：
- before 正序：A → B → C → 模型
- after 逆序：模型 → C → B → A

形状像沙漏，进去正着走，出来倒着走。

签名：
```python
def before_model(self, state, runtime) -> dict | None:
    return {"messages": [...]}  # 只能返回 state 更新

def after_model(self, state, runtime) -> dict | None:
    return None
```

能力：看一眼、改 state（注入消息、改参数）
不能：阻止执行、重试、捕获异常

适用场景：日志、统计、注入、标题生成、记忆持久化、对话摘要

### 洋葱模型（wrap）

钩子：`wrap_model_call`、`wrap_tool_call`

组装方式：列表第一个是最外层，最后一个最内层
`middleware = [A, B, C]` → 组装成 `A(B(C(实际执行)))`

请求流向：A → B → C → 实际执行
响应流向：实际执行 → C → B → A

签名：
```python
def wrap_model_call(self, request, handler):
    result = handler(request)  # handler = 通往下一层的函数
    return result

def wrap_tool_call(self, request, handler):
    return handler(request)
```

handler 是什么：通往下一层的入口函数。调 handler = 放行到下一层。

能力：
- 不调 handler → 阻止执行
- 调多次 handler → 重试
- 修改 request 再传给 handler → 改参数
- 修改 handler 返回值 → 改结果
- try/except handler → 捕获异常做降级

适用场景：重试、缓存、安全拦截、工具错误降级、澄清请求拦截、审计

## 管道 vs 洋葱 对比

| 管道做不到 | 洋葱怎么做 |
|-----------|-----------|
| 缓存：before 返回后模型一定会执行 | wrap 不调 handler，模型不执行 |
| 拦截：管道没有工具执行的钩子时机 | wrap_tool_call 直接包裹工具，不调 handler 就拦住 |
| 重试：after_model 只跑一次，没东西可重复调 | wrap 里 `for i in range(3): handler(request)` |
| 降级：模型抛异常时 after 根本不会执行 | wrap 里 `try: handler() except: 备用方案` |
| 审计：管道只看到模型那步，工具执行是盲区 | 洋葱外层包裹一切，内层拦截还是放行都能记录 |

本质区别：管道只能返回 state dict（旁观者），洋葱持有 handler 函数（拦截者）。

## 管道钩子的返回值机制（重点）

管道钩子返回 `{"messages": [...]}` 后，LangGraph 的 **add_messages reducer** 会处理：

### 替换 vs 追加（由消息 id 决定）

```
返回的消息 id 和已有消息 id 相同 → reducer 替换原消息（原地更新）
返回的消息 id 是新的             → reducer 追加到列表末尾
```

### 怎么保持 id 相同？model_copy

```python
updated_msg = last_msg.model_copy(update={"tool_calls": truncated})
return {"messages": [updated_msg]}
```

`model_copy` 是 Pydantic v2 的方法：复制原对象的所有字段（包括 id），只覆盖 update 指定的字段。
所以新消息和原消息 id 相同 → reducer 识别为替换。

LangChain 还提供了封装好的工具函数：
```python
from deerflow.agents.middlewares.tool_call_metadata import clone_ai_message_with_tool_calls
updated_msg = clone_ai_message_with_tool_calls(last_msg, truncated_tool_calls)
```
这个函数不仅做 model_copy，还会同步 additional_kwargs["tool_calls"] 和 response_metadata。

### 怎么创建新消息追加？

```python
reminder = HumanMessage(name="todo_reminder", content="你的待办列表还在...")
return {"messages": [reminder]}
```

HumanMessage 没指定 id → 自动生成新 id → reducer 追加到末尾。

### 管道钩子的位置限制

管道钩子返回的消息只能被 reducer **追加到末尾**（或替换同 id 的消息）。
无法控制"插入到消息列表的中间某个位置"。

例如不能做到：在 [Human, AI(悬空), Human] 的 AI 后面插入一条 ToolMessage。

### DeerFlow 中间件的三种操作模式

| 模式 | 用到的钩子 | 怎么做到的 | 例子 |
|------|-----------|-----------|------|
| **替换**（原地改） | before/after_model | model_copy 保持 id → reducer 替换 | SubagentLimit 截断 task 调用 |
| **追加**（加到末尾） | before/after_model | 新消息新 id → reducer 追加 | TodoMiddleware 注入提醒 |
| **精确插入**（中间位置） | wrap_model_call | 直接操作完整消息列表，任意位置插入 | DanglingToolCall 在 AI 后插 ToolMessage |

选择依据：
- 要改原消息内容 → 替换（model_copy）
- 要加新信息、放哪都行 → 追加（新消息）
- 要控制插入位置 → wrap_model_call

### DeerFlow 全部中间件操作模式一览

**替换模式（model_copy 保持 id）**：

| 中间件 | 替换什么 |
|--------|----------|
| SubagentLimit | 替换 AIMessage，截断多余 task tool_calls |
| LoopDetection | 替换 AIMessage，剥离 tool_calls 或追加警告文本 |
| TokenUsage | 替换 AIMessage，往 additional_kwargs 加归因数据 |
| Summarization | 替换 AIMessage，拆分 tool_calls（skill rescue） |

**追加模式（新消息新 id）**：

| 中间件 | 追加什么 |
|--------|----------|
| Todo | 追加 HumanMessage(name="todo_reminder") |
| ViewImage | 追加 HumanMessage，含图片 base64 数据 |
| DynamicContext | 追加 reminder_msg + user_msg（两个消息） |

**精确插入模式（wrap_model_call）**：

| 中间件 | 插入什么 |
|--------|----------|
| DanglingToolCall | 在悬空 AIMessage 后插入 ToolMessage(status="error") |

## AgentMiddleware 6 个钩子

| 钩子 | 时机 | 模型 |
|------|------|------|
| before_agent | agent 循环开始前（只调一次） | 管道 |
| before_model | 每次调用 LLM 前 | 管道 |
| after_model | 每次调用 LLM 后 | 管道 |
| after_agent | agent 循环结束后（只调一次） | 管道 |
| wrap_model_call | 包裹模型调用 | 洋葱 |
| wrap_tool_call | 包裹工具调用 | 洋葱 |

## create_agent 编译成 LangGraph

`create_agent(model, tools, middleware=[...])` 内部把中间件链编译成 LangGraph StateGraph：

```
START
  → before_agent（正序）
  → [循环开始]
    → before_model（正序）
    → 模型节点（内含 wrap_model_call 洋葱）
    → after_model（逆序）
    → 有 tool_calls？
      → 是 → 工具节点（内含 wrap_tool_call 洋葱）→ 回到 before_model
      → 否 → 跳出循环
  → after_agent（逆序）
  → END
```

## AIMessage 中工具调用的两份存储

一条 AIMessage 发出工具调用时，数据存在两个地方：

```python
AIMessage(
    tool_calls=[                           # ① LangChain 标准格式
        {"id": "call_1", "name": "bash", "args": {"command": "ls"}}
    ],
    additional_kwargs={
        "tool_calls": [                    # ② Provider 原始格式（如 OpenAI）
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "bash", "arguments": '{"command":"ls"}'}
            }
        ]
    }
)
```

- **① 标准格式**：所有 Provider 统一转成的 LangChain 格式，args 是 dict
- **② 原始格式**：Provider 返回的原始数据，格式因 Provider 而异（OpenAI 是 function.arguments JSON 字符串）

两份内容是同一批调用，但**包装格式不同**。修改 tool_calls 时必须同步修改 additional_kwargs["tool_calls"]，
否则会出现"标准格式说只有 2 个调用，但原始格式还存着 4 个"的不一致。

`clone_ai_message_with_tool_calls` 就是做这个同步的，它还会处理：
- `response_metadata["finish_reason"]`：tool_calls 全清空时从 "tool_calls" 改成 "stop"
- `additional_kwargs["function_call"]`：tool_calls 全清空时删掉这个旧格式字段

## 记忆口诀

管道: before 正着走，after 倒着走（沙漏）
洋葱: 第一个最外层，最后一个最内层（洋葱皮）
管道 = 流水线工位，只能看和改
洋葱 = 拦截器，能阻止、重试、改参数、改结果
handler = 往里走的门，有门你就能选择开不开、开几次
替换靠 id 相同（model_copy），追加靠 id 不同（新消息）
管道只能追加/替换，精确插入用洋葱
