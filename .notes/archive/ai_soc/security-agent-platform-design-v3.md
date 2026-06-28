# 安全预警 Agent 平台架构设计

> 场景：安全告警从 Kafka 流式过来 → Agent 7×24 自动研判 → 复杂告警升级人工 → 分析师也可主动调 Agent
> 模式：混合（全自动 + 人工协同）

---

## 一、背景与问题定义

### 1.1 业务场景

```
安全告警从 Kafka 流式过来 → Agent 7×24 自动研判 → 复杂告警升级人工 → 分析师也可主动调 Agent
模式：混合（全自动 + 人工协同）
```

### 1.2 核心挑战

```
为什么不是简单地在 DeerFlow 上加个 Kafka 消费者？

Claude Code / opencode（交互式编码工具）：
  用户 → CLI → Agent Loop → 工具调用 → 返回
  特点：单用户、交互式、短生命周期、人一直在

DeerFlow（通用 Agent 框架）：
  用户 → Frontend → Gateway → Agent Loop → 返回
  特点：多用户、请求-响应、Web + IM 多渠道

安全预警平台（7×24 事件驱动）：
  Kafka → 消费者 → Agent 自动研判 → 结果推出去
  同时：分析师 → CLI/Web → 手动调 Agent → 结果返回
  特点：事件驱动 + 请求-响应双模式、无人值守 + 人工兜底

核心区别：前两个是"人发起，Agent 执行"，安全平台是"事件发起 + 人也能发起，Agent 都能执行"。
```

### 1.3 参考项目的设计精髓

| 设计模式 | Claude Code | OpenClaw | opencode | 本平台采用 |
|---------|-------------|----------|----------|----------|
| 入口形态 | CLI + IDE | 25+ IM channels + CLI + Web | CLI + Desktop + IDE | **CLI-first + IM + API（Web 只是看板）** |
| 控制面 | 单进程 Agent Loop | Gateway daemon (WebSocket) | 单进程 | **Security Gateway daemon** |
| 生命周期拦截 | Hooks (4 个事件) | 无显式 hooks | 无 | **Hooks (6 个安全事件)** |
| 扩展机制 | Plugin = Cmd+Agent+Skill+Hook | Skills + Config routing | Skills | **Plugin = Cmd+Agent+Skill+Hook+ApprovalPolicy** |
| 多 Agent | Task tool dispatch | Multi-agent routing | Multi-session | **Multi-session + Agent routing** |+-
| 中途干预 | 无 | Steering (3 种模式) | 无 | **Steering + Approval Gate** |
| 沙箱 | OS sandbox | Docker sandbox per session | 无 | **Docker sandbox per session** |
| 记忆 | CLAUDE.md + conversation | Workspace files + session JSONL | 项目配置 | **Security playbook + alert history + session JSONL** |

---

## 二、核心设计原则

### 原则 1：CLI-first

```
分析师的主界面是终端，不是浏览器
  secops triage ALERT-1234       ← 一条命令完成研判
  secops approve TASK-5678       ← 一条命令审批处置
  secops steer SESS-9012 "..."   ← 中途注入指令

CLI > WebUI 的原因：
  1. 速度：一条命令 vs 点 5 下鼠标
  2. 可脚本化：for alert in $(secops list --priority high); do secops triage $alert; done
  3. 可组合：管道 + 重定向 + cron
  4. 肌肉记忆：分析师本来就用 Wireshark/tcpdump/nmap
  5. SSH 远程：手机 SSH 进来就能处理告警

Web 的定位（降级为展示面）：
  - 研判报告展示（大屏展示，格式化好看）
  - 统计仪表盘（告警趋势、处置率、MTTR）
  - 管理层看板（非技术人员看）
  - 新人培训（可视化学习告警处理流程）

架构分层：
  CLI = 操作面（分析师日常用）
  Web = 展示面（看板 + 报告 + 管理）
  IM  = 通知面（告警推送 + 快速审批）
```

### 原则 2：Gateway 是唯一的控制面

```
所有入口（Kafka / CLI / IM / API）→ 连到同一个 Security Gateway
Gateway 负责：Agent 调度 + Hook 执行 + Session 管理 + Steering + 审计日志 + 限流

为什么不用直接调 DeerFlow：
  直接调：CLI→DF / Kafka→DF / IM→DF → 没有统一 Hook 点 / Session 分散 / Steering 不可能
  通过 Gateway：所有入口→Gateway→Hook→Router→Worker(DF)→Hook→结果 → 统一控制
```

### 原则 3：Hooks 治理一切敏感操作

```
PreSession   → 去重、白名单过滤
PreToolUse   → 敏感工具审批、缓存检查
PostToolUse  → 结果增强、自动关联内部情报
PreResponse  → 置信度检查、升级人工
PostSession  → 报告生成、通知推送
OnApproval   → 审批流程编排

为什么不用硬编码审批：
  硬编码：每加一个新工具要改代码，不同客户策略不同，无法热加载
  Hooks：加新工具不改代码（hooks.yaml 配置），不同客户有不同的配置，可以热加载
```

### 原则 4：Steering 实现真正的人工协同

```
Agent 在跑的过程中，分析师随时可以 inject 指令
  "这个 IP 是蜜罐，别封"
  "查一下同一子网有没有其他被感染的"
不需要等 Agent 跑完再干预 → 省 50% 的 API 额度和时间
→ 真正的 Human-in-the-Loop（不是 Human-at-the-End）
```

### 原则 5：Plugin = 场景化扩展

```
phishing-plugin = triage-cmd + phishing-agent + email-analysis-skill + approval-hook
不是零散的功能点，是完整的场景解决方案
```

---

## 三、系统架构总览

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Clients（CLI-first）                          │
│                                                                         │
│   ┌──────────────────────────────────────────────────────────────────┐ │
│   │  secops CLI（分析师的一等公民界面）                                │ │
│   │                                                                  │ │
│   │  $ secops triage ALERT-1234          研判告警                     │ │
│   │  $ secops investigate ALERT-1234     深度调查                     │ │
│   │  $ secops approve TASK-5678          审批处置                     │ │
│   │  $ secops reject TASK-5678           拒绝处置                     │ │
│   │  $ secops steer SESS-9012 "..."      中途注入指令                 │ │
│   │  $ secops sessions                  列出所有活跃 session          │ │
│   │  $ secops dashboard                 打开 Web 看板（可选）         │ │
│   │  $ secops hunt --query "..."         主动威胁狩猎                 │ │
│   │  $ secops audit-log --today          查看审计日志                 │ │
│   └──────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐             │
│   │ 飞书/钉钉 │  │ WebChat  │  │ REST API │  │ Kafka    │             │
│   │ @secops  │  │ (看板)   │  │ (外部系统)│  │ (告警流) │             │
│   └──────────┘  └──────────┘  └──────────┘  └──────────┘             │
└─────────────────────────────┬───────────────────────────────────────────┘
                              │
                  ┌───────────┴───────────┐
                  │  Security Gateway      │
                  │  (长驻 daemon)          │
                  │                        │
                  │  ┌──────────────────┐  │
                  │  │  Hook Engine     │  │    ← 6 个安全生命周期事件
                  │  └──────────────────┘  │
                  │  ┌──────────────────┐  │
                  │  │  Agent Router    │  │    ← 告警类型 → Agent 映射
                  │  └──────────────────┘  │
                  │  ┌──────────────────┐  │
                  │  │  Priority Queue  │  │    ← 优先级排队 + 限流
                  │  │  + Rate Limiter  │  │
                  │  └──────────────────┘  │
                  │  ┌──────────────────┐  │
                  │  │  Dedup Engine    │  │    ← 告警去重 & 合并
                  │  └──────────────────┘  │
                  │  ┌──────────────────┐  │
                  │  │  Steering Queue  │  │    ← 中途注入 & 审批
                  │  └──────────────────┘  │
                  │  ┌──────────────────┐  │
                  │  │  Session Manager │  │    ← JSONL transcript + audit
                  │  └──────────────────┘  │
                  │  ┌──────────────────┐  │
                  │  │  Worker Pool     │  │    ← DeerFlow Harness
                  │  └──────────────────┘  │
                  └───────────┬───────────┘
                              │
             ┌────────────────┼────────────────┐
             ▼                ▼                ▼
      ┌────────────┐  ┌────────────┐  ┌────────────┐
      │ triage     │  │ investigate│  │ response   │
      │ Agent      │  │ Agent      │  │ Agent      │
      │ SOUL.md    │  │ SOUL.md    │  │ SOUL.md    │
      │ 5 tools    │  │ 8 tools    │  │ 6 tools    │
      └────────────┘  └────────────┘  └────────────┘
      ┌────────────┐  ┌────────────┐
      │ malware    │  │ threat-intel│
      │ Agent      │  │ Agent      │
      │ SOUL.md    │  │ SOUL.md    │
      │ 5 tools    │  │ 4 tools    │
      │ sandboxed  │  │            │
      └────────────┘  └────────────┘
             │                │                │
             └────────────────┼────────────────┘
                              │
                  ┌───────────┴───────────┐
                  │  Tool & Knowledge Layer│
                  │  MCP + Internal Tools  │
                  └───────────┬───────────┘
                              │
             ┌────────────────┼────────────────┐
             ▼                ▼                ▼
      ┌────────────┐  ┌────────────┐  ┌────────────┐
      │ Kafka      │  │ PostgreSQL │  │ Qdrant     │
      │ (告警输入)  │  │ (结果存储)  │  │ (RAG 知识) │
      └────────────┘  └────────────┘  └────────────┘
