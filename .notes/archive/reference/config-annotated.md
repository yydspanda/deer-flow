# config.yaml 全字段中文注解

> 基于 DeerFlow `config.example.yaml`（1091 行）逐节拆解。
> 目标：读完这篇，你改任何一个配置项都知道"会发生什么"。

---

## 前置知识：配置是怎么加载的？

在看每个字段之前，先搞懂整条加载链路：

```
config.yaml
  → AppConfig.from_file()                          # app_config.py:146
    → yaml.safe_load(f)                            # 把 YAML 文本变成 Python dict
    → resolve_env_variables(config_data)           # 把 $OPENAI_API_KEY 替换成真实值
    → model_validate(config_data)                  # Pydantic 校验 + 转成 AppConfig 对象
    → _apply_singleton_configs(result)             # 分发到各子模块的全局单例
  → get_app_config()                               # 全局入口，带缓存 + 热重载
    → 检查文件 mtime 是否变化 → 变了就重新 from_file()
```

**关键点：**

1. **环境变量解析时机**：`$OPENAI_API_KEY` 在 `from_file()` 里、Pydantic 校验之前就被替换了（`app_config.py:164`）。所以 Pydantic model 拿到的已经是真实字符串。
2. **热重载**：`get_app_config()` 每次调用都比较文件的 `st_mtime`，发现变化就自动重读。不需要重启服务。
3. **缓存 + 单例**：`_app_config` 是模块级全局变量，多线程共享。ContextVar 机制允许测试场景覆盖。

---

## 前置知识：`use` 字段是什么？怎么从字符串变成 Python 对象？

config.yaml 里到处都是 `use: xxx.yyy:Zzz` 这样的字符串。它的魔法在 `reflection/resolvers.py` 的 `resolve_variable()` 函数：

```python
# 格式："模块路径:变量名" 
resolve_variable("langchain_openai:ChatOpenAI")
# 等价于：
#   module = importlib.import_module("langchain_openai")  # 导入模块
#   return getattr(module, "ChatOpenAI")                  # 取出类/变量
```

**分解步骤：**

1. 用 `:` 分割 → 左边 `"langchain_openai"` 是模块名，右边 `"ChatOpenAI"` 是类名
2. `importlib.import_module("langchain_openai")` → 动态导入
3. `getattr(module, "ChatOpenAI")` → 拿到类对象本身
4. 如果指定了 `expected_type`，还会做 `isinstance()` 校验

**models 的 `use` vs tools 的 `use`：**

| 场景 | use 值 | 解析方式 | 期望类型 |
|------|--------|----------|----------|
| 模型 | `langchain_openai:ChatOpenAI` | `resolve_class(use, BaseChatModel)` | LangChain 的 Chat 类 |
| 工具 | `deerflow.sandbox.tools:bash_tool` | `resolve_variable(use, BaseTool)` | LangChain 的 BaseTool 实例 |
| 沙箱 | `deerflow.sandbox.local:LocalSandboxProvider` | `resolve_class(use, SandboxProvider)` | SandboxProvider 子类 |

本质都是同一个反射机制，只是期望的类型不同。`resolve_class` 是 `resolve_variable` 的包装，额外检查"是不是一个 class"。

---

## 1. config_version

### 大白话讲清楚

这是配置文件的版本号。DeerFlow 团队每次改了 config.yaml 的 schema（加了新字段、改了默认值），就会把 `config.example.yaml` 里的这个数字 +1。

**改了会怎样？** 你自己改这个数字没意义。它是用来检测你的本地 `config.yaml` 是不是过时了。如果你的版本号 < example 的版本号，启动时会打印警告：`Your config.yaml (version X) is outdated`。

### YAML 精简

```yaml
config_version: 9
```

### 跟代码的关系

- `AppConfig._check_config_version()` 在 `app_config.py:228` 读取你的 `config.yaml` 的 `config_version`，然后找同目录的 `config.example.yaml` 比较版本号
- 只打警告，不阻止启动
- 可以跑 `make config-upgrade` 自动合并新字段

---

## 2. log_level

### 大白话讲清楚

控制 DeerFlow 自己代码的日志级别。`info` 是默认值，日常够用。调试问题可以改成 `debug`。

