# 设计模式手册

> 从 DeerFlow 2.0 源码中提取的 **AI Agent 设计模式**
> Part 1: AI Agent 核心模式（8 个）— 你构建 Agent 框架真正需要的
> Part 2: 通用基础设施模式（4 个）— Python 工程基础，简明参考

## 目录

### Part 1: AI Agent 核心设计模式

| # | 模式 | Category | 核心挑战 |
|---|------|----------|---------|
| 1 | Agent 中间件拦截链 | extensibility | LLM 行为不可预测，如何在不改 Agent 核心的前提下拦截和修改它的行为？ |
| 2 | 延迟工具发现 | structure | 100+ 工具的 schema 塞给 LLM 撑爆 context window，怎么管理？ |
| 3 | 压缩前记忆抢救 | lifecycle | 对话压缩会删旧消息，里面可能有用户偏好等关键信息，怎么抢救？ |
| 4 | 信号驱动记忆更新 | state | 不是所有对话都值得记忆。怎么检测"用户纠正"和"用户肯定"？ |
| 5 | Token 预算分配 | state | context window 是稀缺资源，怎么在 prompt/memory/tools/对话之间分配？ |
| 6 | Sub-Agent 委派隔离 | concurrency | 复杂任务需要拆给子 Agent，怎么隔离、防递归、可取消？ |
| 7 | 护栏自适应拒绝 | resilience | Agent 调了危险工具，不能崩溃，要让 Agent 自己换方式 |
| 8 | 三层 Agent 配置 | structure | 内置 Agent + 用户自定义 Agent + 按实例覆盖，全局默认不能污染自定义 |

### Part 2: 通用基础设施模式

| # | 模式 | Category | 一句话 |
|---|------|----------|--------|
| 9 | 去抖动队列 | concurrency | 攒一波再调 LLM，省钱 |
| 10 | 原子文件写入 | resilience | 先写 .tmp 再 rename，防崩溃丢数据 |
| 11 | 线程安全懒单例 | lifecycle | 全局共享资源 + 懒加载 + 可 reset |
| 12 | ContextVar 请求隔离 | concurrency | async 请求间状态隔离，比 global 安全比参数简洁 |

---

# Part 1: AI Agent 核心设计模式

---

### 1. Agent 中间件拦截链 / Agent Middleware Interception Chain

`extensibility`

**解决什么问题：**
  LLM Agent 的行为不可预测。它可能调危险工具、陷入循环、发不恰当内容、
  消息太长超 token 限制。你需要一种机制，在不修改 Agent 核心（LLM + tools）
  的前提下，拦截和修改它的行为。

  为什么不用继承？→ 每个 Agent 子类都要重写，18 个关注点 × N 个 Agent = 维护地狱。
  为什么不用 AOP/装饰器？→ Python 没有 AOP，装饰器无法感知 Agent 状态（消息历史、线程 ID）。
  为什么不用 LangGraph 的条件边？→ 条件边是路由层，不适合做"每个请求都要跑"的横切关注点。

**怎么解决的：**

  ```
  LangGraph 的 AgentMiddleware 提供四个拦截点：

    wrap_model_call()    拦截 LLM 调用 → 可以修改 prompt / 过滤 tools / 注入上下文
    wrap_tool_call()     拦截工具调用 → 可以授权 / 错误处理 / 审计
    before_model()       LLM 调用前  → 可以压缩历史 / 添加日期 / 注入记忆
    after_agent()        Agent 完成后 → 可以提取记忆 / 生成标题 / 统计 token

  中间件按顺序组成链，每个请求穿链而过：

    用户消息
      → [ThreadDataMiddleware]      设 thread_id
      → [SandboxMiddleware]         懒初始化沙箱
      → [SummarizationMiddleware]   压缩旧消息（太长时触发）
      → [DynamicContextMiddleware]  注入日期+记忆到 <system-reminder>
      → [MemoryMiddleware]          提取对话信号，入队记忆更新
      → [GuardrailMiddleware]       拦截危险工具调用
      → [ToolErrorMiddleware]       工具报错转 ToolMessage（不让异常崩掉 Agent）
      → LLM 调用
      → [ToolErrorMiddleware]       包装工具异常
      → [GuardrailMiddleware]       检查工具调用是否安全
      → 工具执行
      → 返回结果
  ```

  关键设计决策：

- **顺序不可乱**：ThreadData 必须在 Sandbox 前（需要 thread_id），
    Clarification 必须最后（拦截最终输出），Summarization 要早（减少后续 token）
- **GraphBubbleUp 必须透传**：LangGraph 的 interrupt/pause 信号通过异常传播，
    任何中间件吞了它都会破坏人机协作流程
- **错误降级为 ToolMessage**：工具异常不抛出去（会崩 Agent），而是转为
    `ToolMessage(status="error")` 让 LLM 自己换方式

**最小可复现代码：**

  ```python
  from dataclasses import dataclass
  from typing import Protocol, Callable, Any

  @dataclass
  class ToolCallRequest:
      tool_name: str
      args: dict
      call_id: str

  @dataclass
  class ToolCallResult:
      content: str
      status: str = "success"  # yyds: "error" 让 LLM 知道失败了

  class AgentMiddleware(Protocol):
      """拦截 Agent 行为的中间件"""
      def wrap_tool_call(
          self, request: ToolCallRequest, handler: Callable
      ) -> ToolCallResult:
          """拦截工具调用：授权/审计/错误处理"""
          return handler(request)

  class ErrorHandlingMiddleware:
      """把工具异常转为 ToolMessage — 不让工具崩溃传播到 LLM"""
      def wrap_tool_call(self, request, handler):
          try:
              return handler(request)
          except Exception as e:
              msg = str(e)[:500]  # yyds: 截断，防撑爆 context
              return ToolCallResult(
                  content=f"Tool '{request.tool_name}' failed: {msg}. Try alternative.",
                  status="error",
              )

  class GuardrailMiddleware:
      """拦截危险工具调用 — 拒绝并引导 LLM 自适应"""
      def __init__(self, denied_tools: set[str]):
          self._denied = denied_tools

      def wrap_tool_call(self, request, handler):
          if request.tool_name in self._denied:
              return ToolCallResult(
                  content=f"Guardrail: '{request.tool_name}' blocked. Choose alternative.",
                  status="error",
              )
          return handler(request)

  def build_middleware_chain(middlewares: list, request, final_handler):
      """按顺序穿链 — 最后一个 middleware 包裹 final_handler"""
      handler = final_handler
      for mw in reversed(middlewares):
          prev = handler
          handler = lambda req, h=prev, m=mw: m.wrap_tool_call(req, h)
      return handler(request)
  ```