```

---

## 四、数据模型

### 4.1 SecurityTask — 统一任务模型

```python
@dataclass
class SecurityTask:
    task_id: str
    source: str              # "kafka" / "cli" / "im" / "api"
    alert_type: str          # "phishing" / "lateral_movement" / "malware" / "brute_force" / ...
    priority: str            # "critical" / "high" / "medium" / "low"
    raw_alert: dict          # 原始告警 JSON
    context: dict            # 附加上下文（历史告警、关联资产等）
    created_at: datetime
    dedup_key: str | None    # 去重键（同 IP + 同 IOC + 5min 窗口）
```

### 4.2 SecuritySession — 会话模型

```python
@dataclass
class SecuritySession:
    session_id: str
    task: SecurityTask
    agent_name: str
    mode: str                # "auto"（Kafka 触发）/ "interactive"（人触发）
    status: str              # "running" / "waiting_approval" / "completed" / "failed" / "approval_timeout"
    created_at: datetime
    completed_at: datetime | None
    transcript_path: str     # JSONL transcript 文件路径
    approval_timeout: int    # 秒，等待审批的超时时间
    metadata: dict           # 扩展字段（置信度、风险等级等）
```

### 4.3 HookResult — Hook 返回值

```python
@dataclass
class HookResult:
    action: str              # "allow" / "block" / "warn" / "modify" / "defer"
    reason: str | None
    modifications: dict | None  # action="modify" 时修改的参数
    approval_required: bool     # action="defer" 时是否需要人工审批
    timeout: int | None         # 审批超时秒数
    notify: dict | None         # 通知配置（channel, message）
```

### 4.4 SteeringItem — 中途注入消息

```python
@dataclass
class SteeringItem:
    message: str
    mode: str                # "steer"（立即注入）/ "followup"（等当前轮结束）
    approval: bool           # 是否是审批结果
    timestamp: datetime
    source: str              # "analyst" / "system"
```

---

## 五、六大核心子系统

### 5.1 secops CLI — 分析师的一等公民

#### 命令集

```bash
# 1. 研判告警
$ secops triage ALERT-20240107-0042
🤖 Triage Agent 正在分析...
  ✅ 查询 VT: 1.2.3.4 → 恶意度 3/72
  ✅ 查询 CMDB: 关联资产 server-prod-03
  ✅ 查询 SIEM: 最近 1h 同 IP 事件 7 条
  ⚠️  判定: 高风险 — 可能横向移动
  📋 建议: 升级为 investigate
→ 输入 "go" 开始深度调查, "approve" 直接处置, "dismiss" 关闭

# 2. 中途注入指令（Steering）
$ secops steer SESS-abc123 "这个 IP 是我们的 CDN 节点，排除它再查"
🤖 已注入，Agent 下一轮将处理你的指令...
  ✅ 排除 1.2.3.4，重新分析...
  ✅ 剩余可疑 IP: 5.6.7.8
  ⚠️  更新判定: 中风险 — 可能误报

# 3. 后台并行处理多个告警
$ secops triage ALERT-0043 --bg
Session SESS-def456 started in background
$ secops triage ALERT-0044 --bg
Session SESS-ghi789 started in background
$ secops sessions
  SESS-abc123  ALERT-0042  triage      running   2m ago
  SESS-def456  ALERT-0043  triage      running   30s ago
  SESS-ghi789  ALERT-0044  triage      running   10s ago

# 4. 审批处置（必须人工）
$ secops pending-approvals
  TASK-001  block_ip 5.6.7.8         SESS-abc123  2m ago
  TASK-002  isolate_host server-03    SESS-def456  1m ago
$ secops approve TASK-001
✅ 已执行: block_ip 5.6.7.8 → firewall updated
$ secops reject TASK-002 --reason "先确认业务影响"
⏸️ 已拒绝: 返回 Agent 重新评估

# 5. 主动威胁狩猎
$ secops hunt --query "过去 7 天所有来自 10.0.0.0/8 的异常登录"
🤖 启动 Hunting Agent...
  ✅ 查询 SIEM: 发现 23 条异常登录记录
  ✅ 关联分析: 其中 5 条来自已离职员工账号
  ⚠️  发现: 可能存在凭证泄露
  📋 建议: 对 5 个账号执行凭证重置

# 6. 审计日志
$ secops audit-log --today
  09:01  triage    ALERT-0042  auto        completed
  09:03  steer     SESS-abc123  analyst-01  "排除 CDN IP"
  09:05  approve   TASK-001     analyst-01  block_ip 5.6.7.8
  09:08  triage    ALERT-0043  auto        completed
  09:10  reject    TASK-002     analyst-02  "确认业务影响"
```

#### CLI 技术实现

```
CLI 框架：Python (Typer + Rich) 或 Go（性能更好、单二进制分发）
通信方式：WebSocket client → Security Gateway（实时流式）
配置文件：~/.secops/config.yaml（Gateway 地址、API key、默认参数）
补全支持：shell completion（bash/zsh/fish）
输出格式：默认 Rich 表格，加 --json 输出 JSON（可管道）
```

### 5.2 Security Gateway — 统一控制面

单进程长驻 daemon，拥有所有 surface：

- Kafka consumer surface（自动告警）
- CLI surface（分析师交互）
- IM surface（飞书/钉钉通知）
- API surface（外部系统调用）

```python
# gateway/daemon.py