**注意：** 只影响 `deerflow` 和 `app` 这两个 logger，不影响第三方库（uvicorn、sqlalchemy 等不会被你改乱）。

**改了会怎样？** 改成 `debug` 后你会看到大量工具加载、配置重载、中间件执行细节的日志。改成 `warning` 就安静很多。

### YAML 精简

```yaml
log_level: info
```

### 跟代码的关系

- `apply_logging_level()` 在 `app_config.py:69` 读取这个值，设置 `logging.getLogger("deerflow")` 和 `logging.getLogger("app")` 的级别
- 支持 `debug` / `info` / `warning` / `error` 四个级别

---

## 3. token_usage

### 大白话讲清楚

开关：要不要追踪每次 LLM 调用的 token 用量（输入 token、输出 token、总 token）。

**改了会怎样？** 开启后，每次 LLM 调用都会记录 token 数据，前端 workspace UI 可以看到用量统计。关闭后就不记录了，省一点开销。

### YAML 精简

```yaml
token_usage:
  enabled: true
```

### 跟代码的关系

- `TokenUsageConfig` 在 `config/token_usage_config.py` 定义，就一个 `enabled: bool` 字段
- `TokenUsageMiddleware` 在 `agents/middlewares/` 里读取这个配置，决定是否记录每次调用的 token metadata

---

## 4. models（★ 重点）

### 大白话讲清楚

这是你最需要理解的配置——它定义了 DeerFlow 可以用哪些 LLM 模型。每个模型条目告诉系统：

- 用什么 provider 类（`use` 字段）来连接这个模型
- 模型叫什么名字（`model` 字段）
- 用什么 API key
- 这个模型有没有特殊能力（thinking、vision）

**改了会怎样？** 你可以配置多个模型，DeerFlow 默认用列表里的第一个。subagent 可以通过 `model: "qwen3:32b"` 指定用不同的模型。

### YAML 精简

```yaml
models:
  - name: gpt-4
    display_name: GPT-4
    use: langchain_openai:ChatOpenAI
    model: gpt-4
    api_key: $OPENAI_API_KEY
    request_timeout: 600.0
    max_retries: 2
    supports_vision: true

  - name: deepseek-v3
    display_name: DeepSeek V3 (Thinking)
    use: deerflow.models.patched_deepseek:PatchedChatDeepSeek
    model: deepseek-reasoner
    api_key: $DEEPSEEK_API_KEY
    supports_thinking: true
    supports_vision: false
    when_thinking_enabled:
      extra_body:
        thinking:
          type: enabled
    when_thinking_disabled:
      extra_body:
        thinking:
          type: disabled
```

### 关键字段解释

**`use` 字段**：指定 LangChain 的 Chat 类。不是随便写的：

- `langchain_openai:ChatOpenAI` → OpenAI 和所有 OpenAI 兼容 API（Novita、MiniMax、OpenRouter 等）
- `langchain_anthropic:ChatAnthropic` → Anthropic Claude
- `langchain_ollama:ChatOllama` → Ollama 本地模型（原生 API，保留 thinking content）
- `langchain_google_genai:ChatGoogleGenerativeAI` → Google Gemini（原生 SDK）
- `deerflow.models.patched_deepseek:PatchedChatDeepSeek` → DeerFlow 自己的 DeepSeek 补丁
- `deerflow.models.patched_openai:PatchedChatOpenAI` → DeerFlow 自己的 OpenAI 补丁（用于 Gemini thinking）
- `deerflow.models.vllm_provider:VllmChatModel` → vLLM 自部署模型

**`supports_thinking`**：标记这个模型是否支持"思考模式"（即 extended thinking / deep thinking）。不是所有模型都支持。如果设为 `true`，DeerFlow 会根据用户选择在 `when_thinking_enabled` 和 `when_thinking_disabled` 之间切换。

**`supports_vision`**：标记模型能不能看图。设为 `true` 后，DeerFlow 会自动加载 `view_image_tool`，让 agent 可以分析图片。

**`when_thinking_enabled` / `when_thinking_disabled`**：这是两套额外的构造参数，在 thinking 模式开启/关闭时分别注入给 provider 类。比如 DeepSeek 模型在 thinking 开启时需要发 `extra_body.thinking.type: "enabled"`，关闭时发 `"disabled"`。