**什么时候用：**
  ✓ 任何需要"不改 Agent 核心、扩展 Agent 行为"的场景
  ✓ 需要审计/监控/限流/安全检查的 Agent 系统
  ✓ 需要在 LLM 调用前后注入上下文（日期、记忆、技能提示）
  ✓ 需要压缩对话历史、检测循环、限制并发子任务
  ✗ 只有 2-3 个固定行为的简单 Agent（装饰器或条件分支更简单）
  ✗ 需要跨 Agent 实例共享状态的场景（中间件是无状态的，用 ContextVar）

**在哪里见过：**

- DeerFlow: `agents/middlewares/` — 18 个中间件，覆盖安全/记忆/压缩/错误/token/标题/循环检测
- DeerFlow: `agents/middlewares/tool_error_handling_middleware.py` — 错误降级为 ToolMessage + 500 字符截断
- DeerFlow: `agents/middlewares/deferred_tool_filter_middleware.py` — 双层拦截：wrap_model_call 隐藏 schema + wrap_tool_call 阻止调用
- LangChain: `AgentMiddleware` 基类 — 四个拦截点的定义者
- Django: 中间件系统 — 同样的洋葱模型，request → through layers → response
- Express.js: middleware chain — `req, res, next` 模式
- Claude Code: tool_use 前后的权限检查和安全拦截

---

### 2. 延迟工具发现 / Deferred Tool Discovery

`structure`

**解决什么问题：**
  你的 Agent 系统接入了几十个 MCP 工具。每个工具的 OpenAI function schema
  约 50-200 tokens。100 个工具 = 20000 tokens，每次调 LLM 都带着：
    - 浪费 context window（LLM 一轮可能只用 3 个工具）
    - 浪费钱（每轮都计费这些 schema tokens）
    - 降低 LLM 决策质量（选择太多，反而选不好）

  为什么不按需注册？→ LLM 不知道有哪些工具可用，没法"按需"。
  为什么不限制工具数？→ 不同任务需要不同工具，你没法预知。

**怎么解决的：**

  ```
  三方协作：

  ① DeferredToolRegistry（注册表）
     启动时：100 个 MCP 工具全部注册（只存 name + description）
     → LLM 只看到名字列表（每个只占几个 token），注入到 system prompt：
       <available-deferred-tools>
       slack_send_message
       github_create_issue
       ...
       </available-deferred-tools>

  ② DeferredToolFilterMiddleware（拦截器，两个拦截点）
     wrap_model_call()：从 request.tools 里移除延迟工具的 schema
       → LLM 收到的 tools 里没有 slack_send_message 的参数定义
     wrap_tool_call()：如果 LLM 偷偷调了未 promote 的工具
       → 返回 ToolMessage("先调 tool_search 获取 schema")

  ③ tool_search 工具（LLM 可调用的"解锁"工具）
     LLM 想：用户要发 Slack 消息 → 我需要 slack_send 的完整参数
     → 调 tool_search("select:slack_send")
     → registry.search() 匹配 → 返回 JSON schema
     → registry.promote() 从延迟列表移除
     → 下一次 wrap_model_call 不再过滤这个工具
  ```

  关键设计决策：

- **注册表存 ContextVar 不存全局变量** → 并发请求间隔离，sub-agent 继承父 agent 的 promote 状态
- **promote = 从列表移除** → 不需要额外的 `is_active` 字段，简单
- **三种搜索**（精确选择 / 关键词 / 正则）→ 适应 LLM 的不同表达方式
- **MAX_RESULTS = 5** → 一次拉太多 schema 又撑爆

**最小可复现代码：**

  ```python
  import re
  from dataclasses import dataclass

  @dataclass
  class ToolEntry:
      name: str
      description: str
      schema: dict  # yyds: 完整参数 schema，延迟暴露

  class DeferredRegistry:
      def __init__(self):
          self._deferred: list[ToolEntry] = []

      def register(self, entry: ToolEntry) -> None:
          self._deferred.append(entry)

      @property
      def deferred_names(self) -> set[str]:
          """中间件用这个决定过滤谁"""
          return {e.name for e in self._deferred}

      def search(self, query: str) -> list[ToolEntry]:
          """LLM 调 tool_search 时触发：精确 / 关键词 / 正则"""
          if query.startswith("select:"):
              names = {n.strip() for n in query[7:].split(",")}
              return [e for e in self._deferred if e.name in names][:5]
          try:
              regex = re.compile(query, re.IGNORECASE)
          except re.error:
              regex = re.compile(re.escape(query), re.IGNORECASE)
          scored = []
          for e in self._deferred:
              if regex.search(f"{e.name} {e.description}"):
                  score = 2 if regex.search(e.name) else 1  # yyds: name 权重 > description
                  scored.append((score, e))
          scored.sort(key=lambda x: x[0], reverse=True)
          return [e for _, e in scored][:5]

      def promote(self, names: set[str]) -> None:
          """LLM 获取 schema 后立即 promote = 不再被过滤"""
          self._deferred = [e for e in self._deferred if e.name not in names]

  # yyds: 中间件的 wrap_model_call 拦截点
  def filter_deferred_tools(tools, registry):
      active = [t for t in tools if t.name not in registry.deferred_names]
      return active  # yyds: LLM 只看到 active 的 schema

  # yyds: 中间件的 wrap_tool_call 拦截点
  def block_unpromoted_call(tool_name, registry):
      if registry.deferred_names and tool_name in registry.deferred_names:
          return f"Error: '{tool_name}' is deferred. Call tool_search first."
      return None  # yyds: None = 放行
  ```

