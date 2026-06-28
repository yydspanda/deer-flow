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
| 当前目标 | 建立 SOC Agent 最小可靠闭环：contracts schema、Runtime 状态机、step trace、validator、headless CLI analyze、run 输入快照、replay contract、PostgreSQL 持久化接口 |
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
| 8 | PostgreSQL run repository | Partial | 已有 SOC ORM row + SQLAlchemy repository + headless CLI wiring；正式 Alembic migration / config wiring 待补 |

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
  - `soc analyze ALERT.json --persist`
  - `soc show RUN_ID`
  - `soc replay RUN_ID`
  - 数据库 URL 通过 `--database-url` 或 `SOC_DATABASE_URL` 传入；PostgreSQL URL 会归一化为 sync `postgresql+psycopg://`。
- 说明：
  - 测试使用 SQLite in-memory / temp file 只是 SQLAlchemy unit harness；SOC runtime 策略仍是 PostgreSQL。
  - 下一步需要补正式 Alembic migration / config wiring，避免长期依赖 `soc db init` 的 `create_all`。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format --check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_runtime.py tests/test_soc_agent_service.py tests/test_soc_agent_repository.py tests/architecture/test_soc_agent_boundaries.py`