**`extra="allow"`**：`ModelConfig` 用了 Pydantic 的 `ConfigDict(extra="allow")`，意思是 YAML 里除了 `name`、`use`、`model` 这些已知字段之外的**所有额外字段**都会被保留下来，原封不动传给 provider 类的构造函数。所以你可以直接写 `temperature: 0.7`、`max_tokens: 4096`，它们会变成 `ChatOpenAI(temperature=0.7, max_tokens=4096)` 的参数。

### 跟代码的关系

- `ModelConfig` 定义在 `config/model_config.py`，Pydantic model
- `create_chat_model()` 在 `models/factory.py:55` 是核心工厂函数：
  1. 根据 `name` 找到 `ModelConfig`
  2. `resolve_class(model_config.use, BaseChatModel)` 把 `use` 字符串解析成 Python 类
  3. `model_config.model_dump(exclude_none=True, exclude={...})` 把除了已知字段之外的所有参数收集起来
  4. 根据 `thinking_enabled` 决定要不要注入 `when_thinking_enabled` / `when_thinking_disabled` 的参数
  5. `model_class(**model_settings)` 创建实例

---

## 5. tool_groups

### 大白话讲清楚

工具的逻辑分组。就像给工具贴标签，方便后续按组控制访问。

**改了会怎样？** 如果自定义 agent 的 `config.yaml` 指定了 `tool_groups: ["web"]`，那这个 agent 只能用 `group: web` 的工具。不指定就能用全部。

### YAML 精简

```yaml
tool_groups:
  - name: web
  - name: file:read
  - name: file:write
  - name: bash
```

### 跟代码的关系

- `ToolGroupConfig` 在 `config/tool_config.py` 定义
- `get_available_tools(groups=["web"])` 在 `tools/tools.py:171` 按组过滤工具
- 自定义 agent 的 `config.yaml` 可以设 `tool_groups` 限制可用工具范围

---

## 6. tools（★ 重点）

### 大白话讲清楚

这是你给 agent 装的"工具箱"。每个工具告诉 agent "你能做什么操作"——搜网页、读文件、写文件、跑命令，都在这里配。

**改了会怎样？** 你注释掉某个工具，agent 就不能做那个操作了。比如注释掉 `bash`，agent 就不能执行命令行。新增一个工具（比如换个搜索引擎），agent 就能用新的。

### YAML 精简

```yaml
tools:
  - name: web_search
    group: web
    use: deerflow.community.ddg_search.tools:web_search_tool
    max_results: 5

  - name: read_file
    group: file:read
    use: deerflow.sandbox.tools:read_file_tool

  - name: bash
    group: bash
    use: deerflow.sandbox.tools:bash_tool
```

### 关键字段解释

**`use` 字段格式 `"package.module:class_or_variable"`**：

- `deerflow.sandbox.tools:bash_tool` → 导入 `deerflow.sandbox.tools` 模块，取出 `bash_tool` 变量
- 这个变量必须是一个 LangChain `BaseTool` 的实例（`resolve_variable(cfg.use, BaseTool)` 做类型检查）

**`group` 字段**：把工具归到之前定义的 `tool_groups` 里。用来：

1. 自定义 agent 限制可用工具范围
2. 安全过滤（`group: bash` 的工具在 LocalSandbox 下会被过滤掉）

**`extra="allow"`**：`ToolConfig` 也用了 `extra="allow"`，所以 `max_results: 5`、`timeout: 10` 这些工具特有的参数会被原封不动传给工具实例。

### 跟代码的关系

- `ToolConfig` 在 `config/tool_config.py` 定义
- `get_available_tools()` 在 `tools/tools.py:111` 是核心函数，执行步骤：
  1. 读取 `config.tools` 列表
  2. 按 `groups` 参数过滤
  3. 安全过滤：`LocalSandbox` + `allow_host_bash=false` → 移除 bash 工具
  4. `resolve_variable(cfg.use, BaseTool)` → 字符串变 BaseTool 对象
  5. 名称冲突检测
  6. 给 async-only 工具补 sync wrapper
  7. 合并内置工具 + MCP 工具 + ACP 工具
  8. 按名称去重（优先级：配置 > 内置 > MCP > ACP）

