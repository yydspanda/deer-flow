# Claude Code (Hermes) 可借鉴的 Agent 设计模式

> 研究 Claude Code 源码（restored-src/），提取 DeerFlow 框架不具备的工程化模式，用于 SOC Agent 设计参考。
> 整理：2026-06-17

---

## 一、多级权限决策管道（Permission Decision Pipeline）

### 代码位置

| 文件 | 关键内容 |
|---|---|
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/types/permissions.ts:24-29` | `PermissionMode`: default / plan / acceptEdits / dontAsk / auto |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/types/permissions.ts:44` | `PermissionBehavior`: allow / deny / ask |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/Tool.ts:123-138` | `ToolPermissionContext` 类型定义 |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/hooks/useCanUseTool.tsx:28-191` | `useCanUseTool()` 完整决策流程 |

### 决策链路

```
rule match → mode override → classifier (async) → hook → user prompt
     ↓              ↓               ↓              ↓         ↓
  allow/deny    dontAsk/auto   speculative      pre-tool   dialog
```

每一步都可以短路整条链路。

### 对 SOC Agent 的映射

```
lessons_learned → 运行模式(daemon/cli) → 分类器预判 → hook 检查 → 人工确认
     ↓                    ↓                  ↓           ↓          ↓
  auto_close        daemon=autoClose    similarity>0.9   威胁情报   escalate
```

你现有的 ⑥ 只有 3 档（auto_close / escalate / human），可以扩展为 5 层决策链。

---

## 二、异步分类器 + 投机预判（Async Classifier with Speculative Check）

### 代码位置

| 文件 | 关键内容 |
|---|---|
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/tools/BashTool/bashPermissions.ts:1483-1587` | `speculativeChecks` Map + `consumeSpeculativeClassifierCheck()` |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/tools/BashTool/bashPermissions.ts:1555-1587` | `awaitClassifierAutoApproval()` |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/tools/BashTool/bashPermissions.ts:1605-1658` | `executeAsyncClassifierCheck()` 后台异步执行 |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/classifierApprovals.ts:15-17` | `CLASSIFIER_CHECKING` Set + `classifierChecking` Signal |

### 核心流程

```
用户触发 Bash → 启动投机分类器（后台跑）→ 弹出确认框
                                          ↓
                      分类器返回 high confidence 且用户未操作？
                         YES → 自动批准，对话框消失
                         NO  → 用户正常确认
```

`speculativeChecks` Map 缓存正在进行中的分类请求，避免相同命令重复调 API。

### 对 SOC Agent 的映射

放到流水线步骤 ②③ 之间：

```python
# 在步骤 ② 经验快查和 ③ 关联查询的同时，后台跑分类器
async def handle_alert(alert):
    speculative = asyncio.create_task(classifier.prejudge(alert))
    
    lesson = check_lessons(alert)        # ② 同步
    history = correlate(alert)           # ③ 同步
    
    classifier_result = await speculative  # 等待分类器
    if classifier_result.confidence == "high" and classifier_result.action == "auto_close":
        return skip_llm_and_close(alert, classifier_result)
    
    # 继续走 ④⑤ 正常流水线
```

效果：很多低风险预警在 LLM 调用前就被过滤。

---

## 三、Signal 响应式事件系统

### 代码位置

| 文件 | 关键内容 |
|---|---|
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/signal.ts:18-26` | `Signal<Args>` 类型定义 |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/signal.ts:27-43` | `createSignal()` 实现 |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/mailbox.ts:19-73` | `Mailbox` 类（基于 Signal） |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/classifierApprovals.ts:17` | `classifierChecking = createSignal()` 使用示例 |

### 核心实现（20 行）

```typescript
export function createSignal<Args extends unknown[] = []>(): Signal<Args> {
  const listeners = new Set<(...args: Args) => void>()
  return {
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener) },
    emit(...args)       { for (const l of listeners) l(...args) },
    clear()             { listeners.clear() },
  }
}
```

### 对 SOC Agent 的映射

替换流水线步骤间的轮询 / 文件监听：

```python
# 主 Agent 内的 Signal 总线
step_started = Signal()     # 步骤开始
step_completed = Signal()   # 步骤完成
alert_dispatched = Signal() # 预警分配给子 Agent
alert_completed = Signal()  # 子 Agent 返回结论

# 大屏订阅
alert_completed.subscribe(lambda result: dashboard.update(result))
```