**什么时候用：**
  ✓ 工具/插件 > 20 个的 Agent 系统（MCP 集成场景几乎都会到这个量级）
  ✓ LLM function calling 场景（每个工具 schema 占 50-200 tokens）
  ✓ 工具按需激活（不是每次都用全部工具）
  ✗ 工具 < 10 个（全加载更简单，复杂度不值得）
  ✗ 每次都用大部分工具的场景（延迟发现没有收益）

**在哪里见过：**

- DeerFlow: `tools/builtins/tool_search.py` — DeferredToolRegistry + 三种搜索 + promote
- DeerFlow: `agents/middlewares/deferred_tool_filter_middleware.py` — 双层拦截（schema 隐藏 + 执行阻止）
- Claude Code: tool_use 的延迟工具加载 — 同样的 "名字列表 → 按需 schema" 模式
- VS Code: 扩展按需激活（`activationEvents`）— 原理相同，只是触发源是编辑器而非 LLM
- GraphQL: 按需查询字段 — 不拉全部 schema，只拉需要的

---

### 3. 压缩前记忆抢救 / Pre-Compression Memory Rescue

`lifecycle`

**解决什么问题：**
  LLM 的 context window 有限（128k tokens）。对话超过限制时，必须压缩：
  删掉旧消息，保留最近的 + LLM 生成的摘要。
  但旧消息里可能有用户偏好、关键决策等重要信息。删除 = 永久丢失。

  为什么不全部存下来？→ context window 放不下，LLM 也处理不了无限长的历史。
  为什么不让 LLM 在摘要时保留关键信息？→ 摘要 prompt 不保证 100% 保留，
  且摘要本身也占 tokens。最可靠的方式是：删除前先抢救。

**怎么解决的：**

  ```
  SummarizationMiddleware 的压缩流程（删除前有两个抢救机会）：

  ① Skill Bundle Rescue（技能抢救，内置）
     扫描旧消息 → 找到"读取了 skill 文件"的 ToolMessage
     → 把这些消息从"待压缩区"移到"保留区"
     → 预算控制：最多 5 个 bundle，总共 < 25000 tokens
     → 目的：Agent 加载的技能文件不要被删，否则下次又要重新读

  ② BeforeSummarizationHook（钩子抢救，可扩展）
     fire_hooks(event) → 遍历所有注册的钩子
     → memory_flush_hook 拿到即将被删的消息
     → 过滤出用户和 AI 的对话（去掉工具调用等无关消息）
     → 检测 correction/reinforcement 信号
     → queue.add_nowait(0s) 入队记忆更新（0 秒！消息马上要被删了）
     → 记忆系统用 LLM 提取关键信息 → 写入 memory.json
     → 然后旧消息才被 RemoveAll 安全删除

  时序：
    消息总数超阈值
      → partition_with_skill_rescue（抢救 skill 相关消息）
      → fire_hooks（钩子抢救，如 memory_flush_hook）
      → LLM 生成摘要
      → RemoveAll 删除全部旧消息
      → 追加摘要 + 保留的消息
  ```

  关键设计决策：

- **钩子在删除前同步执行** → 确保抢救完成后才开始删除
- **add_nowait(0s) 而不是 add(30s)** → 正常路径用 30s 去抖动，抢救路径等不了
- **钩子失败不阻塞压缩** → `try/except` 包裹每个钩子，记忆系统挂了不影响对话压缩
- **SummarizationEvent 是 frozen dataclass** → 钩子不能修改事件，只能读

**最小可复现代码：**

  ```python
  from typing import Protocol, Callable
  from dataclasses import dataclass

  @dataclass(frozen=True)
  class CompressionEvent:
      items_to_delete: tuple  # yyds: frozen，钩子不能改
      preserved_items: tuple

  class BeforeCompressionHook(Protocol):
      def __call__(self, event: CompressionEvent) -> None: ...

  class Compressor:
      def __init__(self, max_items: int = 100):
          self._max_items = max_items
          self._hooks: list[Callable] = []

      def add_hook(self, hook: Callable):
          self._hooks.append(hook)

      def maybe_compress(self, items: list) -> list:
          if len(items) <= self._max_items:
              return items

          to_compress = items[:-self._max_items // 2]
          to_preserve = items[-self._max_items // 2:]

          # yyds: 先抢救
          event = CompressionEvent(
              items_to_delete=tuple(to_compress),
              preserved_items=tuple(to_preserve),
          )
          for hook in self._hooks:
              try:
                  hook(event)
              except Exception:
                  pass  # yyds: 钩子失败不阻塞压缩

          # yyds: 再生成摘要 + 删除
          summary = self._generate_summary(to_compress)
          return [summary] + list(to_preserve)

      def _generate_summary(self, items) -> list:
          return [{"type": "summary", "content": f"Summary of {len(items)} items"}]

  # yyds: 使用
  def rescue_important(event: CompressionEvent):
      important = [i for i in event.items_to_delete if i.get("important")]
      if important:
          save_to_persistent_storage(important)

  compressor = Compressor(max_items=100)
  compressor.add_hook(rescue_important)
  ```

**什么时候用：**
  ✓ LLM Agent 的对话压缩（context window 有限）
  ✓ 任何"删除前需要抢救"的批量操作（缓存淘汰、日志归档）
  ✓ 需要可扩展的"删除前"逻辑（不修改压缩核心代码）
  ✗ 数据不重要、丢了无所谓的场景
  ✗ 数据量小、不需要压缩的场景

**在哪里见过：**