---

## 7. tool_search

### 大白话讲清楚

工具延迟加载开关。当你接了很多 MCP 服务器（可能有 100+ 工具），全部塞给 LLM 会吃掉大量 token。

开启后，MCP 工具不直接出现在 LLM 的 function schema 里，而是：

1. 工具名列表写在 system prompt 里（极省 token）
2. LLM 需要某个工具时，调 `tool_search` 工具搜索获取完整 schema
3. 搜索到后才真正加载到当前对话

**改了会怎样？** 默认关闭。开启后，100 个 MCP 工具不再一次性吃掉上下文，而是按需加载。缺点是多一次搜索调用。

### YAML 精简

```yaml
tool_search:
  enabled: false
```

### 跟代码的关系

- `ToolSearchConfig` 在 `config/tool_search_config.py`
- `get_available_tools()` 在 `tools/tools.py:258` 检查 `config.tool_search.enabled`，如果为 `true`：
  - 创建 `DeferredToolRegistry`，注册所有 MCP 工具
  - 把 `tool_search_tool` 加入 builtin_tools
  - MCP 工具本身不加入最终列表

---

## 8. loop_detection

### 大白话讲清楚

防死循环机制。agent 如果反复调用同样的工具（参数也一样），这大概率是卡住了。

**两层检测：**

1. **精确匹配**：最近 20 次调用里，同一组工具调用重复了 3 次 → 警告，5 次 → 强制停止
2. **频率统计**：不管参数，同一个工具累计调了 30 次 → 警告，50 次 → 强制停止

**改了会怎样？** 如果你的工作流确实需要高频调用 bash（比如批处理 100 个文件），可以在 `tool_freq_overrides` 里给 bash 单独放宽阈值。

### YAML 精简

```yaml
loop_detection:
  enabled: true
  warn_threshold: 3
  hard_limit: 5
  window_size: 20
  max_tracked_threads: 100
  tool_freq_warn: 30
  tool_freq_hard_limit: 50
  # tool_freq_overrides:
  #   bash:
  #     warn: 150
  #     hard_limit: 300
```

### 跟代码的关系

- `LoopDetectionConfig` 在 `config/loop_detection_config.py`
- `LoopDetectionMiddleware` 在 `agents/middlewares/` 里：
  - 维护每个线程的最近 N 次工具调用历史
  - 精确匹配组：调用序列完全相同才算重复
  - 频率统计组：按工具名累计次数
  - 警告时注入提示到 LLM 上下文，强制停止时抛异常

---

## 9. sandbox（★ 重点）

### 大白话讲清楚

这是 agent 执行命令的地方。你有两种选择：

1. **LocalSandbox**（默认）：直接在你本机执行。agent 读写的是你磁盘上的真实文件，跑的是你机器上的真实命令。**不是真正的沙箱！**
2. **AioSandbox**（Docker）：在 Docker 容器里执行。agent 读写的是容器里的文件，跑的是容器里的命令。**真正的隔离。**

### YAML 精简

```yaml
# 本地模式（默认）
sandbox:
  use: deerflow.sandbox.local:LocalSandboxProvider
  allow_host_bash: false
  bash_output_max_chars: 20000
  read_file_output_max_chars: 50000
  ls_output_max_chars: 20000

# Docker 模式
# sandbox:
#   use: deerflow.community.aio_sandbox:AioSandboxProvider
#   replicas: 3
#   mounts:
#     - host_path: /home/user/my-project
#       container_path: /mnt/my-project
#       read_only: true
#   environment:
#     API_KEY: $MY_API_KEY
```

### 关键概念

**`allow_host_bash` 的安全含义**：

- `LocalSandbox` 不是真正的沙箱，bash 就是在你宿主机上跑
- 默认 `allow_host_bash: false` → 即使配了 bash 工具，agent 也用不了（被安全过滤掉了）
- 设为 `true` → 允许 agent 在你机器上执行任意命令。**只在完全信任的环境才开！**
- `AioSandbox` 不受此限制——它天然在容器里跑，默认允许 bash

**`mounts` 机制**：

- 只在 Docker 模式下有意义
- 把宿主机目录挂载到容器里，让 agent 可以访问你的项目文件
- `read_only: true` 只读挂载，agent 不能改你的文件

