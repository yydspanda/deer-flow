# OpenAI vs Anthropic API 深度对比

> 给自己的参考文档：理解两个 Provider 的 API 差异，
> 以及在 LangChain/DeerFlow 中如何处理这些差异。
> 所有示例都是**原始 HTTP JSON**（非 SDK），方便看清本质。

---

## 一、总览：核心设计哲学差异

| 维度 | OpenAI | Anthropic |
|------|--------|-----------|
| **消息结构** | `messages[].content` 是 `str`（简单） | `content` 是 **content blocks 数组**（结构化） |
| **工具调用** | `tool_calls` 数组挂在 message 顶层 | `tool_use` 是 **content block 的一种类型** |
| **工具结果** | 独立的 `role: "tool"` 消息 | `tool_result` 是 **user message 的 content block** |
| **思考模式** | `reasoning` item（Responses API）或 `reasoning_content`（Chat Completions） | `thinking` content block（内嵌在响应中） |
| **图像输入** | `input_image` content block | `image` content block（base64 或 URL） |
| **系统提示** | `role: "system"` 消息 | 顶层 `system` 参数（不在 messages 里） |
| **停止原因** | `finish_reason` | `stop_reason` |
| **并行工具调用** | 默认支持，可 `parallel_tool_calls: false` 禁用 | 默认支持，可 `disable_parallel_tool_use: true` 禁用 |

---

## 二、基础文本对话

### OpenAI（Chat Completions API）

```json
// 请求
POST https://api.openai.com/v1/chat/completions
{
  "model": "gpt-4.1",
  "messages": [
    {"role": "system", "content": "你是一个有帮助的助手。"},
    {"role": "user", "content": "你好，介绍一下自己"}
  ]
}

// 响应
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": "你好！我是 GPT，一个 AI 助手。"  // ← str，永远是字符串
    },
    "finish_reason": "stop"
  }]
}
```

### Anthropic（Messages API）

```json
// 请求
POST https://api.anthropic.com/v1/messages
{
  "model": "claude-sonnet-4-20250514",
  "max_tokens": 1024,
  "system": "你是一个有帮助的助手。",           // ← 系统提示在顶层，不在 messages 里
  "messages": [
    {"role": "user", "content": "你好，介绍一下自己"}
  ]
}

// 响应
{
  "content": [                                   // ← 数组！不是字符串
    {"type": "text", "text": "你好！我是 Claude，一个 AI 助手。"}
  ],
  "role": "assistant",
  "stop_reason": "end_turn"                      // ← 不叫 finish_reason
}
```

### 差异总结

| | OpenAI | Anthropic |
|---|---|---|
| 系统提示 | `messages` 里加 `role: "system"` | 顶层 `system` 参数 |
| 响应 content | `str`（字符串） | `list[dict]`（content blocks） |
| 停止原因字段 | `finish_reason: "stop"` | `stop_reason: "end_turn"` |
| max_tokens | 可选 | **必须** |

---

## 三、工具调用（Tool Use / Function Calling）

这是差异最大的部分，也是中间件里需要针对性处理的核心。

### OpenAI

```json
// 请求
{
  "model": "gpt-4.1",
  "messages": [{"role": "user", "content": "北京天气怎么样？"}],
  "tools": [{
    "type": "function",
    "function": {                                // ← 嵌套在 function 字段里
      "name": "get_weather",
      "description": "获取城市天气",
      "parameters": {                            // ← 叫 parameters
        "type": "object",
        "properties": {
          "city": {"type": "string"}
        },
        "required": ["city"]
      }
    }
  }]
}

// 响应 — 工具调用
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": null,                           // ← 可能为 null
      "tool_calls": [                            // ← 顶层字段
        {
          "id": "call_abc123",                   // ← call_id
          "type": "function",
          "function": {
            "name": "get_weather",
            "arguments": "{\"city\":\"北京\"}"    // ← JSON 字符串！需要 json.loads
          }
        }
      ]
    },
    "finish_reason": "tool_calls"                // ← "tool_calls" 不是 "tool_use"
  }]
}

// 后续请求 — 返回工具结果
{
  "model": "gpt-4.1",
  "messages": [
    {"role": "user", "content": "北京天气怎么样？"},
    {"role": "assistant", "content": null, "tool_calls": [
      {"id": "call_abc123", "type": "function", "function": {"name": "get_weather", "arguments": "{\"city\":\"北京\"}"}}
    ]},
    {"role": "tool", "tool_call_id": "call_abc123", "content": "晴天，25°C"}  // ← 独立的 role: "tool" 消息
  ]
}
```