LangGraph StateGraph 节点间传递的是 State 对象，Signal 提供**带外的、非 State 的事件通知**。

---

## 四、Hook 三阶段生命周期 + 进度流式输出

### 代码位置

| 文件 | 关键内容 |
|---|---|
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/hooks/hookEvents.ts:22-28` | `HookStartedEvent` 类型 |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/hooks/hookEvents.ts:29-37` | `HookProgressEvent` 类型（含 stdout/stderr） |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/hooks/hookEvents.ts:39-49` | `HookResponseEvent` 类型（含 exitCode/outcome） |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/hooks/hookEvents.ts:51-54` | `HookExecutionEvent` 联合类型 |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/hooks/hookEvents.ts:93-106` | `emitHookStarted()` |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/hooks/hookEvents.ts:108-151` | `emitHookProgress()` + `startHookProgressInterval()` 每秒轮询 |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/hooks/hookEvents.ts:153-177` | `emitHookResponse()` |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/hooks/hookEvents.ts:61-81` | `registerHookEventHandler()` + pending queue 缓冲 |

### 生命周期

```
HookStarted → HookProgress (每秒) → HookProgress (每秒) → HookResponse
   (hookId)      (stdout, stderr)      (stdout, stderr)      (exitCode, outcome)
```

不是简单的 before/after。每个阶段都是独立事件，可以有独立的 handler。

### 对 SOC Agent 的映射

每个流水线步骤都可以有 hook：

```python
class PipelineStep:
    hooks: list[StepHook]
    
    async def execute(self, state):
        for hook in self.hooks:
            hook.emit_started(step=self.name, alert_id=state.alert_id)
        
        for progress in self.run(state):
            for hook in self.hooks:
                hook.emit_progress(progress)
        
        for hook in self.hooks:
            hook.emit_response(result=result)

# 例如：④ 漏斗关联 LLM 调用期间，大屏可以看到实时进度
# "Phase 1 粗筛中... 已收到 30 条摘要，LLM 正在筛选"
```

---

## 五、多级优雅关闭（Graceful Shutdown with Failsafe）

### 代码位置

| 文件 | 关键内容 |
|---|---|
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/gracefulShutdown.ts:391-523` | `gracefulShutdown()` 完整实现 |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/gracefulShutdown.ts:416-425` | failsafe 定时器：`max(5s, hook_budget + 3.5s)` |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/gracefulShutdown.ts:443-467` | cleanup 阶段（2s timeout） |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/gracefulShutdown.ts:469-480` | SessionEnd hooks 阶段（可配置 timeout） |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/gracefulShutdown.ts:504-510` | analytics flush（500ms timeout） |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/gracefulShutdown.ts:525-529` | `CleanupTimeoutError` |

### 关键设计

```
阶段 1: cleanupTerminalModes() + printResumeHint()  ← 立即执行，不依赖 I/O
阶段 2: runCleanupFunctions()                       ← 2s timeout
阶段 3: executeSessionEndHooks()                    ← 可配置 timeout，默认 1.5s
阶段 4: flushAnalytics()                            ← 500ms timeout
阶段 5: forceExit()

全程 failsafe: max(5s, hook_budget + 3.5s) 后强制退出
```

每阶段独立 timeout，任意阶段超时不影响后续阶段的 try-catch。failsafe 定时器保证进程最终一定退出。

### 对 SOC Agent 的映射

Daemon 收到 SIGTERM 时：

```python
async def graceful_shutdown():
    sigterm_received.set()  # 通知 Kafka consumer 停止消费
    
    # 阶段 1: 等待当前处理中的预警完成（30s timeout）
    await asyncio.wait_for(wait_active_subagents(), timeout=30)
    
    # 阶段 2: 提交 Kafka offset（2s timeout）
    await asyncio.wait_for(consumer.commit(), timeout=2)
    
    # 阶段 3: 标记未完成子 Agent 为 interrupted（1s timeout）
    await asyncio.wait_for(mark_interrupted(), timeout=1)
    
    # 阶段 4: 清理 
    await asyncio.wait_for(cleanup(), timeout=2)
    
    # failsafe: 60s 后无论如何退出
    sys.exit(0)
```

---

## 六、Mailbox 消息队列模式

### 代码位置

