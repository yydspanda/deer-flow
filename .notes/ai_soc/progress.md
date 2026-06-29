# SOC Agent 开发进度

> 本文件是 SOC Agent 长期开发的进度台账。聊天记录不作为进度来源；每完成一个可验证切片，都在这里追加记录。

## 工作方式

每次开始 SOC Agent 开发任务时按以下顺序执行：

1. 先读 `.notes/ai_soc/soc-agent-solution.md` 和相关 `.notes/reference-index/*.md`。
2. 明确当前任务属于哪个 Phase、解决哪个用户/工程问题。
3. 再用 CodeGraph / Understand Anything 查 DeerFlow 代码落点和参考实现。
4. 优先新增 SOC 独立模块、adapter、schema、CLI/API 入口，不侵入 DeerFlow 上游核心。
5. 完成后记录改动、验证命令、遗留风险和下一步。

## 当前状态

| 项 | 状态 |
|---|---|
| 当前阶段 | Phase 1：CLI + Runtime 可靠性闭环 |
| 当前目标 | 建立 SOC Agent 最小可靠闭环：contracts schema、Runtime 状态机、step trace、validator、headless CLI analyze、run 输入快照、replay contract、PostgreSQL repository、Alembic migration、alert summary 读模型 |
| 上游策略 | DeerFlow fork 内增量开发，默认不修改上游核心代码 |
| 数据库策略 | PostgreSQL 是业务存储；Phase 1 可先定义 schema/接口，落库实现按最小闭环推进 |
| LLM 策略 | Runtime 固定控制流；LLM 只作为固定节点或 stub，不掌握主流程 |

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

## 进度记录

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