### Anthropic

```json
// 请求
{
  "model": "claude-sonnet-4-20250514",
  "max_tokens": 1024,
  "messages": [{"role": "user", "content": "北京天气怎么样？"}],
  "tools": [{
    "name": "get_weather",                       // ← 直接在顶层，没有 function 嵌套
    "description": "获取城市天气",
    "input_schema": {                            // ← 叫 input_schema，不叫 parameters
      "type": "object",
      "properties": {
        "city": {"type": "string"}
      },
      "required": ["city"]
    }
  }]
}

// 响应 — 工具调用
{
  "content": [                                   // ← 工具调用是 content block 的一种
    {"type": "text", "text": "让我查一下北京的天气。"},
    {"type": "tool_use",                         // ← type: "tool_use"
      "id": "toolu_abc123",                      // ← toolu_ 前缀
      "name": "get_weather",
      "input": {"city": "北京"}                   // ← 已经是 dict！不是 JSON 字符串
    }
  ],
  "stop_reason": "tool_use"                      // ← "tool_use" 不是 "tool_calls"
}

// 后续请求 — 返回工具结果
{
  "model": "claude-sonnet-4-20250514",
  "max_tokens": 1024,
  "messages": [
    {"role": "user", "content": "北京天气怎么样？"},
    {"role": "assistant", "content": [
      {"type": "text", "text": "让我查一下北京的天气。"},
      {"type": "tool_use", "id": "toolu_abc123", "name": "get_weather", "input": {"city": "北京"}}
    ]},
    {"role": "user", "content": [                // ← 注意！是 user 角色，不是 tool 角色
      {"type": "tool_result",                    // ← tool_result content block
        "tool_use_id": "toolu_abc123",            // ← 字段名不同
        "content": "晴天，25°C"
      }
    ]}
  ]
}
```

### 工具调用差异对比表

| 维度 | OpenAI | Anthropic |
|------|--------|-----------|
| **工具定义位置** | `tools[].function` | `tools[]` 顶层 |
| **参数定义字段名** | `parameters` | `input_schema` |
| **调用 ID 前缀** | `call_` | `toolu_` |
| **参数格式** | JSON **字符串** `"arguments"` | **dict** `"input"` |
| **调用在响应中的位置** | `message.tool_calls[]`（顶层字段） | `content[].type === "tool_use"`（content block） |
| **finish_reason** | `"tool_calls"` | `"tool_use"` |
| **结果消息的 role** | `"tool"`（独立角色） | `"user"`（是 user 消息的 content block） |
| **结果关联字段** | `tool_call_id` | `tool_use_id` |
| **结果格式** | `{"role": "tool", "content": "..."}` | `{"type": "tool_result", "content": "..."}` |

---

## 四、思考模式（Thinking / Reasoning）

### OpenAI（o 系列 / GPT-5 reasoning）

OpenAI 的推理模型使用 `reasoning` 效果，但具体的推理过程**不暴露给用户**。

```json
// Chat Completions API 响应
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": "27 * 453 = 12,231",
      "reasoning_content": null                   // ← o 系列可能有，但不保证
    },
    "finish_reason": "stop"
  }]
}

// Responses API 响应
{
  "output": [
    {
      "type": "reasoning",                        // ← reasoning item
      "id": "rs_abc123",
      "content": [],                              // ← 推理内容可能为空（加密/不暴露）
      "summary": []                               // ← 可选摘要
    },
    {
      "type": "message",
      "content": [{"type": "output_text", "text": "27 * 453 = 12,231"}]
    }
  ]
}
```

**关键**：OpenAI 的推理过程是**黑盒**，用户无法看到模型的思考步骤。
推理 token 的消耗体现在 `usage.completion_tokens_details.reasoning_tokens` 中。

### Anthropic（Extended Thinking）

Anthropic 的 Extended Thinking **暴露完整的思考过程**。

```json
// 请求
{
  "model": "claude-sonnet-4-20250514",
  "max_tokens": 16000,
  "thinking": {                                  // ← 顶层参数
    "type": "enabled",
    "budget_tokens": 10000                       // ← 最少 1024
  },
  "messages": [{"role": "user", "content": "27 * 453 = ?"}]
}

// 响应
{
  "content": [
    {
      "type": "thinking",                         // ← thinking content block
      "thinking": "让我一步步算...\n27 * 453\n= 27 * 400 + 27 * 50 + 27 * 3\n= 10800 + 1350 + 81\n= 12231",
      "signature": "EqQBCgIYAhIM..."              // ← 加密签名（验证完整性）
    },
    {
      "type": "text",
      "text": "27 * 453 = 12,231"
    }
  ],
  "stop_reason": "end_turn"
}
```

