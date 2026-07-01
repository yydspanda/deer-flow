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
| 13 | CLI summary list | Done | `soc list` 输出持久化 `AlertSummary`，用于验证 Web/TUI 列表字段 |

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
