# Tool / MCP / Skill —— 大模型的三种能力来源

> 大模型有三种"能力来源"：内置工具（Tool）、远程工具协议（MCP）、工作指南（Skill）。
> 它们的激活方式完全不同，但对大模型来说都是"我能做的事"。

---

## 一、本质区别

```
Tool（工具）   → Python 函数，有 JSON Schema，LLM 通过 function calling 触发
MCP（工具协议） → 远程服务器的工具，通过标准协议接入，对 LLM 来说和 Tool 没区别
Skill（技能）   → Markdown 文档（工作指南），注入 system prompt，LLM 用 read_file 加载
```

| 概念 | 本质 | 大模型怎么用 | 配置在哪 |
|------|------|-------------|---------|
| **Tool** | Python 函数 | function calling 直接调用 | `config.yaml` 的 `tools:` |
| **MCP** | 远程服务器的工具 | 和 Tool 一样 | `extensions_config.json` 的 `mcpServers:` |
| **Skill** | Markdown 文档 | read_file 读取，按指南操作 | `extensions_config.json` 的 `skills:` |

**Tool 和 MCP 是大模型的"手"（执行动作），Skill 是大模型的"教材"（教它怎么做）。**

---

## 二、Tool —— 从定义到被大模型调用

### 定义（以 `present_files` 为例）

`backend/packages/harness/deerflow/tools/builtins/present_file_tool.py:84`：

```python
@tool("present_files", parse_docstring=True)   # LangChain 的 @tool 装饰器
def present_file_tool(runtime, filepaths: list[str], tool_call_id):
    """Make files visible to the user..."""      # docstring = LLM 看到的工具说明
    ...
```

`@tool` 做了两件事：
1. 把函数变成 `BaseTool` 对象（有 `.name`、`.description`、`.args_schema`）
2. 自动从 docstring + 类型注解生成 **JSON Schema**（大模型看到的参数定义）

### 注册到 Agent

`tools.py:163`：

```python
all_tools = loaded_tools + builtin_tools + mcp_tools + acp_tools
# 去重后返回
return unique_tools
```

### 传给大模型

`agent.py:397-414`：

```python
return create_agent(
    model=create_chat_model(...),
    tools=get_available_tools(...) + extra_tools,    # ← 所有工具列表
    ...
)
```

LangGraph 的 `create_agent` 内部调用 `model.bind_tools(tools)`，把所有工具的 JSON Schema 塞进大模型的 API 请求（OpenAI 的 `tools` 参数）。

### 大模型怎么"调用"

```
用户: "帮我展示刚才生成的报告"
  ↓
LLM 看到工具列表中有 present_files，参数是 {filepaths: string[]}
  ↓
LLM 返回: tool_call={name: "present_files", arguments: {filepaths: ["/mnt/user-data/outputs/report.md"]}}
  ↓
LangGraph 拦截 tool_call，执行函数，把结果（ToolMessage）送回 LLM
  ↓
LLM 继续生成回复
```

**关键：大模型不执行代码，它只是"选择"调哪个函数、传什么参数。执行由 LangGraph 完成。**

---

## 三、MCP —— 远程工具协议

MCP 的本质：**别人写的服务器提供了一堆工具，DeerFlow 通过标准协议接入。**

### 配置（`extensions_config.json`）

```json
{
  "mcpServers": {
    "filesystem": {
      "enabled": true,
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem"]
    }
  }
}
```

### 加载链

```
extensions_config.json (enabled: true 的服务器)
  ↓
MultiServerMCPClient (langchain-mcp-adapters 提供)
  ↓ 通过 stdio/SSE/HTTP 连接 MCP 服务器
  ↓ 询问"你有哪些工具？"
  ↓
服务器返回工具列表（name + description + JSON Schema）
  ↓
转成 LangChain BaseTool 对象
  ↓
缓存到 _mcp_tools_cache（cache.py）
  ↓
get_available_tools() 把 MCP 工具加到工具列表
  ↓
model.bind_tools() → 大模型看到和内置工具一样的 JSON Schema
```

### 大模型视角

大模型**分不清**内置工具和 MCP 工具——它们都被 `bind_tools()` 转成了同样的 JSON Schema。

```
大模型看到的：
  tools: [
    {name: "bash", parameters: {...}},           ← 内置（config.yaml 配的）
    {name: "present_files", parameters: {...}},  ← 内置
    {name: "fs_read_file", parameters: {...}},   ← MCP（filesystem 服务器）
    {name: "web_search", parameters: {...}},     ← MCP（tavily 服务器）
  ]

大模型不知道也不关心哪个是内置的、哪个是 MCP 的
```

---

## 四、Skill —— 提示词注入，不是工具

Skill 不走 `bind_tools()`，而是注入到 system prompt。只注入**元数据**（name + description + location），完整内容需要 LLM 自己用 `read_file` 加载。

### 存储格式

`skills/public/deep-research/SKILL.md`：

```markdown
---
name: deep-research
description: Use this skill for ANY question requiring web research...
---

# Deep Research Skill
## Research Methodology
### Phase 1: Broad Exploration
...（198 行详细的工作指南）
```

### 注入到 system prompt

`prompt.py:589-603` 生成的 XML 块：

