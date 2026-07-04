# SOC Agent 开发进度

> 本文件是 SOC Agent 长期开发的进度台账。聊天记录不作为进度来源；每完成一个可验证切片，都在这里追加记录。

## 工作方式

每次开始 SOC Agent 开发任务时按以下顺序执行：

1. 先读 `.notes/ai_soc/soc-agent-solution.md` 和相关 `.notes/reference-index/*.md`。
2. 明确当前任务属于哪个 Phase、解决哪个用户/工程问题。
3. 再用 CodeGraph / 源码读取查 DeerFlow 代码落点和参考实现：
   - 局部实现切片优先 CodeGraph，用来定位本仓库符号、调用点和低侵入接入点。
   - 架构型或跨项目切片也默认使用 CodeGraph + 最小源码读取；参考项目只在本地方案尚未定型时使用，常见触发点是 memory、approval policy、多 Agent、stream/event protocol、context compaction、tool runtime。
   - 不再把 Understand Anything 放入日常流程；它消耗较高，且 scoped 增量存在路径作用域问题。
   - 项目顶层 `.understand-anything` 和所有参考项目的 `.understand-anything` 只作为静态快照保留，不再更新。
   - 只有用户明确要求“使用 Understand”时才临时使用；临时结论仍必须经过 CodeGraph/源码确认。
4. 优先新增 SOC 独立模块、adapter、schema、CLI/API 入口，不侵入 DeerFlow 上游核心。
5. 如果切片改变产品方向、runtime pipeline、contract 语义、Phase 边界或下一步顺序，必须同步更新 `.notes/ai_soc/soc-agent-solution.md`；工程规则同步更新 `.notes/reference-index/soc-agent-engineering-contracts.md`。
6. 代码改动后运行 `codegraph sync .`，确保新增/修改的 SOC 符号进入本地索引。
7. 完成后记录改动、验证命令、遗留风险和下一步。

## 当前状态

| 项 | 状态 |
|---|---|
| 当前阶段 | Phase 1 收口：Runtime 可靠性 + SOC Lead Agent MVP |
| 当前目标 | Kafka ingestion 基线已收口；SOC Lead Agent 已复用 DeerFlow custom-agent/profile/skills/chat entry，能接收 ReviewQueue bounded context，并能把显式 action proposal 路由到 policy/approval boundary；Web/TUI 审批入口可展示 proposal 来源和参数；第一个具体 read-only adapter 已可由 Lead Agent 显式 proposal 进入受控运行态 |
| 上游策略 | DeerFlow fork 内增量开发，默认不修改上游核心代码 |
| 数据库策略 | 生产/准生产使用 PostgreSQL；本地开发可用 SOC SQLite 测试库跑 Web/API/CLI 闭环 |
| LLM 策略 | Runtime 固定控制流；LLM 只作为固定节点或 stub，不掌握主流程 |
| 当前下一刀 | MCP adapter bridge / real read-only data source planning |

## Phase 1 切片计划

| 序号 | 切片 | 状态 | 验收标准 |
|---|---|---|---|
| 1 | SOC Agent 代码落点确认与骨架创建 | Done | 明确包目录、CLI 接入方式、测试目录；新增空骨架不破坏现有测试 |
| 2 | contracts + core state | Done | 定义 `AlertInput`、`AnalysisResult`、`Decision`、`AnalysisRun`、`PipelineStepTrace` 等 schema/状态 |
| 3 | fixed Runtime pipeline | Done | `normalize -> entity_extract -> analyze_stub -> validate -> decide -> trace` 固定执行，LLM 不能跳步 |
| 4 | CLI `soc analyze` | Done | 能读取 JSON 文件/字符串，输出结构化 JSON 结果和 step trace |
| 5 | golden alert samples | Partial | 覆盖批准扫描器误报、恶意 IOC、低置信未知、字段缺失；坏 JSON 模拟待补 |
| 6 | Phase 1 最小测试 | Partial | 字段缺失不崩、输出过 schema/domain validation、每步有 trace、不执行自动处置；坏 JSON repair 待补 |
| 7 | replay contract | Done | `AnalysisRun` 记录 input payload/hash；`SocAnalysisService.replay()` 通过 repository 生成新 run，不覆盖旧 run |
| 8 | PostgreSQL run repository | Done | SOC ORM row + SQLAlchemy repository + Alembic migration + headless CLI `show/replay` 已完成 |
| 9 | manual correction loop | Done | `soc correct RUN_ID` 更新 operational decision，保留原 AI verdict，追加 correction record，不自动写 confirmed memory |
| 10 | decision audit log | Done | `soc_decision_audit_log` 独立表记录 analyze/replay/correct 的结构化审计记录 |
| 11 | alert summary read model | Done | `soc_alert_summaries` 保存可查询摘要，analyze/replay/correct 通过 service 维护 summary |
| 12 | legacy platform normalizer | Done | 平安旧预警平台 envelope 转 canonical `AlertInput`，APT/EDR demo 可提取核心实体 |
| 13 | CLI summary list | Done | `soc list` 输出持久化 `AlertSummary`，用于验证 Web/TUI 列表字段 |
| 14 | ZEUS evidence input policy | Done | 平安 ZEUS/天眼 raw message 优先，缺失时 fallback 到 `zeusRawLogs` 并显式降级可信度 |
| 15 | fact reconstruction layer | Done | `entity_extract` 后生成 `FactReconstructionResult`，记录字段可信度、角色候选和冲突报告 |
| 16 | LLM-ready analysis request | Done | `fact_reconstruct` 后生成 `LLMAnalysisRequest`，analyzer 只消费有界分析上下文 |
| 17 | Prompt Builder + SOC prompt golden tests | Done | Prompt 只能从 `LLMAnalysisRequest` 生成；覆盖 PingAn APT/EDR、raw message 缺失 fallback、字段冲突；不把完整 raw payload 无脑塞进 prompt |
| 18 | LLM JSON parser + bad JSON repair | Done | 先严格 JSON parse，再 repair，再 Pydantic/domain validation；覆盖代码块、尾逗号、半截 JSON、字段类型错误 |
| 19 | 真实 LLM analyzer behind flag | Done | 默认继续走 `analyze_stub`；显式配置开启后才调用模型；输出必须经过 prompt builder、JSON parser、schema/domain validation |
| 20 | Offline eval：stub / llm / replay diff | Done | 同一批样本比较 verdict、confidence、needs_review、parse success、冲突字段处理质量 |
| 21 | ReviewQueue API | Done | Gateway 暴露 review queue 列表、调查上下文、关闭、纠正接口；业务动作仍走 `SocReviewService` |
| 22 | ReviewQueue TUI thin client | Done | 基于 service/API 展示 open queue、打开 context、关闭 item、发起 correction；不复制业务逻辑 |
| 23 | SOC Agent chat stream contract | Done | `SocAgentChatService` 输出 DeerFlow-compatible stream event；可加载 ReviewQueue context；不调用 LLM、不替代 core service |
| 24 | SOC TUI chat runtime adapter | Done | 将 `SocAgentStreamEvent` 翻译成 DeerFlow TUI reducer actions；支持 `soc.review_context` custom event；保持纯函数、无 Textual/DB 依赖 |
| 25 | SOC Agent chat TUI workbench shell | Done | `soc chat tui` 启动 DeerFlow-aligned Textual chat workbench；支持普通消息和 `/open REV-...` context loading；业务仍走 `SocAgentChatService` |
| 26 | SOC Agent capability router MVP | Done | `SocAgentCapabilityRouter` 对 chat request 生成白名单 route decision；stream 发出 `soc.route_decision`；TUI 显示 allowed/denied |
| 27 | SOC Agent route -> service/action dispatcher | Done | `SocAgentActionDispatcher` 将 allowed route 映射为显式 service action result；stream 发出 `soc.action_result`；`review.open_context` 通过 `SocReviewService` 执行 |
| 28 | SOC Agent action permission / human approval | Done | `SocAgentActionPolicy` 在 action dispatch 前输出 permission decision；read-only 允许、analyst-write 需 analyst 角色、高风险要求人工审批且不执行 |
| 29 | SOC Agent approval request event | Done | 高风险 action 被拒绝时生成 `SocAgentApprovalRequest`；stream 发出 `soc.approval_request`；TUI 显示 pending approval request |
| 30 | SOC Agent approval grant token | Done | `SocAgentApprovalService` 将 pending approval request 转成一次性 `SocAgentApprovalGrant`；仅 `soc_approver`/`soc_admin` 可批准；仍不执行真实动作 |
| 31 | SOC Agent approval grant persistence / dry-run | Done | `approve()` 可保存 grant；`dry_run_approved_action()` 用 execution token 校验 route/action/expiry，只返回 dry-run result，不执行外部副作用 |
| 32 | ReviewQueue Web thin page | Done | Next.js 工作台新增 `/workspace/soc/review`，通过 Gateway ReviewQueue API 展示队列/上下文并提交 close/correct；前端不复制业务逻辑 |
| 33 | ReviewQueue Web actor/context headers | Done | Web 请求携带 surface/trace/idempotency；Gateway 用认证用户覆盖可伪造 actor header，并把 `surface=web` 写入 service context |
| 34 | approved-action consume/audit boundary | Done | `execute_approved_action()` 要求 `dry_run=False` + idempotency，消费一次性 token，记录 consumed/execution result payload；仍不执行外部副作用 |
| 35 | approval grant repository persistence | Done | 新增 `soc_approval_grants` 表和 SQLAlchemy repository 方法，持久化 approval grant approve/consume 状态 |
| 36 | approved action Gateway API | Done | 新增 `/api/soc/approvals/*`，支持 create grant、dry-run、execute；Gateway admin 映射为 `soc_admin` |
| 37 | approved action Web workbench | Done | ReviewQueue Web 页面新增审批动作面板，复用 Gateway API 完成 create grant、dry-run、execute 边界验证 |
| 38 | approval request inbox API | Done | 新增 `soc_approval_requests` 持久化表和 `/api/soc/approvals/requests` inbox API，供 Kafka daemon、Agent middleware、Web/TUI 共用 |
| 39 | approval inbox Web consumption | Done | Web 审批动作面板从 approval inbox 拉取 pending request，支持列表、详情、approve、dry-run、execute |
| 40 | Agent/daemon approval inbox write boundary | Done | `SocAgentChatService` 可持久化高风险 approval request；`SocDaemonService` 暴露同一 approval inbox 写入边界；真实 Kafka consumer / DeerFlow middleware 仍后续接入 |
| 41 | approval inbox TUI consumption | Done | `soc review tui` 展示 pending approval request，支持打开详情并 approve 生成 execution token；不执行真实动作 |
| 42 | TUI approved-action dry-run / execute command | Done | `soc review tui` 支持 dry-run token 校验和 execute boundary token 消费；execute 要求显式 idempotency key；仍不执行外部副作用 |
| 43 | Kafka daemon scaffold / approval request ingestion | Done | 新增 versioned daemon message contract、`SocDaemonService.process_message()` 和 `soc daemon process` 本地入口；支持 alert 分析与 approval_request 入箱；尚未连接 Kafka broker |
| 44 | SOC Lead Agent approval middleware | Planned | 等 SOC Lead Agent / skills / MCP tool chain 落地后接入；当前只保留 service-level approval boundary，不提前做无宿主 middleware |
| 45 | Kafka consumer adapter planning | Done | 新增 `.notes/ai_soc/kafka-consumer-adapter-plan.md`，明确 mapper/runner/offset/dead-letter/metrics 方案和下一刀 |
| 46 | Kafka record -> daemon message mapper | Done | 新增 `soc_agent.daemon.kafka_mapper`，纯 stdlib + contracts；支持 alert/approval topics、custom topic set、坏 JSON/未知 topic 错误 |
| 47 | Kafka consumer runner skeleton | Done | 新增 `SocKafkaConsumerRunner` 和 `KafkaConsumerPort`，串行 map -> process -> commit；mapper/service failure 进 dead-letter，仍不接真实 broker |
| 48 | Kafka consumer settings + null adapter | Done | 新增 `KafkaConsumerSettings` 环境变量配置 contract 和 `NullKafkaConsumerPort`；默认禁用、启用但无真实 adapter 时 fail-fast |
| 49 | `soc daemon consume` disabled wiring | Done | CLI 读取 `KafkaConsumerSettings` 并运行有限次 runner poll；默认 idle 输出 JSON，disabled path 不要求 DB/Kafka |
| 50 | Confluent Kafka broker adapter | Done | 新增 `backend[kafka]` optional extra 和 `ConfluentKafkaConsumerPort`；支持 subscribe/poll/manual commit/dead-letter produce+flush |
| 51 | Kafka smoke runner + live Redpanda smoke | Done | 新增 `backend/scripts/soc_kafka_smoke.py`，真实 Redpanda smoke 已验证 sample publish、daemon consume、summary、dead-letter、post-commit idle |
| 52 | Kafka bounded runner loop counters | Done | `SocKafkaConsumerRunner.run()` 下沉有限循环，返回 processed/dead_lettered/idle/committed counters；CLI 输出 counters，为后续 metrics/readiness 铺路 |
| 53 | Kafka daemon status/readiness contract | Done | 新增 `soc daemon status`，输出 versioned JSON；检查 database readiness，支持显式 `--check-broker` 轻量 broker poll |
| 54 | Kafka daemon long-running run loop | Done | 新增 `SocKafkaDaemonRunner` 和 `soc daemon run`；支持 SIGINT/SIGTERM graceful stop、idle sleep、bounded local validation 和结构化 run result |
| 55 | Kafka daemon metrics/backoff | Done | `soc daemon run` 输出 run metrics；adapter/runtime error 会 backoff，可配置连续错误阈值，避免故障热循环 |
| 56 | Kafka daemon production entrypoint / healthcheck | Done | 新增 `soc_daemon_entrypoint.sh`、`soc_daemon_healthcheck.sh` 和 production runbook；固定 env、healthcheck、日志采集和 Docker overlay 约定 |
| 57 | Kafka isolated run-mode smoke | Done | `soc_kafka_smoke.py --mode run` 使用隔离 topic 验证 `soc daemon run` 真实 broker 消费、commit、summary 和 dead-letter |
| 58 | Kafka daemon JSONL metric sink | Done | `soc daemon run --metric-jsonl stderr|stdout` 可持续输出 start/result/error/stop JSONL 事件；entrypoint 支持 `SOC_DAEMON_METRIC_JSONL` |
| 59 | Kafka daemon production compose overlay | Done | 新增 `docker-compose.soc-daemon.yaml`，显式 opt-in 启动 SOC daemon；默认不进入 DeerFlow 主 docker 流程 |
| 60 | Kafka daemon Dockerfile multi-extra support | Done | `backend/Dockerfile` 支持 comma/whitespace 分隔 `UV_EXTRAS`；SOC daemon overlay 默认 `postgres,kafka` |
| 61 | Kafka daemon K8s deployment contract | Done | 新增 opt-in K8s template，固定 ConfigMap/Secret/probes/resources/logging 标签；Compose 与 K8s 配置等价关系写入 runbook |
| 62 | Kafka worker pool / concurrency planning | Done | 新增并发规划文档，明确 poller ownership、partition-aware commit、bounded in-flight、幂等前置和 LLM 独立限流 |
| 63 | Kafka partition commit tracker | Done | 新增纯内存 `PartitionCommitTracker`，锁定乱序完成、dead-letter pending、多 partition 和已提交边界的 commit 推进规则 |
| 64 | Kafka daemon idempotency hardening | Done | `SocAnalysisService` 通过 audit idempotency key 复用既有 run，避免同一 Kafka offset 重放重复写 summary/review/audit |
| 65 | Kafka WorkerPoolResult contract | Done | 新增 `KafkaWorkerResult` / `SocKafkaWorker`，worker 只返回 processed/dead_letter_required/retryable/fatal 结构化结果；不 commit、不 dead-letter、不启动并发 |
| 66 | SocSkillResolver + SOC Lead Agent MVP | Done | 复用 DeerFlow custom-agent/profile/skills 机制；按 source/detection/entities/conflict 选择 SOC domain skills；新增只读 CLI `soc agent profile` / `soc agent resolve-skills` |
| 67 | Skill-selected bounded context for analysis/chat | Done | `LLMAnalysisRequest.skill_context`、PromptBuilder、LLM metadata、ReviewContext chat stream 和 TUI translate 已接入 compact skill context；记录 skill/hash/token budget；不让 LLM 动态加载未知 skill |
| 68 | SOC Lead Agent DeerFlow profile installation path | Done | 新增 `soc agent install-profile`，把推荐 profile 写入 DeerFlow per-user custom-agent storage；默认 dry-run/skip 安全语义，`--overwrite` 才覆盖 |
| 69 | SOC Lead Agent chat entry wiring | Done | 新增 `SocLeadAgentChatService`，通过 DeerFlowClient `agent_name=soc-triage` 进入现有 lead_agent；`soc chat tui --lead-agent` 可选启用 |
| 70 | SOC Lead Agent review context bridge | Done | 将 ReviewQueue context 以 bounded context/artifact 形式提供给 DeerFlow SOC Lead Agent；不让 Lead Agent 直接读 repository 或执行处置 |
| 71 | SOC Lead Agent action proposal boundary | Done | 约束 Lead Agent 后续如何输出结构化 action proposal；仍不直接执行 MCP/tool/处置动作，必须回到 policy/approval/service 边界 |
| 72 | Approval inbox proposal payload rendering | Done | Web/TUI 审批入口展示 `source_proposal_id`、`action_payload`、`context_refs`，让分析师审批前能看见 Lead Agent 候选动作来源和参数 |
| 73 | Action adapter registry contract planning | Done | 规划真实 `response.block_ip` / `endpoint.isolate_host` / MCP tool adapter registry 的 contract、幂等、审计和 dry-run 要求；新增 registry/descriptor/protocol/dry-run-only adapter，不直接接生产动作 |
| 74 | Approval service adapter dry-run integration | Done | `SocAgentApprovalService.dry_run_approved_action()` 在 token 校验后可选调用 action adapter registry dry-run，校验 allowlist、payload 和 context refs；默认仍兼容无 registry 的 token-only dry-run |
| 75 | Execute adapter preflight before token consume | Done | `execute_approved_action()` 在消费 token 前可选校验 adapter 存在性、execute 支持度、payload 和 context refs；仍不接生产副作用 |
| 76 | First concrete safe read-only adapter | Done | 先接只读查询类 adapter（资产归属查询或 EDR 进程树查询），验证 adapter descriptor、dry-run、execute preflight 与审计 payload；不接封禁/隔离等写动作 |
| 77 | Read-only adapter dispatcher / tool gateway wiring | Done | 明确 `asset.lookup` 如何通过受控 route/tool gateway 进入运行态；默认不加入 chat router 白名单；结果必须写入 action result / audit payload |
| 78 | SOC Lead Agent read-only tool proposal bridge | Done | Lead Agent 只能通过结构化 envelope 请求 `asset.lookup` 等只读能力；bridge 转成同一条 router/policy/dispatcher/registry 链路；不直接调用 adapter/MCP |
| 79 | MCP adapter bridge / real read-only data source planning | Planned | 规划真实资产系统、EDR 只读查询或 MCP readonly tool 如何通过 adapter descriptor 接入；write/destructive 仍走 approval |

## 进度记录

### 2026-07-05 — SOC Lead Agent read-only tool proposal bridge 切片

- 背景：
  - `asset.lookup` 已能通过显式 chat/tool gateway metadata 进入 dispatcher/registry。
  - 但 SOC Lead Agent 只能输出 high-risk action proposal 到 approval inbox，还不能用同一条边界请求只读查询。
- 变更：
  - `SocLeadAgentActionProposalBoundary` 增加可选 read-only bridge：
    - 只处理 policy 判定为 `read_only` 且 `allowed=True` 的 proposal。
    - 构造显式 `SocAgentChatRequest.metadata["soc_route"]` 和 `metadata["action_payload"]`。
    - 必须经过注入的 `SocAgentCapabilityRouter` allowlist 和 `SocAgentActionDispatcher`。
    - dispatcher 仍通过 action adapter registry 精确匹配 route/action。
  - `SocLeadAgentChatService` 对 read-only proposal 输出标准 stream events：
    - `soc.action_proposal`
    - `soc.route_decision`
    - `soc.permission_decision`
    - `soc.action_result`
  - 高风险 proposal 的审批路径不变，仍输出 `soc.permission_decision` 和 `soc.approval_request`。
  - `soc chat tui --lead-agent` 本地装配加入空的 `InMemoryAssetLookupActionAdapter` registry，用于验证 contract；生产资产系统仍需独立 adapter/MCP bridge。
  - `SOC_LEAD_AGENT_SOUL` 和 `soc-alert-triage` skill 补充只读 `asset.lookup` proposal 约束。
  - 更新 action adapter plan、工程契约、主方案和 alert lifecycle 文档。