class SecurityGateway:
    """
    单进程长驻 daemon，安全平台唯一的控制面。
    整合：Hook Engine + Agent Router + Priority Queue + Dedup + Steering + Session + Rate Limiter
    """

    def __init__(self, config):
        self.hook_engine = HookEngine(config.hooks)
        self.agent_router = AgentRouter(config.agents, config.routing)
        self.priority_queue = PriorityQueue(config.queues)
        self.dedup = DedupEngine(config.dedup)
        self.steering_queue = SteeringQueue()
        self.session_manager = SessionManager(config.sessions)
        self.rate_limiter = RateLimiter(config.rate_limits)
        self.worker_pool = WorkerPool(config.workers, self)
        self.kafka_consumer = KafkaConsumer(config.kafka)

    async def start(self):
        await asyncio.gather(
            self.kafka_consumer.start(self._on_alert),
            self._serve_cli(),
            self._serve_api(),
            self._serve_im(),
            self._process_queue(),
        )

    # ---- Kafka 自动告警入口 ----

    async def _on_alert(self, alert: dict):
        task = SecurityTask.from_alert(alert)

        # Hook: PreSession — 去重 / 白名单过滤
        hook_result = await self.hook_engine.fire("PreSession", task=task)
        if hook_result.action == "block":
            return

        # 去重引擎
        if self.dedup.is_duplicate(task):
            self.dedup.merge(task)
            return

        # Agent Router — 决定用哪个 Agent
        agent_name = self.agent_router.route(task)

        # 优先级排队
        self.priority_queue.enqueue(task, agent_name)

    # ---- 队列消费 ----

    async def _process_queue(self):
        while True:
            task, agent_name = await self.priority_queue.dequeue()
            if self.rate_limiter.acquire(task.priority):
                session = self.session_manager.create(
                    agent=agent_name,
                    task=task,
                    mode="auto",
                )
                self.worker_pool.submit(session)
            else:
                self.priority_queue.requeue(task, agent_name)

    # ---- CLI 交互入口 ----

    async def _on_cli_command(self, cmd: CLITask):
        if cmd.type == "triage":
            task = SecurityTask.from_cli(cmd)
            agent_name = self.agent_router.route(task)
            session = self.session_manager.create(
                agent=agent_name,
                task=task,
                mode="interactive",
            )
            self.worker_pool.submit(session)
            return session

        elif cmd.type == "steer":
            session = self.session_manager.get(cmd.session_id)
            await self.steering_queue.inject(
                session,
                message=cmd.message,
                mode="steer",
            )

        elif cmd.type == "approve":
            session = self.session_manager.get(cmd.session_id)
            await self.steering_queue.inject(
                session,
                message=f"用户审批通过: {cmd.task_id}",
                mode="steer",
                approval=True,
            )

        elif cmd.type == "reject":
            session = self.session_manager.get(cmd.session_id)
            await self.steering_queue.inject(
                session,
                message=f"用户拒绝: {cmd.task_id}，原因: {cmd.reason}",
                mode="steer",
                approval=True,
            )
```

### 5.3 Hook Engine — 安全治理的核心

6 个安全生命周期事件，每个事件可以注册多个 Hook 脚本，按顺序执行。

```python
# gateway/hooks/engine.py

class HookEvent(str, Enum):
    PreSession = "PreSession"       # 新 session 创建前（去重/白名单/预加载上下文）
    PreToolUse = "PreToolUse"       # 工具执行前（敏感工具审批/缓存检查/参数校验）
    PostToolUse = "PostToolUse"     # 工具执行后（结果增强/自动关联内部情报）
    PreResponse = "PreResponse"     # Agent 返回结果前（置信度检查/升级人工）
    PostSession = "PostSession"     # session 结束后（报告生成/通知推送/工单创建）
    OnApproval = "OnApproval"      # 需要人工审批时触发（审批流程编排）


class HookAction(str, Enum):
    allow = "allow"       # 放行
    block = "block"       # 阻止（不执行）
    warn = "warn"         # 警告（执行但记录）
    modify = "modify"     # 修改参数再执行
    defer = "defer"       # 延迟到人工审批


class HookEngine:
    def __init__(self, hooks_config: list[dict]):
        self.hooks: dict[HookEvent, list[Hook]] = defaultdict(list)
        for cfg in hooks_config:
            hook = Hook.from_config(cfg)
            self.hooks[hook.event].append(hook)

    async def fire(self, event: HookEvent, **ctx) -> HookResult:
        for hook in self.hooks.get(event, []):
            result = await hook.execute(ctx)
            if result.action in ("block", "defer"):
                return result
            if result.action == "modify":
                ctx.update(result.modifications)
        return HookResult(action="allow")
```

#### Hooks 配置示例

```yaml
# hooks.yaml

hooks:
  # PreSession — 入口过滤
  - event: PreSession
    name: dedup-check
    script: hooks/dedup_check.py          # 检查是否重复告警（5min 内同 IP 同 IOC）
    timeout: 5

  - event: PreSession
    name: whitelist-filter
    script: hooks/whitelist_filter.py     # 已知白名单直接跳过
    timeout: 5

  - event: PreSession
    name: context-enrichment
    script: hooks/context_enrichment.py   # 预加载相关资产信息、历史告警
    timeout: 10

  # PreToolUse — 工具调用拦截
  - event: PreToolUse
    name: sensitive-tool-gate
    script: hooks/sensitive_tool_gate.py  # block_ip/isolate_host 需要审批
    timeout: 10

  - event: PreToolUse
    name: cache-check
    script: hooks/cache_check.py          # 检查 VT/Shodan 是否有缓存，避免重复查询
    timeout: 3

  # PostToolUse — 结果增强
  - event: PostToolUse
    name: enrich-result
    script: hooks/enrich_result.py        # 查完 VT 自动关联内部情报库
    timeout: 10

  # PreResponse — 出结论前检查
  - event: PreResponse
    name: confidence-check
    script: hooks/confidence_check.py     # 置信度低于阈值，升级人工
    timeout: 5

  # PostSession — 后处理
  - event: PostSession
    name: generate-report
    script: hooks/generate_report.py      # 自动生成研判报告
    timeout: 30

  - event: PostSession
    name: notify-escalation
    script: hooks/notify_escalation.py    # 高风险通知飞书群
    timeout: 10

  - event: PostSession
    name: create-ticket
    script: hooks/create_ticket.py        # 自动创建工单
    timeout: 10

  # OnApproval — 审批流程
  - event: OnApproval
    name: approval-gate
    script: hooks/approval_gate.py        # 审批流程编排
    timeout: 3600  # 1 小时等待人工
```

#### Hook 示例：sensitive-tool-gate.py

```python
# hooks/sensitive_tool_gate.py

SENSITIVE_TOOLS = {"block_ip", "isolate_host", "reset_credentials", "disable_user"}

def main(ctx):
    tool_name = ctx["tool_name"]
    if tool_name not in SENSITIVE_TOOLS:
        return {"action": "allow"}

    session = ctx["session"]
    task = session.task

    # critical 告警 + 交互模式 → 自动放行（分析师已经在场）
    if task.priority == "critical" and session.mode == "interactive":
        return {"action": "warn", "reason": f"敏感工具 {tool_name}，交互模式下自动放行"}

    return {
        "action": "defer",
        "reason": f"工具 {tool_name} 需要人工审批",
        "approval_required": True,
        "timeout": 3600,
        "notify": {
            "channel": "feishu",
            "message": f"⚠️ 需要审批: {tool_name}\n参数: {ctx['tool_args']}\nsecops approve {ctx['task_id']}"
        }
    }
```

### 5.4 Agent System — 专用 Agent 定义

#### 为什么用多个专用 Agent 而不是一个万能 Agent

```
一个万能 Agent：
  triage + investigate + threat_intel + response 全塞一个 SOUL.md
  → prompt 太长，模型记不住
  → 工具太多（20+），模型选不对
  → 流程太复杂，容易跳步