- DeerFlow: `agents/middlewares/summarization_middleware.py` — Skill Rescue + BeforeSummarizationHook Protocol + _fire_hooks
- DeerFlow: `agents/memory/summarization_hook.py` — memory_flush_hook（入队 add_nowait(0s)）
- DeerFlow: `agents/lead_agent/agent.py:153-155` — 钩子注册点
- Redis: `maxmemory-policy volatile-lru` — 淘汰前的回调
- Git: `git gc` — 清理前保留 reachable objects

---

### 4. 信号驱动记忆更新 / Signal-Driven Memory Update

`state`

**解决什么问题：**
  Agent 每轮对话都调 LLM 提取记忆 → 太贵。但不是所有对话都值得记忆。
  用户说"不对，应该是…" → 纠正信号，必须覆盖旧记忆。
  用户说"对，就是这样" → 肯定信号，加强当前记忆。
  用户说"帮我查个天气" → 普通对话，不值得记忆。

  为什么不每次都提取？→ LLM 调用贵（每次 ~0.01-0.1 美元），连聊 10 轮 = $0.1-1.0。
  为什么不用固定间隔？→ 纠正信号需要立即覆盖，等 30 秒可能导致中间轮用错记忆。
  为什么不让用户手动标记？→ 用户体验差。

**怎么解决的：**

  ```
  MemoryMiddleware.after_agent() — 每轮对话结束后执行：

  ① 过滤消息：只保留 HumanMessage + AIMessage（去掉工具调用中间步骤）
  ② 信号检测（只扫最近 6 条消息）：
     correction = detect_correction(messages)   → "不对"/"try again"/"你理解错了"
     reinforcement = detect_reinforcement(messages) → "完全正确"/"yes exactly"
     纠正优先级 > 肯定（互斥）
  ③ 入队（30s 去抖动）：
     queue.add(messages, correction=True, reinforcement=True)
  ④ 定时器到期 → MemoryUpdater.update_memory()
     → LLM 分析对话 → 返回 JSON 更新指令
     → _apply_updates() 执行合并：
       correction=True → confidence >= 0.95 → 强制覆盖旧记忆
       reinforcement=True → confidence >= 0.9 → 加强现有记忆
       都没有 → confidence >= 0.7 → 正常提取
  ⑤ 写入 memory.json（原子写入）

  信号如何影响 LLM prompt：
    correction_detected=True → prompt 里加：
      "用户纠正了之前的信息。请用最新信息覆盖旧信息。"
    reinforcement_detected=True → prompt 里加：
      "用户确认了之前的信息是正确的。请加强这些信息。"
  ```

  关键设计决策：

- **10 个纠正 pattern + 13 个肯定 pattern**，中英文双语，正则匹配
- **只扫最近 6 条** → 避免把 50 轮前的信号误当当前的
- **信号通过 add() 传递而不是独立存储** → ConversationContext 合并新旧信号（OR）
- **confidence 阈值分级** → 0.95（纠正）/ 0.9（肯定）/ 0.7（普通）

**最小可复现代码：**

  ```python
  import re

  CORRECTION_PATTERNS = [
      r"(?i)(不对|错了|try again|你理解错|不是这样|incorrect)",
  ]
  REINFORCEMENT_PATTERNS = [
      r"(?i)(完全正确|exactly|正是我想要|spot on|没错)",
  ]

  def detect_correction(messages: list[str], window: int = 6) -> bool:
      """检测纠正信号：用户否定 Agent 的输出"""
      recent = messages[-window:]
      combined = " ".join(recent)
      return any(re.search(p, combined) for p in CORRECTION_PATTERNS)

  def detect_reinforcement(messages: list[str], window: int = 6) -> bool:
      """检测肯定信号：用户确认 Agent 的输出（纠正优先级更高）"""
      recent = messages[-window:]
      combined = " ".join(recent)
      return any(re.search(p, combined) for p in REINFORCEMENT_PATTERNS)

  @dataclass
  class MemoryUpdateRequest:
      messages: list[str]
      correction: bool = False      # yyds: 纠正 → 覆盖旧记忆
      reinforcement: bool = False   # yyds: 肯定 → 加强记忆

  def get_confidence_threshold(req: MemoryUpdateRequest) -> float:
      """信号驱动置信度阈值"""
      if req.correction:
          return 0.95  # yyds: 纠正 → 高置信度，强制覆盖
      if req.reinforcement:
          return 0.9   # yyds: 肯定 → 较高置信度，加强
      return 0.7       # yyds: 普通对话 → 标准阈值

  def build_extraction_prompt(req: MemoryUpdateRequest) -> str:
      base = "分析对话，提取应该记住的信息。"
      if req.correction:
          base += "\n重要：用户纠正了之前的信息。用最新信息覆盖旧信息。"
      if req.reinforcement:
          base += "\n注意：用户确认了之前的某些信息是正确的。加强这些信息。"
      return base
  ```

**什么时候用：**
  ✓ Agent 需要跨对话记住用户偏好/关键信息
  ✓ 需要区分"纠正"和"新增"的 Agent 系统
  ✓ 用户会多次交互、记忆需要持续更新的场景
  ✗ 无状态的 Agent（每次对话独立，不需要记忆）
  ✗ 一次性对话场景（没有"跨对话记忆"的需求）

**在哪里见过：**

- DeerFlow: `agents/memory/message_processing.py` — 10+13 正则 pattern，中英文双语，6 条窗口
- DeerFlow: `agents/middlewares/memory_middleware.py` — after_agent 8 步流水线，纠正 > 肯定（互斥）
- DeerFlow: `agents/memory/updater.py` — _apply_updates 按 confidence 阈值决定覆盖/增强/新增
- ChatGPT Memory: 类似的"记住用户偏好"机制，但信号检测是闭源的
- Claude: "remember this about me" 指令 — 显式标记 vs DeerFlow 的隐式检测

---

### 5. Token 预算分配 / Token Budget Allocation

`state`