| 文件 | 关键内容 |
|---|---|
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/mailbox.ts:1-5` | `MessageSource` / `Message` 类型 |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/mailbox.ts:19-73` | `Mailbox` 类 |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/mailbox.ts:33-46` | `send()`: 先查 waiters，命中直接 resolve，否则入 queue |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/mailbox.ts:54-65` | `receive()`: 查 queue，有则返回，无则创建 waiter |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/mailbox.ts:48-52` | `poll()`: 非阻塞检查 |

### 核心设计

```typescript
class Mailbox {
  queue: Message[]      // 未消费的消息
  waiters: Waiter[]     // 等待特定消息的消费者（{fn, resolve}）
  changed: Signal       // 状态变更通知（用于 subscribe）
  
  send(msg)  → 先查 waiters，命中则 resolve waiter，否则入 queue
  receive(fn) → 查 queue，有则返回，无则创建 waiter 等待
  poll(fn)   → 非阻塞查 queue，不创建 waiter
}
```

不同于普通队列：`send` 优先匹配等待者（`waiters.findIndex`），避免消息入队再出队的延迟。

### 对 SOC Agent 的映射

子 Agent 和主 Agent 之间的通信：

```python
class AgentMailbox:
    queue: deque[Message]
    waiters: dict[str, asyncio.Future]  # key = waiter_id
    
    async def send(self, msg):
        # 优先匹配等待者
        for waiter_id, future in list(self.waiters.items()):
            if future.done():
                continue
            if matches(msg, waiter_id):
                future.set_result(msg)
                return
        self.queue.append(msg)
        await self.signal.emit()
    
    async def receive(self, filter_fn) -> Message:
        for msg in self.queue:
            if filter_fn(msg):
                self.queue.remove(msg)
                return msg
        # 创建 waiter
        future = asyncio.Future()
        self.waiters[id(filter_fn)] = future
        return await future
```

---

## 七、EndTruncatingAccumulator — 大字符串安全截断

### 代码位置

| 文件 | 关键内容 |
|---|---|
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/stringUtils.ts:140-220` | `EndTruncatingAccumulator` 类 |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/stringUtils.ts:149` | `constructor(maxSize)` |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/stringUtils.ts:156-176` | `append(data)`: 超限截断尾部，保留头部 |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/stringUtils.ts:181-189` | `toString()`: 自动追加 `[output truncated - XXKB removed]` |

### 核心逻辑

```
append("hello")   → content = "hello"
append(" world")  → content = "hello world"
append("VERY_LONG_STRING") → 超限 → content = "hello world" (头部保留)
                                      isTruncated = true
toString()        → "hello world\n... [output truncated - 500KB removed]"
```

关键设计：**截断尾部保留头部**（不是截中间），因为程序输出开头最重要。且 `toString()` 自动附带头部大小信息。

### 对 SOC Agent 的映射

步骤 ⑦ 写入存储时，alert 完整日志可能很大：

```python
class EndTruncatingAccumulator:
    def __init__(self, max_chars=10000):
        self.content = ""
        self.truncated = False
        self.total = 0
    
    def append(self, data: str):
        self.total += len(data)
        if self.truncated:
            return
        if len(self.content) + len(data) > self.max_chars:
            remaining = self.max_chars - len(self.content)
            self.content += data[:remaining]
            self.truncated = True
        else:
            self.content += data
    
    def to_string(self):
        if not self.truncated:
            return self.content
        truncated_kb = round((self.total - self.max_chars) / 1024)
        return f"{self.content}\n... [truncated {truncated_kb}KB]"

# 使用：写入 alert_summaries.summary 时，自动截断过长的分析结论
acc = EndTruncatingAccumulator(500)
acc.append(llm_analysis["investigation"])
db.insert("alert_summaries", summary=acc.to_string())
```

---

## 八、ToolResult.contextModifier — 执行结果回写上下文

### 代码位置

| 文件 | 关键内容 |
|---|---|
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/Tool.ts:321-336` | `ToolResult<T>` 类型定义 |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/Tool.ts:329-330` | `contextModifier?: (context: ToolUseContext) => ToolUseContext` |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/services/tools/toolExecution.ts:342-348` | `executeToolUse()` 中 `contextModifier` 的调用时机 |

### 核心设计

```typescript
type ToolResult<T> = {
  data: T
  newMessages?: (...)
  contextModifier?: (context: ToolUseContext) => ToolUseContext  // ← 关键
}
```

Tool 执行完后，除了返回 `data`，还可以返回一个函数来修改后续所有步骤的共享上下文。例如 Bash 执行后 `cd /foo`，则后续所有路径操作都基于 `/foo`。