多个专用 Agent：
  triage-agent:      5 个工具，1 页 SOUL.md
  investigate-agent: 8 个工具，1 页 SOUL.md
  malware-agent:     5 个工具，1 页 SOUL.md
  response-agent:    6 个工具，1 页 SOUL.md
  threat-intel-agent: 4 个工具，1 页 SOUL.md
  → 每个 Agent 的 prompt 短、工具少、流程清晰
  → 模型表现更好（工具越少，选择越准确）
```

#### 五个专用 Agent 详细定义

```
triage-agent（告警研判）：
  输入：原始告警 JSON
  流程：收集上下文 → 判断是否误报 → 评估风险等级 → 给出 next_action
  输出：{risk_level, is_false_positive, reasoning, next_action, confidence}
  工具：[query_virustotal, search_siem_logs, query_cmdb, search_internal_docs, query_internal_intel]
  沙箱：不需要
  模型：deepseek-v4（4096 tokens）
  中间件：SecurityFlowMiddleware(triage 流程)

investigate-agent（深度调查）：
  输入：triage 结果 + 原始告警 + 关联历史事件
  流程：调查攻击链路 → 确定影响范围 → 关联历史事件 → 时间线还原
  输出：{attack_chain, affected_assets, timeline, recommendations, evidence}
  工具：[search_siem_logs, search_edr_events, query_cmdb, query_network_topology,
         query_virustotal, search_internal_docs, analyze_pcap, query_internal_intel]
  沙箱：不需要
  模型：deepseek-v4（8192 tokens）
  中间件：SecurityFlowMiddleware(investigation 流程)

malware-agent（恶意样本分析）：
  输入：恶意样本 hash / URL / 文件
  流程：沙箱 detonation → IOC 提取 → YARA 规则匹配 → 关联已知家族
  输出：{malware_family, iocs, yara_matches, behavior_summary, related_campaigns}
  工具：[query_virustotal, analyze_sample_in_sandbox, extract_iocs,
         search_internal_docs, query_cmdb]
  沙箱：需要（Docker，malware-analysis:latest 镜像）
  模型：deepseek-v4（8192 tokens）

response-agent（自动处置）：
  输入：调查结果 + 处置建议
  流程：准备处置方案 → **必须人工审批** → 执行处置 → 验证结果
  输出：{action_taken, result, verification, rollback_plan}
  工具：[block_ip, isolate_host, reset_credentials, disable_user, create_ticket, search_siem_logs]
  沙箱：不需要
  模型：deepseek-v4（4096 tokens）
  特殊：所有操作必须审批（通过 PreToolUse Hook 实现）

threat-intel-agent（威胁情报关联）：
  输入：IOC（IP/域名/哈希）
  流程：查询多个情报源 → 交叉验证 → 评估可信度
  输出：{ioc_verdict, sources, confidence, related_campaigns}
  工具：[query_virustotal, query_shodan, query_internal_intel, search_internal_docs]
  沙箱：不需要
  模型：deepseek-v4（4096 tokens）
```

#### Agent 之间通过 Task 上下文传递，不直接对话

```
triage-agent 完成后 → 结果写入 DB
Gateway 看到 next_action = "investigate" → 自动创建 investigate Task
investigate Task 携带 triage 结果作为上下文

这不是多 Agent 协作，是单 Agent + 状态机编排：
  每个 Agent 独立运行，通过 DB 传递上下文
  → 简单、可调试、可审计
  → 任何一个环节失败，可以单独重试
  → 审计日志完整（谁做了什么、什么时候做的）

状态机流转：
  告警进入 → triage → 判定结果
                        ├── 误报 → 关闭
                        ├── low/medium → 记录 + 通知
                        └── high/critical → investigate → 调查结果
                                                          ├── 需要处置 → response → 审批 → 执行
                                                          └── 观察即可 → 记录 + 监控
```

#### Agent Router — 配置化路由

```python
# gateway/router.py

class AgentRouter:
    def __init__(self, agents_config: dict, routing_config: list):
        self.agents: dict[str, AgentConfig] = {}
        self.routing_rules: list[RoutingRule] = []
        for agent_cfg in agents_config:
            self.agents[agent_cfg["name"]] = AgentConfig(**agent_cfg)
        for rule_cfg in routing_config:
            self.routing_rules.append(RoutingRule(**rule_cfg))

    def route(self, task: SecurityTask) -> str:
        for rule in self.routing_rules:
            if rule.matches(task):
                return rule.agent_name
        return "triage-agent"
```

```yaml
# routing.yaml

agents:
  - name: triage-agent
    soul: agents/triage/SOUL.md
    tools: [query_virustotal, search_siem_logs, query_cmdb, search_internal_docs, query_internal_intel]
    sandbox: false
    max_tokens: 4096
    model: deepseek-v4

  - name: investigate-agent
    soul: agents/investigate/SOUL.md
    tools: [search_siem_logs, search_edr_events, query_cmdb, query_network_topology,
            query_virustotal, search_internal_docs, analyze_pcap, query_internal_intel]
    sandbox: false
    max_tokens: 8192
    model: deepseek-v4

  - name: malware-agent
    soul: agents/malware/SOUL.md
    tools: [query_virustotal, analyze_sample_in_sandbox, extract_iocs,
            search_internal_docs, query_cmdb]
    sandbox: true
    sandbox_image: malware-analysis:latest
    max_tokens: 8192
    model: deepseek-v4

  - name: response-agent
    soul: agents/response/SOUL.md
    tools: [block_ip, isolate_host, reset_credentials, disable_user, create_ticket, search_siem_logs]
    sandbox: false
    max_tokens: 4096
    model: deepseek-v4
    approval_required: true

  - name: threat-intel-agent
    soul: agents/threat_intel/SOUL.md
    tools: [query_virustotal, query_shodan, query_internal_intel, search_internal_docs]
    sandbox: false
    max_tokens: 4096
    model: deepseek-v4

routing:
  - match: {alert_type: "phishing"}
    agent: triage-agent
  - match: {alert_type: "malware"}
    agent: malware-agent
  - match: {alert_type: "lateral_movement"}
    agent: investigate-agent
  - match: {alert_type: "brute_force"}
    agent: triage-agent
  - match: {alert_type: "threat_intel"}
    agent: threat-intel-agent
  - match: {priority: "critical"}
    agent: investigate-agent
  - match: {}
    agent: triage-agent
```

### 5.5 Steering — 中途接管

```python
# gateway/steering.py

class SteeringQueue:
    """
    两种模式：
      steer:    在当前轮工具调用完成后、下一轮 LLM 调用前注入
      followup: 在当前 session turn 结束后注入（开启新 turn）

    核心场景：
      Agent 正在分析 → 分析师发现关键信息 → inject 进去
      不需要等 Agent 跑完
    """

    def __init__(self):
        self._queues: dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)

    async def inject(self, session: SecuritySession, message: str,
                     mode: str = "steer", approval: bool = False):
        item = SteeringItem(
            message=message,
            mode=mode,
            approval=approval,
            timestamp=datetime.now(),
            source="analyst",
        )
        await self._queues[session.id].put(item)
        if session.mode == "auto":
            notify_analyst(f"你已向 {session.id} 注入指令: {message}")

    async def drain(self, session_id: str) -> list[SteeringItem]:
        items = []
        while not self._queues[session_id].empty():
            items.append(await self._queues[session_id].get())
        return items