**解决什么问题：**
  context window 是 AI Agent 的核心稀缺资源。128k tokens 看着大，但要装：
    - system prompt（角色定义 + 技能描述 + 工具使用指南）~2000-5000 tokens
    - tools（每个 50-200 tokens × N 个工具）
    - memory（用户画像 + 历史摘要 + 事实列表）
    - conversation（对话历史）
    - 每一项都可能撑爆。尤其是记忆注入：100 条事实 × 50 tokens = 5000 tokens。

  为什么不全部塞进去？→ 超限报错，LLM 调用失败。
  为什么不固定各部分大小？→ 不同对话需要的分配不同（短对话需要更多记忆，长对话需要更多历史）。

**怎么解决的：**

  ```
  format_memory_for_injection(memory_data) 的分配策略：

  ① 三段式注入（固定结构）：
     <memory>
     == User Context ==
     [工作背景/个人偏好/当前关注 — 每段 1-2 句]

     == Conversation History ==
     [近期/早期/长期摘要 — 每段 1-2 句]

     == Important Facts ==
     [事实列表 — 按 confidence 降序，截断到 token 预算]
     </memory>

  ② 事实排序 + 截断：
     facts.sort(key=confidence, reverse=True)
     → 逐条累加 token 数
     → 超过预算就停止（高置信度的事实优先保留）

  ③ 预算硬上限：
     memory_max_tokens 配置项（默认 1000）
     → User Context + History + Facts 总计不超过这个数
     → 超了就截断 Facts（最灵活的部分）

  ④ 优先级（从高到低）：
     User Context（用户画像）> History（历史摘要）> Facts（事实列表）
     → 前两者几乎总是完整保留，Facts 按置信度排序截断
  ```

  关键设计决策：

- **tiktoken 计数 + len//4 回退** → 准确优先，性能回退
- **Facts 按 confidence 降序** → 纠正/肯定产生的高置信度事实优先保留
- **注入位置是 `<system-reminder>` 标签** → 不占 system prompt 的 prefix cache 位置
- **每轮对话前重新计算** → 记忆可能在这轮被更新了

**最小可复现代码：**

  ```python
  def format_memory_for_injection(
      memory_data: dict,
      max_tokens: int = 1000,
  ) -> str:
      """把 memory.json 的内容格式化为可注入 prompt 的文本，控制在 token 预算内"""
      sections = []

      # ① User Context（优先级最高，几乎不截断）
      user_ctx = memory_data.get("user", {})
      user_text = _format_user_context(user_ctx)
      sections.append(("User Context", user_text))

      # ② History（优先级次高，几乎不截断）
      history = memory_data.get("history", {})
      history_text = _format_history(history)
      sections.append(("History", history_text))

      # ③ Facts（最灵活，按置信度排序 + 截断）
      facts = memory_data.get("facts", [])
      facts.sort(key=lambda f: f.get("confidence", 0), reverse=True)  # yyds: 高置信度优先

      remaining_budget = max_tokens
      for _, text in sections:
          remaining_budget -= count_tokens(text)

      facts_text = _format_facts_within_budget(facts, remaining_budget)  # yyds: 截断到剩余预算
      sections.append(("Facts", facts_text))

      result = "<memory>\n"
      for title, text in sections:
          if text.strip():
              result += f"== {title} ==\n{text}\n\n"
      result += "</memory>"
      return result

  def _format_facts_within_budget(facts: list[dict], budget: int) -> str:
      lines = []
      used = 0
      for fact in facts:
          line = f"- [{fact.get('category')}] {fact.get('content')} (confidence: {fact.get('confidence')})"
          tokens = count_tokens(line)
          if used + tokens > budget:
              break  # yyds: 超预算就停，高置信度的已经排在前面了
          lines.append(line)
          used += tokens
      return "\n".join(lines)

  def count_tokens(text: str) -> int:
      try:
          import tiktoken
          return len(tiktoken.encoding_for_model("gpt-4").encode(text))
      except Exception:
          return len(text) // 4  # yyds: 回退：4 字符 ≈ 1 token
  ```

**什么时候用：**
  ✓ 任何需要把"记忆/知识"注入 LLM prompt 的场景
  ✓ Agent 系统的 persistent memory 注入
  ✓ RAG 检索结果的 prompt 注入（同样需要 token 预算控制）
  ✗ 每次对话内容都很少、不会超 token 的简单场景
  ✗ 使用无限 context window 模型的场景（如 Gemini 1M tokens）— 但即使是 1M，过多注入也降低 LLM 决策质量

**在哪里见过：**

- DeerFlow: `agents/memory/prompt.py` — format_memory_for_injection，三段式 + tiktoken + 置信度排序截断
- DeerFlow: `agents/middlewares/dynamic_context_middleware.py` — 注入 `<system-reminder>` 标签
- LangChain: `ConversationSummaryMemory` — 类似的摘要 + 注入策略
- RAG 系统: chunk retrieval + token budget 截断 — 同样的"按相关性排序 + 截断到预算"

---

### 6. Sub-Agent 委派隔离 / Sub-Agent Delegation with Isolation

`concurrency`

**解决什么问题：**
  Lead Agent 收到"重构这个项目的所有测试文件"这种复杂任务。
  需要拆成子任务，每个交给专门的 Agent 处理。
  但子 Agent 不能和主 Agent 共享状态（可能冲突），不能递归创建子 Agent（无限套娃），
  用户可以取消，任务可能超时。

  为什么不在同一个 Agent 里顺序执行？→ 主 Agent 被阻塞，用户看不到进度。
  为什么不用 asyncio.create_task？→ LangGraph 的 agent.astream 不是纯 async，
  需要独立的 event loop 来跑。
  为什么不让子 Agent 也能创建子 Agent？→ Agent-A 交给 Agent-B，B 又交给 C，
  C 又交给 A → 无限循环 + token 爆炸。