### 对 SOC Agent 的映射

```python
class AlertAnalysisResult:
    data: dict           # verdict + confidence + summary
    context_modifier: Callable[[MainAgentContext], MainAgentContext] | None

# 步骤 ④ 漏斗关联发现新威胁情报：
def funnel_correlation(alert, history):
    result = llm.call(...)
    iocs = result.get("new_iocs", [])
    
    def modifier(ctx):
        # 更新主 Agent 的实时威胁索引
        for ioc in iocs:
            ctx.threat_index.add(ioc)
        ctx.recent_window.append(alert.digest())
        return ctx
    
    return AlertAnalysisResult(data=result, context_modifier=modifier)
```

---

## 九、System Prompt 前缀缓存感知

### 代码位置

| 文件 | 关键内容 |
|---|---|
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/api.ts:119-266` | `toolToAPISchema()` 含缓存逻辑 |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/api.ts:147-152` | cacheKey 构建（含 inputJSONSchema） |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/api.ts:207-209` | cache.set/get |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/services/api/claude.ts:333-434` | `getPromptCachingEnabled()` + `should1hCacheTTL()` |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/services/api/claude.ts:1373-1379` | `buildSystemPromptBlocks()` 按 block 标记 cache_control |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/QueryEngine.ts:316-325` | `asSystemPrompt()` 组装多段 prompt |

### 核心设计

System prompt 拆分为多个 block，每个 block 独立标记 `cache_control`:

```
block 1: 默认 system prompt (ephemeral cache)
block 2: CLAUDE.md (ephemeral cache)       ← 不变，可以缓存
block 3: memory.json facts                 ← 可能变，不缓存
block 4: append system prompt              ← 自定义，不缓存
block 5: memory mechanics prompt           ← 条件注入
```

### 对 SOC Agent 的映射

步骤 ⑤ LLM 综合分析时：

```python
def build_prompt(alert, soul, memory_facts, correlation):
    cacheable_blocks = [
        system_base_prompt(),      # 固定，可缓存
        soul.content(),            # 不变，可缓存
    ]
    volatile_blocks = [
        format_memory_facts(memory_facts),  # 每次都变
        format_correlation(correlation),    # 每条预警不同
        format_alert(alert),                # 输入
    ]
    
    # 使用 Anthropic 的 cache_control
    return [{"type": "text", "text": b, "cache_control": {"type": "ephemeral"}}
            for b in cacheable_blocks] + \
           [{"type": "text", "text": b}
            for b in volatile_blocks]
```

soul 不变时命中缓存，每次都省 ~2K tokens。

---

## 十、查询引擎生命周期分离（QueryEngine Pattern）

### 代码位置

| 文件 | 关键内容 |
|---|---|
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/QueryEngine.ts:130-173` | `QueryEngineConfig` — 所有依赖注入 |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/QueryEngine.ts:184-207` | `QueryEngine` 类定义 + constructor |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/QueryEngine.ts:200-206` | constructor 中初始化：mutableMessages, abortController, permissionDenials |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/QueryEngine.ts:209-212` | `submitMessage()` generator — 每次调用产生一个新 turn |

### 核心设计

```
QueryEngine
├── config: QueryEngineConfig          ← 只读配置
├── mutableMessages: Message[]         ← 跨 turn 持久
├── abortController: AbortController   ← 可取消
├── permissionDenials: [...]           ← 跨 turn 累积
├── readFileState: FileStateCache      ← 跨 turn 共享
├── totalUsage: Usage                  ← 跨 turn 累积
└── discoveredSkillNames: Set          ← 单 turn 生命周期
```

一个 Engine 实例对应一个「完整会话」，内部状态跨 turn 保持。每个 `submitMessage()` 是一个新 turn。

### 对 SOC Agent 的映射

主 Agent 持久，子 Agent 短命：

```python
class MainAgent:  # 对应 QueryEngine
    soul: str
    memory: MemoryManager
    recent_window: deque[AlertDigest]
    pattern_index: dict[str, list[str]]
    active_subs: dict[str, SubAgentHandle]
    
    async def on_alert(self, alert) -> AlertResult:
        # 每个 alert 对应一个 submitMessage() turn
        
class SubAgent:   # 对应单个 Tool call（但更复杂，有自己的流水线）
    def __init__(self, alert, context_ref, main_ctx):
        self.abort_controller = asyncio.TimeoutController(timeout=120)
    
    async def run(self) -> AnalysisResult:
        # 独立 7 步流水线
```