- 边界：
  - 不让 Lead Agent 直接调用 adapter、MCP 或资产系统。
  - 不从自然语言或 Markdown 猜测 lookup。
  - 不接生产资产库。
  - 不开放 write/destructive action 执行。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/action_proposals.py soc_agent/lead_agent_chat.py soc_agent/cli.py soc_agent/lead_agent.py tests/test_soc_lead_agent_chat.py`
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/action_proposals.py soc_agent/lead_agent_chat.py soc_agent/cli.py soc_agent/lead_agent.py tests/test_soc_lead_agent_chat.py --check`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_lead_agent_chat.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_action_adapters.py tests/test_soc_agent_service.py tests/test_soc_lead_agent_chat.py tests/test_soc_tui_chat_runtime.py tests/test_soc_agent_lead_agent.py`
  - `codegraph sync .`
- 下一步：
  - 做 MCP adapter bridge / real read-only data source planning：先明确真实资产系统、EDR 只读查询、MCP readonly tool 的 adapter descriptor、配置和审计边界，再接生产数据源。

### 2026-07-05 — Read-only adapter dispatcher / tool gateway wiring 切片

- 背景：
  - `asset.lookup` adapter 已存在，但仍只在 adapter/approval contract 层验证。
  - 需要先打通只读 adapter 的受控运行态入口，再考虑让 Lead Agent 或 MCP bridge 使用。
- 变更：
  - 新增 `SocAgentActionCommand` 作为 adapter 基础 command contract；`SocAgentApprovedActionCommand` 继承它并额外要求 `execution_token_id`。
  - `SocAgentActionDispatcher` 增加可选 `action_adapter_registry`：
    - read-only action 通过 registry execute 调用 adapter。
    - 缺少 registry、adapter 或 payload 校验失败时 fail-fast。
    - 不影响 high-risk approval request / approved action token 流程。
  - `SocAgentChatService` 的 route 解析支持显式 `metadata.soc_route`；adapter payload 只从 `metadata.action_payload` 和 request context refs 构造，不从自然语言猜测。
  - `soc.action_result` stream event 增加 `payload`，让 TUI/Web/Channels 能看到 read-only adapter 输出。
  - `asset.lookup` 默认仍不在 chat router 白名单内；必须显式构造 `SocAgentCapabilityRouter(allowed_routes={"asset.lookup"})` 并注入 adapter registry 才能运行。
  - 更新 action adapter plan、工程契约、主方案，固定 read-only tool gateway 边界。
- 边界：
  - 不接生产资产系统。
  - 不让 Lead Agent 直接调用 adapter/MCP。
  - 不开放自然语言 route/payload 推断。
  - 不接封禁、隔离、禁用账号等 write/destructive action。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/action_adapters.py soc_agent/contracts/schemas.py soc_agent/contracts/__init__.py soc_agent/core/service.py soc_agent/protocols.py tests/test_soc_action_adapters.py tests/test_soc_agent_service.py`
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/action_adapters.py soc_agent/contracts/schemas.py soc_agent/contracts/__init__.py soc_agent/core/service.py soc_agent/protocols.py tests/test_soc_action_adapters.py tests/test_soc_agent_service.py --check`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_action_adapters.py tests/test_soc_agent_service.py::test_agent_chat_service_does_not_allow_asset_lookup_by_default tests/test_soc_agent_service.py::test_agent_chat_service_dispatches_explicit_read_only_asset_lookup_adapter`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_action_adapters.py tests/test_soc_agent_service.py tests/test_soc_approvals_router.py tests/test_soc_lead_agent_chat.py tests/test_soc_tui_chat_runtime.py`
  - `codegraph sync .`
- 下一步：
  - 做 SOC Lead Agent read-only tool proposal bridge：Lead Agent 只能输出结构化只读 tool/proposal envelope，由 bridge 转成显式 route/payload，再走同一条 router/policy/dispatcher/registry 链路。

### 2026-07-05 — First concrete safe read-only adapter 切片

- 背景：
  - action adapter registry、approval dry-run 和 execute preflight 已完成，但还没有具体 read-only adapter 验证真实 adapter result 结构。
  - 下一步不能先做封禁/隔离这类写动作；应先用只读资产查询验证 adapter contract。
- 变更：
  - 新增 `SocAssetLookupRecord` contract，作为资产查询 adapter 的结构化返回记录。
  - 新增 `asset_lookup_adapter_descriptor()`：
    - `route/action=asset.lookup`
    - `risk_level=read_only`
    - `external_side_effect=read`
    - `execute_supported=True`
    - required payload field 为 `asset_key`
  - 新增 `InMemoryAssetLookupActionAdapter`：
    - 使用 in-memory/static inventory 做只读查询。
    - dry-run 只校验 `asset_key`，返回 `external_side_effect=not_executed`。
    - execute 只读查询 inventory，找到返回 `asset_record`，未找到返回 `asset_found=false`；不修改状态。
    - 支持按 `asset_key`、`asset_id`、`hostname`、`primary_ip` 建索引。
  - `SocAgentActionPolicy` 将 `asset.lookup` 登记为 read-only action。
  - `_action_name_for_route()` 支持识别 policy 中登记的 read-only / analyst-write / high-risk action route；但 `asset.lookup` 仍未加入默认 chat router 白名单。
- 边界：
  - 不接生产资产系统。
  - 不接 EDR/F5/SOAR/MCP。
  - 不开放 Lead Agent / TUI / Web 自动调用入口。
  - 不接封禁、隔离、禁用账号等 write/destructive action。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/action_adapters.py soc_agent/contracts/schemas.py soc_agent/contracts/__init__.py soc_agent/core/service.py tests/test_soc_action_adapters.py tests/test_soc_agent_service.py`
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/action_adapters.py soc_agent/contracts/schemas.py soc_agent/contracts/__init__.py soc_agent/core/service.py tests/test_soc_action_adapters.py tests/test_soc_agent_service.py --check`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_action_adapters.py tests/test_soc_agent_service.py::test_agent_action_policy_allows_asset_lookup_as_read_only`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_action_adapters.py tests/test_soc_agent_service.py tests/test_soc_approvals_router.py tests/test_soc_lead_agent_chat.py tests/test_soc_tui_chat_runtime.py`
  - `codegraph sync .`
- 下一步：
  - 做 Read-only adapter dispatcher / tool gateway wiring：决定 `asset.lookup` 通过 action dispatcher、tool gateway 还是 Lead Agent tool bridge 进入运行态；结果必须进入 `SocAgentActionResult` 和审计 payload，不能只进入 prompt。

### 2026-07-05 — Execute adapter preflight before token consume 切片

- 背景：
  - approval dry-run 已能校验 action adapter registry，但 execute 仍然会在 adapter 不存在/不支持 execute 时消费 token。
  - 真实 EDR/F5/SOAR/MCP 接入前，必须保证 execute 在消费 token 前先做 adapter preflight。
- 变更：
  - `SocActionAdapterRegistry` 新增 `preflight_execute()`：
    - 精确解析 `route/action`。
    - 校验 adapter `execute_supported=True`。
    - 校验 `idempotency_key`、required payload fields 和 required context refs。
    - 只返回 `preflight_only=True` 的 `SocAgentActionResult`，不调用 `adapter.execute()`。
  - `SocActionAdapterRegistryPort` 新增 `preflight_execute()`。
  - `SocAgentApprovalService.execute_approved_action()`：
    - 在 `grant.status=approved` 且 route/action 校验后、消费 token 前调用 registry preflight。
    - 合并 approval request 的 `action_payload/context_refs` 与 command payload。
    - preflight 失败时抛 `SocServiceError`，grant 保持 `approved`，不写 consumed/result。
    - preflight 成功后继续按 Phase 1 语义消费 token，但仍不调用真实 adapter execute。
  - 新增测试覆盖 registry preflight 不调用 adapter.execute、dry-run-only adapter 被拒绝、service preflight 成功消费 token、preflight 失败不消费 token。
- 边界：
  - 不接生产 EDR/F5/SOAR/MCP。
  - 不调用 `adapter.execute()`。
  - 不改变无 registry 时的 execute boundary 行为。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/action_adapters.py soc_agent/core/service.py soc_agent/protocols.py tests/test_soc_action_adapters.py tests/test_soc_agent_service.py`
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/action_adapters.py soc_agent/core/service.py soc_agent/protocols.py tests/test_soc_action_adapters.py tests/test_soc_agent_service.py --check`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_action_adapters.py tests/test_soc_agent_service.py::test_agent_approval_service_execute_preflights_adapter_before_consuming_token tests/test_soc_agent_service.py::test_agent_approval_service_execute_preflight_failure_does_not_consume_token tests/test_soc_agent_service.py::test_agent_approval_service_execute_consumes_token_and_is_idempotent`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_action_adapters.py tests/test_soc_agent_service.py tests/test_soc_approvals_router.py tests/test_soc_lead_agent_chat.py tests/test_soc_tui_chat_runtime.py`
  - `codegraph sync .`
- 下一步：
  - 做 First concrete safe read-only adapter：优先选择资产归属查询或 EDR 进程树查询类只读 adapter，验证 adapter wiring、审计字段和 UI/TUI 展示，不碰封禁/隔离写动作。

### 2026-07-04 — Approval service adapter dry-run integration 切片

- 背景：
  - action adapter registry contract 已固定，但 approval dry-run 仍只校验 execution token。
  - 真实 EDR/F5/SOAR/MCP 动作接入前，需要让 dry-run 能验证 adapter allowlist、proposal payload 和 context refs。
- 变更：
  - 新增 `SocActionAdapterRegistryPort` protocol，core service 只依赖协议，不 import 具体 registry 实现。
  - `SocAgentApprovalService` 增加可选 `action_adapter_registry`。
  - `dry_run_approved_action()`：
    - 先按原逻辑校验 approval grant token、expiry、route/action。
    - 有 registry 时，合并 approval request 的 `action_payload/context_refs` 与 command payload，再调用 registry dry-run。
    - command payload 是显式覆盖；无 registry 时仍返回 token-only dry-run 结果。
    - registry validation error 被映射为 `SocServiceError`，Gateway 会返回 400。
  - Gateway `/api/soc/approvals/actions/dry-run` 的默认 service wiring 会透传 `request.app.state.soc_action_adapter_registry`。
- 边界：
  - 不改变 `execute_approved_action()`。
  - 不消费 token。
  - 不调用真实外部工具。
  - 不要求 Web/TUI 复制 proposal payload；service 会从 approval request repository 合并。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/core/service.py soc_agent/protocols.py app/gateway/routers/soc_approvals.py tests/test_soc_agent_service.py tests/test_soc_action_adapters.py`
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/core/service.py soc_agent/protocols.py app/gateway/routers/soc_approvals.py tests/test_soc_agent_service.py tests/test_soc_action_adapters.py --check`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_action_adapters.py tests/test_soc_agent_service.py::test_agent_approval_service_dry_runs_approved_action_without_side_effect tests/test_soc_agent_service.py::test_agent_approval_service_dry_run_uses_action_adapter_registry_payload tests/test_soc_agent_service.py::test_agent_approval_service_dry_run_maps_adapter_validation_error tests/test_soc_agent_service.py::test_agent_approval_service_dry_run_maps_missing_adapter_error tests/test_soc_agent_service.py::test_agent_approval_service_dry_run_rejects_mismatched_action tests/test_soc_approvals_router.py::test_soc_approvals_api_dry_runs_and_executes_approved_action`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_action_adapters.py tests/test_soc_agent_service.py tests/test_soc_approvals_router.py tests/test_soc_lead_agent_chat.py tests/test_soc_tui_chat_runtime.py`
  - `codegraph sync .`
- 下一步：
  - 做 Execute adapter preflight before token consume：执行前先确认 adapter 存在、支持 execute、payload/context refs 满足要求，避免 token 被消费后才发现 adapter 不可执行。

### 2026-07-04 — Action adapter registry contract planning 切片

- 背景：
  - Lead Agent action proposal、approval inbox 和 Web/TUI proposal 展示已打通。
  - 下一步接 EDR/F5/SOAR/MCP 前，需要先固定 action adapter registry contract，避免真实动作靠字符串猜测或绕过 approval boundary。
- 变更：
  - 新增 `SocAgentActionAdapterDescriptor`：
    - 声明 `adapter_id`、`route/action`、`risk_level`、`adapter_kind`、`external_side_effect`、dry-run/execute 支持度、必需 payload/context refs 和幂等要求。
  - 新增 `SocActionAdapter` protocol：
    - 真实 adapter 只能实现 `dry_run()` 和 `execute()`。
  - 新增 `backend/soc_agent/action_adapters.py`：
    - `SocActionAdapterRegistry` 精确按 `route/action` allowlist 解析 adapter。
    - 没有注册 adapter 时 fail-fast，不 fallback 到自然语言或任意 MCP。
    - `DryRunOnlySocActionAdapter` 可验证参数，但 execute 只能返回 failed + `external_side_effect=not_executed`。
  - 新增 `.notes/ai_soc/action-adapter-registry-plan.md`，记录后续 dry-run integration、execute preflight、只读查询 adapter 和 MCP bridge 顺序。
- 边界：
  - 不修改 `SocAgentApprovalService` 当前执行语义。
  - 不调用真实 EDR/F5/SOAR/MCP。
  - 不消费 approval token 之外的任何外部动作能力。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/action_adapters.py soc_agent/contracts/schemas.py soc_agent/contracts/__init__.py soc_agent/protocols.py tests/test_soc_action_adapters.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_action_adapters.py`
- 下一步：
  - 做 Approval service adapter dry-run integration：让审批 token dry-run 同时校验 action adapter registry allowlist、payload 和 context refs，仍不产生外部副作用。

### 2026-07-04 — Approval inbox proposal payload rendering 切片

- 背景：
  - Lead Agent action proposal boundary 已能把高风险候选动作写入 approval inbox。
  - 审批人不能只看到 `action=response.block_ip`，还需要看到 proposal 来源、候选参数和上下文引用。
- 变更：
  - TUI：
    - `render_approval_request()` 展示 `source_proposal_id`、`action_payload`、`context_refs`。
    - 新增 `backend/tests/test_soc_tui_render.py` 覆盖 proposal 字段展示。
  - Web：
    - `SocAgentApprovalRequest` TypeScript 类型增加 `source_proposal_id`、`action_payload`、`context_refs`。
    - ReviewQueue workbench 审批列表对 proposal request 标记 `proposal`。
    - 审批详情上方增加只读 `Lead Agent proposal` 摘要，展示 action payload 和 context refs；保留原 JSON textarea 作为手工兜底。
- 边界：
  - 不改变 approval grant / dry-run / execute 语义。
  - 不新增真实外部动作 adapter。
  - 不让前端复制审批业务逻辑，只展示后端 request payload。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/tui/render.py tests/test_soc_tui_render.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_tui_render.py tests/test_soc_lead_agent_chat.py tests/test_soc_tui_chat_runtime.py`
  - `cd frontend && pnpm check`
  - `codegraph sync .`
- 下一步：
  - 做 Action adapter registry contract planning：先设计真实 adapter registry 的 contract、幂等、dry-run 和审计约束，再决定是否接具体 EDR/F5/MCP。

### 2026-07-04 — SOC Lead Agent action proposal boundary 切片

- 背景：
  - ReviewQueue bounded context 已能进入 DeerFlow `lead_agent`。
  - 下一步需要约束 Lead Agent 如何提出处置/查询候选动作，避免自然语言建议被误当成执行能力。
- 变更：
  - 新增 `backend/soc_agent/action_proposals.py`：
    - 只识别 `<soc_action_proposal>...</soc_action_proposal>` 内的显式 JSON。
    - `extract_action_proposals_from_text()` 会剥离 marker、校验 schema、保留普通回复文本。
    - `SocLeadAgentActionProposalBoundary` 用 `SocAgentActionPolicy` 评估候选动作。
    - 高风险 proposal 会转换成 `SocAgentApprovalRequest`，可通过注入的 `SocAgentApprovalService` 写入 approval inbox。
  - 新增 `SocAgentActionProposal` contract。
  - `SocAgentApprovalRequest` 增加可选 `source_proposal_id`、`action_payload`、`context_refs`，随完整 JSON payload 保存，不需要新迁移列。
  - `SocLeadAgentChatService`：
    - 从 Lead Agent message event 中提取 proposal marker。
    - 发出 `soc.action_proposal`、`soc.permission_decision`、`soc.approval_request` 或 `soc.action_proposal_error` stream event。
    - 不执行任何 action，不调用 MCP/tool。
  - `soc chat tui --lead-agent` 注入同一个 approval service，确保高风险 proposal 进入既有 approval inbox。
  - TUI translate 新增 action proposal / proposal error 展示。
  - `SOC_LEAD_AGENT_SOUL` 增加 action proposal marker 格式。
- 边界：
  - 只有显式 marker 会触发 proposal boundary；普通自然语言不会被猜测为动作。
  - policy 只决定允许、拒绝或需要人工审批；本切片不新增真实 action adapter。
  - approval request 只是 pending inbox 项，不是执行授权。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/action_proposals.py soc_agent/lead_agent_chat.py soc_agent/lead_agent.py soc_agent/cli.py soc_agent/tui/chat_runtime.py soc_agent/contracts/schemas.py soc_agent/contracts/__init__.py soc_agent/core/service.py tests/test_soc_lead_agent_chat.py tests/test_soc_tui_chat_runtime.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_lead_agent_chat.py tests/test_soc_tui_chat_runtime.py tests/test_soc_agent_service.py::test_agent_chat_service_persists_approval_request_to_inbox tests/architecture/test_soc_agent_boundaries.py`
  - `codegraph sync .`
- 下一步：
  - 做 Approval inbox proposal payload rendering：让 Web/TUI 审批面板展示 proposal 来源、payload 和 context refs，避免审批人只能看到 action 名。

### 2026-07-04 — SOC Lead Agent review context bridge 切片

- 背景：
  - `soc chat tui --lead-agent` 已能进入 DeerFlow `lead_agent`，但不能带 ReviewQueue context。
  - 需要让 Lead Agent 看见当前工单上下文，同时不能让它直接读 repository、绕过 service，或执行处置动作。
- 变更：
  - 新增 `backend/soc_agent/context_bridge.py`：
    - `build_lead_agent_review_context_artifact()` 从 `InvestigationContext` 生成 redacted/bounded artifact。
    - artifact 只包含 review/summary/analysis/fact_context/similar_alerts/skill_context 摘要和 hash，不塞完整 raw payload。
    - `render_lead_agent_review_context_message()` 将 artifact 作为 bounded context 前缀交给 DeerFlow Lead Agent。
    - `skill_context_from_investigation_context()` 统一 deterministic chat 和 Lead Agent bridge 的 skill context 生成逻辑。
  - 新增 `SocLeadAgentReviewContextArtifact` contract。
  - `SocLeadAgentChatService` 新增可选 `review_service`：
    - 当 `SocAgentChatRequest.queue_id` 存在时，通过 `SocReviewService.get_investigation_context()` 取 context。
    - stream 发出 `custom kind=soc.lead_agent_review_context`，包含 artifact id、queue/run/alert、context hash、skill context hash 和 bounded artifact。
    - `/open REV-...` 会转成自然语言调查意图，不把 slash command 原样交给 DeerFlow Lead Agent。
  - `soc chat tui --lead-agent --queue-id REV-...` 已放开，CLI 注入同一个 `SocReviewService`。
  - TUI translate 新增 `soc.lead_agent_review_context` 系统消息，只显示 queue/run/alert 和短 hash。
- 边界：
  - 不修改 DeerFlow upstream `lead_agent`。
  - 不创建第二套 SOC LangGraph runtime。
  - 不给 Lead Agent 直接 repository 权限。
  - 不开放真实处置工具；后续 action proposal 必须回到 policy/approval/service 边界。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/context_bridge.py soc_agent/lead_agent_chat.py soc_agent/core/service.py soc_agent/cli.py soc_agent/tui/chat_runtime.py tests/test_soc_lead_agent_chat.py tests/test_soc_tui_chat_runtime.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_lead_agent_chat.py tests/test_soc_tui_chat_runtime.py tests/test_soc_agent_service.py::test_agent_chat_service_loads_review_context tests/architecture/test_soc_agent_boundaries.py`
  - `codegraph sync .`
- 下一步：
  - 做 SOC Lead Agent action proposal boundary：让 Lead Agent 只能输出结构化候选动作，由 SOC policy/approval/service 决定能否进入 approval inbox 或 execute boundary。

### 2026-07-04 — SOC Lead Agent chat entry wiring 切片

- 背景：
  - SOC profile 已能安装到 DeerFlow per-user custom-agent storage。
  - 下一步需要真实入口以 `agent_name=soc-triage` 进入 DeerFlow `lead_agent`，而不是继续停留在 SOC deterministic chat shell。
- 变更：
  - 新增 `backend/soc_agent/lead_agent_chat.py`：
    - `SocLeadAgentChatService`
    - `SocLeadAgentProfileNotInstalledError`
    - 通过 `DeerFlowClient(agent_name="soc-triage")` 转发 stream。
    - stream 开头发出 `custom kind=soc.lead_agent_entry`，标明 agent/thread/surface。
    - 默认要求 profile 已安装；未安装时提示运行 `soc agent install-profile`。
  - `soc chat tui` 新增 `--lead-agent`：
    - 默认仍使用 deterministic `SocAgentChatService`。
    - 传 `--lead-agent` 时切到 DeerFlow SOC Lead Agent entry。
    - 当前 `--lead-agent` 不支持 `--queue-id` 直开 review context，避免把 review repository 绕给 LLM。
  - TUI translate 新增 `soc.lead_agent_entry` 系统消息。
  - 新增 `backend/tests/test_soc_lead_agent_chat.py`。
- 边界：
  - 不创建第二套 SOC LangGraph runtime。
  - 不修改 DeerFlow upstream `lead_agent`。
  - 不开放 SOC 处置工具。
  - 不让 Lead Agent 直接访问 review repository；review context bridge 作为下一刀单独设计。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/lead_agent_chat.py soc_agent/cli.py soc_agent/tui/runner.py soc_agent/tui/chat_app.py soc_agent/tui/chat_runtime.py tests/test_soc_lead_agent_chat.py tests/test_soc_tui_chat_runtime.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_lead_agent_chat.py tests/test_soc_tui_chat_runtime.py tests/test_soc_agent_profile_install.py tests/architecture/test_soc_agent_boundaries.py`
  - `codegraph sync .`
- 下一步：
  - 做 SOC Lead Agent review context bridge：把 ReviewQueue context 转成 bounded context/artifact 供 DeerFlow Lead Agent 使用，同时保留 service/action/approval 边界。

### 2026-07-04 — SOC Lead Agent DeerFlow profile installation path 切片

- 背景：
  - `soc agent profile` 已能输出 SOC custom-agent payload，但还没有写入 DeerFlow existing profile storage。
  - 用户要求能用 DeerFlow 现有能力就复用，避免为 SOC 再建一套 agent 配置系统。
- 变更：
  - 新增 `backend/soc_agent/agent_profile.py`：
    - `SocLeadAgentProfileInstaller`
    - 写入 DeerFlow per-user layout：`.deer-flow/users/{user_id}/agents/soc-triage/config.yaml` 和 `SOUL.md`。
    - 复用 DeerFlow `validate_agent_name()`、`get_paths()`、`get_effective_user_id()`。
  - 新增 contract：
    - `SocLeadAgentInstallResult`
  - 新增 CLI：
    - `soc agent install-profile --dry-run`
    - `soc agent install-profile --user-id USER`
    - `soc agent install-profile --overwrite`
  - 新增测试 `backend/tests/test_soc_agent_profile_install.py`：
    - dry-run 不写文件。
    - install 后可用 DeerFlow `load_agent_config()` / `load_agent_soul()` 反读。
    - 默认不覆盖已有 user-scoped profile。
    - `overwrite=True` 才更新。
    - legacy shared 同名 agent 存在时跳过，避免 shadow。
- 边界：
  - 不修改 DeerFlow upstream core。
  - 不调用独立 SOC agent runtime。
  - 不自建 SOC agent profile storage。
  - 不通过 CLI 静默覆盖用户已有 `soc-triage`。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/agent_profile.py soc_agent/cli.py soc_agent/contracts/schemas.py soc_agent/contracts/__init__.py tests/test_soc_agent_profile_install.py tests/test_soc_agent_lead_agent.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_profile_install.py tests/test_soc_agent_lead_agent.py tests/test_custom_agent.py::TestLoadAgentConfig tests/test_custom_agent.py::TestLoadAgentSoul tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && DEER_FLOW_HOME=/tmp/soc-agent-profile-cli-smoke ./.venv/bin/python -m soc_agent.cli agent install-profile --user-id soc-user --dry-run --pretty`
  - `codegraph sync .`
- 下一步：
  - 讨论 SOC Lead Agent chat entry wiring：让 Web/TUI/CLI 能以 `agent_name=soc-triage` 进入 DeerFlow `lead_agent`，同时保留 SOC service/action/approval 边界。

### 2026-07-04 — Skill-selected bounded context for analysis/chat 切片

- 背景：
  - 上一刀已经能选择 SOC domain skills，但 analysis prompt 和 chat stream 还没有消费这份选择结果。
  - 本刀目标是把 selected skills 变成可审计、可 replay diff 的 bounded context，而不是把完整 `SKILL.md` 塞进 prompt。
- 变更：
  - 新增 contracts：
    - `SocSkillContextItem`
    - `SocSkillContext`
  - `backend/soc_agent/skills.py` 新增 `build_soc_skill_context()`：
    - 从 `SocSkillResolution` 生成 compact skill context。
    - 每个 skill 记录 `skill_name`、reason、confidence、matched_fields、summary、`content_hash`、`token_budget`。
    - `content_hash` 来自 `skills/public/<skill>/SKILL.md` 的 sha256，用于审计和 replay diff。
  - `build_llm_analysis_request()` 自动附带 `skill_context`。
  - `build_analysis_prompt()` 将 `skill_context` 注入 bounded analysis context。
  - `JsonLLMAnalyzer` metadata 记录 `skill_context_hash` 和 `selected_skills`。
  - `SocAgentChatService` 在打开 review context 时额外发出 `custom kind=soc.skill_context`。
  - `soc_agent.tui.chat_runtime.translate()` 可把 `soc.skill_context` 显示为 TUI system message。
- 边界：
  - 不加载完整 skill 文本进 prompt。
  - 不让 LLM 动态加载未知 skill。
  - 不改变 runtime 控制流、不执行工具、不写 memory。
  - Chat/TUI 只展示 selected skill context，不把业务逻辑放到 TUI。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/contracts/schemas.py soc_agent/contracts/__init__.py soc_agent/skills.py soc_agent/pipeline/analysis_context.py soc_agent/prompts/analysis.py soc_agent/llm/analyzer.py soc_agent/core/service.py soc_agent/tui/chat_runtime.py tests/test_soc_agent_lead_agent.py tests/test_soc_agent_prompts.py tests/test_soc_agent_llm_analyzer.py tests/test_soc_agent_service.py tests/test_soc_tui_chat_runtime.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_lead_agent.py tests/test_soc_agent_prompts.py tests/test_soc_agent_llm_analyzer.py tests/test_soc_agent_service.py tests/test_soc_tui_chat_runtime.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m soc_agent.cli analyze samples/alerts/pingan_legacy_edr.json --pretty`
  - `codegraph sync .`
- 下一步：
  - 讨论是否需要把 `soc agent profile` 安装到 DeerFlow existing agents API/profile storage，或者先继续做 SOC Lead Agent 与 DeerFlow chat runtime 的真实接入。

### 2026-07-04 — SocSkillResolver + SOC Lead Agent MVP 切片

- 背景：
  - 用户明确要求：SOC Lead Agent 能用 DeerFlow 已有能力就复用，避免二次开发造成维护困难。
  - DeerFlow 已有 custom-agent 机制：所有 assistant 仍走同一个 `lead_agent`，通过 `agent_name` 加载 per-user `SOUL.md` / `config.yaml`，并用 `skills` / `tool_groups` 白名单限制能力。
- 变更：
  - 新增 `backend/soc_agent/skills.py`：
    - `SocSkillResolver`
    - `SOC_LEAD_AGENT_SKILLS`
    - 按 `source_type`、detection/category/entity/conflict 选择 SOC domain skills。
  - 新增 `backend/soc_agent/lead_agent.py`：
    - `build_soc_lead_agent_profile()` 输出 DeerFlow `/api/agents` 可用的 profile payload。
    - 不写 `.deer-flow`，不新建 LangGraph 图。
  - 新增 contracts：
    - `SocSkillRecommendation`
    - `SocSkillResolution`
    - `SocLeadAgentProfile`
  - 新增 DeerFlow public SOC skills：
    - `soc-alert-triage`
    - `soc-endpoint-triage`
    - `soc-network-apt-triage`
    - `soc-waf-f5-triage`
    - `soc-asset-direction`
  - 新增 CLI：
    - `soc agent profile`
    - `soc agent resolve-skills`
- 复用 DeerFlow 的部分：
  - `make_lead_agent`
  - custom-agent `agent_name`
  - `SOUL.md` / `config.yaml.skills`
  - `SkillActivationMiddleware`
  - `get_available_tools()`
  - `allowed-tools` tool policy
  - existing Web/Gateway agents API