```xml
<skill_system>
You have access to skills...

**Progressive Loading Pattern:**
1. When a user query matches a skill's use case, call `read_file` on the skill's main file
2. Read and understand the skill's workflow
3. Follow the skill's instructions precisely

<available_skills>
    <skill>
        <name>deep-research</name>
        <description>Use this skill for ANY question requiring web research...</description>
        <location>/mnt/skills/public/deep-research/SKILL.md</location>
    </skill>
    <skill>
        <name>image-generation</name>
        <description>...</description>
        <location>/mnt/skills/public/image-generation/SKILL.md</location>
    </skill>
</available_skills>
</skill_system>
```

### 大模型怎么用 Skill

```
用户: "帮我深度研究一下 AI Agent 市场格局"
  ↓
LLM 看到 <skill name="deep-research" location="/mnt/skills/.../SKILL.md">
  ↓
LLM 判断匹配 → 调用 read_file 加载 198 行工作指南
  ↓
按指南执行：Phase 1 广泛搜索 → Phase 2 深入 → Phase 3 验证 → Phase 4 综合
  ↓
每一步调用 web_search / web_fetch 等工具（这些是 MCP 或社区工具）
```

**Skill 本质是"教大模型怎么工作的教材"，大模型用 `read_file` 自己读。**

---

## 五、完整激活链路

```
config.yaml                    extensions_config.json
  │                              │
  ├── tools:                     ├── mcpServers:
  │   - bash (use: deerflow...)  │     filesystem: {enabled: true}
  │   - ls                       │     tavily: {enabled: true}
  │   - read_file                │
  │                              │
  │  skills.path: skills/        │  skills:
  │                              │    deep-research: {enabled: true}
  │                              │    ppt-generation: {enabled: true}
  │                              │
  ▼                              ▼
┌─────────┐              ┌──────────────┐
│resolve_  │              │MultiServerMCP│
│variable()│              │Client        │
│反射加载   │              │连接远程服务器  │
└────┬─────┘              └──────┬───────┘
     │                           │
     ▼                           ▼
  BaseTool[]                  BaseTool[]
  (bash, ls, read_file...)   (fs_read, web_search...)
     │                           │
     └───────────┬───────────────┘
                 │
                 ▼
        get_available_tools()     ← tools.py:163 去重合并
                 │
                 ▼
        create_agent(tools=[...])  ← agent.py:397
                 │
                 ▼
        model.bind_tools(tools)    ← LangGraph 内部调用
                 │
                 ▼
        OpenAI API 请求中的         ← 大模型看到的 function schemas
        "tools": [{...}, {...}]

另外一条并行路径（Skill）：

  skills/public/*/SKILL.md
       │
       ▼
  SkillStorage.load_skills(enabled_only=True)
       │
       ▼
  get_skills_prompt_section()     ← prompt.py:606
       │
       ▼
  <skill_system> XML 块           ← 注入 system prompt（不走 bind_tools）
       │
       ▼
  LLM 看到 skill 列表 → 需要时用 read_file 加载完整内容
```

---

## 六、三者在 Agent 中的位置

```
┌─────────────────────────────────────────────────┐
│                 System Prompt                    │
│  <skill_system>                                  │  ← Skill：元数据在 prompt 里
│    <skill name="deep-research" location="..."/>  │     完整内容需 read_file 加载
│    <skill name="ppt-generation" .../>            │
│  </skill_system>                                 │
│  <memory>...</memory>                            │  ← Memory 也注入 prompt
│  <available-deferred-tools>...</...>             │  ← Deferred tool names
└─────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│           bind_tools() 注入的工具 Schema          │
│                                                  │
│  ┌── 内置工具（config.yaml tools: 配置）──────┐   │
│  │ bash, ls, read_file, write_file, str_replace│   │
│  │ present_files, ask_clarification            │   │
│  └──────────────────────────────────────────┘   │
│                                                  │
│  ┌── MCP 工具（extensions_config.json 配置）─┐   │
│  │ fs_read_file, web_search, web_fetch...     │   │
│  └──────────────────────────────────────────┘   │
│                                                  │
│  ┌── 可选工具（按条件激活）──────────────────┐   │
│  │ tool_search, skill_manage, task,           │   │
│  │ view_image, setup_agent, update_agent      │   │
│  └──────────────────────────────────────────┘   │
│                                                  │
│  LLM 看到统一的 JSON Schema，不区分来源          │
└─────────────────────────────────────────────────┘
```

---

## 七、关键配置门控

| 控制什么 | 门控条件 | 代码位置 |
|---------|---------|---------|
| Config 工具 | `config.yaml` `tools:` + `groups` 过滤 | `tools.py:59` |
| MCP 工具 | `extensions_config.json` `mcpServers.*.enabled` | `tools.py:119` |
| Deferred tool search | `config.yaml` `tool_search.enabled` | `tools.py:126` |
| Task/subagent 工具 | 运行时参数 `subagent_enabled` | `tools.py:91` |
| View image 工具 | 模型的 `supports_vision` 标志 | `tools.py:101` |
| Skill manage 工具 | `config.yaml` `skill_evolution.enabled` | `tools.py:85-88` |
| ACP agent 工具 | `config.yaml` `acp_agents:` 非空 | `tools.py:153` |
| Skills 在 prompt | `extensions_config.json` `skills.*.enabled` + agent 白名单 | `prompt.py:606-636` |
| 自定义 agent tool_groups | Agent 的 `config.yaml` `tool_groups:` | `agent.py:401` |
| 自定义 agent skills | Agent 的 `config.yaml` `skills:` | `agent.py:411` |