**`bash_output_max_chars`** 等截断设置：

- 防止命令输出太长吃掉 LLM 上下文
- bash 用中间截断（保留头+尾），因为错误信息可能在任何位置
- read_file / ls 用头部截断，因为内容是前置的
- 设为 0 关闭截断

### 跟代码的关系

- `SandboxConfig` 在 `config/sandbox_config.py`
- `sandbox_provider.py:69` 用 `resolve_class(config.sandbox.use, SandboxProvider)` 加载沙箱 provider
- `security.py` 的 `is_host_bash_allowed()` 判断 bash 是否被允许：
  - `AioSandboxProvider` → 直接返回 `true`
  - `LocalSandboxProvider` → 返回 `allow_host_bash` 的值
- `tools/tools.py:177` 在加载工具时调用 `is_host_bash_allowed()`，决定是否过滤掉 bash 工具

---

## 10. subagents（★ 重点）

### 大白话讲清楚

subagent 是 agent 的"下属"。主 agent（lead agent）可以通过 `task` 工具把子任务派给 subagent 并行执行。

这个配置控制：

- subagent 最多运行多久（`timeout_seconds`）
- 最多跑几轮对话（`max_turns`）
- 用什么模型（可以和主 agent 不同）
- 可以自定义新的 subagent 类型

### YAML 精简

```yaml
subagents:
  timeout_seconds: 900           # 默认 15 分钟
  # max_turns: 120               # 全局最大轮次覆盖

  agents:
    general-purpose:
      timeout_seconds: 1800      # 30 分钟
      max_turns: 160
      # model: qwen3:32b         # 用指定模型
    bash:
      timeout_seconds: 300       # 5 分钟
      max_turns: 80

  custom_agents:
    analysis:
      description: "数据分析专家"
      system_prompt: |
        你是数据分析 subagent。
      tools:
        - bash
        - read_file
        - write_file
      model: inherit             # 继承父 agent 的模型
      max_turns: 80
      timeout_seconds: 600
```

### 关键概念

**`timeout_seconds`**：subagent 的硬性超时。到期后 subagent 被强制停止，已执行的步骤结果保留。默认 900 秒（15 分钟）。

**`model: "inherit"`**：特殊值，表示"用主 agent 的模型"。也可以写具体模型名如 `qwen3:32b`，必须和 `models` 列表里的 `name` 匹配。

**`custom_agents`**：你可以定义自己的 subagent 类型。比如 `analysis` 类型的 subagent 只配了 bash + file 工具，专注数据分析。定义后，lead agent 可以通过 `task` 工具指定 `agent_type: "analysis"` 来使用。

**分层覆盖**：`per-agent override > global default > builtin default`。比如 `general-purpose` 的超时可以单独设成 30 分钟，其他 subagent 还是用 15 分钟。

### 跟代码的关系

- `SubagentsAppConfig` 在 `config/subagents_config.py`
- `get_timeout_for(agent_name)` 实现分层覆盖逻辑
- `task_tool.py` 在派任务时读取这些配置
- `model: "inherit"` 在 `task_tool` 里被替换成主 agent 当前使用的模型名

---

## 11. acp_agents

### 大白话讲清楚

ACP（Agent Client Protocol）让你调用外部 agent，比如 Claude Code、Codex CLI。这些是独立运行的 agent 进程，DeerFlow 通过标准协议和它们通信。

**改了会怎样？** 配置后，agent 会多一个 `invoke_acp_agent` 工具，可以启动外部 agent 执行子任务。

### YAML 精简

```yaml
acp_agents:
  claude_code:
    command: npx
    args: ["-y", "@zed-industries/claude-agent-acp"]
    description: Claude Code for implementation
    model: null
  codex:
    command: npx
    args: ["-y", "@zed-industries/codex-acp"]
    description: Codex CLI for code generation
    model: null
```

### 跟代码的关系

- `ACPAgentConfig` 在 `config/acp_config.py`
- `invoke_acp_agent_tool.py` 的 `build_invoke_acp_agent_tool()` 根据配置构建工具
- `get_available_tools()` 的第⑧步加载 ACP 工具

---

## 12. skills