```

### 5.6 Plugin System — 场景化扩展

Plugin = Command + Agent + Skill + Hook 一体化，一个 Plugin 就是一个完整的场景解决方案。

```
security-agent-platform/
├── plugins/
│   ├── phishing/
│   │   ├── plugin.yaml              ← 插件元数据
│   │   ├── commands/
│   │   │   └── triage-phishing.md   ← /triage-phishing 命令
│   │   ├── agents/
│   │   │   └── phishing-analyst.md  ← 专用 phishing sub-agent
│   │   ├── skills/
│   │   │   └── email-header-analysis.md
│   │   └── hooks/
│   │       └── hooks.yaml           ← phishing 场景专用 hooks
│   │
│   ├── malware/
│   │   ├── plugin.yaml
│   │   ├── commands/
│   │   │   └── analyze-sample.md
│   │   ├── agents/
│   │   │   └── malware-researcher.md
│   │   ├── skills/
│   │   │   ├── yara-rule-generation.md
│   │   │   └── ioc-extraction.md
│   │   └── hooks/
│   │       └── hooks.yaml           # sandbox 启动/清理 hooks
│   │
│   └── incident-response/
│       ├── plugin.yaml
│       ├── commands/
│       │   ├── respond.md
│       │   └── playbook.md
│       ├── agents/
│       │   └── responder.md
│       ├── skills/
│       │   ├── containment.md
│       │   └── eradication.md
│       └── hooks/
│           └── hooks.yaml           # 所有 response 操作必须审批
```

```yaml
# plugins/phishing/plugin.yaml
name: phishing
description: 钓鱼邮件分析插件
version: 1.0.0

commands:
  - name: triage-phishing
    file: commands/triage-phishing.md
    description: 分析钓鱼邮件告警

agents:
  - name: phishing-analyst
    file: agents/phishing-analyst.md
    description: 钓鱼邮件分析专家
    tools: [query_virustotal, extract_urls, analyze_email_headers, search_siem_logs]

skills:
  - name: email-header-analysis
    file: skills/email-header-analysis.md
    auto_invoke: true

hooks:
  - file: hooks/hooks.yaml
```

---

## 六、工具 & 知识层

### 6.1 安全工具集（分组定义）

```yaml
tool_groups:
  threat_intel:
    - name: query_virustotal
      description: 查询文件/URL/IP 信誉（VirusTotal API）
      params: {query: str, query_type: "ip|url|hash|domain"}
      approval: false

    - name: query_shodan
      description: 查询互联网暴露面
      params: {query: str}
      approval: false

    - name: query_internal_intel
      description: 查询内部威胁情报库（历史 IOC、已知攻击者）
      params: {ioc_type: str, ioc_value: str}
      approval: false

  asset_management:
    - name: query_cmdb
      description: 查询资产信息（IP→主机、负责人、业务线）
      params: {query: str, query_type: "ip|hostname|owner"}
      approval: false

    - name: query_network_topology
      description: 查询网络拓扑（子网、防火墙规则、VLAN）
      params: {target: str, depth: int}
      approval: false

    - name: query_vulnerability_db
      description: 查询漏洞库（CVE 详情、影响版本、PoC）
      params: {cve_id: str}
      approval: false

  log_analysis:
    - name: search_siem_logs
      description: 搜索 SIEM 日志（Splunk/ELK）
      params: {query: str, time_range: str, limit: int}
      approval: false

    - name: search_edr_events
      description: 搜索 EDR 事件（进程、文件、网络行为）
      params: {host: str, event_type: str, time_range: str}
      approval: false

    - name: analyze_pcap
      description: 分析抓包数据（提取连接、DNS、HTTP 元数据）
      params: {pcap_file: str, filter: str}
      approval: false

  response:
    - name: block_ip
      description: 封禁 IP（防火墙规则）
      params: {ip: str, duration: str, reason: str}
      approval: true              # ⚠️ 必须审批

    - name: isolate_host
      description: 隔离主机（网络隔离）
      params: {hostname: str, reason: str}
      approval: true              # ⚠️ 必须审批

    - name: reset_credentials
      description: 重置凭证（AD 密码重置）
      params: {username: str, reason: str}
      approval: true              # ⚠️ 必须审批

    - name: disable_user
      description: 禁用账号
      params: {username: str, reason: str}
      approval: true              # ⚠️ 必须审批

    - name: create_ticket
      description: 创建工单（Jira/ServiceNow）
      params: {title: str, body: str, priority: str}
      approval: false

  knowledge:
    - name: search_internal_docs
      description: RAG 查询内部安全文档（处置手册、安全策略）
      params: {query: str, top_k: int}
      approval: false

    - name: search_cve
      description: 查询 CVE 漏洞库
      params: {cve_id: str, keyword: str}
      approval: false

    - name: search_playbook
      description: 查询处置手册（按告警类型）
      params: {alert_type: str}
      approval: false
```

### 6.2 工具实现方式

```
方式 1：MCP Server（推荐）
  每个工具组 = 一个 MCP Server
  threat_intel_server.py → 提供 query_virustotal / query_shodan / query_internal_intel
  通过 DeerFlow 的 MCP 集成自动加载
  优点：标准化、可复用、可独立部署