- 边界：
  - 本切片不创建第二套 SOC Lead Agent runtime。
  - `SocSkillResolver` 只推荐 skill，不加载 `SKILL.md` 内容、不执行工具、不写 DB。
  - SOC skills 当前只开放只读/计划型工具：`ask_clarification`、`present_files`、`read_file`、`task`。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/core/runtime.py soc_agent/core/service.py soc_agent/core/__init__.py soc_agent/cli.py soc_agent/contracts/schemas.py soc_agent/contracts/__init__.py soc_agent/skills.py soc_agent/lead_agent.py tests/test_soc_agent_lead_agent.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_lead_agent.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_lead_agent_skills.py tests/test_skills_parser.py tests/test_skills_loader.py tests/test_skills_validation.py tests/test_skills_bundled.py`
  - `cd backend && ./.venv/bin/python -m soc_agent.cli agent profile --pretty`
  - `cd backend && ./.venv/bin/python -m soc_agent.cli agent resolve-skills --json '{"source":{"source_type":"edr","product":"EDR"},"detection":{"rule_name":"Suspicious endpoint process"},"entities":{"process":{"process_name":"powershell.exe"}}}' --pretty`
  - `codegraph sync .`
- 架构修正：
  - `soc agent resolve-skills` 通过 `SocSkillResolutionService` 进入 core service，不从 CLI 直接调用 normalizer/pipeline。
- 备注：
  - `tests/test_slash_skills.py` 单跑在既有 async middleware 测试处长时间未退出，已中断；本切片未修改 slash middleware。
- 下一步：
  - 将 selected skills 接入 analysis/chat bounded context，记录 skill name/hash/token budget，为后续 SOC Lead Agent 对话和 replay diff 打基础。

### 2026-07-04 — Kafka WorkerPoolResult contract 收口切片

- 背景：
  - Kafka 串行 runner、真实 broker adapter、daemon run loop、production entrypoint、healthcheck、JSONL metrics、K8s template、partition commit tracker 和幂等写入边界已经具备。
  - 继续实现 worker pool 会把当前工作带入 Phase 4 吞吐优化，偏离 SOC Agent 主线。
- 变更：
  - 新增 `backend/soc_agent/daemon/kafka_worker.py`。
  - 新增 `KafkaWorkerResultStatus`：`processed`、`dead_letter_required`、`retryable_error`、`fatal_error`。
  - 新增 `KafkaWorkerError` 和 `KafkaWorkerResult`，明确 worker result 不包含 commit/dead-letter 状态。
  - 新增 `SocKafkaWorker`，只负责 `KafkaRecord -> SocDaemonMessage -> SocDaemonService.process_message()`。
  - `SocKafkaConsumerRunner` 改为复用 `SocKafkaWorker`，但仍由 runner 负责 commit 和 dead-letter，现有串行语义不变。
- 边界：
  - 本切片不启动线程、不实现 bounded worker pool、不改变生产默认 `worker_concurrency=1`。
  - 真正并发等真实 Kafka/DB/K8s 参数、吞吐/延迟数据和 LLM 限流策略明确后再打开。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/daemon/kafka_worker.py soc_agent/daemon/kafka_runner.py soc_agent/daemon/__init__.py tests/test_soc_daemon_kafka_worker.py tests/test_soc_daemon_kafka_runner.py tests/test_soc_daemon_kafka_commit_tracker.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_daemon_kafka_worker.py tests/test_soc_daemon_kafka_runner.py tests/test_soc_daemon_kafka_commit_tracker.py`
- 下一步：
  - Kafka 进入收口/暂缓状态。
  - 切回 SOC Agent 主线，先做 `SocSkillResolver + SOC Lead Agent MVP` 的 contract 和最小实现。

### 2026-07-03 — Kafka daemon idempotency hardening 切片

- 背景：
  - 并发/重试/重启后，同一 Kafka offset 可能被再次处理。
  - 如果不加幂等，同一 `kafka:{topic}:{partition}:{offset}` 会重复生成 run、summary、review queue item 和 audit。
- 变更：
  - `soc_decision_audit_log` 增加 `idempotency_key` 索引字段。
  - 新增 migration `0007_audit_idempotency_key`。
  - `DecisionAuditRepository` 增加 `find_audit_record_by_idempotency_key()`。
  - `SqlAlchemyAlertRepository` 支持按 `idempotency_key` + action 查询 audit。
  - `SocAnalysisService._analyze()` 在执行 runtime 前检查同 key、同 action 的既有 audit/run；命中时直接返回旧 run。
  - completion event payload 增加 `idempotent_replay` 标记。
- 语义：
  - 首次处理：正常 runtime -> save run -> save summary/review/audit。
  - 同 key 重放：不再执行 runtime，不新增 summary/review/audit，返回第一次 run。
  - audit 存在但 run 缺失时继续正常分析，用于容忍不完整历史数据。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/core/service.py soc_agent/db/repositories.py soc_agent/db/models.py soc_agent/protocols.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_service.py::test_analysis_service_reuses_existing_run_for_same_idempotency_key tests/test_soc_agent_service.py::test_daemon_service_processes_alert_message_through_analysis_service tests/test_soc_agent_repository.py::test_sqlalchemy_alert_repository_finds_audit_by_idempotency_key tests/test_soc_agent_repository.py::test_sqlalchemy_alert_repository_supports_service_replay`
- 下一步：
  - 做 `WorkerPoolResult` contract，先固定 worker 不 commit、不 dead-letter 的结构化结果语义。

### 2026-07-03 — Kafka partition commit tracker 切片

- 背景：
  - worker pool 并发前必须先锁住 partition-aware commit 推进规则。
  - 当前不改变串行 runner，也不连接真实 Kafka。
- 新增：
  - `backend/soc_agent/daemon/kafka_commit_tracker.py`
  - `backend/tests/test_soc_daemon_kafka_commit_tracker.py`
- 行为：
  - `PartitionCommitTracker` 只做内存状态计算，不 poll、不 commit、不 dead-letter、不调用 core service。
  - `mark_in_flight()` 注册 worker in-flight offset。
  - `mark_processed()` 只在同 partition 连续 offset 完成时返回 `KafkaCommitAdvance`。
  - `mark_dead_letter_pending()` 将失败 offset 标记为不可提交。
  - `mark_dead_lettered()` 只在 dead-letter 成功后把 offset 纳入可推进范围。
  - 多 partition 独立推进。
  - 已推进边界之前的 offset 会被拒绝。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/daemon/__init__.py soc_agent/daemon/kafka_commit_tracker.py tests/test_soc_daemon_kafka_commit_tracker.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_daemon_kafka_commit_tracker.py tests/test_soc_daemon_kafka_runner.py tests/test_soc_daemon_kafka_daemon.py`
- 下一步：
  - 做 daemon idempotency hardening，确保同一 `kafka:{topic}:{partition}:{offset}` 重放不会重复污染 summary、approval inbox、audit 或后续 memory。

### 2026-07-03 — Kafka worker pool / concurrency planning 切片

- 背景：
  - 目前无真实 Kafka/DB/K8s 参数，不适合直接实现并发。
  - 当前串行 runner 语义清楚：poll -> map -> process -> commit/dead-letter。
  - 后续并发最大风险是 offset 越过未完成消息、dead-letter 失败后错误 commit、重复写 summary/approval/audit。
- 新增：
  - `.notes/ai_soc/kafka-worker-pool-concurrency-plan.md`
- 决策：
  - 默认保持 `worker_concurrency=1`，等价当前串行安全模式。
  - 并发只能在 runner/daemon/controller 层扩展，不进入 `SocDaemonService` 内部。
  - Kafka poll/commit/pause/resume ownership 必须留在 poller/controller。
  - worker 不直接 commit、不直接 dead-letter，只返回结构化 result。
  - commit 必须 partition-aware，只能推进同一 partition 连续完成 offsets。
  - 并发前必须先补 daemon idempotency hardening，确保同一 `kafka:{topic}:{partition}:{offset}` 重放不会重复污染数据。
  - LLM concurrency 与 Kafka worker concurrency 分离。
- 同步：
  - README 增加文档入口。
  - engineering contracts 增加 worker pool / concurrency 约束。
  - solution / kafka plan 更新下一步。
- 下一步：
  - 若继续 Kafka daemon 可靠性，优先做 `PartitionCommitTracker` 纯单测切片，或先做 daemon idempotency hardening。

### 2026-07-03 — Kafka daemon K8s deployment contract 切片

- 背景：
  - SOC daemon 已具备生产 entrypoint、healthcheck、JSONL metric sink、compose overlay 和 Dockerfile multi-extra support。
  - 需要把生产部署边界固定成可审阅模板，但不能接入默认 DeerFlow 部署流程。
- 新增：
  - `docker/k8s/soc-daemon.yaml`
  - `backend/tests/test_soc_daemon_k8s_template.py`
- 行为：
  - K8s 模板显式 opt-in，不被默认脚本加载。
  - `ConfigMap` 保存非敏感 Kafka/daemon 配置。
  - `Secret` 保存 `SOC_DATABASE_URL` 和 Kafka password。
  - `SOC_KAFKA_SASL_PASSWORD_ENV=SOC_KAFKA_PASSWORD`，代码只读取 secret env 名。
  - Deployment command 复用 `backend/scripts/soc_daemon_entrypoint.sh`。
  - readiness/liveness 复用 `backend/scripts/soc_daemon_healthcheck.sh`。
  - 不创建 Service；daemon 先通过 stderr JSONL 暴露最低观测面。
  - 模板包含 resource requests/limits 和日志标签。
- 同步：
  - runbook 补 K8s template、环境变量和 Compose/K8s 等价关系。
  - engineering contracts 补 K8s 模板边界。
  - solution / kafka plan 更新当前状态和下一步。
- 下一步：
  - 如果有真实环境参数，验证 image、namespace、secret manager、日志采集标签、resource sizing。
  - 如果继续产品闭环，进入 worker pool / concurrency planning，明确什么时候从单条串行消费扩到并发。

### 2026-07-03 — Kafka daemon Dockerfile multi-extra support 切片

- 背景：
  - SOC daemon 生产镜像通常需要同时安装 PostgreSQL 与 Kafka optional extras。
  - 之前 Dockerfile build-time `UV_EXTRAS` 只能可靠处理单个 extra，compose overlay 只能保守默认 `kafka`。
- 变更：
  - `backend/Dockerfile`：
    - `UV_EXTRAS` 支持 comma/whitespace 分隔，例如 `postgres,kafka` 或 `postgres kafka`。
    - 每个 extra 名称校验为 `[A-Za-z][A-Za-z0-9_-]*`。
    - build sync 改为 `uv sync --all-packages $EXTRAS_FLAGS`。
  - `docker/docker-compose.soc-daemon.yaml`：
    - 默认 `SOC_DAEMON_UV_EXTRAS=postgres,kafka`。
    - 本地 SQLite + Kafka 验证仍可显式设置 `SOC_DAEMON_UV_EXTRAS=kafka`。
  - `scripts/detect_uv_extras.py`：
    - 更新说明，确认 Dockerfile 与 dev-entrypoint/local detect 采用一致的多 extra 语义。
- 测试：
  - 新增 Dockerfile 静态回归断言，防止退回 `${UV_EXTRAS:+--extra $UV_EXTRAS}`。
  - 更新 compose overlay 测试，锁住默认 `postgres,kafka`。
- 下一步：
  - 补 deployment hardening / K8s template planning：secret 注入、resource limits、restart policy、日志采集标签、Compose 与 K8s 配置等价关系。

### 2026-07-03 — Kafka daemon production compose overlay 切片

- 背景：
  - 生产 entrypoint、healthcheck、JSONL metric sink 已完成。
  - 需要一个可执行的 compose overlay 示例，但不能改 DeerFlow 默认 docker 启动行为。
- 新增：
  - `docker/docker-compose.soc-daemon.yaml`
- 行为：
  - 显式 opt-in：
    - `docker compose -p deer-flow-dev -f docker-compose-dev.yaml -f docker-compose.soc-daemon.yaml up -d soc-daemon`
  - 默认不被 `scripts/docker.sh` / `make docker-start` 加载。
  - service：`soc-daemon`
  - command：`backend/scripts/soc_daemon_entrypoint.sh`
  - healthcheck：`backend/scripts/soc_daemon_healthcheck.sh`
  - 默认 `SOC_DAEMON_METRIC_JSONL=stderr`。
  - 默认 build extra 使用 `postgres,kafka`，由 Dockerfile multi-extra support 展开。
- 已补充测试：
  - overlay 包含 entrypoint、healthcheck、metric env。
  - `scripts/docker.sh` 不加载 `docker-compose.soc-daemon.yaml`。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format tests/test_soc_daemon_compose_overlay.py`
  - `cd backend && ./.venv/bin/python -m ruff check tests/test_soc_daemon_compose_overlay.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_daemon_compose_overlay.py tests/test_soc_daemon_scripts.py`
  - `cd docker && docker compose -p deer-flow-dev -f docker-compose-dev.yaml -f docker-compose.soc-daemon.yaml config --services`
  - compose config 输出包含 `soc-daemon`；本地未设置 `DEER_FLOW_ROOT` 时会有 compose warning，不影响 overlay 解析。
- 后续已完成：
  - Dockerfile multi-extra build arg support 已在下一切片补齐。

### 2026-07-03 — Kafka daemon JSONL metric sink 切片

- 背景：
  - `soc daemon run` 之前只在进程退出时输出 summary。
  - 长驻 daemon 需要运行中事件流，便于容器日志采集、排障和后续 Prometheus exporter。
- 新增：
  - `KafkaDaemonMetricSink` protocol。
  - `JsonLineKafkaDaemonMetricSink`。
  - `soc daemon run --metric-jsonl stdout|stderr`。
  - `SOC_DAEMON_METRIC_JSONL` entrypoint env。
- 行为：
  - 默认不启用 JSONL metric sink，保持现有 CLI/smoke 输出兼容。
  - 开启后输出 schema：`soc.kafka_daemon_metric.v1`。
  - 事件类型：
    - `start`
    - `result`
    - `error`
    - `stop`
  - 推荐生产使用 `--metric-jsonl stderr` 或 `SOC_DAEMON_METRIC_JSONL=stderr`，让 stdout 保留最终 run summary。
  - result 事件只包含 record metadata 和 daemon_result 摘要，不输出完整告警 payload。
  - error 事件只记录 loop-level adapter/runtime error；mapper/service failure 仍由 runner dead-letter 语义处理。
- 已补充测试：
  - runner emits start/result/stop。
  - runner emits error。
  - JSONL sink 一行一个 JSON object。
  - CLI `--metric-jsonl stderr` 不污染 stdout summary。
  - entrypoint `SOC_DAEMON_METRIC_JSONL=stderr` 可输出 JSONL。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/daemon/__init__.py soc_agent/daemon/kafka_daemon.py soc_agent/cli.py tests/test_soc_daemon_kafka_daemon.py tests/test_soc_agent_runtime.py tests/test_soc_daemon_scripts.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/daemon/__init__.py soc_agent/daemon/kafka_daemon.py soc_agent/cli.py tests/test_soc_daemon_kafka_daemon.py tests/test_soc_agent_runtime.py tests/test_soc_daemon_scripts.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_daemon_kafka_daemon.py tests/test_soc_agent_runtime.py::test_cli_daemon_run_can_emit_metric_jsonl_to_stderr tests/test_soc_agent_runtime.py::test_cli_daemon_run_disabled_by_default_outputs_bounded_run tests/test_soc_daemon_scripts.py::test_soc_daemon_entrypoint_can_emit_metric_jsonl_to_stderr`
  - `cd backend && ./.venv/bin/python -m soc_agent.cli daemon run --max-loops 1 --idle-sleep-ms 0 --metric-jsonl stderr --pretty`
- 下一步：
  - Prometheus exporter 暂缓，方案记录在 `kafka-consumer-adapter-plan.md`；下一步进入 production overlay planning。

### 2026-07-03 — Kafka isolated run-mode smoke 切片

- 背景：
  - 生产 daemon 入口是 `soc daemon run`，此前 live smoke 主要验证 `soc daemon consume`。
  - 需要一个隔离 topic 的 run-mode smoke，避免用默认 topic + 新 group 时消费历史消息。
- 变更：
  - `backend/scripts/soc_kafka_smoke.py` 新增 `--mode {consume,run}`。
  - 默认仍是 `consume`，保持已有调用兼容。
  - `--mode run` 使用：
    - `soc daemon run`
    - `--max-loops 1`
    - `--idle-sleep-ms 0`
    - `--error-backoff-ms 0`
    - `--include-results`
  - post-commit idle 检查继续用同一 group 的 `soc daemon consume --max-records 1`，验证 run-mode 处理后 offset 已提交。
  - smoke result 新增 `mode` 字段。
- 已补充测试：
  - `_daemon_command(mode="consume")`
  - `_daemon_command(mode="run")`
  - unknown mode fail-fast。
  - daemon result 提取校验。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format scripts/soc_kafka_smoke.py tests/test_soc_kafka_smoke_script.py`
  - `cd backend && ./.venv/bin/python -m ruff check scripts/soc_kafka_smoke.py tests/test_soc_kafka_smoke_script.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_kafka_smoke_script.py`
  - `cd backend && ./.venv/bin/python scripts/soc_kafka_smoke.py --help`
  - `cd backend && ./.venv/bin/python scripts/soc_kafka_smoke.py --mode run --database-url sqlite+pysqlite:////tmp/soc_kafka_smoke_20260703_runmode.db --include-dead-letter --timeout-seconds 30`
- live run-mode smoke 结果：
  - broker：`localhost:9092`
  - group_id：`soc-smoke-1783067390`
  - topic：`soc.alerts.raw.v1.smoke.1783067390`
  - mode：`run`
  - run_id：`RUN-F8E8B65D7FFB`
  - alert_id：`ALT-SAMPLE-FP-001`
  - consume_result：`processed`, `committed=true`
  - summary_count：`1`
  - dead-letter key：`smoke-bad-1783067391`
  - dead-letter error_type：`KafkaMapperError`
  - post_commit_result：`idle`
- 下一步：
  - 做 daemon JSONL metric sink，让长驻 daemon 运行过程可持续输出结构化运行事件，而不是只在退出时输出 run summary。

### 2026-07-03 — Kafka daemon production entrypoint / healthcheck 切片

- 背景：
  - `soc daemon run` 已具备长驻 loop、graceful stop、metrics 和 backoff。
  - 需要把生产启动方式、healthcheck、环境变量和日志采集约定固定下来，避免后续部署脚本各写一套。
- 新增：
  - `backend/scripts/soc_daemon_entrypoint.sh`
  - `backend/scripts/soc_daemon_healthcheck.sh`
  - `.notes/ai_soc/soc-daemon-production-runbook.md`
- 行为：
  - entrypoint 默认要求 `SOC_KAFKA_ENABLED=true`。
  - 未显式设置时，entrypoint 会导出 `SOC_KAFKA_ENABLED=true`，避免生产容器悄悄跑在 null adapter。
  - 只有测试/本地验证允许 `SOC_DAEMON_ALLOW_DISABLED=true`。
  - 可选 `SOC_DAEMON_UPGRADE_DB=true` 在启动前执行 `soc db upgrade`；生产更推荐独立 migration job。
  - 可选 `SOC_DAEMON_PRESTART_STATUS_CHECK=true` 在启动前执行 healthcheck。
  - healthcheck 默认执行 `soc daemon status --check-broker`，只检查 DB/broker readiness，不处理业务消息。
  - 没有直接修改 DeerFlow 主 docker-compose；SOC daemon 作为业务扩展进程，后续通过独立 overlay/生产模板接入。
- 已补充测试：
  - entrypoint 在 `SOC_KAFKA_ENABLED=false` 且无 override 时 fail-fast。
  - entrypoint 支持 disabled bounded local validation。
  - healthcheck 支持无 broker 的本地 config/DB 验证。
- 已验证：
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_daemon_scripts.py tests/test_soc_daemon_kafka_daemon.py tests/test_soc_agent_runtime.py::test_cli_daemon_run_disabled_by_default_outputs_bounded_run`
- 下一步：
  - 做 daemon JSONL metric sink 或 isolated run-mode smoke，优先让长驻 run 模式也有不依赖历史 topic 的可重复验收。

### 2026-07-03 — Kafka daemon metrics/backoff 切片

- 背景：
  - `soc daemon run` 已具备长驻 loop 和 graceful stop。
  - 生产运行还需要最小 metrics 和错误退避，否则 broker/DB 短暂故障可能造成热循环，且 supervisor 无法判断运行质量。
- 新增：
  - `KafkaDaemonRunResult` 运行 metrics：
    - `started_at`
    - `stopped_at`
    - `error_count`
    - `consecutive_error_count`
    - `last_success_at`
    - `last_error_at`
    - `last_error_type`
    - `last_error_message`
  - `SocKafkaDaemonRunner(error_backoff_seconds=..., max_consecutive_errors=...)`
  - CLI 参数：
    - `soc daemon run --error-backoff-ms`
    - `soc daemon run --max-consecutive-errors`
- 行为：
  - `SocKafkaDaemonRunner` 捕获 poll/runtime 层异常，记录 metrics，并在继续前按 `error_backoff_seconds` sleep。
  - 达到 `max_consecutive_errors` 后停止，`stop_reason=max_consecutive_errors_reached`。
  - `--max-consecutive-errors 0` 表示不设连续错误上限。
  - `--error-backoff-ms 0` 仅用于测试/本地快速验收；生产不应设为 0。
  - per-record 语义不变：mapper/service failure 仍由 `SocKafkaConsumerRunner.process_record()` 进入 dead-letter + commit；daemon controller 不直接处理业务消息。
  - 输出 schema 仍是 `soc.kafka_daemon_run_result.v1`，新增 `metrics` 节点；原 `counters` 保持 processed/dead_lettered/idle/committed 不变。
- 已补充测试：
  - transient error 后 backoff 并继续处理下一轮。
  - 达到连续错误阈值后停止。
  - invalid backoff / consecutive error 参数 fail-fast。
  - CLI run 输出 metrics。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/daemon/kafka_daemon.py soc_agent/cli.py tests/test_soc_daemon_kafka_daemon.py tests/test_soc_agent_runtime.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/daemon/__init__.py soc_agent/daemon/kafka_daemon.py soc_agent/cli.py tests/test_soc_daemon_kafka_daemon.py tests/test_soc_agent_runtime.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_daemon_kafka_daemon.py tests/test_soc_daemon_kafka_status.py tests/test_soc_daemon_kafka_runner.py tests/test_soc_daemon_kafka_config.py tests/test_soc_daemon_kafka_mapper.py tests/test_soc_agent_runtime.py::test_cli_daemon_run_disabled_by_default_outputs_bounded_run tests/test_soc_agent_runtime.py::test_cli_daemon_run_rejects_invalid_loop_args tests/test_soc_agent_runtime.py::test_cli_daemon_consume_disabled_by_default_outputs_idle tests/test_soc_agent_runtime.py::test_cli_daemon_consume_enabled_requires_database_before_kafka tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m soc_agent.cli daemon run --max-loops 2 --idle-sleep-ms 0 --error-backoff-ms 0 --include-results --pretty`
- 下一步：
  - 明确 production supervisor / Docker entrypoint 约定：进程命令、env contract、healthcheck、readiness 调用、日志采集和隔离 topic smoke。

### 2026-07-03 — Kafka daemon long-running run loop 切片

- 背景：
  - `soc daemon consume` 适合 smoke 和有限 poll，不应该被改成默认长驻命令。
  - 生产后台进程需要单独的 run loop：可优雅停止、可空闲 sleep、可在本地用 loop cap 验证，不改变 per-record commit/dead-letter 语义。
- 新增：
  - `soc_agent.daemon.kafka_daemon`
  - `KafkaDaemonStopSignal`
  - `KafkaDaemonRunResult`
  - `SocKafkaDaemonRunner`
  - CLI：`soc daemon run`
- 行为：
  - `SocKafkaDaemonRunner` 包装现有 `SocKafkaConsumerRunner.process_next()`，不重写 Kafka record 处理逻辑。
  - `run(max_loops=None)` 默认长驻，直到 stop signal。
  - `--max-loops` 只用于本地验收、测试和 smoke，不是生产默认。
  - `--idle-sleep-ms` 控制 idle poll 后 sleep；测试可设为 `0`。
  - CLI 安装 `SIGINT` / `SIGTERM` handler，收到信号后设置 stop flag，当前 poll 返回后退出。
  - 不论正常停止还是异常，controller 都会调用 `runner.close()`，确保 consumer port 释放。
  - 输出 schema 固定为 `soc.kafka_daemon_run_result.v1`，默认只输出 counters；`--include-results` 才输出每轮结果，避免长驻进程输出无限增长。