**怎么解决的：**

  ```
  完整委派流程：

  ① task_tool 创建 SubagentExecutor
     → 获取 sub-agent 配置（三层合并）
     → 提取父 Agent 上下文（sandbox_state, thread_data, trace_id）
     → 技能白名单交集（父子取交集）
     → 递归防护：disallowed_tools=["task"]（三层防线）

  ② execute_async() 提交到后台
     → copy_context() 保留 ContextVar
     → _scheduler_pool(max_workers=3) 控制并发数
     → _isolated_subagent_loop 持久化 event loop
     → 返回 task_id

  ③ task_tool 轮询结果（每 5 秒）
     → SSE 推送进度（task_running）
     → 超时/取消/失败 → 协作式终止

  ④ _aexecute() 在隔离环境里跑子 Agent
     → build_state（注入父 sandbox + thread_data）
     → create_agent（精简版中间件链）
     → agent.astream()（逐 chunk 迭代，每个检查 cancel_event）

  ⑤ 结果回传
     → 最后一条 AIMessage 作为结果
     → token 用量报告给父 Agent

  三层递归防护：
    Layer 1: get_available_tools(subagent_enabled=False) → 不加载 task_tool
    Layer 2: SubagentConfig.disallowed_tools=["task"] → 配置级黑名单
    Layer 3: _filter_tools(all_tools, allowed, disallowed) → 运行时过滤
  ```

  关键设计决策：

- **max_workers=3** → 最多 3 个子 Agent 并发，防止资源耗尽
- **精简版中间件链** → 子 Agent 不需要 Todo/Title/Memory 等中间件
- **sandbox_state 共享** → 子 Agent 在父 Agent 的沙箱里操作（文件共享）
- **技能白名单交集** → 子 Agent 只能用父 Agent 和配置都允许的技能
- **token 用量传播** → 子 Agent 的 token 计入父 Agent 的 RunJournal

**最小可复现代码：**

  ```python
  import threading
  import asyncio
  from dataclasses import dataclass
  from concurrent.futures import Future, ThreadPoolExecutor

  @dataclass
  class SubTask:
      id: str
      prompt: str
      cancel_event: threading.Event = None
      def __post_init__(self):
          if self.cancel_event is None:
              self.cancel_event = threading.Event()

  class AgentDelegator:
      def __init__(self, max_concurrent: int = 3):
          self._pool = ThreadPoolExecutor(max_workers=max_concurrent)
          self._tasks: dict[str, SubTask] = {}
          self._loop = self._ensure_loop()

      def delegate(self, prompt: str, tools: list, parent_context: dict) -> str:
          """委派任务给子 Agent，返回 task_id"""
          task = SubTask(id=generate_id(), prompt=prompt)
          self._tasks[task.id] = task

          # yyds: 递归防护 — 从工具列表中移除 delegation 工具
          safe_tools = [t for t in tools if t.name != "delegate"]

          def run():
              return self._loop.run_until_complete(
                  self._run_sub_agent(task, safe_tools, parent_context)
              )
          self._pool.submit(run)
          return task.id

      async def _run_sub_agent(self, task, tools, ctx):
          """在隔离环境里跑子 Agent"""
          agent = create_agent(
              model=ctx.get("model"),
              tools=tools,
              system_prompt=ctx.get("system_prompt"),
          )
          result_chunks = []
          async for chunk in agent.astream({"messages": [("user", task.prompt)]}):
              if task.cancel_event.is_set():  # yyds: 协作式取消
                  return "CANCELLED"
              result_chunks.append(chunk)
          return result_chunks[-1] if result_chunks else "EMPTY"

      def cancel(self, task_id: str):
          if task_id in self._tasks:
              self._tasks[task_id].cancel_event.set()

      def poll(self, task_id: str) -> dict:
          """轮询结果（task_tool 每 5 秒调一次）"""
          task = self._tasks.get(task_id)
          return {"id": task_id, "status": "running"} if task else {"id": task_id, "status": "not_found"}
  ```

**什么时候用：**
  ✓ Lead Agent 需要委派子任务给专门的 Agent
  ✓ 任务可以并行化（多个独立子任务）
  ✓ 子任务需要不同于主 Agent 的工具集/模型/prompt
  ✗ 子任务很简单（直接在主 Agent 里调工具就行，不需要委派）
  ✗ 需要跨进程/跨机器的分布式任务（用 Celery/Ray）
  ✗ 子任务间需要严格的事务一致性（Agent 系统不适合强一致性场景）

**在哪里见过：**

- DeerFlow: `tools/builtins/task_tool.py` — 完整委派 + 5s 轮询 + SSE 进度 + 递归防护
- DeerFlow: `subagents/executor.py` — 隔离 loop + copy_context + 协作式取消
- DeerFlow: `subagents/config.py:65` — `disallowed_tools=["task"]` 配置级递归防护
- CrewAI: `Crew` → `Agent` 委派 — 类似的任务分解模式，但 CrewAI 是同步的
- AutoGen: `GroupChat` + `Agent` 委派 — 多 Agent 协作，但没有 DeerFlow 这么强的隔离
- Claude Code: sub-agent 调用 — 类似的 "task tool + 轮询" 模式

---

### 7. 护栏自适应拒绝 / Guardrail with Adaptive Rejection

`resilience`

**解决什么问题：**
  LLM Agent 可能调用危险工具（删库、执行恶意命令、访问未授权文件）。
  传统方案：拦截 + 拒绝 + 抛异常 → Agent 崩溃 → 用户体验差。

  DeerFlow 的方案：拦截 + 拒绝 + **返回错误 ToolMessage** → Agent 看到错误
  → **自己换一种方式**。这不是简单的"拒绝"，这是"教 Agent 自适应"。

  为什么不用黑白名单直接阻止？→ 阻止后 Agent 不知道为什么被阻止，会重试同样的操作。
  为什么不用人工审批？→ 企业场景需要无人值守，程序化决策（毫秒级）优于人工（分钟级）。
  为什么不是 fail-open？→ 安全场景宁可误拦不能漏放。