方式 2：Python 直接实现
  engine/tools/*.py
  作为 DeerFlow 的 Tool Groups 加载
  优点：简单、适合快速开发

方式 3：外部 API 封装
  对已有的安全系统（Splunk、CrowdStrike、Palo Alto）做 API 封装
  通过 MCP 或直接 HTTP 调用
```

---

## 七、Human-in-the-Loop 设计

### 7.1 三级审批模型

```
Level 0：自动处理（不需要人）
  告警研判 → 判定为误报 → PreSession Hook 自动 block → 自动关闭
  告警研判 → 判定为 low 风险 → PostSession Hook 记录 + 低优先级通知

Level 1：通知 + 可干预（人可以选择介入）
  告警研判 → 判定为 medium 风险 → PostSession Hook 通知分析师
  分析师可以 secops steer 注入额外指令
  30 分钟内没响应 → confidence-check Hook 检查 → 降级为 Level 0 标准流程

Level 2：必须人工审批
  任何封禁 IP、隔离主机、重置凭证的操作 → sensitive-tool-gate Hook defer
  critical 级别的告警 → confidence-check Hook 升级
  模型置信度低于阈值的判断 → confidence-check Hook defer
  审批方式：
    CLI: secops approve TASK-001
    IM:  飞书卡片点"确认"
    API: POST /api/approval/{task_id}/approve
  超时（默认 1h）：approval_gate Hook 处理超时逻辑
```

### 7.2 人工介入方式

```
方式 1：CLI 审批（推荐）
  secops pending-approvals           # 查看待审批
  secops approve TASK-001            # 一条命令审批
  secops reject TASK-001 --reason "先确认业务影响"  # 拒绝并反馈

方式 2：Steering 中途注入
  secops steer SESS-abc123 "这个 IP 是蜜罐，别封"     # 纠偏
  secops steer SESS-abc123 "查一下同一子网的其他主机"  # 补充指令

方式 3：IM 快速响应
  飞书群里 Agent 发消息："检测到 XX 攻击，建议封禁 IP 1.2.3.4"
  分析师回复"确认" → Agent 执行
  分析师回复"拒绝，原因：XXX" → Agent 重新评估

方式 4：Web 界面（降级方案）
  看板展示 + 审批按钮（给不用 CLI 的管理层/新人用）
```

### 7.3 审批流程时序

```
Agent 要执行 block_ip(5.6.7.8)
  │
  ▼
PreToolUse Hook: sensitive-tool-gate
  │ action=defer, timeout=3600
  │ notify: 飞书群推送 "secops approve TASK-001"
  ▼
Session 挂起，等待审批
  │
  ├─→ 分析师: secops approve TASK-001
  │     → Steering inject(approval=True)
  │     → Session 恢复，执行 block_ip
  │
  ├─→ 分析师: secops reject TASK-001 --reason "先确认业务影响"
  │     → Steering inject(approval=False, reason)
  │     → Agent 收到拒绝反馈，重新评估
  │
  └─→ 超时 1h 无人响应
        → Session status = approval_timeout
        → 通知 escalation
```

---

## 八、Worker 核心 — DeerFlow Harness 集成

Worker 封装 DeerFlowClient，在每个 Agent step 之间插入 Hook 检查和 Steering drain。

```python
# engine/worker.py

from deerflow.client import DeerFlowClient
from deerflow.config import get_app_config

class SecurityWorker:
    """
    Worker 封装 DeerFlowClient，加入 Steering 和 Hooks。
    DeerFlowClient 负责底层 Agent Loop，Worker 负责：
      1. 在 Agent Loop 每个 step 之间检查 Steering queue
      2. 在工具调用前后触发 Hooks
      3. 在 session 结束后触发 PostSession hooks
      4. 在 Agent 完成后检查是否需要创建 follow-up task
    """

    def __init__(self, gateway: SecurityGateway):
        self.client = DeerFlowClient()
        self.config = get_app_config()
        self.gateway = gateway

    async def process(self, session: SecuritySession):
        task = session.task

        # 注入 bootstrap files（安全处置手册 + 告警上下文 + Playbook）
        bootstrap = self._build_bootstrap(session)

        # DeerFlowClient step-by-step 执行（流式）
        for step in self.client.run_stream(
            message=self._format_task(task),
            thread_id=session.id,
            agent_name=session.agent_name,
            model_name=self._resolve_model(task),
            bootstrap_context=bootstrap,
            is_plan_mode=True,
        ):
            # Hook: PreToolUse
            if step.type == "tool_call":
                hook_result = await self.gateway.hook_engine.fire(
                    "PreToolUse",
                    tool_name=step.tool_name,
                    tool_args=step.tool_args,
                    session=session,
                )
                if hook_result.action == "block":
                    step.skip(reason=hook_result.reason)
                    continue
                if hook_result.action == "defer":
                    await self._wait_for_approval(session, hook_result)
                    continue
                if hook_result.action == "modify":
                    step.update_args(hook_result.modifications)

            # Steering check — 每步之间检查是否有人 inject
            steering_items = await self.gateway.steering_queue.drain(session.id)
            if steering_items:
                for item in steering_items:
                    self.client.inject_context(session.id, item.message)

            # Hook: PostToolUse
            if step.type == "tool_result":
                await self.gateway.hook_engine.fire(
                    "PostToolUse",
                    tool_name=step.tool_name,
                    result=step.result,
                    session=session,
                )

            # Hook: PreResponse（Agent 要出结论了）
            if step.type == "response":
                hook_result = await self.gateway.hook_engine.fire(
                    "PreResponse",
                    response=step.content,
                    session=session,
                )
                if hook_result.action == "defer":
                    await self._wait_for_approval(session, hook_result)

        # Hook: PostSession
        session.status = "completed"
        session.completed_at = datetime.now()
        await self.gateway.hook_engine.fire("PostSession", session=session)

        # 结果写入 DB
        result = session.to_result()
        save_result(session.task.task_id, result)

        # 如果 next_action 需要升级 → 创建 follow-up task
        if result.get("next_action") == "investigate":
            self.gateway.create_followup_task(session, "investigate-agent")

    async def _wait_for_approval(self, session, hook_result):
        """挂起 session，等待人工审批"""
        session.status = "waiting_approval"
        notify_approval_needed(session, hook_result)

        approval = await self.gateway.approval_queue.wait(
            session.id, timeout=hook_result.timeout
        )
        if approval is None:
            session.status = "approval_timeout"
            notify_approval_timeout(session)
        elif approval.approved:
            session.status = "running"
        else:
            session.status = "running"
            self.client.inject_context(
                session.id,
                f"人工拒绝了操作，原因: {approval.reason}。请重新评估。"
            )

    def _resolve_model(self, task: SecurityTask) -> str:
        # 多模型路由：简单告警用小模型，复杂用大模型
        if task.priority in ("low", "medium") and task.alert_type in ("brute_force",):
            return self.config.models.get("small", "deepseek-v4")
        return self.config.models[0].name

    def _build_bootstrap(self, session: SecuritySession) -> dict:
        return {
            "SECURITY.md": self._load_security_playbook(),
            "ALERT.md": self._format_alert_context(session.task),
            "PLAYBOOK.md": self._load_playbook(session.task.alert_type),
        }

    def _format_task(self, task: SecurityTask) -> str:
        return f"""请分析以下安全告警：
告警类型: {task.alert_type}
优先级: {task.priority}
来源: {task.source}
原始数据: {json.dumps(task.raw_alert, ensure_ascii=False, indent=2)}
附加上下文: {json.dumps(task.context, ensure_ascii=False, indent=2)}
"""
```

---

## 九、生产级设计

### 9.1 限流与资源控制

```python
# gateway/rate_limiter.py

class RateLimiter:
    """
    防止 API 额度爆了：
      - 全局并发上限（如 5 个同时运行的 Agent）
      - 每分钟 LLM 调用上限（如 60 rpm）
      - 按优先级分配配额（critical 保留 2 个并发槽位）
    """

    def __init__(self, config):
        self.max_concurrent = config.max_concurrent          # 全局并发上限
        self.critical_reserved = config.critical_reserved    # critical 保留槽位
        self.rpm_limit = config.rpm_limit                    # 每分钟 LLM 调用上限
        self._active = 0
        self._active_critical = 0
        self._rpm_counter = TokenBucket(self.rpm_limit, 60)

    def acquire(self, priority: str = "medium") -> bool:
        if self._active >= self.max_concurrent:
            if priority == "critical" and self._active_critical < self.critical_reserved:
                pass  # critical 有保留槽位
            else:
                return False
        if not self._rpm_counter.consume():
            return False
        self._active += 1
        if priority == "critical":
            self._active_critical += 1
        return True

    def release(self, priority: str = "medium"):
        self._active -= 1
        if priority == "critical":
            self._active_critical -= 1
```

### 9.2 去重引擎

```python
# gateway/dedup.py

class DedupEngine:
    """
    5 分钟内同一 IP + 同一 IOC 的告警合并为一个 Task。
    同一攻击链路的多个告警 → 关联为一个 Investigation Task。
    """

    def __init__(self, config):
        self.window_seconds = config.window_seconds  # 默认 300（5 分钟）
        self._seen: dict[str, datetime] = {}

    def is_duplicate(self, task: SecurityTask) -> bool:
        key = task.dedup_key
        if not key:
            return False
        if key in self._seen:
            elapsed = (datetime.now() - self._seen[key]).total_seconds()
            if elapsed < self.window_seconds:
                return True
        self._seen[key] = datetime.now()
        return False

    def merge(self, task: SecurityTask):
        """合并重复告警到已有的 session — 把新告警作为附加上下文 inject"""
        pass
```

**效果**：

```
没有去重：1000 条告警/小时 → 1000 个并发 Agent → API 费用爆了
有去重：  1000 条 → 去重后 200 条 → 费用降 80%
```

### 9.3 Session 审计

```python
# gateway/session.py

class SessionManager:
    """
    Session 管理 + JSONL 审计。
    每条记录：{timestamp, event, agent, tool, args, result, hook_actions, steering_items}
    """

    def create(self, agent: str, task: SecurityTask, mode: str) -> SecuritySession:
        session_id = f"SESS-{uuid4().hex[:8]}"
        transcript_path = f"sessions/{session_id}.jsonl"
        session = SecuritySession(
            session_id=session_id,
            task=task,
            agent_name=agent,
            mode=mode,
            status="running",
            created_at=datetime.now(),
            completed_at=None,
            transcript_path=transcript_path,
            approval_timeout=3600,
            metadata={},
        )
        self._sessions[session_id] = session
        self._append_transcript(session, {"event": "session_created", "task": task.__dict__})
        return session

    def _append_transcript(self, session: SecuritySession, entry: dict):
        entry["timestamp"] = datetime.now().isoformat()
        with open(session.transcript_path, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
```

### 9.4 监控 & 告警

```
监控指标（Prometheus 暴露）：
  - secops_sessions_total{status="running|completed|failed|timeout"}
  - secops_tasks_total{type="triage|investigate|response", priority="critical|high|medium|low"}
  - secops_hooks_duration_seconds{event="PreToolUse|PostToolUse|..."}
  - secops_approval_wait_seconds
  - secops_llm_tokens_total{agent="triage|investigate|..."}
  - secops_queue_depth{priority="critical|high|medium|low"}
  - secops_dedup_saved_total

告警规则：
  - 队列深度 > 50 持续 5 分钟 → 通知（可能限流过严或 Agent 出错）
  - 审批等待 > 30 分钟 → 通知（可能漏审批）
  - Worker 成功率 < 90% 持续 10 分钟 → 通知（Agent 可能出错）
  - LLM API 调用失败率 > 5% → 通知（API 可能有问题）
```

### 9.5 异常恢复

```
Worker 异常：
  try/except 捕获所有异常 → session.status = "failed" → 记录 transcript → 通知
  失败的 task 可以重试：secops retry TASK-001

Gateway 重启：
  Session 状态持久化到 PostgreSQL
  Gateway 启动时加载未完成 session → 根据 transcript 恢复或标记为 interrupted
  JSONL transcript 保证审计链不断

Kafka 消费偏移：
  手动提交 offset（处理成功后才 commit）
  避免消息丢失
```

---

## 十、项目结构

```
security-agent-platform/
├── gateway/                           ← Security Gateway daemon
│   ├── daemon.py                      ← 主进程（WebSocket server + Kafka consumer）
│   ├── hooks/                         ← Hook Engine
│   │   ├── engine.py                  ← Hook 执行引擎
│   │   └── builtins/                  ← 内置 hooks
│   │       ├── dedup_check.py
│   │       ├── whitelist_filter.py
│   │       ├── context_enrichment.py
│   │       ├── sensitive_tool_gate.py
│   │       ├── cache_check.py
│   │       ├── enrich_result.py
│   │       ├── confidence_check.py
│   │       ├── generate_report.py
│   │       ├── notify_escalation.py
│   │       ├── create_ticket.py
│   │       └── approval_gate.py
│   ├── router.py                      ← Agent Router（配置化路由）
│   ├── steering.py                    ← Steering Queue（中途注入）
│   ├── session.py                     ← Session Manager（JSONL + audit）
│   ├── approval.py                    ← 审批队列
│   ├── dedup.py                       ← 去重引擎
│   ├── rate_limiter.py                ← 限流器
│   └── queue.py                       ← 优先级队列
│
├── cli/                               ← secops CLI
│   ├── main.py                        ← CLI 入口（Typer）
│   ├── commands/
│   │   ├── triage.py
│   │   ├── investigate.py
│   │   ├── approve.py
│   │   ├── reject.py
│   │   ├── steer.py
│   │   ├── sessions.py
│   │   ├── hunt.py
│   │   ├── audit_log.py
│   │   └── dashboard.py
│   └── client.py                      ← WebSocket client（连 Gateway）
│
├── engine/                            ← Agent 引擎层
│   ├── worker.py                      ← Worker：DeerFlowClient + Hooks + Steering
│   ├── agents/                        ← Agent 定义
│   │   ├── triage/
│   │   │   ├── SOUL.md                ← 研判 Agent 人格
│   │   │   └── config.yaml            ← 工具白名单 + 参数
│   │   ├── investigate/
│   │   │   ├── SOUL.md
│   │   │   └── config.yaml
│   │   ├── malware/
│   │   │   ├── SOUL.md
│   │   │   └── config.yaml
│   │   ├── response/
│   │   │   ├── SOUL.md
│   │   │   └── config.yaml
│   │   └── threat_intel/
│   │       ├── SOUL.md
│   │       └── config.yaml
│   ├── middlewares/                   ← 安全流程中间件
│   │   └── security_flow.py           ← 状态机（告警→研判→调查→处置）
│   ├── tools/                         ← 安全工具
│   │   ├── threat_intel.py            ← 威胁情报（VT / Shodan / 内部情报库）
│   │   ├── asset_management.py        ← 资产管理（CMDB / 网络拓扑 / 漏洞库）
│   │   ├── log_analysis.py            ← 日志分析（SIEM / EDR / PCAP）
│   │   ├── response_actions.py        ← 处置动作（封禁 / 隔离 / 重置）
│   │   └── knowledge.py               ← 知识检索（RAG / CVE / Playbook）
│   └── bootstrap/                     ← Bootstrap files（注入到每个 session）
│       ├── SECURITY.md                ← 安全处置手册
│       └── PLAYBOOK.md                ← 按告警类型的处置手册
│
├── plugins/                           ← Plugin 系统
│   ├── phishing/
│   ├── malware/
│   └── incident-response/
│
├── web/                               ← Web 看板（只做展示，不做操作）
│   └── ...
│
├── config.yaml                        ← 主配置
├── hooks.yaml                         ← Hooks 配置
├── routing.yaml                       ← 路由配置
├── docker-compose.yaml                ← Kafka + PostgreSQL + Qdrant + Gateway
└── tests/
```

---

## 十一、技术选型

```
Agent 引擎：  DeerFlow Harness（进程内 DeerFlowClient，支持 step-by-step 流式）
              调用方式：DeerFlowClient（进程内调用，不走 HTTP）
              复用：17 个中间件 + 沙箱 + 记忆系统 + MCP + Tool Groups + IM 7 渠道

Gateway：     Python + FastAPI + WebSocket
CLI：         Python (Typer + Rich) 或 Go（单二进制分发）
Hooks：       Python 脚本

消息队列：    Kafka（告警输入）
Session 存储： JSONL 文件（审计链） + PostgreSQL（查询）
向量数据库：  Qdrant（内部安全文档 RAG）
沙箱：        Docker（恶意样本分析隔离）

模型：        DeepSeek-V4（主模型）+ 小模型（简单告警）
              多模型路由：简单告警用便宜模型，复杂用强模型

IM：          DeerFlow channels（复用，7 个渠道已有）
Plugin：      YAML 配置 + Markdown commands + Python hooks
```

---

## 十二、开发路线图

### Phase 1：CLI + Gateway + 单 Agent（3 周）

```
目标：secops triage ALERT-1234 跑通

✅ secops CLI（triage / sessions / approve 三个命令）
✅ Security Gateway daemon（单进程，WebSocket）
✅ triage-agent（SOUL.md + 5 个工具）
✅ Kafka Consumer → Gateway → Worker → DeerFlowClient
✅ 基础 Hooks：dedup-check + whitelist-filter + generate-report
✅ Session JSONL 审计
✅ 结果写入 PostgreSQL
✅ 基础工具：query_virustotal + search_siem_logs + query_cmdb

不做：
  ❌ Steering（先用 followup 模式）
  ❌ Plugin 系统（先硬编码）
  ❌ Web 界面
  ❌ 更多 Agent
```

### Phase 2：Steering + Hooks + 审批（3 周）

```
目标：完整的安全治理 + 人工协同

✅ Steering queue（steer + followup 两种模式）
✅ 完整 Hook Engine（6 个事件 + 所有内置 hooks）
✅ 审批流程（secops approve/reject + IM 快速审批）
✅ response-agent（所有操作需审批）
✅ 限流器（Rate Limiter + 优先级队列）
✅ CLI 命令补全（secops steer / secops hunt）
✅ 监控指标（Prometheus）
```

### Phase 3：Plugin + 多 Agent + Web 看板（4 周）

```
目标：可扩展、多场景

✅ Plugin 系统（加载 + 注册 + 热加载）
✅ phishing / malware / incident-response 三个插件
✅ investigate-agent + malware-agent + threat-intel-agent
✅ Agent Router 配置化
✅ Web 看板（Next.js，只做展示）
✅ IM 通知增强（飞书卡片 + 快速审批按钮）
✅ 去重引擎（5 分钟窗口）
```

### Phase 4：智能升级（持续）

```
✅ RAG 知识库（内部安全文档 + 处置手册）
✅ SFT 微调（安全研判场景）
✅ 多模型路由（简单告警用小模型，复杂用大模型）
✅ 主动威胁狩猎（hunt 命令）
✅ 告警关联分析（攻击链路还原）
✅ 异常恢复机制（Gateway 重启恢复）
```

---

## 十三、设计决策记录

### 13.1 为什么用 DeerFlow 而不是从零写

| 从零写 | 用 DeerFlow |
|--------|-----------|
| 自己实现中间件链（2 周） | 已有 17 个中间件 |
| 自己实现沙箱（1 周） | Local + Docker 双模式 |
| 自己实现记忆系统（1 周） | LLM 提取 + 去抖动 |
| 自己实现流式输出（3 天） | SSE 已有 |
| 自己实现工具动态加载（3 天） | MCP + Tool Groups 已有 |
| 自己接 IM 渠道（1 周） | 7 个渠道已有 |
| **总计 6-8 周** | **总计 0** |

省下的 6-8 周用来写安全领域特有的：Gateway + Hooks + Steering + 安全工具 + Plugin。

### 13.2 为什么 CLI-first 而不是 Web-first

```
1. 分析师群体特性：每天用 Wireshark/tcpdump/nmap/dig/curl，肌肉记忆在终端
2. 效率对比：CLI 1 条命令 2 秒，Web 打开浏览器→登录→找告警→点分析 30 秒起
3. 可自动化：CLI 可以写脚本、cron、管道，Web 只能点点点
4. SSH 友好：在家/出差/手机 → SSH → secops，Web 需要 VPN + 浏览器
5. Web 不扔掉：降级为展示面（看板 + 报告 + 管理层）
```

### 13.3 为什么用 Gateway 而不是直接调 DeerFlow

```
直接调 DeerFlow：
  没有统一 Hook 点 → 每个入口自己写审批逻辑
  没有统一 Session → 审计日志散落
  Steering 不可能 → DeerFlowClient 不支持中途注入
  限流散落 → API 额度爆了

通过 Gateway：
  统一 Hook → 所有入口共享同一套治理逻辑
  统一 Session → 审计链完整
  Steering → Gateway 层实现中途注入
  限流 → Gateway 统一控制并发
```

### 13.4 为什么 Hooks 而不是硬编码审批

```
硬编码审批：
  每加一个新工具要改代码，不同客户策略不同，无法热加载

Hooks：
  加新工具不需要改代码（hooks.yaml 配置）
  不同客户有不同的 hooks.yaml
  可以热加载（改配置不重启）
  Hook 脚本可以做比"审批"更复杂的事（关联查询、结果增强、上下文注入）
```

### 13.5 为什么需要 Steering

```
没有 Steering：
  Agent 跑完 → 人看结果 → 发现方向错了 → 只能重新跑 → 浪费 API 额度

有 Steering：
  Agent 跑到一半 → 人发现关键信息 → secops steer 注入 → Agent 立即调整
  → 省 50% 的 API 额度和时间
  → 真正的 Human-in-the-Loop（不是 Human-at-the-End）
```

### 13.6 为什么 Agent 之间不直接对话

```
Agent 直接对话（多 Agent 协作）：
  上下文传递中丢失 → 调试困难 → 一个挂了整条链挂了

Agent 通过 DB + Gateway 传递上下文（状态机编排）：
  triage 完成 → 结果写 DB → Gateway 看到 → 创建 investigate Task
  每个 Agent 独立，互不影响
  任何环节失败可以单独重试
  审计日志完整（谁做了什么、什么时候做的）
```

---

## 十四、参考架构关系图

```
            Claude Code                OpenClaw                 opencode
                │                          │                        │
    ┌───────────┼──────────┐    ┌───────────┼──────────┐   ┌────────┼────────┐
    │ Plugin    │          │    │ Gateway   │          │   │ CLI    │        │
    │ Commands  │          │    │ (daemon)  │          │   │ first  │        │
    │ Agents    │          │    │ Multi-ch  │          │   │ Multi  │        │
    │ Skills    │          │    │ Routing   │          │   │ Session│        │
    │ Hooks(4)  │          │    │ Steering  │          │   │        │        │
    │ MCP       │          │    │ Session   │          │   │        │        │
    │ Safety    │          │    │ Workspace │          │   │        │        │
    └───────────┼──────────┘    └───────────┼──────────┘   └────────┼────────┘
                │                          │                        │
                └──────────┬───────────────┴────────────┬──────────┘
                           ▼                            ▼
                ┌──────────────────────┐     ┌──────────────────────────┐
                │  平台借鉴了：         │     │  平台的安全特性：         │
                │  - Plugin (CC)       │     │  - 6 个安全 Hook 事件     │
                │  - Hooks (CC)        │     │  - 三级审批模型           │
                │  - Gateway (OC)      │     │  - 敏感工具审批门控       │
                │  - Steering (OC)     │     │  - 去重引擎              │
                │  - Routing (OC)      │     │  - 限流器                │
                │  - Session (OC)      │     │  - 告警关联分析          │
                │  - CLI-first (open)  │     │  - 研判报告生成          │
                │  - Multi-session     │     │  - JSONL 审计链          │
                └──────────────────────┘     │  - 安全工具集(5组20+工具) │
                           │                 │  - 监控 + 可观测性       │
                           ▼                 │  - 异常恢复              │
                ┌──────────────────────┐     └──────────────────────────┘
                │  DeerFlow Harness     │                  │
                │  (Agent 引擎层)       │◄─────────────────┘
                │  DeerFlowClient       │
                │  中间件链 (17个)      │
                │  工具系统 (MCP+TG)    │
                │  记忆系统             │
                │  沙箱 (Local+Docker)  │
                │  IM 渠道 (7个)        │
                └──────────────────────┘
```

---

## 十五、参考产品

| 产品/项目 | 参考价值 |
|----------|---------|
| **DeerFlow** | Agent 引擎层，复用 Harness（中间件/沙箱/记忆/MCP/IM） |
| **Claude Code** | Hooks System + Plugin 架构 + Agent Dispatch 设计 |
| **OpenClaw** | Gateway daemon + Steering + Multi-Agent Routing + Session Model |
| **opencode** | CLI-first + Multi-Session + Skill 系统 |
| **Torq HyperSOC** | 调度层 + 多 Agent 系统 + Hyperautomation 参考 |
| **Microsoft Security Copilot** | 安全专用工具集成 + Playbook 参考 |
| **CrowdStrike Charlotte AI** | 告警研判流程 + 人工审批参考 |
| **Palo Alto Cortex XSIAM** | AI-Native SOC 架构参考 |
| **AgentSOC（论文）** | 多层 Agent 架构学术参考 |