---

## 十一、Task 生命周期管理

### 代码位置

| 文件 | 关键内容 |
|---|---|
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/Task.ts:6-14` | `TaskType`: local_bash / local_agent / remote_agent / workflow / dream |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/Task.ts:15-21` | `TaskStatus`: pending → running → completed / failed / killed |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/Task.ts:27-29` | `isTerminalTaskStatus()` |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/Task.ts:72-76` | `Task` 接口：name, type, kill() |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/Task.ts:98-106` | `generateTaskId()` 带类型前缀 |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/tasks/stopTask.ts:10-18` | `StopTaskError` 带 typed error code |
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/tasks/stopTask.ts:38-100` | `stopTask()` 查找→验证→kill→清理 |

### 核心设计

```typescript
TaskStatus: pending → running → completed | failed | killed

interface Task {
  name: string
  type: TaskType
  kill(taskId: string, setAppState: SetAppState): Promise<void>
}
```

每个 Task 有独立 ID（类型前缀）、abortController、outputFile。

### 对 SOC Agent 的映射

```python
class AlertTask:
    id: str              # 类型前缀: "sub_" + uuid
    status: TaskStatus   # pending → running → completed / failed / killed
    alert: dict
    abort: asyncio.Event # 取消信号
    output: AnalysisResult | None
    
    async def kill(self):
        self.abort.set()
        self.status = "killed"
    
    def is_terminal(self) -> bool:
        return self.status in ("completed", "failed", "killed")
```

---

## 十二、锁定文件延迟加载 (Lazy Lockfile)

### 代码位置

| 文件 | 关键内容 |
|---|---|
| `/home/yydspei/projects/claude-code-sourcemap/restored-src/src/utils/lockfile.ts:14-43` | 完整实现 |

### 核心设计

```typescript
let _lockfile: Lockfile | undefined

function getLockfile(): Lockfile {
  if (!_lockfile) {
    _lockfile = require('proper-lockfile')  // 首次调用才加载
  }
  return _lockfile
}
```

`proper-lockfile` 依赖 `graceful-fs`，后者 monkey-patch 所有 fs 方法，首次 require 需要 ~8ms。延迟加载让 `--help` 等不需要锁的场景零开销。

### 对 SOC Agent 的映射

SOC Agent 的 heavy 依赖（威胁情报库、MCP client、模型客户端）都可以延迟加载：

```python
_threat_intel: ThreatIntel | None = None

def get_threat_intel() -> ThreatIntel:
    global _threat_intel
    if _threat_intel is None:
        _threat_intel = ThreatIntel(api_key=config.ti_api_key)
    return _threat_intel
```

---

## 合计优先级矩阵

| 优先级 | 模式 | 代码位置 | 对 SOC 的映射位置 | 复杂度 |
|---|---|---|---|---|
| P0 | 异步分类器 + 投机预判 | `bashPermissions.ts:1483-1587` | 步骤②③之间 | 中 |
| P0 | 多级权限决策管道 | `useCanUseTool.tsx:28-191` | 步骤⑥ 置信度决策 | 低 |
| P0 | 主 Agent + 子 Agent 编排 | `QueryEngine.ts:184-207` | 新增编排层 | 中 |
| P1 | Hook 三阶段生命周期 | `hookEvents.ts:22-177` | 每个 PipelineStep | 低 |
| P1 | Signal 响应式事件 | `signal.ts:27-43` | 步骤间通信 | 低 |
| P1 | Task 生命周期 | `Task.ts:6-76` | 子 Agent 管理 | 中 |
| P2 | Mailbox 消息队列 | `mailbox.ts:19-73` | 主-子 Agent 通信 | 中 |
| P2 | 多级优雅关闭 | `gracefulShutdown.ts:391-523` | daemon.py | 中 |
| P2 | 缓存感知 Prompt | `api.ts:119-266` | 步骤⑤ prompt 组装 | 中 |
| P2 | EndTruncatingAccumulator | `stringUtils.ts:140-220` | 步骤⑦ 写入 | 低 |
| P3 | contextModifier | `Tool.ts:329-330` | 步骤间上下文传递 | 低 |
| P3 | 延迟加载 | `lockfile.ts:14-43` | 全局 heavy 依赖 | 低 |