### 思考模式差异对比表

| 维度 | OpenAI | Anthropic |
|------|--------|-----------|
| **启用方式** | 选择推理模型（o1/o3/GPT-5）自动启用 | 请求中加 `thinking: {type: "enabled", budget_tokens: N}` |
| **推理过程可见性** | 黑盒（`reasoning.content = []`） | **完全可见**（`thinking` block 有完整文本） |
| **token 预算控制** | `reasoning_effort: "low"/"medium"/"high"` | `budget_tokens: 1024~N`（精确控制） |
| **签名/加密** | 无 | `signature` 字段（防篡改） |
| **安全审查** | 不暴露 | 可能有 `redacted_thinking` block |
| **tool_use 兼容** | 需要把 reasoning items 传回 | **必须把 thinking blocks 传回** |
| **Claude 4 特殊** | 无 | summarized thinking（返回摘要，非完整思考） |

---

## 五、图像输入（Vision）

### OpenAI

```json
// 方式一：URL
{
  "role": "user",
  "content": [
    {"type": "text", "text": "图片里有什么？"},
    {
      "type": "image_url",                        // ← 注意类型名
      "image_url": {
        "url": "https://example.com/photo.jpg",
        "detail": "high"                          // ← low/high/auto
      }
    }
  ]
}

// 方式二：Base64
{
  "type": "image_url",
  "image_url": {
    "url": "data:image/jpeg;base64,/9j/4AAQ...",
    "detail": "high"
  }
}
```

### Anthropic

```json
// 方式一：Base64
{
  "role": "user",
  "content": [
    {"type": "text", "text": "图片里有什么？"},
    {
      "type": "image",                            // ← 注意类型名：image，不是 image_url
      "source": {
        "type": "base64",
        "media_type": "image/jpeg",
        "data": "/9j/4AAQ..."
      }
    }
  ]
}

// 方式二：URL
{
  "type": "image",
  "source": {
    "type": "url",
    "url": "https://example.com/photo.jpg"
  }
}
```

### 图像差异对比表

| 维度 | OpenAI | Anthropic |
|------|--------|-----------|
| **content block 类型** | `image_url` | `image` |
| **Base64 格式** | `data:image/jpeg;base64,...`（data URL） | `{"type":"base64", "media_type":"image/jpeg", "data":"..."}`（结构化） |
| **URL 格式** | `{"url": "https://..."}` | `{"source": {"type": "url", "url": "https://..."}}` |
| **detail 控制** | `detail: "low"/"high"/"auto"` | 无（自动处理） |
| **支持的格式** | PNG, JPEG, WEBP, GIF | PNG, JPEG, WEBP, GIF |
| **大小限制** | 20MB/张 | 约 5MB/张（base64 编码后） |

---

## 六、流式响应（Streaming）

### OpenAI（Chat Completions Streaming）

```
data: {"choices":[{"delta":{"content":"你"},"finish_reason":null}]}
data: {"choices":[{"delta":{"content":"好"},"finish_reason":null}]}
data: {"choices":[{"delta":{"content":"！"},"finish_reason":null}]}
data: {"choices":[{"delta":{},"finish_reason":"stop"}]}
data: [DONE]
```

- 纯文本：`delta.content` 字符串片段
- 工具调用：`delta.tool_calls[].function.arguments` JSON 片段

### Anthropic（Messages Streaming）

```
event: message_start
data: {"type":"message_start","message":{"id":"msg_xxx","content":[],"role":"assistant",...}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"你"}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"好"}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"！"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":15}}

event: message_stop
data: {"type":"message_stop"}
```

### 流式差异对比表

| 维度 | OpenAI | Anthropic |
|------|--------|-----------|
| **协议** | 换行分隔 JSON（`data: {...}`） | SSE（`event: xxx\ndata: {...}`） |
| **结束标记** | `data: [DONE]` | `event: message_stop` |
| **文本 delta** | `delta.content` | `delta.text_delta.text` |
| **工具参数 delta** | `delta.tool_calls[].function.arguments` | `delta.input_json_delta.partial_json` |
| **思考 delta** | 不暴露 | `delta.thinking_delta.thinking` + `delta.signature_delta.signature` |
| **结构** | 扁平（choices[0].delta） | 分层（content_block_start → delta → stop） |

---

## 七、LangChain 如何统一这些差异