### 大白话讲清楚

技能（Skills）是 agent 可以动态加载的专业知识模块。比如 "web-search" 技能教 agent 怎么高效搜索。

**`path`**：技能目录在宿主机上的位置。默认是项目根目录的 `skills/` 文件夹。
**`container_path`**：Docker 模式下，技能目录在容器内的挂载路径。默认 `/mnt/skills`。

### YAML 精简

```yaml
skills:
  # path: /absolute/path/to/custom/skills
  container_path: /mnt/skills
```

### 跟代码的关系

- `SkillsConfig` 在 `config/skills_config.py`
- `get_skills_path()` 按优先级解析路径：显式 `path` > 环境变量 `DEER_FLOW_SKILLS_PATH` > 项目根 `skills/` > 旧版路径
- `get_skill_container_path(skill_name, category)` 返回容器内的完整路径，如 `/mnt/skills/public/web-search`

---

## 13. title

### 大白话讲清楚

自动给对话生成标题。每次新对话开始时，DeerFlow 用 LLM 生成一个简短标题（如"帮我写一个 Python 爬虫"）。

**改了会怎样？** `enabled: false` 后，对话标题不再自动生成。`max_words: 6` 控制标题最多几个词。

### YAML 精简

```yaml
title:
  enabled: true
  max_words: 6
  max_chars: 60
  model_name: null
```

### 跟代码的关系

- `TitleConfig` 在 `config/title_config.py`
- `model_name: null` 表示用默认模型（`models` 列表第一个）。可以指定一个便宜的模型来生成标题。
- `prompt_template` 定义了生成标题的提示词模板

---

## 14. summarization（★ 重点）

### 大白话讲清楚

长对话自动压缩。当对话历史太长快要超出模型的上下文窗口时，自动把旧消息压缩成一段摘要，只保留最近的消息。

这就像你读书时做笔记——不需要记住每一页的原文，只需要记住关键信息。

### YAML 精简

```yaml
summarization:
  enabled: true
  model_name: null               # 用哪个模型做摘要，null=默认模型

  trigger:                       # 触发条件（任一命中就触发，OR 逻辑）
    - type: tokens
      value: 15564               # token 数达到 15564 时触发
    # - type: messages
    #   value: 50                # 消息数达到 50 条时触发
    # - type: fraction
    #   value: 0.8               # 达到模型最大上下文的 80% 时触发

  keep:                          # 摘要后保留多少最近历史
    type: messages
    value: 10                    # 保留最近 10 条消息

  trim_tokens_to_summarize: 15564
  summary_prompt: null           # 自定义摘要提示词

  preserve_recent_skill_count: 5         # 保留最近 5 个技能文件不压缩
  preserve_recent_skill_tokens: 25000    # 技能文件总 token 预算
  preserve_recent_skill_tokens_per_skill: 5000  # 每个技能文件最大 token
  skill_file_read_tool_names:
    - read_file
    - read
    - view
    - cat
```

### 关键概念

**`trigger` 类型**：

- `tokens: 15564` → 对话 token 数达到阈值时触发
- `messages: 50` → 消息条数达到阈值时触发
- `fraction: 0.8` → 达到模型最大输入 token 的 80% 时触发（最智能，自动适配不同模型）
- 可以写多个，任一命中就触发（OR 逻辑）

**`keep` 策略**：

- `type: messages, value: 10` → 压缩后保留最近 10 条消息
- `type: tokens, value: 3000` → 压缩后保留最近 3000 token
- `type: fraction, value: 0.3` → 压缩后保留模型最大上下文的 30%

**`preserve_recent_skill_count`**：这个特别重要！agent 加载技能后，技能内容占大量 token。如果被压缩掉了，agent 就"失忆"——忘记自己有什么技能。这个设置保证最近加载的 5 个技能文件不被压缩，总 token 预算 25000，每个技能最多 5000 token。

### 跟代码的关系

- `SummarizationConfig` 在 `config/summarization_config.py`
- `SummarizationMiddleware` 在 `agents/middlewares/` 里：
  - 每次工具调用后检查是否触发阈值
  - 触发后：把 `keep` 之外的历史消息发给 LLM 做摘要
  - 摘要替换旧消息，保留最近的消息
  - 技能文件通过 `skill_file_read_tool_names` 识别（agent 用 `read_file` 读的 `.md` 文件），保护最近 N 个不被压缩