- 已补充测试：
  - daemon runner 到达 `max_loops` 后停止并 close consumer。
  - stop signal 预先触发时不处理 loop，但仍 close consumer。
  - idle sleep 后可由 stop signal 停止。
  - invalid `idle_sleep_seconds` / `max_loops` fail-fast。
  - CLI disabled bounded run 输出 structured JSON。
  - CLI invalid args 返回 exit code 2。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/daemon/__init__.py soc_agent/daemon/kafka_daemon.py soc_agent/cli.py tests/test_soc_daemon_kafka_daemon.py tests/test_soc_agent_runtime.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/daemon/__init__.py soc_agent/daemon/kafka_daemon.py soc_agent/cli.py tests/test_soc_daemon_kafka_daemon.py tests/test_soc_agent_runtime.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_daemon_kafka_daemon.py tests/test_soc_daemon_kafka_status.py tests/test_soc_daemon_kafka_runner.py tests/test_soc_daemon_kafka_config.py tests/test_soc_daemon_kafka_mapper.py tests/test_soc_agent_runtime.py::test_cli_daemon_run_disabled_by_default_outputs_bounded_run tests/test_soc_agent_runtime.py::test_cli_daemon_run_rejects_invalid_loop_args tests/test_soc_agent_runtime.py::test_cli_daemon_consume_disabled_by_default_outputs_idle tests/test_soc_agent_runtime.py::test_cli_daemon_consume_enabled_requires_database_before_kafka tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m soc_agent.cli daemon run --max-loops 2 --idle-sleep-ms 0 --include-results --pretty`
  - `cd backend && SOC_KAFKA_ENABLED=true SOC_KAFKA_BOOTSTRAP_SERVERS=localhost:9092 SOC_KAFKA_GROUP_ID=soc-daemon-run-check-1783066000 ./.venv/bin/python -m soc_agent.cli daemon run --database-url sqlite+pysqlite:////tmp/soc_daemon_status_20260703.db --max-loops 1 --idle-sleep-ms 0 --pretty`
- live broker run 结果：
  - broker：`localhost:9092`
  - command：`soc daemon run --max-loops 1`
  - `stop_reason=max_loops_reached`
  - `processed=1`
  - `committed=1`
  - 注意：这次使用默认 topic + 新 group，消费到历史 topic 中的一条消息；后续 smoke 仍应使用隔离 topic，避免历史消息干扰验收。
- 下一步：
  - 做 metrics/backoff/production supervisor planning：失败退避、last_success_at、continuous counters、可接 Prometheus/日志的 event sink、Docker entrypoint 约定。

### 2026-07-03 — Kafka daemon status/readiness contract 切片

- 背景：
  - bounded runner loop 已有 counters，但还缺一个 supervisor / 人工验收可调用的 readiness 入口。
  - 在进入长驻 daemon 前，先固定状态输出 contract，避免后续 Docker/K8s/运维脚本各自判断。
- 新增：
  - `soc_agent.daemon.kafka_status`
  - `KafkaDaemonStatus`
  - `KafkaDaemonDatabaseStatus`
  - `KafkaDaemonBrokerStatus`
  - `build_kafka_daemon_status()`
  - CLI：`soc daemon status`
- 行为：
  - 输出 schema 固定为 `soc.kafka_daemon_status.v1`。
  - 默认检查 database URL 是否配置且可执行 `SELECT 1`。
  - 默认不连接 broker；Kafka broker 连通性必须显式传 `--check-broker`。
  - `SOC_KAFKA_ENABLED=false` 时 kafka status 表示 adapter configured / broker check skipped，适合本地和 CI。
  - `SOC_KAFKA_ENABLED=true --check-broker` 时通过真实 adapter 做一次轻量 `poll()`，不处理业务消息、不提交 offset、不写 DB。
  - database URL 输出会隐藏 password。
- 已补充测试：
  - database 未配置 -> unready。
  - SQLite database 可达 -> ready。
  - skip database check。
  - Kafka enabled 但不检查 broker。
  - broker checker success / failure。
  - CLI status JSON 输出和 exit code。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/daemon/__init__.py soc_agent/daemon/kafka_status.py soc_agent/cli.py tests/test_soc_daemon_kafka_status.py tests/test_soc_agent_runtime.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_daemon_kafka_status.py tests/test_soc_agent_runtime.py::test_cli_daemon_status_outputs_readiness_json tests/test_soc_agent_runtime.py::test_cli_daemon_status_returns_unready_when_database_missing tests/test_soc_agent_runtime.py::test_cli_daemon_consume_disabled_by_default_outputs_idle tests/test_soc_agent_runtime.py::test_cli_daemon_consume_enabled_requires_database_before_kafka tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m soc_agent.cli daemon status --database-url sqlite+pysqlite:////tmp/soc_daemon_status_20260703.db --pretty`
  - `cd backend && SOC_KAFKA_ENABLED=true SOC_KAFKA_BOOTSTRAP_SERVERS=localhost:9092 SOC_KAFKA_GROUP_ID=soc-status-check-1783065000 ./.venv/bin/python -m soc_agent.cli daemon status --database-url sqlite+pysqlite:////tmp/soc_daemon_status_20260703.db --check-broker --pretty`
- live readiness 结果：
  - broker：`localhost:9092`
  - `ready=true`
  - database reachable：`true`
  - kafka checked：`true`
  - kafka reachable：`true`
- 下一步：
  - 进入 long-running daemon / graceful shutdown 规划：signal handling、loop lifecycle、backoff、metrics emission、supervisor/Docker entrypoint。

### 2026-07-03 — Kafka bounded runner loop counters 切片

- 背景：
  - live smoke 已验证 broker path。
  - 下一步做 readiness / 长驻 daemon 前，需要先把 CLI 中的手写 poll loop 下沉为 runner 级稳定入口。
- 新增：
  - `KafkaRunnerLoopResult`
  - `SocKafkaConsumerRunner.run(max_records=..., stop_on_idle=True)`
- 行为：
  - bounded loop 仍是有限 poll，不是生产 supervisor。
  - `max_records < 1` fail-fast。
  - 默认遇到 idle 停止，保持当前 CLI/smoke 行为。
  - loop result 暴露 counters：
    - `processed_count`
    - `dead_lettered_count`
    - `idle_count`
    - `committed_count`
  - `soc daemon consume` 复用 `runner.run()` 并输出 `counters` JSON。
- 边界：
  - per-record 语义不变：成功 commit；mapper/service failure dead-letter 后 commit；dead-letter failure 仍向外抛。
  - 还不做无限循环、signal handling、readiness endpoint、metrics exporter 或 supervisor。
- 已补充测试：
  - `run()` 聚合两条 processed + 一条 idle。
  - `run(max_records=0)` 参数校验。
  - CLI disabled output 包含 counters。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/daemon/__init__.py soc_agent/daemon/kafka_runner.py soc_agent/cli.py tests/test_soc_daemon_kafka_runner.py tests/test_soc_agent_runtime.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/daemon/__init__.py soc_agent/daemon/kafka_runner.py soc_agent/cli.py tests/test_soc_daemon_kafka_runner.py tests/test_soc_agent_runtime.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_daemon_kafka_runner.py tests/test_soc_daemon_kafka_mapper.py tests/test_soc_agent_runtime.py::test_cli_daemon_consume_disabled_by_default_outputs_idle tests/test_soc_agent_runtime.py::test_cli_daemon_consume_enabled_requires_database_before_kafka tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m soc_agent.cli daemon consume --pretty`
  - `cd backend && ./.venv/bin/python scripts/soc_kafka_smoke.py --database-url sqlite+pysqlite:////tmp/soc_kafka_smoke_20260703_loop.db --include-dead-letter --timeout-seconds 30`
- live smoke 结果：
  - group_id：`soc-smoke-1783064507`
  - run_id：`RUN-8F5BAC0AEDC6`
  - topic：`soc.alerts.raw.v1.smoke.1783064507`
  - dead-letter key：`smoke-bad-1783064508`
- 下一步：
  - 设计 readiness / graceful shutdown：DB readiness、broker assignment readiness、signal handling、metrics emission 和 long-running daemon boundary。

### 2026-07-03 — Kafka smoke runner + live Redpanda smoke 切片

- 背景：
  - Confluent Kafka adapter 已完成，需要一个可重复的本地 smoke 验证入口。
  - Docker Desktop / WSL integration 恢复后，已用临时 Redpanda 容器跑通真实 broker smoke。
- 新增：
  - `backend/scripts/soc_kafka_smoke.py`
- smoke runner 行为：
  - 连接已有 Kafka/Redpanda broker，默认 `localhost:9092`。
  - 默认使用带时间戳后缀的临时 smoke topics，避免历史 topic 消息污染；`--stable-topics` 可使用固定 SOC topic。
  - 创建/确认 topics：
    - `soc.alerts.raw.v1.smoke.<ts>` 或 `soc.alerts.raw.v1`
    - `soc.approvals.requests.v1.smoke.<ts>` 或 `soc.approvals.requests.v1`
    - `soc.alerts.dead_letter.v1.smoke.<ts>` 或 `soc.alerts.dead_letter.v1`
  - 发布一条 alert sample，默认 `backend/samples/alerts/approved_scanner.json`。
  - 调用真实 CLI path：`soc daemon consume --database-url ... --max-records 1`。
  - 验证 `consume_result.status=processed`。
  - 调用 `soc list` 验证 `AlertSummary` 已落库。
  - `--include-dead-letter` 可额外发布坏 JSON 并验证 dead-letter topic 中出现 `soc.kafka_dead_letter.v1`。
  - 再用同一 consumer group poll 一次，验证 `post_commit_result.status=idle`，确认 offset commit 不会重复处理。
- 修复：
  - `SocKafkaConsumerRunner` 现在接收 configured `alert_topics` / `approval_request_topics`。
  - CLI 从 `KafkaConsumerSettings` 把 topic set 传给 runner。
  - 修复前，adapter 可以订阅自定义 topic，但 runner mapper 仍只认默认 topic，真实 smoke 会把临时 topic 误判为 unknown topic。
- 使用示例：
  - `cd backend && uv sync --extra kafka`
  - `cd backend && ./.venv/bin/python scripts/soc_kafka_smoke.py --database-url sqlite+pysqlite:////tmp/soc_kafka_smoke.db`
  - `cd backend && ./.venv/bin/python scripts/soc_kafka_smoke.py --include-dead-letter`
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format scripts/soc_kafka_smoke.py`
  - `cd backend && ./.venv/bin/python -m ruff check scripts/soc_kafka_smoke.py soc_agent/daemon/kafka_runner.py soc_agent/cli.py tests/test_soc_daemon_kafka_runner.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_daemon_kafka_runner.py tests/test_soc_daemon_kafka_mapper.py tests/test_soc_agent_runtime.py::test_cli_daemon_consume_disabled_by_default_outputs_idle tests/test_soc_agent_runtime.py::test_cli_daemon_consume_enabled_requires_database_before_kafka`
  - `cd backend && ./.venv/bin/python scripts/soc_kafka_smoke.py --help`
  - `docker run -d --name soc-redpanda-smoke -p 9092:9092 -p 9644:9644 docker.redpanda.com/redpandadata/redpanda:latest ...`
  - `cd backend && ./.venv/bin/python scripts/soc_kafka_smoke.py --database-url sqlite+pysqlite:////tmp/soc_kafka_smoke_20260703_isolated2.db --include-dead-letter --timeout-seconds 30`
- live smoke 结果：
  - broker：`localhost:9092`，container：`soc-redpanda-smoke`
  - group_id：`soc-smoke-1783064070`
  - alert topic：`soc.alerts.raw.v1.smoke.1783064070`
  - alert_id：`ALT-SAMPLE-FP-001`
  - run_id：`RUN-C140EB6BEB70`
  - consume result：`processed`, `committed=true`
  - summary_count：`1`
  - review queue：`[]`，符合 approved scanner false positive / no review 预期
  - dead-letter：`soc.kafka_dead_letter.v1`, key `smoke-bad-1783064071`, offset `1`, error_type `KafkaMapperError`
  - post-commit check：同一 group 再 poll 返回 `idle`
- 下一步：
  - 做 daemon readiness / metrics / long-running loop 规划；当前 CLI smoke 仍是有限 poll，不是生产 daemon supervisor。

### 2026-07-03 — Confluent Kafka broker adapter 切片

- 背景：
  - `soc daemon consume` shell 已存在，但只能 disabled idle。
  - 当前 runner 是同步模型，优先接 `confluent-kafka`，保持生产成熟度和同步 adapter 简洁性。
- 依赖：
  - 新增 optional extra：`backend[kafka]` -> `confluent-kafka>=2.6.0`。
  - 普通 backend install 不强制安装 Kafka SDK；生产 daemon 或本地 broker 验证时显式安装 extra。
- 新增：
  - `ConfluentKafkaConsumerPort`
  - `build_kafka_consumer_port(settings)`
  - `KafkaAdapterError`
- 行为：
  - disabled：factory 返回 `NullKafkaConsumerPort`。
  - enabled：factory 返回 `ConfluentKafkaConsumerPort`。
  - `subscribe()` 订阅 alert topics + approval request topics。
  - `poll()` 将 Confluent message 转为 client-neutral `KafkaRecord`。
  - consumer error / empty value 直接抛 `KafkaAdapterError`，不进入 mapper/core。
  - `commit()` 使用 `TopicPartition(topic, partition, offset + 1)` 同步提交。
  - `send_dead_letter()` 生成 `soc.kafka_dead_letter.v1` payload，写入 dead-letter topic 并同步 `flush()`。
- CLI 顺序修正：
  - `SOC_KAFKA_ENABLED=true` 时先校验/组装 repository-backed `SocDaemonService`，再构造真实 Kafka client。
  - 避免数据库配置错误时先产生 broker 连接尝试。
- 已补充测试：
  - factory disabled -> null port。
  - fake Confluent message -> `KafkaRecord`。
  - manual commit offset = consumed offset + 1。
  - dead-letter payload 内容。
  - consumer error。
  - dead-letter flush failure。
  - enabled consume 缺数据库时先 fail-fast，不连接 Kafka。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/daemon/__init__.py soc_agent/daemon/kafka_adapter.py soc_agent/cli.py tests/test_soc_daemon_kafka_config.py tests/test_soc_agent_runtime.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/daemon/__init__.py soc_agent/daemon/kafka_adapter.py soc_agent/cli.py tests/test_soc_daemon_kafka_config.py tests/test_soc_agent_runtime.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_daemon_kafka_config.py tests/test_soc_daemon_kafka_mapper.py tests/test_soc_daemon_kafka_runner.py tests/test_soc_agent_runtime.py::test_cli_daemon_consume_disabled_by_default_outputs_idle tests/test_soc_agent_runtime.py::test_cli_daemon_consume_enabled_requires_database_before_kafka tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && uv lock --check`
  - `cd backend && ./.venv/bin/python -m soc_agent.cli daemon consume --pretty`
  - `git diff --check`
- 下一步：
  - 做本地 Redpanda/Kafka smoke test：启动 broker、创建 topics、发布一条 alert sample、用 `soc daemon consume --database-url sqlite:///...` 消费并验证 run/summary/review queue 落库。

### 2026-07-03 — `soc daemon consume` disabled wiring 切片

- 背景：
  - `KafkaConsumerSettings` / `NullKafkaConsumerPort` 已完成。
  - 需要先让 CLI daemon consumer 入口存在，但不能要求本地/CI 有 Kafka broker。
- 新增：
  - `soc daemon consume`
  - 默认 `--max-records 1`，只做有限 poll，不会长期挂住。
  - 从 `SOC_KAFKA_*` 读取 `KafkaConsumerSettings`。
  - 使用 `NullKafkaConsumerPort` 和 `SocKafkaConsumerRunner` 完成 disabled-by-default wiring。
  - 输出 `soc.kafka_consume_result.v1` JSON，包含安全配置摘要和每次 runner 结果。
- 当时行为：
  - `SOC_KAFKA_ENABLED` 未设置或为 false：输出 `status=idle`，退出码 0。
  - `SOC_KAFKA_ENABLED=true` 但尚未接真实 broker adapter：stderr 明确报错，退出码 3。
  - `--max-records < 1`：参数错误，退出码 2。
- 边界：
  - 本切片不引入 Kafka SDK。
  - disabled idle 不要求数据库连接。
  - 当前不连接 broker、不消费真实消息、不写 dead-letter topic。
- 已补充测试：
  - disabled default 输出 idle JSON。
  - enabled without broker adapter fail-fast。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/cli.py tests/test_soc_agent_runtime.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/cli.py tests/test_soc_agent_runtime.py soc_agent/daemon/kafka_adapter.py soc_agent/daemon/kafka_config.py soc_agent/daemon/kafka_runner.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py::test_cli_daemon_consume_disabled_by_default_outputs_idle tests/test_soc_agent_runtime.py::test_cli_daemon_consume_enabled_without_broker_adapter_fails_fast tests/test_soc_daemon_kafka_config.py tests/test_soc_daemon_kafka_runner.py tests/architecture/test_soc_agent_boundaries.py`
  - `git diff --check`
- 下一步：
  - 决定真实 broker adapter 依赖：优先评估 `confluent-kafka` vs `aiokafka`；在 adapter 层 behind flag 接入，保持 core/service 不受 Kafka SDK 污染。

### 2026-07-03 — Kafka consumer settings + null adapter 切片

- 背景：
  - runner skeleton 已固定 `poll -> map -> process -> commit/dead-letter` 语义。
  - 接真实 broker 前，需要先固定配置 contract、secret 引用方式和 disabled-by-default 行为。
- 新增：
  - `soc_agent/daemon/kafka_config.py`
  - `KafkaConsumerSettings`：
    - `enabled=False` 默认禁用。
    - `bootstrap_servers=["localhost:9092"]`。
    - 默认 input topics：`soc.alerts.raw.v1`、`soc.approvals.requests.v1`。
    - `dead_letter_topic=soc.alerts.dead_letter.v1`。
    - `security_protocol` 支持 `PLAINTEXT`、`SSL`、`SASL_PLAINTEXT`、`SASL_SSL`。
    - `sasl_password_env` 只保存环境变量名，不把 secret 写入配置对象。
    - `from_env()` 支持 `SOC_KAFKA_*` 环境变量。
  - `soc_agent/daemon/kafka_adapter.py`
  - `NullKafkaConsumerPort`：
    - disabled 时 `poll()` 返回 `None`，可用于本地/测试空跑。
    - enabled 但未配置真实 broker adapter 时 fail-fast，避免误以为已经消费 Kafka。
- 边界：
  - 本切片不引入 Kafka SDK。
  - config contract 不读取 DeerFlow root config，不改上游配置系统。
  - secret 只通过环境变量引用读取，不写入 notes、DB 或 run payload。
- 已补充测试：
  - 默认配置。
  - `SOC_KAFKA_*` 环境变量解析。
  - 空 topic 校验。
  - disabled null consumer idle。
  - enabled null consumer fail-fast。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/daemon/__init__.py soc_agent/daemon/kafka_config.py soc_agent/daemon/kafka_adapter.py tests/test_soc_daemon_kafka_config.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/daemon/__init__.py soc_agent/daemon/kafka_config.py soc_agent/daemon/kafka_adapter.py tests/test_soc_daemon_kafka_config.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_daemon_kafka_config.py tests/test_soc_daemon_kafka_mapper.py tests/test_soc_daemon_kafka_runner.py tests/architecture/test_soc_agent_boundaries.py`
  - `git diff --check`
- 下一步：
  - 先做 broker adapter 依赖选择和 `soc daemon consume` disabled-by-default wiring；真实 broker client 仍 behind flag，不影响现有 deterministic daemon scaffold。

### 2026-07-03 — Kafka consumer runner skeleton 切片

- 背景：
  - `KafkaRecord -> SocDaemonMessage` mapper 已完成。
  - 真实 broker adapter 前，需要先固定 poll/process/commit/dead-letter 语义。
- 新增：
  - `soc_agent/daemon/kafka_runner.py`
  - `KafkaConsumerPort` protocol：`poll()`、`commit(record)`、`send_dead_letter(record, error)`、`close()`。
  - `KafkaRunnerProcessResult`。
  - `SocKafkaConsumerRunner.process_next()` / `process_record()`。
- 处理语义：
  - 成功：`poll -> map -> SocDaemonService.process_message -> commit`。
  - mapper failure：`send_dead_letter -> commit`。
  - service failure：`send_dead_letter -> commit`。
  - dead-letter 写失败：不 commit，异常向上抛出。
- 边界：
  - runner 不引入真实 Kafka SDK。
  - runner 不访问 repository，不调用 pipeline。
  - 真实 broker adapter 后续只实现 `KafkaConsumerPort`。
- 已补充测试：
  - success commit after service success。
  - idle。
  - mapper failure -> dead-letter -> commit。
  - service failure -> dead-letter -> commit。
  - dead-letter failure -> no commit。
  - close。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/daemon/__init__.py soc_agent/daemon/kafka_runner.py tests/test_soc_daemon_kafka_runner.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/daemon/__init__.py soc_agent/daemon/kafka_runner.py tests/test_soc_daemon_kafka_runner.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_daemon_kafka_mapper.py tests/test_soc_daemon_kafka_runner.py tests/architecture/test_soc_agent_boundaries.py`
- 下一步：
  - 做真实 broker adapter/config planning 或实现一个 disabled-by-default broker adapter。优先先定配置 contract 和依赖选择（`aiokafka` vs `confluent-kafka`）。

### 2026-07-03 — Kafka record to daemon message mapper 切片

- 背景：
  - 已有 `SocDaemonMessage` 和 `SocDaemonService.process_message()`，但真实 consumer 还缺 broker record 到 daemon contract 的纯映射层。
- 新增：
  - `soc_agent/daemon/kafka_mapper.py`
  - `KafkaRecord` 轻量 dataclass，不依赖真实 Kafka client。
  - `map_kafka_record_to_daemon_message(record)`。
  - 默认 topic：
    - `soc.alerts.raw.v1` -> `kind=alert`
    - `soc.approvals.requests.v1` -> `kind=approval_request`
- 边界：
  - mapper 只依赖 stdlib 和 `soc_agent.contracts`。
  - mapper 不 import Kafka SDK、不调用 core service、不访问 repository。
  - unknown topic、invalid JSON、non-object JSON、non-UTF8 key 都明确失败，后续 runner 可转 dead-letter。
- 已补充测试：
  - alert topic mapping。
  - approval request topic mapping。
  - custom topic set。
  - unknown topic / invalid JSON / non-object JSON / non-UTF8 key。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/daemon/__init__.py soc_agent/daemon/kafka_mapper.py tests/test_soc_daemon_kafka_mapper.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/daemon/__init__.py soc_agent/daemon/kafka_mapper.py tests/test_soc_daemon_kafka_mapper.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_daemon_kafka_mapper.py tests/architecture/test_soc_agent_boundaries.py`
- 下一步：
  - 做 consumer runner skeleton：定义 poll/process/commit/dead-letter 抽象，不接真实 broker client。

### 2026-07-03 — approval middleware placement + Kafka adapter planning

- 记录：
  - SOC Lead Agent approval middleware 不在当前 ReviewQueue/TUI/API/Kafka scaffold 阶段实现。
  - 它应挂在未来真实 SOC Lead Agent / skills / MCP tool chain 中，用来拦截 tool/action call，再调用 `SocAgentActionPolicy` 和 `SocAgentApprovalService`。
  - 当前已完成的是 service-level approval boundary，足以支撑 Web/TUI/daemon 入口。
- 新增：
  - `.notes/ai_soc/kafka-consumer-adapter-plan.md`
- 下一刀建议：
  - 先做 `soc_agent/daemon/kafka_mapper.py` 与 tests，不接真实 broker：
    - `KafkaRecord` 轻量 dataclass。
    - `map_kafka_record_to_daemon_message(record)`。
    - alert topic -> `SocDaemonMessage(kind="alert")`。
    - approval request topic -> `SocDaemonMessage(kind="approval_request")`。
    - unknown topic / invalid JSON / non-object payload 明确报错。

### 2026-07-03 — Kafka daemon scaffold / approval request ingestion 切片

- 背景：
  - Web/TUI 审批链路已经具备 request -> grant -> dry-run -> execute boundary。
  - 后台自动入口不能直接从 Kafka callback 调 pipeline 或 DB，必须先进入 versioned contract 和 core service。
- 新增：
  - `SocDaemonMessage`：daemon decoded-message contract，包含 `kind=alert|approval_request`、payload、topic/partition/offset/key。
  - `SocDaemonProcessResult`：单条 daemon message 处理结果。
  - `SocDaemonService.process_message()`：
    - `kind=alert`：通过 `SocAnalysisService.analyze()` 进入固定 runtime。
    - `kind=approval_request`：解析 `SocAgentApprovalRequest` 并通过 `SocAgentApprovalService.submit_request()` 写入 approval inbox。
  - daemon context：actor 固定为 `soc-daemon`、`actor_type=service`、`surface=daemon`；Kafka metadata 派生 `idempotency_key=kafka:{topic}:{partition}:{offset}`。
  - CLI 本地验证入口：`soc daemon process PATH|--json ... --database-url ...`。
- 边界：
  - 本切片不连接真实 Kafka broker，不引入 Kafka client 依赖。
  - daemon 不直接访问 repository；CLI 只负责 wiring repository-backed services。
  - daemon 不在 callback 中执行复杂逻辑；未来 Kafka consumer 只应 decode message 后调用 `SocDaemonService.process_message()`。