LangChain 通过 **消息类型系统** 和 **Provider ChatModel 适配器** 来屏蔽差异。

### 消息类型映射

| LangChain 类型 | OpenAI 映射 | Anthropic 映射 |
|---------------|-------------|----------------|
| `SystemMessage` | `{"role": "system", "content": "..."}` | 顶层 `system` 参数 |
| `HumanMessage` | `{"role": "user", "content": "..."}` | `{"role": "user", "content": "..."}` |
| `AIMessage` | `{"role": "assistant", "content": "...", "tool_calls": [...]}` | `{"role": "assistant", "content": [{type:"text"}, {type:"tool_use"}]}` |
| `ToolMessage` | `{"role": "tool", "tool_call_id": "...", "content": "..."}` | `{"role": "user", "content": [{type:"tool_result", tool_use_id:"..."}]}` |

### LangChain 对 content 的处理

```python
# OpenAI 返回 → LangChain 统一
AIMessage(
    content="你好",                    # str（和 OpenAI 原始格式一致）
    tool_calls=[{"id":"call_1","name":"bash","args":{"command":"ls"}}],
    additional_kwargs={}                # 空
)

# Anthropic 返回 → LangChain 统一
AIMessage(
    content=[                           # list！（Anthropic 原始格式）
        {"type":"thinking","thinking":"..."},
        {"type":"text","text":"你好"}
    ],
    tool_calls=[{"id":"toolu_1","name":"bash","args":{"command":"ls"}}],
    additional_kwargs={                  # ← Anthropic 原始 tool_calls 保留在这里
        "tool_calls": [
            {"type":"tool_use","id":"toolu_1","name":"bash","input":{"command":"ls"}}
        ]
    }
)
```

### DeerFlow 中间件为什么要处理差异

这就是你看到的 `DanglingToolCallMiddleware._message_tool_calls` 的来源：

```python
# 标准路径（LangChain 统一格式）
msg.tool_calls  → [{"id":"call_1","name":"bash","args":{}}]

# 降级路径（Anthropic 原始格式还保留在 additional_kwargs 里）
msg.additional_kwargs["tool_calls"]  → [{"type":"tool_use","id":"toolu_1","name":"bash","input":{}}]
```

LangChain 的 `ChatAnthropic` 适配器会尽量把 Anthropic 格式转成 LangChain 标准格式，
但**转换不完美**时（如 thinking 模式下），原始数据还会留在 `additional_kwargs` 里。

---

## 八、实操速查表

### 写中间件/工具时需要处理的差异

```python
# 1. 取工具调用 — 两种格式都要处理
def get_tool_calls(msg):
    # 标准格式（大部分情况）
    if msg.tool_calls:
        return msg.tool_calls
    # Anthropic 原始格式（降级）
    raw = msg.additional_kwargs.get("tool_calls", [])
    return parse_raw_tool_calls(raw)

# 2. 解析工具参数 — 两种类型都要处理
def parse_args(raw_args):
    if isinstance(raw_args, dict):
        return raw_args                          # Anthropic（input 是 dict）
    if isinstance(raw_args, str):
        return json.loads(raw_args)              # OpenAI（arguments 是 JSON string）
    return {}

# 3. 处理 content — 两种类型都要处理
def append_text(content, text):
    if isinstance(content, str):
        return content + text                    # OpenAI 风格
    if isinstance(content, list):
        return content + [{"type":"text","text":text}]  # Anthropic 风格
    if content is None:
        return text
    return str(content) + text

# 4. 构建工具结果 — 角色不同
# OpenAI: ToolMessage(role="tool", tool_call_id="call_123", content="result")
# Anthropic: 在 user message 里加 tool_result content block
# LangChain 统一用 ToolMessage，Provider 适配器负责转换
```

### 什么时候需要写 Provider 特定代码

| 场景 | 是否需要 | 原因 |
|------|---------|------|
| 普通 invoke | 不需要 | LangChain ChatModel 抽象了 |
| bind_tools | 不需要 | LangChain 统一 API |
| 读取 tool_calls | **需要** | OpenAI 字符串 vs Anthropic dict |
| 读取 content | **需要** | str vs list[dict] |
| 构建 ToolMessage | 不需要 | LangChain 统一类型 |
| thinking 模式 | **需要** | Anthropic 有 thinking block，OpenAI 黑盒 |
| 流式 delta | **需要** | 结构完全不同 |

---

## 九、参考链接