---

## 15. memory

### 大白话讲清楚

agent 的长期记忆。它能记住你的偏好、习惯和之前提过的事实。比如你说"我主要用 Python"，下次对话它就记住了。

**改了会怎样？** 关闭后，agent 不再积累和利用长期记忆。每段对话都是全新的。

### YAML 精简

```yaml
memory:
  enabled: true
  storage_path: memory.json
  debounce_seconds: 30
  model_name: null
  max_facts: 100
  fact_confidence_threshold: 0.7
  injection_enabled: true
  max_injection_tokens: 2000
```

### 关键字段

- `debounce_seconds: 30`：对话结束后等 30 秒再处理记忆更新，避免频繁写入
- `max_facts: 100`：最多记住 100 条事实
- `fact_confidence_threshold: 0.7`：只有模型有 70% 以上确信度的事实才会被记住
- `injection_enabled: true`：把记忆注入 system prompt，agent 就能"想起来"
- `max_injection_tokens: 2000`：注入的记忆最多占 2000 token

### 跟代码的关系

- `MemoryConfig` 在 `config/memory_config.py`
- `FileMemoryStorage` 在 `agents/memory/storage.py`，每个用户独立的 `memory.json` 文件
- `MemoryMiddleware` 在对话结束后提取新事实，debounce 后写入存储

---

## 16. agents_api

### 大白话讲清楚

HTTP API 开关。开启后，可以通过 Gateway API 创建和管理自定义 agent 的 SOUL.md（系统提示词）和 USER.md（用户偏好）。

**改了会怎样？** 默认关闭。开启后暴露 agent 管理的 HTTP 端点。**只在受信任的管理网络后面开启！**

### YAML 精简

```yaml
agents_api:
  enabled: false
```

### 跟代码的关系

- `AgentsApiConfig` 在 `config/agents_api_config.py`
- Gateway API 路由检查这个配置决定是否暴露 agent 管理 API

---

## 17. skill_evolution

### 大白话讲清楚

让 agent 自己创建和改进技能文件。开启后，agent 可以在 `skills/custom/` 目录下写新的技能文件。

**改了会怎样？** 默认关闭。开启后 agent 多一个 `skill_manage` 工具，能创建、编辑、删除自定义技能。`moderation_model_name` 可以指定一个独立模型做安全审查。

### YAML 精简

```yaml
skill_evolution:
  enabled: false
  moderation_model_name: null
```

### 跟代码的关系

- `SkillEvolutionConfig` 在 `config/skill_evolution_config.py`
- `get_available_tools()` 在 `tools/tools.py:212` 检查 `skill_evolution.enabled`，为 `true` 时加载 `skill_manage_tool`

---

## 18. database

### 大白话讲清楚

数据持久化后端。存储 LangGraph 的状态检查点和 DeerFlow 的应用数据（对话记录、用户反馈等）。

三种选择：

- `memory`：纯内存，重启后数据全丢（开发用）
- `sqlite`：单文件数据库，单节点够用（默认）
- `postgres`：PostgreSQL，生产多节点部署

### YAML 精简

```yaml
database:
  backend: sqlite
  sqlite_dir: .deer-flow/data

# 生产环境
# database:
#   backend: postgres
#   postgres_url: $DATABASE_URL
```

### 跟代码的关系

- `DatabaseConfig` 在 `config/database_config.py`
- SQLite 模式：检查点和应用共享 `deerflow.db` 文件，WAL 模式允许并发读写
- Postgres 模式：共享数据库 URL 但维护独立连接池
- `sqlite_path` 属性返回完整文件路径：`.deer-flow/data/deerflow.db`
- `app_sqlalchemy_url` 属性返回 SQLAlchemy 连接字符串

---

## 19. run_events

### 大白话讲清楚

运行事件存储——记录每条消息和工具执行的追踪信息。

三种后端：

- `memory`：内存，重启即丢（默认）
- `db`：SQL 数据库，生产查询
- `jsonl`：追加写入 JSONL 文件，轻量持久化

### YAML 精简