- 已补充测试：
  - alert daemon message 通过 analysis service 产生 run，并带 daemon actor/idempotency key。
  - approval request daemon message 写入 shared approval inbox。
  - 缺少 analysis service 时明确 fail-fast。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/contracts/schemas.py soc_agent/contracts/__init__.py soc_agent/core/service.py soc_agent/cli.py tests/test_soc_agent_service.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/contracts/schemas.py soc_agent/contracts/__init__.py soc_agent/core/service.py soc_agent/cli.py tests/test_soc_agent_service.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_service.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_service.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m soc_agent.cli db upgrade --database-url sqlite:////tmp/soc_daemon_cli_test_20260703.db`
  - `cd backend && ./.venv/bin/python -m soc_agent.cli daemon process --database-url sqlite:////tmp/soc_daemon_cli_test_20260703.db --json '<approval_request daemon message>' --pretty`
  - `git diff --check`
- 下一步：
  - 讨论/设计真实 Kafka consumer adapter：consumer 配置、topic schema、反压、重试、offset 提交、dead-letter、metrics/readiness。

### 2026-07-03 — TUI approved-action dry-run / execute command 切片

- 背景：
  - Web 已支持 approval request -> grant -> dry-run -> execute。
  - TUI 上一刀只做到 pending request 展示和 approve token 生成，还不能验证或消费 execution token。
- 新增：
  - `soc review tui` 新增 `/dry-run SAT-... route action`。
  - `soc review tui` 新增 `/execute SAT-... route action idempotency-key`。
  - TUI view state 增加最近一次 `SocAgentActionResult`。
  - approval request detail 渲染 execution token、action result status/message、`execution_result_id`、`external_side_effect`。
- 边界：
  - dry-run 只调用 `SocAgentApprovalService.dry_run_approved_action()`，不修改 grant，不执行外部副作用。
  - execute 只调用 `SocAgentApprovalService.execute_approved_action()`，必须显式传入 idempotency key。
  - 当前 execute 仍只消费 token 并记录 execution boundary，`external_side_effect=not_executed`，不会封禁 IP、隔离终端或调用 MCP。
- 已补充测试：
  - slash command registry 覆盖 `/dry-run`、`/execute`。
  - approved action 参数解析覆盖 token/route/action/idempotency key。
  - TUI request context 覆盖 idempotency key。
  - TUI view state/render 覆盖 `SocAgentActionResult` 展示。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/tui/app.py soc_agent/tui/view_state.py soc_agent/tui/render.py soc_agent/tui/command_registry.py tests/test_soc_review_tui.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/tui/app.py soc_agent/tui/view_state.py soc_agent/tui/render.py soc_agent/tui/command_registry.py tests/test_soc_review_tui.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_review_tui.py tests/test_soc_agent_service.py`
- 下一步：
  - 做 Kafka daemon scaffold / approval request ingestion：先建立可测试 daemon 输入边界和 repository-backed service wiring，不直接接生产 Kafka。

### 2026-07-03 — approval inbox TUI consumption 切片

- 背景：
  - approval inbox 已有 API 和 Web 消费端。
  - 值班/本地运维场景还需要 terminal workbench 从 pending request 选择审批，避免手工粘贴 JSON。
- 新增：
  - `soc review tui` 增加 approval inbox 区块，展示 pending approval requests。
  - 新增 slash commands：
    - `/approvals`：重新加载 pending approval requests。
    - `/approval APR-...`：打开 approval request 详情。
    - `/approve APR-... reason`：用 TUI approver context 生成一次性 execution token。
  - `run_review_tui()` 支持注入 `SocAgentApprovalService`。
  - CLI `soc review tui` 使用同一个 SQLAlchemy repository-backed approval service。
  - CLI `soc chat tui` 注入 approval service，使高风险 chat action 生成的 approval request 能进入同一个 inbox。
- 边界：
  - TUI 只调用 `SocAgentApprovalService`，不直接访问 repository。
  - TUI approve 只生成 `SocAgentApprovalGrant.execution_token_id`，不执行外部动作。
  - TUI 本地 MVP approver actor 固定为 `soc-review-tui` + `soc_approver`；后续接真实用户体系时替换为认证/角色配置。
- 已补充测试：
  - slash command registry 覆盖 `/approvals`、`/approval`、`/approve`。
  - TUI view state 覆盖 approval request / grant。
  - TUI render 覆盖 approval inbox、approval request detail、execution token 展示。
  - TUI approval context 覆盖 `soc_approver` role。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/tui/app.py soc_agent/tui/runner.py soc_agent/tui/view_state.py soc_agent/tui/render.py soc_agent/tui/command_registry.py soc_agent/cli.py tests/test_soc_review_tui.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/tui/app.py soc_agent/tui/runner.py soc_agent/tui/view_state.py soc_agent/tui/render.py soc_agent/tui/command_registry.py soc_agent/cli.py tests/test_soc_review_tui.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_review_tui.py tests/test_soc_tui_chat_app.py tests/test_soc_agent_service.py`
- 下一步：
  - 补 TUI dry-run / execute command，复用 `SocAgentApprovalService.dry_run_approved_action()` 和 `execute_approved_action()`，保持 execute 必须显式 idempotency key。

### 2026-07-03 — Agent/daemon approval inbox write boundary 切片

- 背景：
  - approval inbox API 和 Web consumption 已落地，但高风险 request 仍主要靠 API 手工提交。
  - 后续真实入口有两类：Kafka daemon 自动预警流，以及 SOC Lead Agent / TUI 高风险 action middleware。
- 新增：
  - `SocAgentChatService` 支持注入 `SocAgentApprovalService`。
  - 高风险 action 被 policy 拒绝且需要人工审批时，chat stream 先生成 `SocAgentApprovalRequest`；如果注入 approval service，则同步写入 approval inbox，再发出 `custom kind=soc.approval_request`。
  - `SocDaemonService.submit_approval_request()` 作为 daemon 侧写入边界，内部只调用 `SocAgentApprovalService.submit_request()`。
- 边界：
  - 未注入 approval service 时，chat stream 保持事件输出行为，方便测试和 headless shell，不隐式写 DB。
  - `SocDaemonService.start()` 仍是 Phase 4 placeholder；本切片不实现 Kafka consumer、不消费 broker 消息。
  - Agent middleware / daemon adapter 后续只能通过 `SocAgentApprovalService` 写 inbox，不能直接写 repository 或 DB。
- 已补充测试：
  - chat service 持久化高风险 approval request 到 shared inbox。
  - daemon service submit 边界复用同一 approval service。
  - daemon service 缺少 approval service 时明确报错。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/core/service.py tests/test_soc_agent_service.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/core/service.py tests/test_soc_agent_service.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_service.py`
  - `git diff --check`
- 下一步：
  - 做 TUI approval inbox consumption：从 pending request 选择审批，而不是手工粘贴 JSON。

### 2026-07-03 — approval inbox Web consumption 切片

- 背景：
  - `soc_approval_requests` 和 Gateway inbox API 已落地。
  - Web approval workbench 之前仍依赖手工粘贴 request JSON，不适合作为后台审批入口。
- 新增：
  - `frontend/src/core/soc/api.ts` 增加 `listSocApprovalRequests()` 和 `getSocApprovalRequest()`。
  - `frontend/src/core/soc/hooks.ts` 增加 `useSocApprovalRequests()` 和 `useSocApprovalRequest()`。
  - `frontend/src/components/workspace/soc/soc-review-queue-workbench.tsx` 在审批动作区新增 approval inbox 列表，默认选择 pending request，并把详情填入 approve 表单。
- 边界：
  - Web 只通过 `/api/soc/approvals/requests*` 读取 inbox，仍通过 `/api/soc/approvals/grants` 生成 token。
  - Web 不直接读写 repository，不修改 ApprovalRequest 状态。
  - 手工 JSON fallback 暂时保留，方便本地调试和后端验证。
- 已补充测试：
  - 前端 API 单测覆盖 approval request inbox list 和 detail 路径、headers、URL encoding。
- 已验证：
  - `cd frontend && pnpm exec prettier --write src/core/soc/types.ts src/core/soc/api.ts src/core/soc/hooks.ts src/components/workspace/soc/soc-review-queue-workbench.tsx tests/unit/core/soc/api.test.ts`
  - `cd frontend && pnpm exec eslint src/core/soc src/components/workspace/soc/soc-review-queue-workbench.tsx tests/unit/core/soc/api.test.ts`
  - `cd frontend && pnpm typecheck`
  - `cd frontend && pnpm test -- tests/unit/core/soc/api.test.ts`
- 下一步：
  - 让 Kafka daemon 和 Agent middleware 都写入同一个 approval inbox，然后再做 TUI approval inbox consumption。

### 2026-07-03 — approval request inbox API 切片

- 背景：
  - 实际产品入口有三条：Kafka 自动预警处理、Agent TUI 主动对话、Web 工单/后台人工审批。
  - approved action Web workbench 只能手工粘贴 approval request JSON，适合验证链路，但不能作为多入口统一审批中心。
- 新增：
  - `SocAgentApprovalRequestRepository` protocol。
  - `soc_approval_requests` ORM model 和 Alembic migration `0006_approval_requests.py`。
  - `SqlAlchemyAlertRepository.save_approval_request()` / `get_approval_request()` / `list_approval_requests()`。
  - `SocAgentApprovalService.submit_request()` / `get_request()` / `list_requests()`。
  - Gateway `POST /api/soc/approvals/requests`、`GET /api/soc/approvals/requests`、`GET /api/soc/approvals/requests/{approval_request_id}`。
- 边界：
  - ApprovalRequest 是 pending request，不是执行授权；真实执行仍必须走 ApprovalGrant execution token。
  - API 只调用 `SocAgentApprovalService`，不直接访问 repository。
  - Kafka daemon 和 Agent middleware 后续都应该写入同一个 inbox，Web/TUI 只作为消费和批准入口。
- 已补充测试：
  - service request inbox submit/list/get、missing request、缺 repository。
  - repository 持久化 approval request，approve 时同时保存 request 和 grant。
  - Gateway request inbox create/list/get/404 和 route 暴露。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/protocols.py soc_agent/db/models.py soc_agent/db/repositories.py soc_agent/db/__init__.py soc_agent/db/migrations/versions/0006_approval_requests.py soc_agent/core/service.py app/gateway/routers/soc_approvals.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/test_soc_approvals_router.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/protocols.py soc_agent/db/models.py soc_agent/db/repositories.py soc_agent/db/__init__.py soc_agent/db/migrations/versions/0006_approval_requests.py soc_agent/core/service.py app/gateway/routers/soc_approvals.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/test_soc_approvals_router.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_approvals_router.py tests/test_soc_agent_repository.py tests/test_soc_agent_service.py`
- 下一步：
  - 做 approval inbox Web consumption：Web 不再手工粘贴 JSON，而是从 inbox 选择 pending request 后 approve / dry-run / execute。

### 2026-07-03 — approved action Web workbench 切片

- 背景：
  - approved action Gateway API 已经落地，但分析师还没有 Web 操作入口验证 approve / dry-run / execute 链路。
  - 本切片只做 thin page，不把审批或 token 消费逻辑放到前端。
- 新增：
  - `frontend/src/core/soc/types.ts` 增加 approval request / grant / approved action command / action result contract。
  - `frontend/src/core/soc/api.ts` 增加 `createSocApprovalGrant()`、`dryRunSocApprovedAction()`、`executeSocApprovedAction()`。
  - `frontend/src/core/soc/hooks.ts` 增加对应 React Query mutation hook。
  - `frontend/src/components/workspace/soc/soc-review-queue-workbench.tsx` 增加审批动作面板：输入 pending approval request JSON、生成 execution token、dry-run、execute。
- 边界：
  - Web 只调用 `/api/soc/approvals/*`，不直接访问 repository，不自行消费 token。
  - execute 仍只进入后端 execution boundary，当前不会调用外部 MCP/tool，不会封禁 IP 或隔离终端。
  - 前端本地 execute 成功后只把当前 grant 标记为 consumed，真实幂等和重放拒绝仍由后端控制。
- 已补充测试：
  - approval grant API 路径、请求体和 Web actor/idempotency headers。
  - dry-run 强制发送 `dry_run=true` 且不带 idempotency header。
  - execute 强制发送 `dry_run=false` 且携带 idempotency header。
- 下一步：
  - 做 approval request inbox API，使 Kafka daemon、Agent middleware、Web/TUI 都能共用 pending request 收件箱。

### 2026-07-03 — approved action Gateway API 切片

- 背景：
  - approval grant 已可持久化，execute boundary 已能消费 token。
  - Web/TUI 需要一个统一 API 入口来手工验证 approve / dry-run / execute 链路，不能各自直接调用 repository。
- 新增：
  - `backend/app/gateway/routers/soc_approvals.py`
  - `backend/app/gateway/routers/soc_dependencies.py`，共享 SOC repository/context/role 映射依赖。
  - `POST /api/soc/approvals/grants`
  - `POST /api/soc/approvals/actions/dry-run`
  - `POST /api/soc/approvals/actions/execute`
  - Gateway app 注册 SOC approvals router。
- 边界：
  - API 只调用 `SocAgentApprovalService`，不直接消费 token、不直接写 repository。
  - 创建 grant 需要 `soc_approver` / `soc_admin`；Gateway 当前将 DeerFlow `system_role=admin` 映射为 `soc_admin`。
  - execute endpoint 仍只进入 `execute_approved_action()` 边界，不调用外部 MCP/tool、不封禁 IP、不隔离终端。
- 已补充测试：
  - create grant 记录 Web/admin actor、idempotency key，并持久化 token。
  - dry-run 返回 non-side-effect result。
  - execute 消费 token，返回 `external_side_effect=not_executed`。
  - missing token 映射为 404。
  - router 暴露三条 MVP path。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format app/gateway/app.py app/gateway/routers/__init__.py app/gateway/routers/soc_dependencies.py app/gateway/routers/soc_review.py app/gateway/routers/soc_approvals.py tests/test_soc_approvals_router.py tests/test_soc_review_router.py`
  - `cd backend && ./.venv/bin/python -m ruff check app/gateway/app.py app/gateway/routers/__init__.py app/gateway/routers/soc_dependencies.py app/gateway/routers/soc_review.py app/gateway/routers/soc_approvals.py tests/test_soc_approvals_router.py tests/test_soc_review_router.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_approvals_router.py tests/test_soc_review_router.py tests/test_soc_agent_repository.py tests/test_soc_agent_service.py`
- 下一步：
  - 做 approved action TUI/Web 操作入口，复用 Gateway API 或 service 语义，让分析师能在界面上审批、dry-run、execute。

### 2026-07-03 — approval grant repository persistence 切片

- 背景：
  - `SocAgentApprovalService` 已支持 approve、dry-run 和 execute consume boundary。
  - 但 grant repository 只有 protocol 和 in-memory 测试实现，真实 API/TUI 入口前必须先能持久化 execution token 和 consumed 状态。
- 新增：
  - `SocApprovalGrantRow` ORM model。
  - Alembic migration `0005_approval_grants.py`，新增 `soc_approval_grants` 表。
  - `SqlAlchemyAlertRepository.save_approval_grant()`。
  - `SqlAlchemyAlertRepository.get_approval_grant()`。
  - `SqlAlchemyAlertRepository.get_approval_grant_by_token()`。
- 数据边界：
  - 表中保存扁平索引字段和完整 `grant_payload`。
  - 查询支持按 `approval_grant_id` 和 `execution_token_id`。
  - consume 后的 `consumed_at`、`consumed_by`、`consume_idempotency_key`、`execution_result_id`、`execution_result_payload` 通过 payload 和索引字段持久化。
- 已同步文档：
  - `.notes/ai_soc/soc-agent-solution.md`
  - `.notes/reference-index/soc-agent-engineering-contracts.md`
- 已补充测试：
  - repository 持久化 approve 状态。
  - repository 持久化 execute consume 状态。
  - `SocAgentApprovalService` 通过 SQLAlchemy repository 完成 approve -> execute -> reload。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/db/models.py soc_agent/db/repositories.py soc_agent/db/__init__.py soc_agent/db/migrations/versions/0005_approval_grants.py tests/test_soc_agent_repository.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/db/models.py soc_agent/db/repositories.py soc_agent/db/__init__.py soc_agent/db/migrations/versions/0005_approval_grants.py tests/test_soc_agent_repository.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_repository.py tests/test_soc_agent_service.py`
  - `cd backend && UV_CACHE_DIR=/tmp/uv-cache uv run python -m soc_agent.cli db upgrade --database-url sqlite:////home/yydspei/projects/deer-flow/backend/.deer-flow/data/soc_agent_dev.db`
- 下一步：
  - 做 approved action API/TUI 入口，让 approve/execute 链路可从操作界面或 Gateway 手工验证。

### 2026-07-03 — approved-action consume/audit boundary 切片

- 背景：
  - 之前已有 approval request、approval grant、grant repository protocol 和 dry-run 校验。
  - 但审批通过后的 execution token 还不能被消费，也没有已执行状态、幂等重试和 execution audit payload。
- 新增：
  - `SocAgentApprovalGrant` 增加 `status=approved|consumed`、`consumed_at`、`consumed_by`、`consume_idempotency_key`、`execution_result_id`、`execution_result_payload`。
  - `SocAgentApprovedActionCommand.dry_run` 从只允许 `True` 改为显式 boolean，用于区分 dry-run 和执行边界。
  - `SocAgentApprovalService.execute_approved_action()`：
    - 要求 repository、`dry_run=False`、`context.idempotency_key`。
    - 校验 token 存在、未过期、route/action 匹配、grant 未消费。
    - 消费 grant 并写回 execution result payload。
    - 相同 idempotency key 重试返回原 result；不同 key 重放拒绝。
- 边界：
  - 该方法只消费 token 和记录 execution boundary audit。
  - 不调用外部 MCP/tool、不封禁 IP、不隔离终端、不修改生产系统。
  - 真正外部副作用必须后续通过 action adapter registry 接入，并继续复用这个 token consume / idempotency / audit 边界。
- 已同步文档：
  - `.notes/ai_soc/soc-agent-solution.md`
  - `.notes/reference-index/soc-agent-engineering-contracts.md`