**怎么解决的：**

  ```
  GuardrailMiddleware.wrap_tool_call() 的拦截流程：

  Agent 想调 write_file("/etc/passwd", "hacked")
    → GuardrailMiddleware 拦截
    → build GuardrailRequest(tool_name, tool_input, agent_id)
    → provider.evaluate(request)
      ├─ 正常返回 decision
      │   ├─ allow=True → 执行原始 handler → 返回结果
      │   └─ allow=False → 返回 ToolMessage(
      │       content="Guardrail denied: 'write_file' blocked (oap.tool_not_allowed).
      │                Reason: path outside sandbox. Choose alternative approach.",
      │       status="error")  ← Agent 看到这个，会自己换方式
      ├─ GraphBubbleUp → 直接 raise（LangGraph 控制流信号，不能吞）
      └─ Exception（provider 挂了）
          ├─ fail_closed=True → 拒绝（默认，安全优先）
          └─ fail_closed=False → 放行（可用性优先）

  自适应的关键：
    ToolMessage(status="error") 不是抛异常，而是作为工具调用的"返回值"。
    LLM 看到这个返回值，会理解"这个操作被拒绝了"，然后尝试其他方法。
    比如：write_file 被拒 → Agent 换成 read_file（只读操作，不触发护栏）。
  ```

  关键设计决策：

- **ToolMessage 不是 Exception** → LLM 能理解并自适应，不是直接崩溃
- **status="error"** → 明确告知 LLM 这是失败，不是正常返回
- **reason 包含具体原因** → Agent 知道为什么被拒，能选择更合适的替代方案
- **fail_closed 默认 True** → Provider 故障时宁可误拦（安全优先于可用性）
- **GraphBubbleUp 透传** → LangGraph 的 interrupt/pause 机制不能被护栏吞掉

**最小可复现代码：**

  ```python
  from dataclasses import dataclass
  from typing import Protocol, Callable

  @dataclass
  class Decision:
      allow: bool
      reason: str = ""
      policy_id: str | None = None

  class Evaluator(Protocol):
      def evaluate(self, tool_name: str, tool_input: dict) -> Decision: ...

  class Guardrail:
      def __init__(self, evaluator: Evaluator, *, fail_closed: bool = True):
          self._evaluator = evaluator
          self._fail_closed = fail_closed

      def wrap_tool_call(
          self, tool_name: str, tool_input: dict, handler: Callable
      ) -> dict:
          """拦截工具调用：评估 → 拒绝/放行"""
          try:
              decision = self._evaluator.evaluate(tool_name, tool_input)
          except Exception:
              if self._fail_closed:
                  decision = Decision(allow=False, reason="evaluator error (fail-closed)")
              else:
                  return handler(tool_name, tool_input)  # yyds: 放行

          if not decision.allow:
              # yyds: 不抛异常，返回 ToolMessage 让 Agent 自适应
              return {
                  "content": f"Guardrail denied: '{tool_name}' blocked. "
                             f"Reason: {decision.reason}. Choose alternative.",
                  "status": "error",
              }
          return handler(tool_name, tool_input)
  ```

**什么时候用：**
  ✓ Agent 有危险工具（文件操作、命令执行、网络请求）且需要无人值守运行
  ✓ 企业场景（合规要求、权限隔离、审计追踪）
  ✓ 多租户 Agent（不同用户/角色有不同的工具权限）
  ✗ 所有工具都安全的场景（不需要护栏）
  ✗ 人工审批可接受的场景（opencode 模式就是人肉审批）

**在哪里见过：**

- DeerFlow: `guardrails/middleware.py` — wrap_tool_call + fail_closed + GraphBubbleUp 透传
- DeerFlow: `guardrails/provider.py` — GuardrailProvider Protocol + GuardrailDecision 数据结构
- OpenAI: Moderation API — 类似的内容过滤，但返回的是拒绝消息而非工具拦截
- AWS Bedrock: Guardrails — 同样的 "拦截 + 拒绝 + 引导" 模式
- Nginx: `auth_request` — 上游 5xx → 返回 403（fail-closed）

---

### 8. 三层 Agent 配置 / Three-Layer Agent Config Cascade

`structure`

**解决什么问题：**
  Agent 系统有两种 Agent：
    - 内置 Agent（general-purpose、bash）→ 由开发者定义，有合理的默认值
    - 用户自定义 Agent（code-reviewer、security-scanner）→ 由用户通过 config.yaml 创建
  全局配置（如 timeout=600s）应该影响内置 Agent，但**不应该覆盖用户自定义 Agent 的默认值**。
  如果用户给 code-reviewer 设了 timeout=300s，全局改成 600s 不应该把 code-reviewer 也改成 600s。

  为什么不用简单的"后者覆盖前者"？→ 用户自定义 Agent 的默认值是"用户有意识设定的"，
  全局默认是"平台级的"，后者不应该覆盖前者。否则用户每次调全局默认都要检查所有自定义 Agent。

**怎么解决的：**

  ```
  三层合并：

  Layer 1: 代码默认值（SubagentConfig 的 dataclass 默认值）
    general-purpose: timeout=300, max_turns=10
    bash:            timeout=120, max_turns=5

  Layer 2: 全局配置（config.yaml subagents 段）
    subagents:
      timeout_seconds: 600  ← 只影响内置 Agent
  → general-purpose: timeout=600（被全局覆盖）
  → bash:            timeout=600（被全局覆盖）
  → code-reviewer:   timeout=300（不受影响！用户自己的默认值）

  Layer 3: 按名称覆盖（config.yaml agents 段）
    agents:
      general-purpose:
        timeout_seconds: 1200
      bash:
        model: "claude-sonnet"
  → general-purpose: timeout=1200（Layer 3 覆盖 Layer 2）
  → bash: model=claude-sonnet（新增字段）
  → code-reviewer: 不受影响

  覆盖优先级：Layer 3 > Layer 2（仅内置）> Layer 1
  ```

  关键设计决策：

- **is_builtin 标志** → 决定是否应用 Layer 2
- **dataclass.replace()** → 创建新对象，不修改原始配置（不可变语义）
- **逐字段覆盖** → 只改用户想改的字段，不是整体替换
- **model 和 skills 没有 Layer 2** → 只有 per-name override，没有全局默认