```yaml
run_events:
  backend: memory
  max_trace_content: 10240
  track_token_usage: true
```

### 跟代码的关系

- `RunEventsConfig` 在 `config/run_events_config.py`
- `max_trace_content: 10240`：追踪内容超过 10KB 就截断
- `track_token_usage: true`：把 token 用量累计到 RunRow 记录

---

## 20. channels

### 大白话讲清楚

IM 渠道集成——把 DeerFlow 接入飞书、Slack、Telegram、微信、钉钉、Discord 等。

**改了会怎样？** 默认全部注释掉。取消注释并填入 bot token 后，DeerFlow 就能在对应 IM 平台收发消息。

### YAML 精简

```yaml
# channels:
#   langgraph_url: http://localhost:8001/api
#   gateway_url: http://localhost:8001
#   feishu:
#     enabled: false
#     app_id: $FEISHU_APP_ID
#     app_secret: $FEISHU_APP_SECRET
#   slack:
#     enabled: false
#     bot_token: $SLACK_BOT_TOKEN
#     app_token: $SLACK_APP_TOKEN
#   telegram:
#     enabled: false
#     bot_token: $TELEGRAM_BOT_TOKEN
```

### 跟代码的关系

- 渠道模块在 `backend/packages/harness/deerflow/channels/` 下
- 所有渠道使用出站连接（WebSocket 或轮询），不需要公网 IP
- 支持 `session` 级别的配置覆盖（assistant_id、thinking_enabled 等）
- 每个渠道可以有 `users` 级别的 per-user 覆盖

---

## 21. guardrails

### 大白话讲清楚

工具调用的安全护栏。开启后，每次工具调用前都先经过审批——允许了才执行，拒绝就直接跳过。

三种 provider：

1. **AllowlistProvider**（内置）：简单的黑名单，列出不允许的工具
2. **OAP 护照 provider**：基于 Open Agent Passport 标准的权限控制
3. **自定义 provider**：你自己写审批逻辑

### YAML 精简

```yaml
# guardrails:
#   enabled: true
#   fail_closed: true
#   provider:
#     use: deerflow.guardrails.builtin:AllowlistProvider
#     config:
#       denied_tools: ["bash", "write_file"]
```

### 跟代码的关系

- `GuardrailsConfig` 在 `config/guardrails_config.py`
- `GuardrailMiddleware` 在 `agents/middlewares/tool_error_handling_middleware.py:273`：
  - `resolve_variable(provider.use)` 加载 provider 类
  - 每次工具调用前调用 `provider.evaluate()` 或 `provider.aevaluate()`
  - `fail_closed: true` → provider 异常时默认拒绝

---

## 22. circuit_breaker

### 大白话讲清楚

LLM 调用的熔断器。当某个模型 provider 连续失败时，停止发送请求，等一段时间再尝试恢复。

**改了会怎样？** 默认注释掉（不启用）。取消注释后：

- `failure_threshold: 5` → 连续失败 5 次后熔断
- `recovery_timeout_sec: 60` → 熔断 60 秒后尝试恢复

这在你用不稳定的 API 时特别有用——避免无限重试浪费资源。

### YAML 精简

```yaml
# circuit_breaker:
#   failure_threshold: 5
#   recovery_timeout_sec: 60
```

### 跟代码的关系

- `CircuitBreakerConfig` 直接定义在 `app_config.py:49`
- 熔断器在 LLM 调用链路中检查：
  - 关闭状态（closed）：正常请求
  - 打开状态（open）：直接报错，不发请求
  - 半开状态（half-open）：等 recovery_timeout 后尝试一次

---

## 附录：uploads 配置

文件上传相关的配置虽然在 sandbox 之前，但属于独立功能：

```yaml
uploads:
  max_files: 10
  max_file_size: 52428800    # 50 MiB
  max_total_size: 104857600  # 100 MiB
  auto_convert_documents: false
  pdf_converter: auto        # auto / pymupdf4llm / markitdown
```

- 控制前端文件选择器的限制和后端的文档转换行为
- `auto_convert_documents: false` 是安全考虑——Office/PDF 转换在宿主机执行，不受沙箱保护
- `pdf_converter: auto` 优先用 pymupdf4llm（更好的表格/标题提取），失败回退 MarkItDown