- 已补充测试：
  - dry-run 不消费 token。
  - execute 消费 token 并写 consumed fields。
  - 相同 idempotency key 幂等返回同一 result。
  - 不同 idempotency key 重放被拒绝。
  - execute 必须 `dry_run=False` 且必须带 idempotency key。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/contracts/schemas.py soc_agent/core/service.py tests/test_soc_agent_service.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/contracts/schemas.py soc_agent/core/service.py tests/test_soc_agent_service.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_service.py`
- 下一步：
  - 二选一：先做 approved action API/TUI 入口，让审批后执行链路可手工验证；或先做 approval grant repository 持久化，让 grant/consume 状态不只存在内存测试中。

### 2026-07-03 — ReviewQueue Web actor/context headers 切片

- 背景：
  - ReviewQueue Web thin page 已经能调用 Gateway API，但 close/correct 审计上下文仍会退化为泛化 API 调用。
  - SOC 复核、纠正和后续审批执行必须能区分 Web 操作者、调用 surface、trace 和 idempotency。
- 新增：
  - `frontend/src/core/soc/types.ts` 增加 `SocRequestContext` / `SocEntrySurface`。
  - `frontend/src/core/soc/api.ts` 统一构造 SOC headers：`x-soc-actor-id`、`x-soc-surface`、`x-trace-id`，状态变更请求额外带 `idempotency-key`。
  - `frontend/src/core/soc/hooks.ts` 从 `useAuth()` 注入当前 Web 用户，页面调用自动带 `surface=web`。
  - `backend/app/gateway/routers/soc_review.py` 从 `request.state.user.id` 读取认证用户作为 actor id；没有认证 state 时才回退 `x-soc-actor-id`；`x-soc-surface` 只接受 `api/web` 白名单。
- 边界：
  - 前端 header 只是显式上下文，不能覆盖 Gateway 已认证用户。
  - 非法 surface header 降级为 `api`，不写入任意字符串到审计上下文。
  - 本切片只修复 Web ReviewQueue API context；真实 approved-action consume / token 消费 / external side effect 仍未实现。
- 已同步文档：
  - `.notes/ai_soc/soc-agent-solution.md`
  - `.notes/reference-index/soc-agent-engineering-contracts.md`
- 已补充测试：
  - 前端 API 单测覆盖 Web actor headers 和 idempotency key。
  - 后端 router 单测覆盖认证用户覆盖伪造 actor header，并记录 `surface=web`。
- 已验证：
  - `cd frontend && pnpm exec prettier --check src/core/soc tests/unit/core/soc`
  - `cd frontend && pnpm exec eslint src/core/soc tests/unit/core/soc`
  - `cd frontend && pnpm typecheck`
  - `cd frontend && pnpm test -- tests/unit/core/soc/api.test.ts`
  - `cd backend && ./.venv/bin/python -m ruff format app/gateway/routers/soc_review.py tests/test_soc_review_router.py`
  - `cd backend && ./.venv/bin/python -m ruff check app/gateway/routers/soc_review.py tests/test_soc_review_router.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_review_router.py`
- 下一步：
  - 做 approved-action consume/audit 真实执行边界：一次性 token 消费、已执行状态、审计记录、幂等检查和 dry-run/真实执行分层。

### 2026-07-03 — SOC Agent profile governance 决策记录

- 背景：
  - 后续 SOC Lead Agent、EDR/HIDS/APT/F5 Domain Sub Agent、Skill 和 MCP/tool group 会越来越多。
  - 同事希望能参与配置 skill/MCP 和沉淀安全运营经验；这是合理诉求，但不能让 draft 配置直接影响生产告警。
- 决策：
  - 主控和 sub agent 都可以复用 DeerFlow `lead_agent` 思路生成/编辑 profile 草稿。
  - Profile 必须作为 SOC Runtime 的受控配置使用，不是自由运行的生产 agent。
  - Skill/MCP 开放配置采用 `draft -> validated -> staging -> active -> archived` 生命周期。
  - 同事可配置 draft skill、适用条件、readonly MCP 候选；middleware preset、high-risk MCP、approval/audit policy、Runtime pipeline 必须由代码/审批控制。
- 文档：
  - 新增 `.notes/ai_soc/soc-agent-profile-governance.md`。
  - `.notes/ai_soc/soc-agent-solution.md` 增加 profile 治理摘要和链接。
  - `.notes/reference-index/soc-agent-engineering-contracts.md` 增加工程约束。
- 验证：
  - 文档切片，仅需 `git diff --check`。
- 下一步：
  - 后续实现 profile registry / middleware preset / tool group registry 时，以该治理文档为边界。

### 2026-07-02 — ReviewQueue Web thin page 切片

- 背景：
  - ReviewQueue API 和 TUI thin client 已经可用，但 DeerFlow Web 工作台还没有最小的分析师复核入口。
  - 这次只做产品闭环验证，不把研判、关联、纠正规则放到前端。
- 新增：
  - `frontend/src/core/soc/`：ReviewQueue 类型、API client、React Query hooks。
  - `frontend/src/app/workspace/soc/review/page.tsx` 和 `SocReviewQueueWorkbench`：队列列表、详情上下文、相似告警、结构化产物、关闭复核项、提交人工纠正。
  - Workspace sidebar 新增 `SOC 复核` 入口和中英文 i18n。
  - `frontend/tests/unit/core/soc/api.test.ts` 覆盖 SOC Review API 路径、query 参数、body 和 backend detail 透传。
- 边界：
  - Web 页面只调用 `/api/soc/review/*`，不直接查 DB、不组装 queue item、不运行 pipeline。
  - close/correct 仍由 Gateway API 转入 `SocReviewService`；当前 Web 请求继承 API actor surface，后续如需区分 Web actor，需要补 headers/context contract。
  - 本页是 thin page，不是完整 SOC 大屏；批量复核、case/evidence 图、streaming agent console 后续增量做。
  - 本地人工验证允许 `SOC_DATABASE_URL=sqlite:////.../backend/.deer-flow/data/soc_agent_dev.db`；生产/准生产仍必须使用 PostgreSQL。
- 已验证：
  - `cd frontend && pnpm exec prettier --check src/core/soc src/components/workspace/soc src/app/workspace/soc/review tests/unit/core/soc src/components/workspace/workspace-nav-chat-list.tsx src/core/i18n/locales/types.ts src/core/i18n/locales/en-US.ts src/core/i18n/locales/zh-CN.ts`
  - `cd frontend && pnpm exec eslint src/core/soc src/components/workspace/soc src/app/workspace/soc/review tests/unit/core/soc src/components/workspace/workspace-nav-chat-list.tsx`
  - `cd frontend && pnpm install --frozen-lockfile`
  - `cd backend && UV_CACHE_DIR=/tmp/uv-cache uv run python -m soc_agent.cli db upgrade --database-url sqlite:////home/yydspei/projects/deer-flow/backend/.deer-flow/data/soc_agent_dev.db`
  - `cd backend && SOC_DATABASE_URL=sqlite:////home/yydspei/projects/deer-flow/backend/.deer-flow/data/soc_agent_dev.db uv run uvicorn app.gateway.app:app --host 127.0.0.1 --port 8001`
  - Gateway log: `GET /api/soc/review/items?status=open&limit=50` 返回 `200 OK`。
  - `codegraph sync .`
- 未完成验证：
  - `cd frontend && pnpm test -- tests/unit/core/soc/api.test.ts` 尚未单独补跑。
  - `cd frontend && pnpm typecheck` 尚未补跑全量类型检查。
- 下一步：
  - 若继续产品闭环，补 Web actor/context headers，让 Gateway 能区分 `surface=web`、actor id、trace/idempotency。
  - 若继续 Agent 安全边界，做 approved-action consume/audit 真实执行边界，仍默认 dry-run/无外部副作用。

### 2026-07-02 — SOC Agent approval grant persistence / dry-run 切片

- 背景：
  - 上一刀已经能生成 `SocAgentApprovalGrant`，但 grant 还没有可替换持久化边界，也没有执行前 token 校验入口。
  - 后续接真实封禁、隔离、MCP 调用前，必须先把“审批通过”和“真实执行”之间的 contract 固定下来。
- 新增：
  - `SocAgentApprovedActionCommand`，作为审批后执行/演练入口的显式 contract。
  - `SocAgentApprovalGrantRepository` protocol，提供 `save_approval_grant()`、按 grant id 读取、按 execution token 读取。
  - `SocAgentApprovalService(grant_repository=...)`，`approve()` 在 repository 存在时保存 grant。
  - `SocAgentApprovalService.dry_run_approved_action()`，校验 execution token 存在、grant 未过期、route/action 与授权一致，返回 `SocAgentActionResult`。
- 边界：
  - dry-run 不调用外部工具、不封禁 IP、不隔离终端、不写生产状态。
  - dry-run 当前不消费一次性 token；真实执行层后续必须补 token consume/used 状态、automation action audit、幂等检查和失败补偿。
  - 无 repository 时 fail-fast，不在 service 内偷偷建隐式存储。
- 已验证：
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_service.py tests/test_soc_tui_chat_runtime.py tests/test_soc_tui_chat_app.py tests/test_soc_review_tui.py tests/architecture/test_soc_agent_boundaries.py -q`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_service.py tests/test_soc_tui_chat_runtime.py tests/test_soc_tui_chat_app.py tests/test_soc_review_tui.py tests/architecture/test_soc_agent_boundaries.py`
  - `codegraph sync .`
- Understand incremental 检查：
  - 当前 `backend/soc_agent` 存在未提交代码变更，但 `backend/soc_agent/.understand-anything/meta.json.gitCommitHash` 与 `HEAD` 都是 `a8aaae4f...`。
  - 按 Understand skill 的增量逻辑，`git diff <metaCommit>..HEAD --name-only` 为空，因此它不会识别未提交 working-tree 改动；结论是“提交前的增量更新不可靠，需提交后增量或显式 `--full` scoped rebuild”。
  - 提交后再次检查，`git diff a8aaae4f..HEAD --name-only` 能识别本次 SOC 代码变更；但按 skill 原样传给 `backend/soc_agent` scoped `compute-batches` 时，路径是 repo-root 相对路径，scan inventory 是 scoped 相对路径，导致输出 0 batches。
  - 将 changed-files 过滤为 `backend/soc_agent/**` 并 strip 前缀后，`compute-batches` 能输出 2 batches；说明 scoped 增量存在路径作用域要求，不能盲信原样增量结果。
- 下一步：
  - 若继续 Agent 能力，做 approved-action consume/audit 真实执行边界，仍默认 dry-run/无外部副作用。
  - 若先补产品闭环，做 ReviewQueue Web thin page。

### 2026-07-02 — SOC Agent approval grant token 切片

- 背景：
  - 上一刀已有 pending approval request，但审批通过后的执行授权还不能和审批请求混在一起。
  - 高风险动作必须先有明确 human approver、一次性 token、过期时间和幂等键，后续才能接 dry-run 或真实执行。
- 新增：
  - `SocAgentApprovalGrant` contract，包含 `approval_grant_id`、`execution_token_id`、`approval_request_id`、`permission_decision_id`、`approved_by`、`expires_at`、`idempotency_key`。
  - `SocAgentApprovalService.approve()`，只把 pending request 转成 grant，不执行 action。
  - approval role policy：只有 `soc_approver` 或 `soc_admin` 可以批准；普通 `analyst` 不能批准。
- 边界：
  - grant/token 不是 action result。
  - 当前仍不调用外部工具、不写生产状态、不执行封禁或隔离。
  - 后续如果接执行层，必须校验 token 未过期、单次使用、action/route/risk 与原 request 一致，并写 automation action audit。
- 工具使用：
  - Understand Chat 已按 `.understand-anything/knowledge-graph.json` 搜索，但图谱停在 2026-06-27 `bcce7db...`，早于当前 SOC 代码，未命中 `backend/soc_agent` 新符号。
  - 本切片使用 CodeGraph 定位 `SocAgentActionPolicy`、`SocAgentApprovalRequest`、`DecisionAuditRecord` 等本地落点。
- 已验证：
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_service.py tests/test_soc_tui_chat_runtime.py tests/test_soc_tui_chat_app.py tests/test_soc_review_tui.py tests/architecture/test_soc_agent_boundaries.py -q`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_service.py tests/test_soc_tui_chat_runtime.py tests/test_soc_tui_chat_app.py tests/test_soc_review_tui.py tests/architecture/test_soc_agent_boundaries.py`
  - `codegraph sync .`
- 下一步：
  - 若继续 Agent 能力，做 approval grant persistence / execution dry-run。
  - 若先补产品闭环，做 ReviewQueue Web thin page。

### 2026-07-02 — SOC Agent approval request event 切片

- 背景：
  - 上一刀已经能把 high-risk action 拦截为 `requires_human_approval=True`，但还没有一个可展示、可落库、可审计的审批请求对象。
  - 这会影响后续 Web/TUI 展示、approval token、automation action audit 的一致性。
- 新增：
  - `SocAgentPermissionDecision.decision_id` 和 `approval_request_id`。
  - `SocAgentApprovalRequest` contract。
  - `SocAgentChatService.stream()` 在 high-risk permission denied 时发 `custom kind=soc.approval_request`。
  - `soc_agent.tui.chat_runtime` 将 approval request 转成 DeerFlow `SystemMessage`。
- 边界：
  - approval request 只是 pending request，不代表已批准。
  - 当前仍不执行封禁 IP、隔离终端、任意 MCP 调用等外部副作用动作。
  - 后续执行必须补 approval token、audit record 和 idempotency key。
- 工具使用：
  - 本切片是局部服务契约扩展，已有 `.notes/reference-index` 和本仓库上下文足够；使用 CodeGraph/本地代码定位即可，不跑完整 Understand Anything。
  - 已把“架构型/跨项目切片先考虑 Understand Anything，局部切片不机械运行”的规则写入工作方式。
- 已验证：
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_service.py tests/test_soc_tui_chat_runtime.py tests/test_soc_tui_chat_app.py tests/test_soc_review_tui.py tests/architecture/test_soc_agent_boundaries.py -q`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_service.py tests/test_soc_tui_chat_runtime.py tests/test_soc_tui_chat_app.py tests/test_soc_review_tui.py tests/architecture/test_soc_agent_boundaries.py`
  - `codegraph sync .`
- 下一步：
  - 若继续 Agent 能力，做 approved-action execution token / audit record。
  - 若先补产品闭环，做 ReviewQueue Web thin page。

### 2026-07-02 — SOC Agent action permission / human approval 切片

- 背景：
  - 已有 route -> action dispatcher，但执行前还缺 permission/human approval 闸门。
  - 这一层保证后续 `review.correct`、`analysis.replay`、封禁、隔离、MCP/tool 调用不会绕过审批边界。
- 新增：
  - `SocAgentRiskLevel`：`read_only`、`analyst_write`、`high_risk`、`unknown`。
  - `SocAgentPermissionDecision` contract。
  - `SocAgentActionPolicy`。
  - `SocAgentChatService.stream()` 在 `route_decision` 后、`action_result` 前发 `custom kind=soc.permission_decision`。
  - `soc_agent.tui.chat_runtime` 将 `soc.permission_decision` 转成 DeerFlow `SystemMessage`，拒绝态使用 error tone。
- 当前策略：
  - `chat.ready_message`、`review.open_context` 是 read-only，默认允许。
  - `review.correct`、`analysis.replay` 是 analyst-write，必须 actor 具备 `analyst` role。
  - `response.block_ip`、`endpoint.isolate_host`、`mcp.invoke` 是 high-risk，返回 `requires_human_approval=True` 且不执行。
  - 未注册 action 默认拒绝。
- 边界：
  - permission allowed 才会进入 dispatcher 执行。
  - high-risk action 当前只生成 approval-required decision，不执行真实动作。
  - 后续要执行高风险动作，必须先补审批请求/确认/审计模型。
- 已验证：
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_service.py tests/test_soc_tui_chat_runtime.py tests/test_soc_tui_chat_app.py tests/test_soc_review_tui.py tests/architecture/test_soc_agent_boundaries.py -q`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_service.py tests/test_soc_tui_chat_runtime.py tests/test_soc_tui_chat_app.py tests/test_soc_review_tui.py tests/architecture/test_soc_agent_boundaries.py`
  - `codegraph sync .`
- 下一步：
  - 若继续 Agent 能力，做 approved-action execution：审批通过后的 command token / approval id / audit record。
  - 若先补产品闭环，做 ReviewQueue Web thin page。

### 2026-07-02 — SOC Agent route -> service/action dispatcher 切片

- 背景：
  - 上一刀只做 route 白名单，仍需要明确“route 允许后调用哪个 service action”。
  - 这一层是后续 review.correct、analysis.replay、MCP/tool route、人类审批的前置边界。
- 新增：
  - `SocAgentActionResult` contract。
  - `SocAgentActionDispatcher`。
  - `SocAgentChatService.stream()` 在 route decision 后调用 dispatcher。
  - 每次 action dispatch 都通过 `custom kind=soc.action_result` 出现在 stream 中。
  - `soc_agent.tui.chat_runtime` 将 `soc.action_result` 转成 DeerFlow `SystemMessage`，failed/denied 用 error tone。
- 当前 action 映射：
  - `chat.freeform` -> `chat.ready_message`，只返回 Phase 1 deterministic ready message。
  - `review.open_context` -> `review.open_context`，通过 `SocReviewService.get_investigation_context()` 读取上下文，并继续发 `soc.review_context`。
  - 未映射 route -> `route.unsupported` denied result。
- 边界：
  - dispatcher 只调用 core service，不直接读写 repository。
  - action result 只是执行结果，不自动升级为 memory 或处置。
  - 后续高风险 action 必须先扩展 permission/human approval，再接真实 service command。
- 已验证：
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_service.py tests/test_soc_tui_chat_runtime.py tests/test_soc_tui_chat_app.py tests/test_soc_review_tui.py tests/architecture/test_soc_agent_boundaries.py -q`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_service.py tests/test_soc_tui_chat_runtime.py tests/test_soc_tui_chat_app.py tests/test_soc_review_tui.py tests/architecture/test_soc_agent_boundaries.py`
  - `codegraph sync .`
- 下一步：
  - 若继续 Agent 能力，做 action permission / human approval contract，把 review.correct、analysis.replay 这类高风险 action 先挡在审批边界外。
  - 若先做产品闭环，做 ReviewQueue Web thin page。

### 2026-07-02 — SOC Agent capability router MVP 切片

- 背景：
  - 主 SOC Agent 后续会接 skills/MCP/tool route，必须先有确定性白名单路由，不让 LLM 或 TUI 自由调任意能力。
- 新增：
  - `SocAgentRouteDecision` contract。
  - `SocAgentCapabilityRouter`。
  - 默认白名单：`chat.freeform`、`review.open_context`。
  - `SocAgentChatService.stream()` 每次先发 `custom kind=soc.route_decision`。
  - route 被拒绝时输出明确 assistant message 并结束，不继续执行 context loading。
  - `soc_agent.tui.chat_runtime` 将 `soc.route_decision` 转成 DeerFlow `SystemMessage`，拒绝态使用 error tone。
- 当前 route：
  - 普通消息 -> `chat.freeform`。
  - 带 `queue_id` 或 `/open REV-...` -> `review.open_context`。
  - 未知 slash command -> `command.unknown`，默认拒绝。
- 边界：
  - router 只选择白名单 route，不执行动作。
  - route allowed 不代表处置动作 allowed；高风险动作后续还要走 service command + permission + human approval。
  - `allowed_routes` 可在单次 request 中进一步收窄，不扩大全局白名单。
- 已验证：
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_service.py tests/test_soc_tui_chat_runtime.py tests/test_soc_tui_chat_app.py tests/test_soc_review_tui.py tests/architecture/test_soc_agent_boundaries.py -q`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_service.py tests/test_soc_tui_chat_runtime.py tests/test_soc_tui_chat_app.py tests/test_soc_review_tui.py tests/architecture/test_soc_agent_boundaries.py`
  - `codegraph sync .`
- 下一步：
  - 若继续 Agent 能力，补 route -> service/action 映射 contract，例如 review.open_context、review.correct、analysis.replay 的显式 command boundary。
  - 若先做产品闭环，做 ReviewQueue Web thin page。

### 2026-07-02 — SOC Agent chat TUI workbench shell 切片

- 新增：
  - `backend/soc_agent/tui/chat_app.py`
  - `SocAgentChatTUI`：基于 Textual 的主 SOC Agent chat workbench 壳。
  - 复用 DeerFlow TUI 的 `ComposerInput`、`ViewState/reduce()`、`render_transcript()`、`render_status()`。
  - `soc chat tui` CLI 入口。
  - `run_chat_tui()` runner。
- 当前能力：
  - 普通消息进入 `SocAgentChatService.stream()`。
  - `/open REV-...` 或 `soc chat tui --queue-id REV-...` 加载 review context。
  - `--message` 可在启动时发送初始消息；与 `--queue-id` 同时使用时带上 queue context。
  - TUI 自己生成稳定 `SOC-TUI-*` thread id，保证同一终端会话内多轮消息连续。
- 边界：
  - 这是 shell，不是真实 SOC Lead Agent。
  - 不直接读写 repository；CLI 只构造 service。
  - 不执行 close/correct/analyze。
  - 不定义另一套 view-state；复用 DeerFlow TUI action/reducer/render 语义。
- 新增测试：
  - `backend/tests/test_soc_tui_chat_app.py`
  - 覆盖 chat request 构造、`/open` 解析、显式 queue context、TUI actor surface、header render。
- 已验证：
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_tui_chat_app.py tests/test_soc_tui_chat_runtime.py tests/test_soc_agent_service.py tests/test_soc_review_tui.py -q`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_tui_chat_app.py tests/test_soc_tui_chat_runtime.py tests/test_soc_agent_service.py tests/test_soc_review_tui.py`
  - `cd backend && ./.venv/bin/python -m soc_agent.cli chat tui --help`
  - `codegraph sync .`
- 下一步：
  - 如果先补产品闭环，做 ReviewQueue Web thin page。
  - 如果继续主 Agent 能力，做 capability router：把 `/open`、review context、未来 skills/MCP/tool route 变成可审计的白名单 route，不让 LLM 直接控制主流程。

### 2026-07-02 — SOC TUI chat runtime adapter 切片

- 背景：
  - 上一刀已落地 `SocAgentChatService.stream()` 和 `SocAgentStreamEvent`。
  - 这一刀把 SOC stream 接到 DeerFlow TUI 的纯 action/reducer 层，为后续主 SOC Agent terminal workbench 铺路。
- 新增：
  - `backend/soc_agent/tui/chat_runtime.py`
  - `translate(event)`：复用 DeerFlow TUI 通用 `translate()` 处理 `values`、`messages-tuple`、`end`。
  - `stream_actions(service, request, context=...)`：和 DeerFlow `stream_actions()` 一样输出 `RunStarted -> actions -> RunEnded`，异常转 `AssistantError`。
  - `custom kind=soc.review_context` 转为 DeerFlow `SystemMessage`，用于 TUI 展示 queue/run/alert 上下文已加载。
- 边界：
  - 不启动 Textual。
  - 不直接访问 repository。
  - 不执行 close/correct/analyze 等业务动作。
  - 不把 SOC 结构化上下文放进 artifacts。
- 新增测试：
  - `backend/tests/test_soc_tui_chat_runtime.py`
  - 覆盖通用 DeerFlow-like 消息、SOC custom event、unknown custom ignore、service stream bracketing、reducer 集成、异常转 UI error。
- 已验证：
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_tui_chat_runtime.py tests/test_soc_review_tui.py tests/test_soc_agent_service.py tests/test_tui_runtime.py tests/architecture/test_soc_agent_boundaries.py -q`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_tui_chat_runtime.py tests/test_soc_review_tui.py tests/test_soc_agent_service.py tests/test_tui_runtime.py tests/architecture/test_soc_agent_boundaries.py`
  - `codegraph sync .`
- 下一步：
  - 若继续 TUI 方向，做 SOC Agent chat workbench：复用 DeerFlow Textual app 结构、ComposerInput、view_state/reducer/render，接 `SocAgentChatService`。
  - 若继续产品闭环，做 ReviewQueue Web thin page。

### 2026-07-02 — SOC Agent chat stream contract 切片

- 背景：
  - ReviewQueue TUI 已对齐 DeerFlow Textual 体验，但它仍是 thin client。
  - 主 SOC Agent 后续需要像 DeerFlow 一样支持 TUI/Web/Channels 的交互式调查、澄清、skills/MCP/tool 调用和 artifacts。
- 本切片只建立交互服务协议，不实现完整 Lead Agent：
  - 新增 `SocAgentStreamEvent`，事件类型保持 DeerFlow-like：`values`、`messages-tuple`、`custom`、`end`。
  - 新增 `SocAgentChatRequest` / `SocAgentChatResponse`。
  - `SocAgentChatService.stream()` 是 TUI/Web/Channels 的统一流式入口。
  - `SocAgentChatService.send_message()` 只是 materialize 同一条 stream，避免 headless/API 另起一套协议。
- 当前能力：
  - 无上下文时输出 deterministic ready message，明确 Phase 1 不调用 LLM。
  - 带 `queue_id` 时通过 `SocReviewService.get_investigation_context()` 加载 review context。
  - 通过 `custom kind=soc.review_context` 暴露 queue/run/alert 上下文给未来 TUI/Web 渲染层。
- 边界：
  - 不调用真实 SOC Lead Agent。
  - 不执行处置动作。
  - 不直接读写 repository。
  - 不把 review queue 结构化数据塞进 `ThreadState.artifacts`。
- 新增/更新测试：
  - `backend/tests/test_soc_agent_service.py`
  - 覆盖 DeerFlow-like event sequence、headless materialize、ReviewQueue context loading、缺少 `SocReviewService` 时 fail-fast。
- 已验证：
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_service.py -q`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_service.py`
  - `codegraph sync .`
- 下一步：
  - 若继续 TUI 方向，补 `soc_agent.tui` 的 chat runtime adapter，将 `SocAgentStreamEvent` 翻译为 TUI view-state action。
  - 若继续产品闭环，做 ReviewQueue Web thin page，复用已落地的 Gateway API。

### 2026-07-02 — ReviewQueue TUI thin client 切片

- 产品/架构决策：
  - TUI 必须兼容 DeerFlow 方向，不能另起一套完全独立的终端菜单。
  - 第一版是 ReviewQueue operator workbench，不接 SOC Lead Agent chat stream；后续 SOC Lead Agent 再复用 DeerFlow messages / artifacts / streaming / clarification。
- 新增 SOC TUI 模块：
  - `backend/soc_agent/tui/command_registry.py`
  - `backend/soc_agent/tui/view_state.py`
  - `backend/soc_agent/tui/render.py`
  - `backend/soc_agent/tui/app.py`
  - `backend/soc_agent/tui/runner.py`
- DeerFlow 对齐点：
  - 使用 Textual app。
  - 复用 `deerflow.tui.theme.THEME` 和 `deerflow.tui.widgets.composer.ComposerInput`。
  - 采用 slash command palette、状态/渲染分离、纯 command registry / render 测试的模式。
- TUI 命令：
  - `soc review tui`
  - `/refresh`
  - `/open REV-...`
  - `/close REV-... reason`
  - `/correct RUN-... verdict reason`
  - `/help`
  - `/quit`
- 边界：
  - 所有业务动作仍走 `SocReviewService`。
  - TUI 不直接读写 repository，不组装 queue item，不做自动判断。
  - close/correct 构造 `ServiceRequestContext`，审计 actor 使用 `surface=tui`。
- 新增测试：
  - `backend/tests/test_soc_review_tui.py`
  - 覆盖 slash command、view state、Rich render、correct 参数解析。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_review_tui.py tests/test_soc_review_router.py tests/test_soc_agent_service.py tests/test_soc_agent_runtime.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_review_tui.py tests/test_soc_review_router.py tests/test_soc_agent_service.py tests/test_soc_agent_runtime.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py -q`
  - `cd backend && ./.venv/bin/python -m soc_agent.cli review tui --help`
  - `codegraph sync .`
- 下一步：
  - 如果继续 ReviewQueue 产品闭环，做 Web thin page。
  - 如果转向主 SOC Agent，设计 SOC Lead Agent TUI/chat 如何复用 DeerFlow stream/messages/artifacts/clarification。

### 2026-07-02 — ReviewQueue API MVP 切片

- 产品决策：
  - 在 ReviewQueue UI/API/TUI 方向中，先做 API。
  - Web UI 和 TUI 后续都复用同一套 `SocReviewService` / API 语义，避免前端或终端入口各自拼业务逻辑。
- 新增 Gateway router：
  - `backend/app/gateway/routers/soc_review.py`
  - `GET /api/soc/review/items`
  - `GET /api/soc/review/items/{queue_id}/context`
  - `POST /api/soc/review/items/{queue_id}/close`
  - `POST /api/soc/review/runs/{run_id}/correct`
- API 边界：
  - 业务动作只调用 `SocReviewService`。
  - 如果 `app.state.soc_review_service` 已注入则直接使用，方便测试和未来 TUI/Web adapter。
  - 默认从 `SOC_DATABASE_URL` 或 DeerFlow postgres 配置创建 `SqlAlchemyAlertRepository`。
  - close/correct 会构造 `ServiceRequestContext`，actor surface 固定为 `api`，支持 `x-soc-actor-id`。
- 新增测试：
  - `backend/tests/test_soc_review_router.py`
  - 覆盖列表、调查上下文、关闭、纠正、缺失 404、MVP route path 暴露。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff check app/gateway/routers/soc_review.py app/gateway/app.py soc_agent tests/test_soc_review_router.py tests/test_soc_agent_service.py tests/test_soc_agent_runtime.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_review_router.py tests/test_soc_agent_service.py tests/test_soc_agent_runtime.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py -q`
- 下一步：
  - 做 ReviewQueue TUI thin client 或 Web thin page。
  - 当前建议先做 TUI thin client，因为它更贴近 Phase 1/2 的开发调试和值班操作，且不需要先投入前端页面布局。

### 2026-07-02 — Offline eval：stub / llm / replay diff 切片

- 新增离线评测模块：
  - `backend/soc_agent/eval/offline.py`
  - `run_offline_eval(samples, responses=..., model_name=...)` 对同一批样本分别跑 deterministic stub 和 replayable `JsonLLMAnalyzer`，输出差异报告。
  - `load_eval_responses_jsonl(path)` 支持按 `sample_id` 读取录制/模拟 LLM 输出，`content` 可以是字符串或 JSON object。
- 新增评测 report：
  - `OfflineEvalReport`
  - `OfflineEvalSampleResult`
  - `OfflineEvalResponse`
  - 指标包括 `parse_success_count`、`repair_count`、`failed_count`、`verdict_diff_count`、`needs_review_diff_count`、`average_abs_confidence_delta`。
- 新增 CLI：
  - `soc eval offline PATH --glob "*.json" --llm-response-jsonl responses.jsonl --model-name replay-llm`
  - 没有提供 `--llm-response-jsonl` 时，会把 stub 结果作为 replay response 再走一遍 prompt/parser/runtime，用于 smoke-test LLM 节点工程链路。
  - 提供 JSONL 后，可以对真实模型录制输出或手写 golden 输出做 replay diff；默认仍不调用外部模型。
- 新增测试：
  - `backend/tests/test_soc_agent_offline_eval.py`
  - 覆盖默认 stub replay、verdict diff + bad JSON repair、parse failure 不打断 batch、JSONL object content、CLI 输出。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_offline_eval.py tests/test_soc_agent_llm_analyzer.py tests/test_soc_agent_llm_json_parser.py tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_offline_eval.py tests/test_soc_agent_llm_analyzer.py tests/test_soc_agent_llm_json_parser.py tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/architecture/test_soc_agent_boundaries.py -q`
  - `cd backend && ./.venv/bin/python -m soc_agent.cli eval offline samples/alerts --glob approved_scanner.json --pretty`
- 下一步：
  - Phase 1 固定链路已具备 prompt/parser/LLM adapter/offline eval 基础。
  - 接下来在 `ReviewQueue UI` 与 `Kafka daemon` 之间做选择：如果先服务分析师闭环，做 ReviewQueue/API/Web/TUI；如果先验证流式接入和反压，做 Kafka daemon。

### 2026-07-02 — 真实 LLM analyzer behind flag 切片

- 新增 bounded LLM analyzer：
  - `backend/soc_agent/llm/analyzer.py`
  - `JsonLLMAnalyzer` 只负责 `build_analysis_prompt()` -> injected chat client -> `parse_analysis_result_output()` -> `AnalysisNodeOutput`。
  - `build_optional_llm_analyzer(enabled=False)` 默认返回 deterministic `StubLLMAnalyzer`；`enabled=True` 必须显式注入 client。
- 调整 runtime analyzer 边界：
  - `LLMAnalyzer` protocol 返回 `AnalysisNodeOutput`，包含 `AnalysisResult`、`model_name`、`prompt_version`、`parser_version` 和 metadata。
  - `analyze_alert(payload, analyzer=None)` 默认仍使用 `StubLLMAnalyzer`。
  - `DeterministicAnalysisRuntime(analyzer=...)` 可注入真实 analyzer，后续 API/CLI/daemon 都能共用同一个 runtime 入口。
- 审计记录：
  - `PipelineStepTrace.metadata` 记录 analyzer、`model_name`、`prompt_version`、`parser_version`、`prompt_hash`、`candidate_hash`、`repair_applied`、usage 和 response metadata。
  - 不把完整 prompt 或 raw LLM 输出写入 step metadata，避免 trace 过大和敏感信息扩散。
- 新增测试：
  - `backend/tests/test_soc_agent_llm_analyzer.py`
  - 覆盖默认 flag 返回 stub、enabled 缺 client 失败、fake chat client 走 prompt/parser/repair/runtime trace、默认 runtime 仍保持旧 step 顺序。
- 已验证：
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_llm_analyzer.py tests/test_soc_agent_llm_json_parser.py tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/architecture/test_soc_agent_boundaries.py -q`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_llm_analyzer.py tests/test_soc_agent_llm_json_parser.py tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/architecture/test_soc_agent_boundaries.py`
- 下一步：
  - 做 offline eval：同一批样本跑 stub / llm / replay diff。
  - 先使用 fake/replayable LLM client，不默认调用真实外部模型；评估指标稳定后再接 CLI/API 配置开关。

### 2026-07-02 — SOC Lead Agent / Skill / MCP / Node Prompt 分层决策

- 明确当前 `soc-analysis-v1` 是固定 Runtime 内的 analysis node prompt，不是 SOC Lead Agent 总控 prompt。
- 在 `.notes/ai_soc/soc-agent-solution.md` 增加分层：
  - SOC Lead Agent / Operator Agent：交互、任务理解、选择 skill、选择 MCP/tool、提出调查计划。
  - SOC Runtime / Core Services：固定流水线、状态机、校验、审计、replay、权限和失败处理。
  - Domain Skills：EDR、APT、F5/WAF、资产归属、攻击方向、处置剧本等领域知识。
  - MCP / Tool Gateway：EDR、资产、SOAR、防火墙等外部能力调用。
  - Node Prompts：`llm_analyze`、correlation rerank、knowledge extraction 等固定节点推理。
- 在 `.notes/reference-index/soc-agent-engineering-contracts.md` 增加 Prompt / Skill / Tool 分层约束：
  - 后续 `SocSkillResolver` 先用 deterministic 规则按 `source_type`、`detection_key`、category、entity kind 选择 skill。
  - LLM 只能在白名单 skill 候选中 rerank 或提出建议，不能动态加载未知 skill 后直接影响决策。
  - MCP/tool 调用必须经过 allowlist、policy、audit 和必要的人类审批。
- 当前下一刀不变：
  - 继续做 LLM JSON output parser + schema validation + bad JSON repair golden sample。

### 2026-07-02 — LLM JSON parser + bad JSON repair 切片

- 新增依赖：
  - `json-repair>=0.61.1`
- 新增 SOC LLM parser：
  - `backend/soc_agent/llm/json_parser.py`
  - `ANALYSIS_JSON_PARSER_VERSION = "soc-analysis-json-parser-v1"`
  - `parse_analysis_result_output(response_content)` 返回 `ParsedAnalysisResult`，包含 `AnalysisResult` 和 parser audit metadata。
- Parser 行为：
  - 借鉴 DeerFlow memory updater / suggestions 的方式，先从 string 或 content blocks 提取文本。
  - 去掉 `<think>...</think>` 和整段 markdown code fence。
  - 先用 `json.JSONDecoder().raw_decode()` 抽取严格合法的顶层 `AnalysisResult` JSON object。
  - 严格解析失败后，再调用 `json_repair.loads(..., logging=True, skip_json_loads=True)`。
  - repair 后仍必须通过 raw shape check、`AnalysisResult.model_validate()` 和 `validate_analysis_result()`。
  - 如果 repair 得到空对象、非对象、缺字段、空 evidence、字符串 confidence 等，显式抛 `LLMOutputParseError`，不假装成功。
- 新增 bad JSON golden tests：
  - `backend/tests/test_soc_agent_llm_json_parser.py`
  - 覆盖 strict JSON、`<think>` + code fence、夹杂说明文本、尾逗号、未加引号 key、字符串 confidence、空 evidence、不可恢复文本。
- 已验证：
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_llm_json_parser.py -q`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/test_soc_agent_prompts.py tests/test_soc_agent_llm_json_parser.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/test_soc_agent_prompts.py tests/test_soc_agent_llm_json_parser.py tests/architecture/test_soc_agent_boundaries.py`
  - `codegraph sync .`
- 下一步：
  - 接真实 LLM analyzer behind flag。
  - 默认仍走 deterministic `analyze_stub`，真实模型输出必须经过 `build_analysis_prompt()` 和 `parse_analysis_result_output()`。

### 2026-07-01 — SOC analysis Prompt Builder 切片

- 新增 versioned prompt builder：
  - `backend/soc_agent/prompts/analysis.py`
  - `ANALYSIS_PROMPT_VERSION = "soc-analysis-v1"`
  - `build_analysis_prompt(request: LLMAnalysisRequest)` 只消费 bounded request，不读取 raw vendor payload。
- Prompt 结构：
  - system prompt 固定 runtime/LLM 边界：Runtime 掌握流程，LLM 只输出结构化 JSON。
  - user prompt 注入 bounded analysis context：source、detection、classification、canonical/extracted entities、evidence policy、field trusts、role assignments、conflict reports、warnings。
  - response schema 明确 `AnalysisResult` 所需字段和 verdict 枚举。
- 新增 golden tests：
  - `backend/tests/test_soc_agent_prompts.py`
  - 覆盖 PingAn APT 字段冲突、PingAn EDR 低可信 structured fallback、缺失 evidence policy。
  - 验证 prompt 不把完整 raw payload 字段如 `process__cmd_line` / `finding__desc` 无脑塞入上下文。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/prompts tests/test_soc_agent_prompts.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_prompts.py -q`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/test_soc_agent_prompts.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/test_soc_agent_prompts.py tests/architecture/test_soc_agent_boundaries.py`
  - `codegraph sync .`
- 下一步：
  - 做 LLM JSON output parser + schema validation + bad JSON repair golden sample。
  - parser 完成前，不接真实 LLM analyzer。

### 2026-07-01 — LLM-ready 分析输入切片

- 新增 `LLMAnalysisRequest` contract：
  - 包含 canonical source / detection / classification / entities。
  - 包含 `ExtractedEntities` 和 `FactReconstructionResult`。
  - 包含 `primary_evidence_path`、`conflict_count`、`conflict_types`、`warnings`。
- 新增 deterministic builder：
  - `backend/soc_agent/pipeline/analysis_context.py`
  - runtime 顺序变为 `normalize -> entity_extract -> fact_reconstruct -> build_analysis_input -> analyze_stub -> schema_validate -> decide`。
  - `AnalysisRun.llm_analysis_request` 随 run payload 一起持久化和 replay。
- 调整 analyzer 边界：
  - `analyze_stub()` 改为消费 `LLMAnalysisRequest`。
  - `LLMAnalyzer` protocol 改为 `analyze(request: LLMAnalysisRequest)`。
  - analyzer evidence/reason 会显式引用 fact layer 的低可信 fallback 和字段冲突。
- 当前原则：
  - 真实 LLM 只能消费 `LLMAnalysisRequest`。
  - prompt builder 后续只能从该 request 生成 prompt，不直接塞完整 raw payload。
- 已验证：
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py -q`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `codegraph sync .`
  - `codegraph status .` 显示 index up to date；当前统计为 1,158 files / 21,981 nodes / 49,444 edges。
- 下一步：
  - 先补 Prompt Builder + SOC analysis prompt golden tests；真实 LLM analyzer 仍 behind flag，默认不调用外部模型。
  - 为 PingAn raw message 样本增加 prompt golden case，验证冲突字段如何呈现给模型。
  - 后续顺序固定为：LLM JSON parser + schema validation + bad JSON repair golden sample -> 真实 LLM analyzer behind flag -> offline eval（stub / llm / replay diff）-> ReviewQueue UI 或 Kafka daemon。

### 2026-07-01 — 事实重建最小切片

- 新增事实重建契约：
  - `FieldTrust`
  - `RoleAssignment`
  - `ConflictReport`
  - `FactReconstructionResult`
- 新增 deterministic pipeline 节点：
  - `backend/soc_agent/pipeline/fact_reconstructor.py`
  - runtime 顺序变为 `normalize -> entity_extract -> fact_reconstruct -> analyze_stub -> schema_validate -> decide`。
  - `AnalysisRun.fact_reconstruction` 随 run payload 一起持久化和 replay。
- 当前能力：
  - 根据 `EvidenceInputPolicy` 判断主证据是否可用。
  - raw message 存在时，将 canonical processed fields 标成低可信且不参与主事实重建。
  - raw message 缺失时，structured fallback 会产生低可信 warning。
  - 检测同一角色多候选值、`attacker/source` 不一致、`victim/destination` 不一致、source/destination 重叠。
- 已验证：
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py -q`
  - `codegraph sync .`
  - `codegraph status .` 显示 index up to date；当前统计为 1,157 files / 21,973 nodes / 49,491 edges。
- 下一步：
  - 把 fact layer 输入给后续真实 LLM analyzer，让模型基于“主证据 + 角色候选 + 冲突报告”输出结构化研判，而不是直接吃脏字段。
  - 为 PingAn 真实 `raw_message` 样本增加 parser / LLM extraction 评测样例。

### 2026-07-01 — ZEUS / 天眼证据输入策略

- 根据同事反馈的上游日志方向不可靠、加工字段冲突问题，新增 `.notes/ai_soc/zeus-alert-flow-and-field-trust.md`：
  - 梳理 ZEUS/天眼告警流程。
  - 记录 raw message、结构化原始字段、加工字段、skills/记忆、人工复核的可信度分层。
  - 补充 Mermaid 流程图和泳道图。
- 在工程契约中补充 `EvidenceInputPolicy` 约束：
  - policy 只决定事实重建/LLM 研判的主输入，不代表最终事实结论。
  - 平安 adapter 使用 `raw_message_first + structured_fallback`。
  - raw message 缺失时必须记录 `fallback_reason=raw_message_missing` 和较低 trust level。
- 代码切片：
  - `backend/soc_agent/contracts/schemas.py` 新增 `EvidenceLayer`、`EvidenceTrustLevel`、`EvidenceInputPolicyName`、`EvidenceInputPolicy`。
  - `backend/soc_agent/normalizers/pingan_platform.py` 在 `extensions.evidence_input_policy` 写入主证据选择策略。
  - 支持 `message`；没有 raw message 时 fallback 到完整 `zeusRawLogs`。
- 下一步：
  - 在事实重建节点引入 `FieldTrust` / `ConflictReport`，用于攻击方向、攻击源/受害方、影响资产、处置目标的冲突解释。
  - LLM 只读取 policy 选择后的主证据和必要候选字段，不直接相信上游加工字段。

### 2026-06-28

- 已完成前置准备：
  - `.notes/ai_soc/soc-agent-solution.md` 作为当前权威方案。
  - `.notes/reference-index/soc-agent-engineering-contracts.md` 作为工程契约。
  - CodeGraph index 已更新。
  - Understand Anything 图谱已通过 opencode 更新到当前 HEAD。
  - `AGENTS.md` 已加入 SOC Agent 长期开发工作流和进度台账要求。
- 当前决策：
  - 第一刀不做 Web UI、Daemon、多 Agent、RAG、自动处置。
  - 第一刀做 Phase 1 最小闭环骨架：CLI + Runtime + contracts + trace + samples + tests。
- 下一步：
  - 补 Phase 1 LLM JSON parser / `json_repair` 层和坏 JSON golden sample。
  - 设计 PostgreSQL schema 草案：`analysis_runs`、`pipeline_step_traces`、`decision_audit_log`、`alert_summaries`。
  - 再接真实 LLM analyzer 前，先补 prompt sanitizer 和 prompt/model/pipeline version 审计字段。

### 2026-06-28 — Phase 1 骨架切片完成

- 新增独立 SOC 模块，未修改 DeerFlow harness 核心：
  - `backend/soc_agent/contracts/`
  - `backend/soc_agent/core/`
  - `backend/soc_agent/pipeline/`
  - `backend/soc_agent/cli.py`
- 新增 Phase 1 固定 runtime：
  - `normalize`
  - `entity_extract`
  - `analyze_stub`
  - `schema_validate`
  - `decide`
- 新增 golden samples：
  - `backend/samples/alerts/approved_scanner.json`
  - `backend/samples/alerts/malicious_ioc.json`
  - `backend/samples/alerts/unknown_low_context.json`
  - `backend/samples/alerts/missing_fields.json`
- 新增测试：
  - `backend/tests/test_soc_agent_runtime.py`
- 新增 CLI console script：
  - `soc = "soc_agent.cli:main"`
- 已验证：
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_runtime.py`
  - `cd backend && ./.venv/bin/python -m ruff format --check soc_agent tests/test_soc_agent_runtime.py`
  - `cd backend && ./.venv/bin/python -m soc_agent.cli analyze samples/alerts/approved_scanner.json --pretty`
- 注意：
  - `uv run ...` 在当前沙箱中会尝试写 `~/.cache/uv` 或下载缺失依赖，验证时改用项目已有 `backend/.venv`。
  - 当前 analyzer 是 deterministic stub，不调用 LLM，不落库，不执行自动处置。

### 2026-06-28 — AlertInput 多源告警契约升级

- 将 `AlertInput` 从简单平铺字段升级为“通用 envelope + source/detection/event/classification/entities/extensions/raw”结构。
- 新增 `DetectionRuleRef`：
  - `rule_code` 是可选强标识，不作为必填字段。
  - `detection_key` 由 runtime 归一化生成，按 `rule_code -> rule_name -> category -> raw fingerprint` 降级。
- 新增 `AlertSourceRef` / `AlertSourceType`：
  - 覆盖 SIEM、EDR、XDR、HIDS、NIDS、NDR、WAF、F5、IAM、Cloud、Threat Intel 等来源。
  - 未知厂商/source type 自动降级为 `other`，原始值保留为 `source_system`，避免新客户接入时 schema 失败。
- 新增标准实体集：
  - network / process / user / host / file / http / threat。
  - EDR/HIDS/NIDS/F5/WAF/APT 类告警可通过标准实体表达，特殊字段放 `extensions` 和 `raw`。
- 将外部平铺字段兼容移出核心契约：
  - `AlertInput` 只保留 canonical nested schema，并设置 `extra="forbid"`。
  - 旧样例里的 `rule_name/source_ip/process_name/command_line/...` 由 `normalizers/alert.py` 映射为 canonical schema 后再进入 runtime。
  - extractor/analyzer 只读取 `alert.detection`、`alert.entities`、`alert.classification` 等 canonical 字段。
- 已将 `backend/samples/alerts/*.json` 改成 canonical nested 示例；flat/simple payload 只保留在 normalizer 测试里，用于验证外部接入兼容性。
- 新增 normalizer 层：
  - `backend/soc_agent/normalizers/alert.py`
  - `normalize_alert_payload()` 负责 flat/simple/vendor-like payload 到 `AlertInput` 的转换。
  - 后续 `pingan.py`、`f5.py`、`edr.py`、`nids.py` 等 source-specific adapter 应在该层扩展，不污染核心 schema。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent tests/test_soc_agent_runtime.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_runtime.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py`
- 下一步：
  - 围绕该契约设计 PostgreSQL `alert_summaries` / `analysis_runs` / `pipeline_step_traces` 的字段映射。
  - 后续 Kafka/API adapters 只做 source-specific mapping，不绕过 `AlertInput`。

### 2026-06-28 — 模块接口与协议约束补充

- 已将长期模块边界、public API、Protocol、normalizer、架构测试约束补入 `.notes/reference-index/soc-agent-engineering-contracts.md`。
- 后续新增模块必须先明确：
  - 模块职责、调用方、允许依赖层。
  - 输入/输出 contract 或 domain model。
  - 失败语义、审计事件、持久化边界、replay 行为。
  - 是否读写 memory/facts/lessons，是否需要 human confirmation。
- 固定后续实现原则：
  - CLI/API/Daemon/Web UI 只调用 core service，不直接拼 pipeline。
  - 可替换依赖先定义 `Protocol`，业务代码不直接依赖 PostgreSQL、Kafka、具体 LLM SDK。
  - `AlertInput` 保持 canonical strict schema；flat/vendor payload 只允许在 `normalizers/` 层出现。
  - 架构测试后续要覆盖 import 边界、public exports、contracts strict、pipeline 无 transport imports、tools 必须经过 policy。
- 建议下一切片：
  - 建立 `core/service.py`、`protocols/` 和 `tests/architecture/`，把当前 Runtime 包成稳定 public service。

### 2026-06-28 — Core service 与架构测试切片完成

- 新增稳定业务入口：
  - `backend/soc_agent/core/service.py`
  - `SocAnalysisService.analyze(payload)` 包装当前 deterministic runtime。
- 新增可替换依赖协议：
  - `backend/soc_agent/protocols.py`
  - 当前包含 `AlertNormalizer`、`AnalysisRuntime`、`LLMAnalyzer`、`AlertRepository`。
- CLI 已改为通过 `SocAnalysisService` 进入业务逻辑，不再直接 import `core.runtime`。
- 新增架构边界测试：
  - `backend/tests/architecture/test_soc_agent_boundaries.py`
  - 覆盖 contracts 不 import runtime 层、core 不 import transport、pipeline 不 import transport/基础设施、CLI 通过 core service 进入、`AlertInput` 保持 strict。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format --check soc_agent tests/test_soc_agent_runtime.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_runtime.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py tests/architecture/test_soc_agent_boundaries.py`
- 下一步：
  - 后续 API、Daemon、Web UI 均接 `SocAnalysisService`，不直接拼 pipeline。
  - 如果协议继续膨胀，再将 `protocols.py` 拆成 `protocols/` 包。

### 2026-06-28 — 多入口与 Core Services 方案更新

- 已更新 `.notes/ai_soc/soc-agent-solution.md`：
  - 将“三类入口”升级为 Kafka Daemon、API/Gateway、CLI、TUI/Operator Console、Web UI 多入口。
  - 明确所有入口只做 transport / presentation / session 编排，统一进入 core services。
  - 明确 TUI 可作为 Phase 3/4 的后端 Operator Console / Agent Console，用于值班运营、安全分析、检测工程、授权攻防交互。
  - 补充 service layer：`SocAnalysisService`、`SocReviewService`、`SocMemoryService`、`SocDaemonService`、`SocAgentChatService`。
  - 更新长期 Security Agent Platform 说明：综合入口不是单一 Agent，不同任务必须路由到不同 service/agent，并受 memory scope、tool permission、audit 约束。
- 当前实现已先落地 `SocAnalysisService`；后续 API、Daemon、TUI、Web UI 都应接 service，不直接接 pipeline。

### 2026-06-28 — DeerFlow/TUI 对齐与 Service Context 基座

- 参考方式：
  - 使用 Understand 查看 Hermes / claude-mem 的多入口与 service/runtime 分层。
  - 使用 CodeGraph 查看 DeerFlow `deerflow.tui`、`run_agent`、`RunManager`、`StreamBridge`，确认 TUI 是入口层，底层仍走 runtime/run manager/event stream。
  - 使用 CodeGraph 查看 Claude Code `QueryEngine`、openclaw `Agent.runWithLifecycle`、claude-mem `ServerBetaService` / `SessionManager`，确认统一 lifecycle、event stream、shared service 是可复用模式。
- 已补充代码基座：
  - `ActorContext`、`EntrySurface`、`ServiceRequestContext`、`SocEvent`、`SocEventType`。
  - `SocAnalysisService` 支持 request context、event sink、repository 注入。
  - 新增 `DeterministicAnalysisRuntime`、`NoopEventSink`。
  - 新增 `SocReviewService`、`SocMemoryService`、`SocDaemonService`、`SocAgentChatService` 占位，未实现功能 fail-fast。
  - 新增 `SocEventSink` 协议。
- 已补充测试：
  - `backend/tests/test_soc_agent_service.py`
  - service 事件发送、repository 保存、未实现 service fail-fast。
  - architecture test 增加 core public service exports。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format --check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/architecture/test_soc_agent_boundaries.py`

### 2026-06-28 — 方向收敛与 replay contract

- 已收敛文档关系：
  - `.notes/ai_soc/soc-agent-solution.md` 决定产品方向、阶段顺序和入口取舍。
  - `.notes/reference-index/soc-agent-engineering-contracts.md` 决定代码接口、协议、边界和测试约束。
  - `.notes/ai_soc/README.md` 已写入执行规则，避免多份文档互相覆盖。
- 已修正入口口径：
  - SOC 对齐 DeerFlow 的 Web UI、Gateway API、TUI/Terminal Workbench、Headless CLI、Channels。
  - Kafka/Redpanda 是后台 ingestion adapter，不是替代 Web/TUI 的用户入口。
- 已补充 replay contract：
  - `AnalysisRun.input_payload` 保存可 replay 的输入快照。
  - `AnalysisRun.input_hash` 保存稳定输入 hash。
  - `AnalysisRun.replay_of_run_id` 记录 replay 来源 run。
  - `SocAnalysisService.replay(run_id)` 通过 repository 取回旧 run 输入，生成新的 run，不覆盖历史 run。
  - 新增 `SocServiceNotFoundError` 表达 run 不存在。
- 已补充测试：
  - runtime 记录输入快照和 input hash。
  - service replay 生成新 run，保留旧 run，事件 payload 标记 `replay_of_run_id`。
  - replay 旧 run 不存在时 fail-fast。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format --check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/architecture/test_soc_agent_boundaries.py`
- 下一步：
  - 实现 PostgreSQL `AlertRepository`，把 `AnalysisRun` 存到 SOC 自己的业务表。
  - repository 可用后再把 `soc show` / `soc replay` 挂到 headless CLI。

### 2026-06-28 — SOC SQLAlchemy AlertRepository

- 新增 SOC 自有持久化模块，未修改 DeerFlow harness 核心：
  - `backend/soc_agent/db/base.py`
  - `backend/soc_agent/db/models.py`
  - `backend/soc_agent/db/repositories.py`
- 新增 `SocAnalysisRunRow`：
  - 表名：`soc_analysis_runs`
  - 索引字段：`run_id`、`alert_id`、`status`、`input_hash`、`replay_of_run_id`
  - 保存 `input_payload` 和完整 `run_payload`，保证后续 `show/replay` 不依赖临时内存。
- 新增 `SqlAlchemyAlertRepository`：
  - 实现 `save_run()` 和 `get_run()`。
  - 支持保存、读取、同 run upsert、service replay。
  - 当前以 sync `Session` factory 注入，适合 Phase 1 headless CLI；后续 Gateway async API 需要线程池调用或单独 async adapter。
- 新增测试：
  - `backend/tests/test_soc_agent_repository.py`
  - 覆盖 save/get、upsert、service replay。
  - 架构测试增加 `db` 不 import core/pipeline/transport 的边界约束。
- 新增 headless CLI 持久化闭环：
  - `soc db init`
  - `soc db upgrade`
  - `soc analyze ALERT.json --persist`
  - `soc show RUN_ID`
  - `soc replay RUN_ID`
  - 数据库 URL 通过 `--database-url`、`SOC_DATABASE_URL` 或 DeerFlow `database.backend=postgres` / `database.postgres_url` 解析；PostgreSQL URL 会归一化为 sync `postgresql+psycopg://`。
- 新增 SOC Alembic migration：
  - `backend/soc_agent/db/migrations/versions/0001_soc_analysis_runs.py`
  - 版本表使用 `soc_alembic_version`，不和 DeerFlow harness migration 混用。
- 说明：
  - 测试使用 SQLite in-memory / temp file 只是 SQLAlchemy unit harness；SOC runtime 策略仍是 PostgreSQL。
  - `soc db init` 保留为开发辅助；正式路径使用 `soc db upgrade`。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format --check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`

### 2026-06-28 — Manual correction loop

- 新增 correction contracts：
  - `CorrectionCommand`
  - `CorrectionRecord`
  - `AnalysisRun.corrections`
  - `SocEventType.REVIEW_CORRECTED`
- 实现 `SocReviewService.correct()`：
  - 通过 repository 读取目标 run。
  - 保留原 AI verdict / previous verdict。
  - 更新当前 `run.decision` 为分析师纠正后的 verdict。
  - 追加 `CorrectionRecord`，`candidate_knowledge_status="pending_review"`。
  - 保存 run 并发送 `review.corrected` 事件。
- 新增 headless CLI：
  - `soc correct RUN_ID --verdict false_positive --reason "..."`
  - 纠正依赖 repository，因此需要 `--database-url`、`SOC_DATABASE_URL` 或 DeerFlow PostgreSQL config。
- 安全边界：
  - correction 不执行任何自动处置。
  - correction 不直接写 confirmed memory/fact/lesson；只作为后续 memory extraction 的 pending-review 来源。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format --check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`

### 2026-06-28 — Decision audit log

- 新增审计 contracts：
  - `AuditAction`
  - `DecisionAuditRecord`
  - `DecisionAuditRepository` protocol
- 新增 SOC 审计表：
  - `soc_decision_audit_log`
  - migration：`backend/soc_agent/db/migrations/versions/0002_decision_audit_log.py`
  - 版本仍走 `soc_alembic_version`，与 DeerFlow harness migration 隔离。
- 扩展 `SqlAlchemyAlertRepository`：
  - `save_audit_record()`
  - `list_audit_records(run_id)`
- 扩展 service 审计写入：
  - `SocAnalysisService.analyze()` 写 `AuditAction.ANALYSIS`
  - `SocAnalysisService.replay()` 写 `AuditAction.REPLAY`
  - `SocReviewService.correct()` 写 `AuditAction.CORRECTION`
- 审计记录包含：
  - `run_id`、`alert_id`、`actor`、`input_hash`
  - previous/final verdict、confidence
  - replay source、correction id
  - pipeline/model/prompt version、step count、candidate knowledge status 等 payload。
- 当前边界：
  - 只写审计和 repository 查询测试，不做 CLI/UI 审计查询入口。
  - 审计记录不替代 full `run_payload`；两者分别服务查询指标和完整回放。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format --check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`

### 2026-06-29 — Alert summary read model

- 新增 `AlertSummary` contract：
  - 面向告警列表、review queue、dedup、correlation、Web/TUI 查询。
  - 不替代 `AnalysisRun`；完整事实仍在 `soc_analysis_runs.run_payload`。
  - 字段包括 source/detection/severity/category/entity_keys/verdict/confidence/needs_review/summary/recommended_action。
- 新增 `AlertSummaryRepository` protocol：
  - `save_alert_summary()`
  - `get_alert_summary()`
  - `list_alert_summaries(limit=...)`
- 扩展 core service：
  - `SocAnalysisService.analyze()` 写 run 后维护 summary。
  - `SocAnalysisService.replay()` 为 replay run 写新 summary，并记录 `replay_of_run_id`。
  - `SocReviewService.correct()` 更新同一 run summary 的 operational verdict。
  - CLI/API/TUI/daemon 后续仍只调用 service，不自己拼 summary。
- 新增 SOC 表：
  - `soc_alert_summaries`
  - migration：`backend/soc_agent/db/migrations/versions/0003_alert_summaries.py`
  - 按 `alert_id`、`tenant_id`、`source_type`、`detection_key`、`rule_code`、`verdict`、`needs_review`、`updated_at` 建索引。
- 扩展 `SqlAlchemyAlertRepository`：
  - 实现 summary save/get/list。
  - `soc analyze --persist`、`soc replay`、`soc correct` 均通过 service 注入同一个 repository 维护 summary。
- 已补充测试：
  - service 写 summary。
  - correction 更新 summary。
  - repository 持久化、replay summary、list summary、correction summary。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
- 下一步：
  - 补 `ReviewQueue` 最小 contract/table/service，基于 `AlertSummary.needs_review` 和人工纠正结果沉淀待复查队列。
  - 或先补 `soc list` / future API list 的读取入口，验证 Web/TUI 列表需要的筛选字段是否足够。

### 2026-06-29 — Legacy platform normalizer

- 新增平安旧预警平台 adapter：
  - `backend/soc_agent/normalizers/pingan_platform.py`
  - 识别 `alert.hitLog[].zeusRawLogs[]` envelope。
  - 映射 `alertId`、`ruleCode`、`ruleName`、`topic/topicName`、`riskLevel`、`primary/secondary/tertiaryType`。
  - 映射 APT/NDR 类字段：`sip/dip/sport/dport/host/x_forwarded_for/payload.req_header/att_ck`。
  - 映射 EDR 类字段：`str_source_ip/str_attack_ip/device__hostname/process__cmd_line/process__user__name/file md5/MITRE`。
  - SOAR rows 仅作为 host/user fallback，不直接改变 verdict。
- 通用 normalizer 更新：
  - `normalize_alert_payload()` 在检测到旧平台 envelope 时自动分派到 adapter。
  - `AlertInput` 仍保持 canonical strict；旧平台字段不进入 core schema。
- 新增脱敏 golden samples：
  - `backend/samples/alerts/pingan_legacy_apt.json`
  - `backend/samples/alerts/pingan_legacy_edr.json`
  - 原始 `alert_demo/` 含真实人员/组织/内网信息，仅作为本地参考，不提交入库。
- 新增测试：
  - APT demo 可提取 `alert_id/rule_code/rule_name/source/IP/domain/http/MITRE`。
  - EDR demo 可提取 `alert_id/rule_code/rule_name/source/IP/host/user/process/file hash/MITRE`。
  - 完整 runtime 后 `ExtractedEntities` 不再为空。
- 已用原始本地 demo 验证：
  - `alert_demo/apt-2026494.json` -> `2026494 / ndr / RPAADM_002635 / 30.180.248.178 / 30.185.76.75 / TA0001 / T1190`
  - `alert_demo/edr-1965810.json` -> `1965810 / edr / RPAADM_002583 / 10.43.107.39 / 30.162.29.85 / svchost.exe / WANGJIAN191`
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
- 下一步：
  - 基于真实 normalizer 输出补 `soc list`，先验证 `AlertSummary` 对 Web/TUI 列表字段是否足够。
  - 然后再补 `ReviewQueue`，避免在字段不稳定时提前固化复核队列结构。

### 2026-06-29 — Legacy platform context hardening

- 已将本地原始 demo 目录加入 `.gitignore`：
  - `alert_demo/`
  - 原因：该目录可能包含真实人员、组织、内网资产和平台处置记录，只作为本机验证材料。
- 扩展 `extensions.legacy_platform` 结构：
  - `workflow`：`alert_code`、`alert_name`、`execute_type`、`status`、`created_at`、处理动作和处理人。
  - `taxonomy`：`primary/secondary/tertiaryType`、`profileCode/profileName`、`topic/topicName`。
  - `ownership`：`dst_BUcode`、目标公司/部门、资产组、行业、SOAR 资产归属。
  - `sensor`：探针/节点字段，例如 `device_ip`、`node_ip`、`idc_location`、`vlan/vxlan`、`skyeye_type`。
  - `disposition`：`host_state`、`is_blocked`、`is_banned`、`is_white`、`repeat_count`、`confidence`、风险等级。
  - `correlation`：`alarm_id`、`alert_hash`、`logcloud_msgid`、raw event 数、related alert 数、SOAR 查询名。
  - `soar`：SOAR display names 和脱敏后的资产摘要。
- 设计边界：
  - 平安运营字段仍不进入 `AlertInput` 顶层，避免污染跨供应商 canonical schema。
  - 后续 `soc list` / ReviewQueue / CaseContext 如果需要高频查询，再从 `extensions.legacy_platform` 提升少量字段到 `AlertSummary`。
- 已补充测试：
  - APT golden sample 验证 workflow/taxonomy/ownership/sensor/disposition/correlation。
  - EDR golden sample 验证 SOAR asset summary。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`

### 2026-06-29 — CLI summary list

- 新增 headless CLI：
  - `soc list --database-url ...`
  - `soc list --limit 10 --pretty`
- 功能边界：
  - 只读取已持久化的 `AlertSummary`，不直接读 DB row，不扫描完整 `AnalysisRun.run_payload`。
  - 输出 JSON array，字段来自 `AlertSummary` contract，可作为 Web/TUI 列表字段验证。
  - correction 后列表中的 operational verdict 会跟随 summary 更新。
- 已补充测试：
  - 持久化 PingAn APT/EDR golden samples 后，`soc list` 返回 `alert_id/source_type/rule_code/entity_keys`。
  - 对 EDR run 执行 `soc correct` 后，`soc list` 返回 `verdict=true_positive` 且 `needs_review=false`。
- 当前判断：
  - `AlertSummary` 的基础列表字段已经能支撑 Phase 1/2 的 Web/TUI 告警列表原型。
  - 平安平台特有的 `workflow/ownership/sensor/disposition` 暂时留在 `extensions.legacy_platform`，后续如果列表筛选需要，再提升到 summary 索引列。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
- 下一步：
  - 做 `ReviewQueue` 最小 contract/table/service：由 `AlertSummary.needs_review`、low confidence、manual correction 和 high-risk source 生成复核队列。

### 2026-06-29 — ReviewQueue minimal loop

- 新增 ReviewQueue 最小闭环：
  - `ReviewQueueItem` / `ReviewQueueCloseCommand` / `ReviewQueueStatus` / `ReviewQueuePriority` contract。
  - `ReviewQueueRepository` protocol。
  - `SocAnalysisService.analyze/replay()` 基于 `AlertSummary` 自动生成 open review item。
  - `SocReviewService.correct()` 自动关闭该 run 的 open review item。
  - `SocReviewService.list_queue()` 和 `close_queue_item()` 作为 CLI/API/TUI/daemon 统一服务入口。
- 新增 PostgreSQL 业务表：
  - `soc_review_queue`
  - migration：`backend/soc_agent/db/migrations/versions/0004_review_queue.py`
  - 仍走 SOC 独立 migrations 和 `soc_alembic_version`，不修改 DeerFlow harness persistence。
- 新增 headless CLI：
  - `soc review list --database-url ...`
  - `soc review list --status closed --database-url ...`
  - `soc review close REV-... --reason ... --database-url ...`
- 设计边界：
  - queue item 是人工复核待办读模型，不替代完整 `AnalysisRun`。
  - close queue 只表示待办处理完成；修改 verdict 必须走 `soc correct` / `CorrectionCommand`。
  - 自动入队 reason 目前为 `summary.needs_review`、`low_confidence`、`uncertain_verdict`、`high_severity`。
- 已同步工程契约：
  - `.notes/reference-index/soc-agent-engineering-contracts.md`
- 已补充测试：
  - service：分析入队、correction 关队列、显式 list/close。
  - repository：SQLAlchemy 保存/查询/关闭 review queue。
  - CLI：`soc review list/close` 完整路径。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
- 下一步：
  - 基于 ReviewQueue 做 Phase 1 的 analyst triage surface：先补 API/TUI 可复用的 `review queue item -> investigation context` 查询服务，再进入实体/相似告警/规则记忆的相关性 slice。

### 2026-06-29 — Investigation context service

- 新增分析师复核详情上下文：
  - `InvestigationContext`
  - 包含 `queue_item`、完整 `AnalysisRun`、可选 `AlertSummary`、可选 `DecisionAuditRecord[]`。
- 新增统一 service 入口：
  - `SocReviewService.get_investigation_context(queue_id)`
  - API/TUI/Web/CLI 后续打开复核详情时都应调用这个入口，不自己拼 queue/run/summary/audit。
- 新增 headless CLI：
  - `soc review context REV-... --database-url ...`
- 设计边界：
  - context 是只读研判上下文，不产生新 verdict，不关闭队列，不写 memory。
  - 后续相似告警、confirmed facts、lessons、threat intel 都作为这个 context 的增量字段接入。
- 已同步工程契约：
  - `.notes/reference-index/soc-agent-engineering-contracts.md`
- 已补充测试：
  - service：context 返回 queue/run/summary/audit。
  - service：未知 queue id 返回 not-found。
  - CLI：`soc review context` 输出可复用详情 JSON。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
- 下一步：
  - 给 `InvestigationContext` 增加第一版 `similar_alerts`：基于 `detection_key`、`rule_code`、`entity_keys` 查询历史 `AlertSummary`，先服务人工研判，再为 Phase 2 去重/关联打基础。

### 2026-06-29 — Similar alert retrieval contract

- 新增相似告警 contract：
  - `SimilarAlertQuery`
  - `SimilarAlertMatch`
- 扩展 `InvestigationContext`：
  - 新增 `similar_alerts: list[SimilarAlertMatch]`
- 扩展 repository protocol：
  - `AlertSummaryRepository.find_similar_alert_summaries(query)`
- 第一版仓储实现：
  - SQL 读取最近候选窗口，排除当前 `run_id`。
  - Python 规则打分：`detection_key`、`rule_code`、`source_type`、`category`、`entity_keys` 交集。
  - 输出结构化 `matched_reasons`，便于分析师理解和后续 LLM rerank。
- 设计边界：
  - 当前不让 LLM 直接全库检索；LLM 后续只对 repository 返回的候选集合做排序/解释。
  - PostgreSQL 正式优化时，在同一 repository 协议下替换为 JSONB/GIN 实体交集查询，上层 service/CLI/API/TUI 不变。
- 已同步工程契约：
  - `.notes/reference-index/soc-agent-engineering-contracts.md`
- 已补充测试：
  - service：`InvestigationContext` 包含相似告警。
  - repository：SQLAlchemy 直接返回 scored matches。
  - CLI：`soc review context` 输出稳定包含 `similar_alerts` 字段。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
- 下一步：
  - 设计 LLM-ready entity extraction contract：保留确定性 extractor 做 baseline，让 LLM 只补充 `EntityMention`、角色、置信度和来源，再经 schema/domain validate 后写入 `AnalysisRun` 与 `AlertSummary.entity_keys`。

### 2026-06-29 — LLM-ready entity extraction contract

- 新增实体提取 contract：
  - `EntityKind`
  - `EntityExtractionSource`
  - `EntityMention`
- 扩展 `ExtractedEntities`：
  - 保留旧的 `ips/domains/urls/processes/users/hosts/rule_codes/rule_names/rules` 兼容字段。
  - 新增 `mentions` 作为后续确定性 extractor 和 LLM enrichment 的统一主线。
- 重构确定性 extractor：
  - 为 IP、domain、URL、process、user、host、asset、file hash、rule_code、rule_name、detection_key、MITRE tactic/technique 生成结构化 mention。
  - 每个 mention 包含 `kind/value/key/role/source/evidence_path/confidence`。
  - 旧列表字段由 mentions 派生，保持 analyzer 和现有测试兼容。
- 调整 summary 派生：
  - `AlertSummary.entity_keys` 优先使用 `AnalysisRun.entities.mentions[].key`。
  - 旧 run 没有 mentions 时才 fallback 到旧列表字段。
- 设计边界：
  - 当前不接真实 LLM。
  - 后续 LLM entity extraction 只能补充 `EntityMention`，不能直接写 summary、review queue、memory 或 verdict。
  - LLM 输出必须经过 schema/domain validate 和去重后，才允许进入 `AnalysisRun.entities.mentions`。
- 已同步工程契约：
  - `.notes/reference-index/soc-agent-engineering-contracts.md`
- 已补充测试：
  - PingAn APT：验证 source/destination IP、domain、rule_code、MITRE technique mentions。
  - PingAn EDR：验证 process、parent process、user、host、file hash mentions。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
- 下一步：
  - 增加 `LLMEntityExtractor` protocol 和 fixed runtime enrichment step，占位实现先返回空补充；之后再接真实模型的结构化输出和 domain validator。

### 2026-06-29 — UM account user identity support

- 新增 canonical user 字段：
  - `UserEntityRef.um_account`
- 扩展 normalizer：
  - 通用 flat payload 支持 `um_account`、`umAccount`、`um`、`um_id`、`umId` alias。
  - PingAn adapter 只从明确 UM 字段映射 `um_account`。
  - `uiduserid` / SID 类字段继续作为 `user_id`，不冒充 UM。
- 扩展 extractor：
  - `um_account` 生成 `EntityMention(kind=user, role=um_account, key=user:<value>)`。
  - `user_id` 也生成 user mention，但 role 保持 `user_id`。
- 设计边界：
  - UM 账号是 user identity 的一种角色，不新增独立 `EntityKind.UM_ACCOUNT`。
  - 处置人/审批人/分析师账号默认不进入核心 user 实体，避免污染攻击主体关联。
- 已同步工程契约：
  - `.notes/reference-index/soc-agent-engineering-contracts.md`
- 已补充测试：
  - 通用 flat payload 的 `umAccount` 可规范化并提取为 `role=um_account`。
  - PingAn EDR sample 的 SID 保持为 `role=user_id`。
  - HTTP `x-forwarded-for` nested header alias 可归一为 `entities.http.x_forwarded_for` 并提取为 `role=x_forwarded_for`。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`

### 2026-06-29 — Normalizer alias boundary hardening

- 修正字段别名边界：
  - `pipeline/extractor.py` 只读取 canonical `AlertInput`。
  - `normalizers/alert.py` 负责把 root 或 nested 原始别名归一化到 canonical 字段。
- 增强 HTTP alias：
  - `x_forwarded_for`
  - `xForwardedFor`
  - `x-forwarded-for`
  - `X-Forwarded-For`
  - `xff`
  - `XFF`
- 设计边界：
  - 不让 extractor 记住所有厂商字段名或 header 原名。
  - 后续新增别名优先加 normalizer 测试，不直接往 pipeline 硬塞字段判断。
- 已同步工程契约：
  - `.notes/reference-index/soc-agent-engineering-contracts.md`
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`

### 2026-06-29 — Normalization drift strategy and runtime reports

- 新增策略文档：
  - `.notes/ai_soc/normalization-drift-strategy.md`
  - 明确 LLM 不默认参与每条告警 normalize/entity extraction。
  - LLM 定位为新供应商接入、字段漂移分析、mapping 建议、低频复核样本 enrichment 的辅助能力。
- 新增 runtime report contracts：
  - `NormalizationReport`
  - `ExtractionReport`
- 扩展 `AnalysisRun`：
  - `normalization_report`
  - `extraction_report`
- Runtime 行为：
  - normalize 后生成 normalization report，记录 adapter、source、missing fields、normalized fields、warnings。
  - entity_extract 后生成 extraction report，记录 mention count、entity counts、missing entity kinds、warnings。
  - report 只做观测和漂移检测，不参与 verdict 决策。
- 已同步工程契约：
  - `.notes/reference-index/soc-agent-engineering-contracts.md`
- 已补充测试：
  - 正常样本包含 normalization/extraction report。
  - 缺字段样本能暴露 missing normalized field 和 missing entity kind。
  - `x-forwarded-for` alias 能进入 normalized fields。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
- 下一步：
  - 补 `soc normalize inspect` CLI：对单个样本只跑 normalize + report + entity extract，方便接入新厂商和排查字段漂移。

### 2026-06-29 — Normalize inspect CLI

- 新增 inspect-only 输出 contract：
  - `NormalizationInspectionResult`
- 新增 core service：
  - `SocNormalizationService.inspect(payload)`
  - CLI/API/TUI 后续都应通过该 service 打开样本归一化检查，不能直接 import runtime/normalizer。
- 新增 headless CLI：
  - `soc normalize inspect sample.json`
  - `soc normalize inspect --json '{...}' --pretty`
- 输出内容：
  - canonical `AlertInput`
  - `ExtractedEntities`
  - `NormalizationReport`
  - `ExtractionReport`
- 设计边界：
  - 不跑 `analyze_stub`、decision、review queue 或 persistence。
  - 用于新厂商样本接入、字段漂移排查、normalizer 回归测试。
- 已同步工程契约：
  - `.notes/reference-index/soc-agent-engineering-contracts.md`
- 已补充测试：
  - CLI 输出 PingAn EDR normalized alert、entities、reports。
  - 架构测试确认 CLI 仍通过 core service 进入业务逻辑。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
- 下一步：
  - 抽一个最小 mapping config spike：先不接 LLM，定义 mapping 文件格式和 `soc normalize inspect --mapping ...` 的接口草案。

### 2026-06-29 — Normalize mapping config MVP

- 新增 YAML mapping 归一化器：
  - `backend/soc_agent/normalizers/mapping.py`
  - 只支持显式字段搬运：`canonical.target.path: $.source.path`
  - 不做 LLM 猜测、不运行时修改 mapping。
- 扩展 inspect service：
  - `SocNormalizationService.inspect(..., mapping_path=...)`
  - `SocNormalizationService.inspect(..., mapping_config=...)`
  - CLI/API/TUI 后续继续通过 core service 入口复用。
- 扩展 CLI：
  - `soc normalize inspect sample.json --mapping vendor.yaml`
- 新增样本：
  - `backend/samples/alerts/mapped_waf.json`
  - `backend/samples/mappings/sample_waf.yaml`
- report 行为：
  - mapping adapter 输出为 `mapping:<name>`。
  - 缺失 source path 进入 `NormalizationReport.warnings` 和 `unmapped_fields`。
- 已同步文档：
  - `.notes/ai_soc/normalization-drift-strategy.md`
  - `.notes/reference-index/soc-agent-engineering-contracts.md`
- 已补充测试：
  - service 通过 mapping 文件 inspect 简单 WAF payload。
  - CLI 通过 `--mapping` 输出 canonical alert、entities、reports。
  - 架构测试继续确认 public service export。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
- 下一步：
  - 做 drift aggregation 的最小数据结构和查询入口，先基于 `NormalizationReport`/`ExtractionReport` 聚合，不接 LLM。

### 2026-06-29 — Normalize drift aggregation MVP

- 新增 drift report contracts：
  - `NormalizationDriftSample`
  - `NormalizationDriftReport`
- 扩展 normalization service：
  - `SocNormalizationService.drift(samples, mapping_path=...)`
  - 聚合逻辑复用 `SocNormalizationService.inspect()`，不重复实现 normalize/extract。
- 新增 CLI：
  - `soc normalize drift PATH`
  - `soc normalize drift PATH --mapping vendor.yaml --pretty`
  - `PATH` 可以是单个 JSON 文件或目录；目录默认匹配 `*.json`。
- 输出内容：
  - sample/success/failure counts
  - adapter/source type 分布
  - missing normalized fields / unmapped fields 分布
  - entity kind / missing entity kind 分布
  - warning 分布
  - suspicious samples 和全量 sample summaries
- 设计边界：
  - 不接 DB、不接 LLM、不写 review queue/memory/verdict。
  - CLI 只负责读取样本和输出 JSON；聚合规则在 core service。
  - suspicious 只由 normalize 失败、missing canonical field、unmapped mapping field 触发；抽取 warning 只作为趋势信号，避免 WAF/账号类告警因没有 process 被误报。
- 已同步文档：
  - `.notes/ai_soc/normalization-drift-strategy.md`
  - `.notes/reference-index/soc-agent-engineering-contracts.md`
- 已补充测试：
  - service 聚合 generic 样本 report。
  - CLI 聚合 mapping WAF 样本 report。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m soc_agent.cli normalize drift samples/alerts/mapped_waf.json --mapping samples/mappings/sample_waf.yaml --pretty`
- 下一步：
  - 把 drift aggregation 接到 persisted runs/recent runs 查询；仍先不接 LLM。

### 2026-07-01 — Persisted run drift aggregation

- 扩展 repository 协议：
  - `AlertRepository.list_runs(limit=50)`
  - SQLAlchemy implementation 按 `updated_at desc` 返回最近 `AnalysisRun`。
- 扩展 drift sample：
  - `NormalizationDriftSample.run_id`
  - 本地样本为空；持久化 run 模式填入 run id，方便后续 TUI/API 跳转详情。
- 扩展 normalization service：
  - `SocNormalizationService(repository=...).drift_recent(limit=...)`
  - 只读取已持久化 run 上的 `normalization_report` / `extraction_report`，不重跑 normalize，不接 LLM。
- 扩展 CLI：
  - `soc normalize drift --recent-runs --limit N --database-url ...`
  - `--recent-runs` 与 PATH / `--mapping` 互斥。
- 设计边界：
  - 本地样本聚合用于 vendor onboarding。
  - persisted run 聚合用于线上/测试库最近告警的格式漂移观察。
  - CLI 仍只做参数、repository 注入和 JSON 输出；聚合规则在 core service。
- 已同步文档：
  - `.notes/ai_soc/normalization-drift-strategy.md`
  - `.notes/reference-index/soc-agent-engineering-contracts.md`
- 已补充测试：
  - service 基于 in-memory repository 聚合最近 runs。
  - SQLAlchemy repository 支持 `list_runs(limit=...)`。
  - CLI 从 persisted runs 输出 drift report。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
- 下一步：
  - 进入 `soc normalize suggest` 的离线建议设计：只读 drift/sample report，输出候选 mapping patch，不自动应用。