**最小可复现代码：**

  ```python
  from dataclasses import dataclass, replace

  @dataclass
  class AgentConfig:
      name: str
      timeout: int = 300
      max_turns: int = 10
      model: str | None = None

  # Layer 1: 内置 Agent 的默认值
  BUILTIN_AGENTS = {
      "general": AgentConfig("general", timeout=300),
      "bash": AgentConfig("bash", timeout=120, max_turns=5),
  }

  # 用户自定义 Agent（config.yaml 里配的）
  CUSTOM_AGENTS = {
      "code-reviewer": AgentConfig("code-reviewer", timeout=180),
  }

  def resolve_config(
      name: str,
      *,
      global_timeout: int = 600,       # Layer 2: 全局配置
      per_name_overrides: dict | None, # Layer 3: 按名称覆盖
  ) -> AgentConfig | None:
      config = BUILTIN_AGENTS.get(name) or CUSTOM_AGENTS.get(name)
      if config is None:
          return None

      patch = {}
      is_builtin = name in BUILTIN_AGENTS

      # Layer 2: 全局配置只影响内置 Agent
      if is_builtin and global_timeout != config.timeout:
          patch["timeout"] = global_timeout

      # Layer 3: per-name override 影响所有 Agent
      if per_name_overrides and name in per_name_overrides:
          for field, value in per_name_overrides[name].items():
              if value is not None:
                  patch[field] = value

      return replace(config, **patch) if patch else config

  # yyds: 验证
  assert resolve_config("general", global_timeout=600, per_name_overrides=None).timeout == 600
  assert resolve_config("code-reviewer", global_timeout=600, per_name_overrides=None).timeout == 180  # 不受全局影响！
  assert resolve_config("bash", global_timeout=600, per_name_overrides={"bash": {"model": "sonnet"}}).model == "sonnet"
  ```

**什么时候用：**
  ✓ Agent 系统有内置 + 用户自定义两类组件
  ✓ 全局默认不应该覆盖用户有意识设定的值
  ✓ 类似 CSS 层叠/Spring profile 的多级配置场景
  ✗ 所有组件共享同一套默认值（不需要区分 builtin/custom）
  ✗ 配置层级只有两层的简单场景（过度设计）

**在哪里见过：**

- DeerFlow: `subagents/registry.py:99-158` — get_subagent_config，is_builtin 分支 + dataclass.replace
- CSS 层叠：user-agent → user → author → `!important`
- Spring Boot: default → application.yml → profile-specific → environment variables
- Kubernetes: namespace limit → pod spec → container spec（越具体优先级越高）

---

# Part 2: 通用基础设施模式

> 这些不是 AI Agent 特有的，但在 Agent 系统中频繁使用。简明参考，不展开讲。

---

### 9. 去抖动队列 / Debounce Queue

`concurrency` `Memory`

**一句话**：攒一波再调 LLM，省 token。

**核心**：`add()` 重置 N 秒倒计时，到期才处理。`add_nowait()` 0 秒倒计时，用于抢救场景。

```
add() → 存 item → 重启 30s 倒计时 → 到期 → handler(item)
add_nowait() → 存 item → 0s 倒计时 → 立即 handler(item)
```

**关键行**：

```python
class DebounceQueue:
    def add(self, key, item):
        with self._lock:
            self._queue = [i for i in self._queue if i["key"] != key]
            self._queue.append(item)
            self._restart_timer()  # yyds: 重置 30s

    def add_nowait(self, key, item):
        with self._lock:
            self._queue = [i for i in self._queue if i["key"] != key]
            self._queue.append(item)
            self._schedule_timer(0)  # yyds: 0s，立即处理
```

**在哪**：`agents/memory/queue.py` — 30s 去抖动 + 双模式 + 信号合并

---

### 10. 原子文件写入 / Atomic File Write

`resilience` `Memory`

**一句话**：先写 .tmp 再 rename，防崩溃丢数据。

**核心**：`rename()` 是 OS 级原子操作，要么成功要么没发生。

```python
def atomic_write_json(file_path: Path, data: dict) -> bool:
    temp_path = file_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    with open(temp_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    temp_path.replace(file_path)  # yyds: 原子操作
    return True
```

**在哪**：`agents/memory/storage.py` — memory.json 写入 + mtime 缓存更新

---

### 11. 线程安全懒单例 / Thread-Safe Lazy Singleton

`lifecycle` `Memory / Sub-Agent / Sandbox / Config`

**一句话**：全局共享资源 + 懒加载 + Double-Check Locking + reset() 给测试用。

**核心**：快路径无锁，慢路径加锁 + Double-Check。

```python
_instance = None
_lock = threading.Lock()

def get_instance():
    global _instance
    if _instance is not None:
        return _instance  # yyds: 快路径，无锁
    with _lock:
        if _instance is not None:
            return _instance  # yyds: Double-Check
        _instance = create()
        return _instance
```

**在哪**：`agents/memory/queue.py:get_memory_queue()` / `agents/memory/storage.py:get_memory_storage()` / `sandbox/sandbox_provider.py:get_sandbox_provider()` / `config/app_config.py:get_app_config()`

---

### 12. ContextVar 请求隔离 / ContextVar Per-Request Isolation

`concurrency` `工具系统 / Config`

**一句话**：async 请求间状态隔离，比 global 安全比参数简洁。

**核心**：每个 async context 独立一份值，`copy_context()` 跨线程传播。

```python
current_user: ContextVar[str | None] = ContextVar("current_user", default=None)

async def handle_request(user: str):
    current_user.set(user)          # ← 绑定到当前 async 上下文
    await do_something()            # ← 内部 get() 拿到正确的值

def run_in_thread():
    ctx = copy_context()            # yyds: 捕获 ContextVar
    threading.Thread(target=ctx.run, args=[work]).start()
```

**在哪**：`tools/builtins/tool_search.py` — DeferredToolRegistry 隔离 / `config/app_config.py` — 配置覆盖 push/pop 栈