- [OpenAI Chat Completions API](https://platform.openai.com/docs/api-reference/chat)
- [OpenAI Responses API](https://platform.openai.com/docs/api-reference/responses)
- [OpenAI Function Calling Guide](https://platform.openai.com/docs/guides/function-calling)
- [OpenAI Vision Guide](https://platform.openai.com/docs/guides/images-vision)
- [Anthropic Messages API](https://docs.anthropic.com/en/api/messages)
- [Anthropic Streaming](https://docs.anthropic.com/en/api/messages-streaming)
- [Anthropic Extended Thinking](https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking)
- [LangChain Message Types](https://python.langchain.com/docs/concepts/messages/)
- [OpenAI Structured Outputs](https://platform.openai.com/docs/guides/structured-outputs)
- [OpenAI Reasoning Models](https://platform.openai.com/docs/guides/reasoning)
- [Anthropic Prompt Caching](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching)
- [Anthropic Tool Use Guide](https://docs.anthropic.com/en/docs/build-with-claude/tool-use)

---

## 十、结构化输出（Structured Output / JSON Mode）

### OpenAI — Structured Outputs

OpenAI 提供两级 JSON 控制：

**JSON Mode**（弱保证）：输出一定是合法 JSON，但不保证符合你的 schema。

```json
// 请求
{
  "model": "gpt-4.1",
  "response_format": {"type": "json_object"},     // ← 只保证输出是 JSON
  "messages": [
    {"role": "system", "content": "以 JSON 格式返回结果。"},  // ← 必须在对话中提到 "JSON"
    {"role": "user", "content": "提取人名和地点"}
  ]
}

// 响应 — 合法 JSON，但结构不可控
{
  "choices": [{
    "message": {
      "content": "{\"names\": [\"张三\"], \"places\": [\"北京\"]}"
    }
  }]
}
```

**Structured Outputs**（强保证）：输出严格匹配你提供的 JSON Schema。

```json
// Chat Completions API
{
  "model": "gpt-4.1",
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "extraction",
      "strict": true,                              // ← strict 模式，保证 100% 匹配
      "schema": {
        "type": "object",
        "properties": {
          "names": {"type": "array", "items": {"type": "string"}},
          "places": {"type": "array", "items": {"type": "string"}}
        },
        "required": ["names", "places"],
        "additionalProperties": false               // ← strict 模式必须设 false
      }
    }
  },
  "messages": [{"role": "user", "content": "提取人名和地点"}]
}

// Responses API
{
  "model": "gpt-4.1",
  "text": {
    "format": {
      "type": "json_schema",
      "name": "extraction",
      "strict": true,
      "schema": { ... }
    }
  },
  "input": [{"role": "user", "content": "提取人名和地点"}]
}
```

**strict 模式的约束**：
- 所有字段必须 `required`（可选字段用 `type: ["string", "null"]` 模拟）
- 每个 object 必须设 `additionalProperties: false`
- 根级别必须是 `type: "object"`，不能是 `anyOf`
- 不支持 `allOf`、`not`、`if/then/else`
- 最大 5000 个属性、10 层嵌套

### Anthropic — 通过 Tool Use 实现

Anthropic **没有** `response_format` 或 `json_schema` 参数。
要得到结构化 JSON 输出，要用 **tool_use**：

```json
// 请求 — 定义一个 "假工具"，实际用来约束输出格式
{
  "model": "claude-sonnet-4-20250514",
  "max_tokens": 1024,
  "tool_choice": {"type": "tool", "name": "extract"},   // ← 强制使用这个 "工具"
  "tools": [{
    "name": "extract",
    "description": "提取文本中的人名和地点",
    "input_schema": {
      "type": "object",
      "properties": {
        "names": {"type": "array", "items": {"type": "string"}},
        "places": {"type": "array", "items": {"type": "string"}}
      },
      "required": ["names", "places"]
    }
  }],
  "messages": [{"role": "user", "content": "张三去北京出差，李四在上海开会"}]
}

// 响应 — 模型 "调用" 这个假工具，实际就是结构化输出
{
  "content": [
    {"type": "tool_use", "id": "toolu_xxx", "name": "extract",
      "input": {"names": ["张三", "李四"], "places": ["北京", "上海"]}
    }
  ],
  "stop_reason": "tool_use"
}
```

### 结构化输出差异对比表

| 维度 | OpenAI | Anthropic |
|------|--------|-----------|
| **原生 JSON Mode** | `response_format: {"type": "json_object"}` | 无 |
| **强 Schema 保证** | `json_schema` + `strict: true` | 通过 tool_use 模拟 |
| **控制方式** | `response_format` 参数 | `tool_choice: {type: "tool"}` + 假工具 |
| **输出位置** | `message.content`（JSON 字符串） | `tool_use.input`（dict） |
| **Strict 模式** | 有（JSON Schema 约束） | 无（靠 LLM 遵守 schema） |
| **可靠性** | **100% 保证**（strict 模式） | 高但不保证（模型可能漏字段） |
| **Refusal 处理** | `{"type": "refusal", "refusal": "..."}` | 不调用工具，返回普通文本 |
| **LangChain 支持** | `with_structured_output()` | `with_structured_output()`（底层两种方式都封装了） |

---

## 十一、Prompt Caching（提示缓存）

### OpenAI — Automatic Caching

OpenAI 的缓存是**自动的**，用户无法控制：

- 缓存**完全自动**，不需要任何参数
- 缓存最小长度：1024 tokens
- 缓存命中：input tokens 价格降低 50%
- 缓存 TTL：5-10 分钟（官方未公开精确值）
- 体现在 `usage.prompt_tokens_details.cached_tokens` 中

```json
// 响应 usage
{
  "usage": {
    "prompt_tokens": 2095,
    "prompt_tokens_details": {"cached_tokens": 1500},    // ← 自动缓存了 1500 tokens
    "completion_tokens": 503
  }
}
```

### Anthropic — Explicit Cache Control

Anthropic 的缓存是**显式控制**的，用户在请求中标记缓存断点：

```json
// 请求 — 用 cache_control 标记缓存断点
{
  "model": "claude-sonnet-4-20250514",
  "max_tokens": 1024,
  "system": [
    {"type": "text", "text": "很长的系统提示...", "cache_control": {"type": "ephemeral", "ttl": "1h"}}
  ],
  "tools": [
    {"name": "bash", "description": "...", "input_schema": {...}, "cache_control": {"type": "ephemeral"}}
  ],
  "messages": [{"role": "user", "content": "你好"}]
}

// 响应 usage
{
  "usage": {
    "input_tokens": 100,
    "cache_creation_input_tokens": 2051,          // ← 新建缓存消耗的 tokens
    "cache_read_input_tokens": 0                    // ← 缓存命中 0（第一次）
  }
}

// 第二次请求（同样 system + tools，不同 user message）
{
  "usage": {
    "input_tokens": 50,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 2051                 // ← 命中缓存！只收 10% 价格
  }
}
```

### Prompt Caching 差异对比表

| 维度 | OpenAI | Anthropic |
|------|--------|-----------|
| **控制方式** | 完全自动 | 显式 `cache_control` 断点 |
| **缓存粒度** | 整个 prompt 前缀 | 按层级（tools → system → messages），最多 4 个断点 |
| **TTL 选项** | 固定（~5-10 分钟） | `5m` 或 `1h` |
| **最小长度** | 1024 tokens | 1024（Sonnet）/ 2048（Haiku） |
| **缓存命中价格** | 原价 50% | 原价 **10%** |
| **缓存写入价格** | 原价 | 原价 +25%（5m TTL）/ +100%（1h TTL） |
| **缓存层级** | 无（整个前缀一个缓存） | tools → system → messages（层级独立失效） |
| **thinking block** | 不适用 | 不能直接标 cache_control，但随其他内容自动缓存 |
| **LangChain 支持** | 自动（无需代码） | `bind_tools(cache_control=...)` 或手动构造 |

---

## 十二、Reasoning / Thinking 与 Tool Use 的交互

这是两个 Provider 在 thinking + tool use 交互上差异最大的地方。

### OpenAI — Reasoning Items

```
用户提问 → 模型推理（reasoning tokens，不可见）
         → 返回 reasoning item + function_call item
         → 你执行工具，返回 function_call_output
         → 必须把 reasoning item 一起传回去（不然模型丢失推理上下文）
         → 模型继续推理 → 返回最终回答
```

```json
// 第二轮请求 — 必须传回 reasoning item
{
  "input": [
    {"role": "user", "content": "北京天气怎么样？"},
    {"type": "reasoning", "id": "rs_abc", "content": [], "summary": [...]},    // ← 必须传回
    {"type": "function_call", "id": "fc_abc", "call_id": "call_abc", "name": "get_weather", "arguments": "{\"city\":\"北京\"}"},
    {"type": "function_call_output", "call_id": "call_abc", "output": "晴天，25°C"}
  ]
}
```

**特点**：
- Reasoning 内容是**加密的**（`encrypted_content` 字段），你传回但不读取
- 不传回 reasoning item 也能工作，但推理质量下降
- 无需担心 token 预算——reasoning tokens 不计入上下文窗口

### Anthropic — Thinking Blocks

```
用户提问 → thinking block（可见）+ tool_use block
         → 你执行工具，返回 tool_result
         → 必须把 thinking block 完整传回（包括 signature）
         → 模型继续思考（interleaved thinking）→ 返回最终回答
```

```json
// 第二轮请求 — 必须传回 thinking block
{
  "messages": [
    {"role": "user", "content": "北京天气怎么样？"},
    {"role": "assistant", "content": [
      {"type": "thinking", "thinking": "用户想知道天气...", "signature": "EqQBCg..."},  // ← 必须传回，不能改
      {"type": "tool_use", "id": "toolu_abc", "name": "get_weather", "input": {"city": "北京"}}
    ]},
    {"role": "user", "content": [
      {"type": "tool_result", "tool_use_id": "toolu_abc", "content": "晴天，25°C"}
    ]}
  ]
}
```

**特点**：
- Thinking 内容**完全可见**（但 Claude 4 返回的是摘要）
- `signature` 字段用于验证完整性，必须原样传回
- **Interleaved thinking**（Claude 4 专属）：工具结果返回后，模型会继续思考
- Thinking block 占 `max_tokens` 预算，需要预留足够空间
- 前一轮的 thinking block 在后续请求中**自动从上下文剥离**（不占上下文窗口）

### Thinking + Tool Use 差异对比表

| 维度 | OpenAI | Anthropic |
|------|--------|-----------|
| **传回方式** | reasoning item 在 `input[]` 中 | thinking block 在 `assistant.content[]` 中 |
| **内容可见性** | 加密（`encrypted_content`） | 可见（`thinking` 文本 + `signature`） |
| **不传回的后果** | 推理质量下降 | **API 报错**（严格验证） |
| **内容可修改** | 不可以（加密的） | **不可以**（signature 验证会失败） |
| **Interleaved thinking** | 无 | Claude 4 支持（beta header `interleaved-thinking-2025-05-14`） |
| **token 预算** | reasoning tokens 不占上下文 | thinking 占 `budget_tokens`（interleaved 模式下可超 max_tokens） |
| **安全审查** | 不适用 | `redacted_thinking` block（加密内容，需原样传回） |
| **上下文累积** | reasoning tokens 用后丢弃 | 上一轮 thinking 自动剥离，不累积 |

---

## 十三、错误处理与拒绝（Refusals）

### OpenAI

```json
// 模型拒绝回答（content_filter）
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": null,
      "refusal": "I'm sorry, I cannot assist with that request."    // ← 顶层字段
    },
    "finish_reason": "content_filter"
  }]
}

// Structured Outputs 中的拒绝
{
  "output": [{
    "type": "message",
    "content": [
      {"type": "refusal", "refusal": "I'm sorry, I cannot assist with that."}   // ← content block
    ]
  }]
}
```

### Anthropic

```json
// 模型拒绝回答
{
  "content": [
    {"type": "text", "text": "I apologize, but I'm not able to help with that."}   // ← 普通文本，没有特殊类型
  ],
  "stop_reason": "end_turn"                        // ← 正常结束，没有 "refusal" 类型
}

// 安全审查拒绝
{
  "stop_reason": "refusal"                          // ← 唯一有特殊 stop_reason 的情况
}
```

### 差异对比

| 维度 | OpenAI | Anthropic |
|------|--------|-----------|
| **拒绝字段** | `message.refusal`（Chat Completions）或 `content[].type: "refusal"`（Responses） | 无特殊字段，普通文本 |
| **stop_reason** | `"content_filter"` | `"refusal"` |
| **结构化输出中的拒绝** | `{"type": "refusal", "refusal": "..."}` | 不适用（tool_use 不产生拒绝） |
| **可编程检测** | 可以（字段明确） | 只能通过 `stop_reason == "refusal"` 或解析文本 |

---

## 十四、音频与多模态输出

### OpenAI

OpenAI 支持音频输出（通过 `modalities` 参数）：

```json
// 请求 — 同时返回文本和音频
{
  "model": "gpt-4o-audio-preview",
  "modalities": ["text", "audio"],
  "audio": {"voice": "alloy", "format": "wav"},
  "messages": [{"role": "user", "content": "讲个笑话"}]
}

// 响应 — content 里有 audio block
{
  "choices": [{
    "message": {
      "content": [
        {"type": "text", "text": "为什么程序员喜欢暗色模式？因为光吸引 bug！"},
        {"type": "audio", "data": "base64编码的音频数据...", "transcript": "为什么程序员喜欢暗色模式？..."}
      ]
    }
  }]
}
```

### Anthropic

Anthropic **不支持**音频输出，只支持文本 + 图像输入，不支持音频输入/输出。

| 维度 | OpenAI | Anthropic |
|------|--------|-----------|
| **音频输出** | 支持（`modalities: ["audio"]`） | 不支持 |
| **音频输入** | 支持（`input_audio` content block） | 不支持 |
| **图像输出** | 支持（`image_generation` 工具） | 不支持（只能生成文本） |
| **视频输入** | 不支持 | 不支持 |
| **多模态策略** | 全模态（文本+图像+音频，输入+输出） | 文本为主，图像只输入 |

---

## 十五、Token 计算 / Usage 统计

### OpenAI

```json
{
  "usage": {
    "prompt_tokens": 100,
    "prompt_tokens_details": {
      "cached_tokens": 50                             // ← 缓存命中的 tokens
    },
    "completion_tokens": 200,
    "completion_tokens_details": {
      "reasoning_tokens": 150,                        // ← 推理 tokens（不包含在 completion_tokens 里... 实际包含）
      "accepted_prediction_tokens": 0,
      "rejected_prediction_tokens": 0
    },
    "total_tokens": 300
  }
}
```

### Anthropic

```json
{
  "usage": {
    "input_tokens": 100,
    "cache_creation_input_tokens": 50,                 // ← 缓存写入
    "cache_read_input_tokens": 0,                      // ← 缓存命中
    "output_tokens": 200,
    "server_tool_use": {
      "web_search_requests": 0                         // ← 服务端工具调用次数
    }
  }
}
```

### Usage 差异对比

| 维度 | OpenAI | Anthropic |
|------|--------|-----------|
| **输入 tokens 字段** | `prompt_tokens` | `input_tokens` |
| **输出 tokens 字段** | `completion_tokens` | `output_tokens` |
| **推理 tokens** | `completion_tokens_details.reasoning_tokens` | 包含在 `output_tokens` 中（不可单独看） |
| **缓存命中** | `prompt_tokens_details.cached_tokens` | `cache_read_input_tokens` |
| **缓存写入** | 无（自动，不单独报告） | `cache_creation_input_tokens` |
| **服务端工具** | 无特殊字段 | `server_tool_use.web_search_requests` |

---

## 十六、DeerFlow 模式对应表（更新版）

DeerFlow 的四种模式对应不同的 Provider 能力组合：

| 模式 | Thinking | Tool Use | Parallel Tools | Provider 限制 |
|------|----------|----------|----------------|---------------|
| **Flash** (F,F,F) | 关 | 关 | 关 | 最快，最便宜 |
| **Thinking** (T,F,F) | 开 | 关 | 关 | OpenAI: 自动; Anthropic: `thinking: {type: "enabled"}` |
| **Pro** (T,T,F) | 开 | 开 | 关 | Anthropic: `disable_parallel_tool_use: true`; OpenAI: `parallel_tool_calls: false` |
| **Ultra** (T,T,T) | 开 | 开 | 开 | 完整能力，最贵 |

**关键差异**：
- OpenAI thinking + tool use 时，必须传回 reasoning items
- Anthropic thinking + tool use 时，必须传回 thinking blocks + signature
- Anthropic 的 `tool_choice` 在 thinking 模式下只支持 `auto` 和 `none`（不支持 `any` 和 `tool`）
- OpenAI 无此限制

---

## 十七、更新版：什么时候需要写 Provider 特定代码

| 场景 | 是否需要 | 原因 |
|------|---------|------|
| 普通 invoke | 不需要 | LangChain ChatModel 抽象了 |
| bind_tools | 不需要 | LangChain 统一 API |
| 读取 tool_calls | **需要** | OpenAI 字符串 vs Anthropic dict |
| 读取 content | **需要** | str vs list[dict] |
| 构建 ToolMessage | 不需要 | LangChain 统一类型 |
| thinking 模式 | **需要** | 传回机制完全不同（reasoning items vs thinking blocks） |
| 流式 delta | **需要** | 结构完全不同 |
| 结构化输出 | **不需要** | LangChain `with_structured_output()` 统一封装 |
| prompt caching | **需要** | 控制方式完全不同（自动 vs 显式 cache_control） |
| 拒绝检测 | **需要** | 字段不同（refusal vs stop_reason） |
| 音频 | **不需要** | Anthropic 不支持 |
| token 统计 | **需要** | 字段名完全不同 |
| 错误重试 | **需要** | 429/500 处理方式不同 |
