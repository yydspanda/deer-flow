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
| 当前交付阶段 | `PI` Stage 4 - Real Data & Production Integration（Alpha Gate 已通过，`PI-04-A` 已完成） |
| 当前目标 | `PI-01A` 平安 ZEUS 真实威胁情报 Provider；`D12-B` 保留为 `Parked / internal evidence pending`，不降低其真实资产 Provider 门槛 |
| 上游策略 | DeerFlow fork 内增量开发，默认不修改上游核心代码 |
| 数据库策略 | 生产/准生产目标仍为 PostgreSQL；当前 PingAn 内网 DEV 统一使用独立本地 SOC SQLite，不收集 PostgreSQL 参数 |
| LLM 策略 | Runtime 固定控制流；LLM 只作为固定节点或 stub，不掌握主流程；新 live 输出使用 `AnalysisResult.v2` |
| 当前下一刀 | 在内网用 approved IP 对 `/public/indicatorSearch` 跑 `PI-01A` hit/not-found/error/timeout smoke，核对实际 `ipAnalyseReport` / `ipReputationReport` 字段覆盖，并通过既有 MCP Action/Dispatcher 保存、回读 `mocked=false` InvestigationEvidence；随后进入 `PI-01B1`。 |
| 唯一路线 | `delivery-roadmap.md`：`BD -> AA -> BG -> PI`；未通过当前 Stage Gate 不切换阶段 |

## 阶段交付主线

> 这张表只反映权威阶段顺序。每个阶段的详细 task 和 Gate 以 `delivery-roadmap.md` 为准；下面的能力长表仅用于追踪历史能力，不决定当前下一刀。

| 阶段 | 交付物 | 状态 | 当前边界 | 退出条件 |
|---|---|---|---|---|
| `BD` | Boss Demo v0.1 | **Done / BD Gate Passed** | 已交付浏览器优先 golden path、可重置数据和演示验收 | `BD-01..03` 和 BD Gate 已全部通过 |
| `AA` | SOC Alpha Completeness Audit | **Done / AA Gate Passed** | 50 项唯一矩阵、13 个 Gap 和 7 个冻结工作包已确认 | AA Gate 已于 2026-07-18 通过 |
| `BG` | Close Blocking Gaps | **Done / Alpha Gate Passed** | P0/P1、readiness technical gate、独立评审与具名范围批准已完成 | 2026-07-20 批准进入 Stage 4 integration preparation |
| `PI` | Real Data & Production Integration | **Current / PI-01A Threat Intelligence** | D12-B 验收代码保留但内网证据暂存；当前 TI Provider/MCP 代码已完成外网回归，等待真实 DEV `mocked=false` evidence；共享部署/试点/生产仍未批准 | Pilot readiness review 通过 |

## 2026-08-04 — PI-01A PingAn threat-intelligence provider implemented

- 产品负责人明确暂存 `D12-B`。它保持 `Parked / internal evidence pending`，已有 preflight、seven-case matrix、MCP persistence/readback 和 UI smoke 门槛均不删除、不降级，也不因 PI-01A 推进而标记完成。
- 新增 PingAn-owned `/public/indicatorSearch` Provider、typed result 和 stdio MCP；通用层仍只认识 `threat_intel.ip_reputation.lookup`，没有向固定 Runtime 加入 PingAn 分支或外部 IO。
- Provider 复用 portable `isec_sign` 与共享 ZEUS App ID/App Key，internal 模式强制 HTTPS + host allowlist、缺配置 fail closed；fake/internal 模式互斥，fake 结果始终 `mocked=true`。
- 只投影审阅过的 `judgments`、`tags_classes`、`threatbook_lab`、scene、carrier、location 和 update time。每个 label 保留精确 source path，完整响应只保留 SHA-256；未审阅字段名进入 bounded mapping warning，其值不出 Provider。
- 旧实现的风险分公式、地理乘数、白名单/封禁规则没有迁移。`score`、`confidence` 和 `last_seen` 不把 provider update time 或未知分值伪装成稳定事实；freshness 使用显式 tenant 配置，未知时间按 stale-like evidence 处理。
- MCP-backed Action 结果可经 Dispatcher 写 `InvestigationEvidence`。通用 Domain Evidence helper 现在同时消费 direct result 和 `mcp_result` envelope，修复“证据已保存但 domain handler 看不到 typed result”的衔接缺口。
- 外网回归覆盖 hit、not-found、partial branch、unknown freshness、transport failure、MCP、Action persistence、Domain Triage 和 CLI fake smoke；真实 `mocked=false` ZEUS 调用未在外网执行。当前状态为 `In Progress / code-complete, internal smoke pending`，不能冒充 PI-01A Done。
- 全量 SOC + architecture 回归 `688 passed`；随后针对 invalid JSON、未来时间、scalar path、response/label 上限的最后边界加固继续通过聚焦回归。Ruff、format、JSON example、`git diff --check` 均通过。
- `codegraph sync .` 已纳入最终实现；索引可直接定位 `PingAnThreatIntelService`、`build_pingan_threat_intel_service_from_env` 和通用 `evidence_result_payload`。

## 2026-08-04 — D12-B MCP evidence persistence/readback acceptance implemented

- 新增独立 PingAn 验收模块 `d12b_evidence_acceptance.py` 与 CLI `soc_pingan_d12b_evidence.py`；它从同一 mode-`0600` private matrix 选择一个 `expected_outcome=found` case，并要求已有 open ReviewQueue 工单。
- 执行路径固定为 `SocAgentCapabilityRouter -> SocAgentActionDispatcher -> MCP action adapter -> InvestigationEvidenceRepository`，没有直接调用 PingAn Provider，也没有向通用 Runtime 添加 tenant 分支。
- 通过门槛同时检查 `provider_mode=internal`、`mocked=false`、`evidence_boundary=investigation_only`、`decision_impact=none`、`raw_response_included=false`、request/trace provenance、证据持久化、共享 Review Context/Lead Agent artifact 可见，以及 AnalysisRun/ReviewQueue 哈希前后不变。
- bounded report 只保存 matrix/case/query hash、queue/run/alert/evidence ID、门槛代码和错误类型；不保存 raw query、UM 或 Provider body。它明确标记 `web_or_tui_render_executed=false`，因此不冒充 deployed browser/TUI smoke。
- 隔离回归覆盖 real-shaped success、missing case、非成功 case、mock result 和 Provider failure；MCP/Dispatcher/Context/architecture 相关组合测试 `159 passed`，全部 PingAn 测试 `89 passed`。其中 mock 结果保留 append-only evidence 但 D12-B gate 失败，Provider failure 不持久化成功证据。
- `codegraph sync .` 已纳入 evidence acceptance 模块与 CLI 共 41 个新节点；`run_pingan_d12b_evidence_acceptance` 可直接查询，索引状态为 up to date。
- 外网仍未发起平安内部请求，也没有真实 `mocked=false` 报告。D12-B 继续保持 Current；内网先跑 direct/MCP matrix，再运行该 evidence acceptance，最后补 deployed Web/TUI render 与性能/安全 checklist。

## 2026-08-04 — D12-B direct-provider case matrix implemented

- 新增 `soc.pingan_asset_case_matrix.v1` 私有输入、无值 `plan.v1` 和 bounded `report.v1`；七类必测语义固定为 ZEUS direct hit、asset-to-BU fallback、UM fallback、definite miss、ambiguous、authentication failure 和 timeout。
- 新增 `soc_pingan_d12b_matrix.py`：`--plan-only` 只检查 coverage 且明确 `external_requests_issued=false`；真实执行必须显式 `--confirm-live`、使用 mode-`0600` 的 `*.local.yaml|yml|json` 并指定 report path。
- fallback 验收不只比较最终 outcome，还按顺序比较 expected attempt，并拒绝 forbidden stage；因此能证明 direct hit 未进入 workflow、只有明确 `not_found` 才降级、鉴权/超时没有伪装成查无。
- 负向 case 只允许通过白名单环境变量引用覆盖 ZEUS DEV URL/allowlist/App ID/App Key/timeout；aggregate report 只保留 query hash、阶段/状态、latency 和 error class，不保存 raw query、UM、Provider body 或 override value。
- `write_validation_report()` 改为同目录临时文件 + `fsync` + atomic replace，并强制最终文件 `0600`。验证结果：全部 PingAn 回归 `83 passed`，Provider/Action/architecture 组合 `59 passed`，既有 MCP adapter 隔离回归 `32 passed`；Ruff、format、`git diff --check` 和 example `--plan-only` 通过。一次把两个 stdio MCP 套件串在同一 pytest 进程时暴露既有 closed-capture 顺序污染，隔离重跑全部通过，本切片未修改该上游路径。
- 外网没有执行任何内部请求，也没有产生 `mocked=false` 证据；`codegraph sync .` 已纳入 2 个新增 Python 文件、41 个节点，并可查询 `run_pingan_asset_case_matrix`。
- D12-B 仍是 Current：下一步在内网补齐 private cases 和 Agent Platform import，跑 confirmed direct matrix；随后继续 MCP、`InvestigationEvidence` 持久化及 Web/TUI/Lead Agent 回读，不能因 direct matrix 通过就提前关闭 gate。

## 2026-08-04 — Integration/deferred unfinished-work crosswalk completed

- 新增 `integrations/README.md` 作为非权威状态索引，把 integration 目录中的 Done、Current、Queued、Data-gated、Deferred 和明确非待办逐项映射到 `delivery-roadmap.md`；不建立第二套执行顺序。
- 补出第一次盘点遗漏的独立 gate：`PI-01B1` 是真实 `security_tag.lookup`，`PI-01B2` 是 change/scanner/maintenance/exercise-roster 等权威授权事实来源同步；B1 不能冒充 B2，B2 不可获得时 automation 保持关闭。
- 明确 `asset.lookup` 与 `asset.locate` 不是同义 route：前者是简单资产记录，后者是业务/处置归属；`PI-01D` 必须为前者配置真实 adapter/映射或从 tenant allowlist 禁用，PI-01E 不得继续使用默认 mock。
- 将唯一未落正式工作包的 External Disposition `SkillImprovementCandidate` 固定为 `PI-03C`，补 contract/source refs/人工状态机/replay 边界；路径目录的可选治理升级进入 `PI-03D`，默认继续永久 investigation-only。
- PI-03 同步拆为人工标签基础、Runtime/model/correlation 评测、Skill 候选、tenant knowledge promotion 和 adaptive parser governance；所有项都要求人工来源与离线 replay，不自动激活。
- 修正旧文档差异：PI-04-B 改为等待 `PI-01E` 真实 telemetry；内网收集顺序补 PI-01D/E；handoff 章节编号和 PI-01B/PI-03 checklist 已统一。
- 当前执行指针没有变化：仍只执行 `PI-01/D12-B`，上述 queued/deferred 项不得插队。

## 2026-08-04 — PI-01 real integration and deferred plan reconciled

- 审阅 `.notes/ai_soc/integrations/` 与 `.notes/archive/ai_soc/deferred/`，确认它们不是同一类 backlog：integration 文档包含当前执行 runbook、已完成审计和真实接入缺口；deferred 只有满足显式触发条件后才能重新排期。
- 发现并固定关键 reachability gap：`SocMainOrchestratorService` 当前只执行调用方传入的 `action_specs`，Kafka daemon 和内网 PKL batch 只跑固定 Runtime；真实 Provider 接通后不会自动进入连续告警调查。
- 在 `delivery-roadmap.md` 增加唯一顺序：`D12-B -> PI-01A TI -> PI-01B security/governed facts -> PI-01C external disposition -> PI-01D governed read-only orchestration -> PI-01E internal shadow`。
- `PI-01D` 固定为 application-level deterministic planner，复用现有 dispatcher/registry/evidence；不把外部 IO 放入 Runtime，不让 LLM 自由路由 Kafka 工具，不覆写基础 verdict、关单、写 confirmed memory 或执行高风险动作。
- Deferred 激活顺序和第一实现切片已收口：PI-03 先做可审计 label/correlation/skill candidate；adaptive parser 先做 drift cohort/candidate bundle；PI-04-B 先做 snapshot 薄 Web；Kafka concurrency 必须由真实吞吐证明；Wiki/OKF 只做 DB 单向投影。
- 本切片仅做规划与文档一致性修复，没有修改业务代码，也没有改变当前执行指针；下一刀仍是 `PI-01/D12-B` 内网真实资产 Provider smoke。

## 2026-08-04 — Internal PKL batch and split transfer bundle prepared

- 新增共享 `soc_agent.application.build_soc_analysis_service` composition root；CLI 与内网批跑不再各自组装一套 Runtime。
- 新增受限 DataFrame PKL loader 驱动的 resumable batch：按 source/payload/model/evidence/persistence 指纹续跑，live 必须 `--confirm-live`，默认单 worker、非持久化，逐条完整结果和紧凑 JSONL 均写 Git-ignored `0700/0600` 目录。
- 当前 210 行源 PKL 的 plan-only 验证得到 210 个有效输入、0 个 wrapper error；2 行 stub smoke 和 resume 已验证，不调用模型/MCP、不允许 automation。内网按 `5 -> 50 -> all` 跑 5000+ 数据，技术完成不冒充 PI-03 准确率验收。
- 新增 `build_pingan_internal_transfer.py`：把当前含未提交改动的源码与凭证/PKL/XLSX/compiled SQLite 分成两个 archive；各自携带独立 manifest，支持 SHA-256/member/path 安全检查，私有包及内容强制 `0600`。
- XLSX 已作为私有 overlay 输入并同时提供已编译目录，内网可直接查询或从源 workbook 重建；它仍是 investigation-only candidate knowledge，不是 allowlist。
- 修正 live `--plan-only` 误要求执行确认的问题；实际计划显示当前 PKL 210 行、选中 5 行即精确预估 5 次模型调用。批跑 resume 保留原 batch ID/start time 并记录 `resumed_at`。最终 SOC/architecture 回归 `663 passed`，transfer/batch 回归 `12 passed`，changed-file Ruff 与 `git diff --check` 通过。

## 2026-08-04 — PingAn historical EDR software-path catalog implemented

- 将旧 `Deepseek_Qwen_32B_EDR_Analysis_Ignored_Paths_Sup (1).xlsx` 从“后续候选”落实为 PingAn integration 内的离线编译目录；没有把 Excel 或租户规则放进 generic Runtime。
- 新增 `software_path_catalog.py`：编译 source SHA、行级 lineage、历史 disposition、出现次数/时间、规则码、进程名和可关联 MD5 到 Git-ignored SQLite。首次真实构建覆盖 3,654 行，得到 1,329 个路径条目和 7,656 个去重 observation；60 行原始日志 JSON 异常被显式计数，未阻止路径目录构建。
- 查询只做 Windows 路径大小写/分隔符规范化后的 exact match，并可选校验 MD5；拒绝迁移旧实现的 basename、版本通配、前缀和删除目录段模糊匹配。
- 历史处置与位置治理分离：命中历史“忽略”只表示 candidate context；`D:`、用户可写和临时目录仍为 high attention，C 盘系统路径仍需防范 LOLBin。
- 新增 `endpoint.software_path.lookup` read-only action、stdio MCP 和 PingAn DEV 组合 extensions profile。结果经 Action Dispatcher 写 `InvestigationEvidence`，固定 `decision_impact=none`、`automation_eligible=false`，不能跳过 Runtime、改 verdict、关单、授权动作或写 confirmed memory。
- 生成的 catalog/build report 与私有 XLSX 均被 Git 忽略；compiler 原子替换目录并设置文件权限 `0600`。
- 聚焦单元测试覆盖真实编译语义、D/C 盘分类、hash mismatch/staleness、no-fuzzy、MCP 输出和 InvestigationEvidence 持久化。真实 catalog 重建后，`soc mcp tools` 发现 `pingan_software_path_software_path_lookup`，`soc mcp smoke` 经 MCP-backed Action Adapter 成功返回 `D:\\ps\\psexec.exe` 的 high-attention candidate context；扩展 action/service/Lead Agent/architecture 回归 `139 passed`，changed-file Ruff 和 `git diff --check` 通过；`codegraph sync .` 新增 2 个文件、41 个节点。
- 当前交付指针不变：继续 `PI-01/D12-B` 内网真实 `asset.locate` smoke；路径目录不是 D12-B 外部 Provider 完成证据。

## 2026-08-03 — D12-B portable DEV profile, signer, preflight and direct smoke prepared

- 已审阅 `validation/original_works/raw_program/sec-model`：确认 LOCAL profile 的 OpenAI-compatible loopback endpoint 和 `DeepSeek_V4_Flash` provider alias；DeerFlow 仍使用稳定 profile 名 `deepseek-v4-flash`。
- 新增 `backend/samples/pingan_dev/`，并在 Git-ignored `config.pingan-dev.local` / `.env.soc-dev.local` 中准备实际 DEV 配置。真实值可直接留在本地配置，不进入 commit。
- 旧 `util.util_tools:isec_sign` 因 import-time 依赖旧 `service`/pandas/OpenAI 等模块不可移植；新增 `integrations/pingan/zeus_signing.py`，保持签名 material/header wire contract、移除默认 App Key，并以固定 timestamp/nonce 测试锁定兼容性。
- 新增 D12-B no-network preflight 与 direct-provider smoke 脚本；报告不输出 secret/raw query，区分 hit/not-found/ambiguous/auth/timeout/unavailable/invalid response，并在每个 sanitized provider attempt 记录耗时。外网 preflight 当前只被内部 Agent Platform `run_workflow` 依赖阻塞，这是预期 data gate。
- 修复资产 fallback：外部调用失败不再被当成正常查无继续降级；只有明确 `not_found` 才进入下一层，失败携带 sanitized attempts 并 fail closed。
- 审阅 ZEUS 0..10 状态流转：旧“status != 待审阅就跳过 AI”不迁移；后续由 PingAn source adapter 转 `SocExternalDispositionIngressCommand`，generic Runtime 不识别 ZEUS status。
- 审阅 3,654 行 EDR safe-path XLSX：它是历史模型输出候选，不是权威白名单；后续只做版本化 PingAn tenant knowledge/InvestigationEvidence，不允许 match 即忽略。详见 `integrations/pingan-legacy-source-audit.md`。
- 验证：changed-file Ruff 通过；PingAn provider/preflight/signer + SOC architecture 聚焦回归 `35 passed`；真实 local model profile 正确解析为 `PatchedChatDeepSeek` + loopback `DeepSeek_V4_Flash` + SQLite；外网 preflight 只报告内部 workflow runner 缺失，direct smoke 返回 `preflight_failed` 且 `external_attempt_count=0`，证明未越过预检发请求；两个 local config 权限设为 `0600`；`codegraph sync .` 纳入 2 个新增代码文件、41 个节点。
- 下一步：复制/重建 ignored local config 到内网，提供 Agent Platform import root，启动旧模型 gateway，通过 preflight 后执行 D12-B direct + MCP case matrix。

## 2026-08-03 — PI-01 D12-B resumed; unconfirmed context lookup mocks removed

- 产品负责人确认平安当前不具备独立 EDR 进程树查询或 HIDS 主机上下文查询能力，逐告警补查耗时也不可接受；相应 mock action、contract、默认 registry、Lead Agent proposal、policy、domain/scenario route、fixture 和测试已删除。
- 进程树、命令行、登录账号和主机事件继续由 PingAn normalizer 从告警原生 message/structured fallback 提取，经 bounded native evidence 进入 Runtime；不存在外部工具不再被记作能力缺口或决策降级原因。
- D12-B 执行指针恢复。新增 `integrations/pingan-dev-information-collection.md`，区分已从旧代码确认的信息、需要脱敏带出的接口契约和只能留在内网的 secret/test values。
- 当前 DEV 数据库固定为 `backend/.deer-flow/data/soc_agent_dev.db`；`resolve_database_url()` 在没有显式参数和 `SOC_DATABASE_URL` 时，会根据 DeerFlow `database.backend: sqlite` 自动选择该独立文件，`soc db upgrade` 会创建缺失的父目录。显式 URL 仍优先，DeerFlow PostgreSQL 配置仍用于准生产/生产。Kafka、K8s、PostgreSQL DEV 和真实高风险动作不进入本轮收集或实现。
- 验证：changed-file Ruff 通过；聚焦 action/Lead Agent/domain/eval 回归 `146 passed`；SQLite resolution/migration/CLI 聚焦回归 `51 passed`；最终完整 `tests/test_soc_*.py` 加 SOC architecture gate `643 passed`；修改后的三份 eval JSON 可解析，`git diff --check` 通过。
- 下一步：取得脱敏 `root_config`/环境选择逻辑后，实现 DEV-only profile/preflight，再复制项目到内网执行 D12-B `mocked=false` smoke。

## 2026-08-02 — PI-04-A operations snapshot completed; D12-B remains parked

- `92d3bfff feat(soc): add gated PingAn asset provider` 已推送到 `origin/yyds-dev`。
- 产品负责人决定 D12-B 暂时空置：它继续保持 `Waiting / data-gated`，不删除、不降级验收条件，也不以
  新 fake provider 替代；未来只有内网 `mocked=false` smoke 才能恢复并关闭 PI-01。
- Stage 4 内部执行指针切换到并完成 `PI-04-A`。选择理由：PI-02 需要真实 Kafka/PostgreSQL/K8s 参数，PI-03
  需要人工标签；Operations Snapshot 可以复用现有真实 DB/Kafka/normalization 信号，先解决运营人员
  无法统一查看任务、积压和组件可用性的产品问题。
- 已新增 `soc.operations_snapshot.v1`、`SocOperationsService`、独立 SQL aggregate repository 和
  secret-free Kafka probe。run status、open ReviewQueue、pending approval、normalization backlog/baseline、
  pending memory candidate 均为无分页上限的精确 aggregate，不从 `list(limit=...)` 估算。
- 公共入口为 `soc ops snapshot` 和 passive `GET /api/soc/operations/snapshot`；只有 CLI 显式
  `--check-broker` 执行 connectivity probe。输出不包含 DB URL、broker address、username、credential 或
  raw diagnostic，也不输出 overall health。
- Kafka consumer lag、模型/算力和 production SLO 继续显式为 `not_measured`；本切片没有 migration，
  没有改变 Runtime/Kafka consumer/Review/Approval/Memory 主流程。
- 验证：changed-file Ruff 通过；最终 operations/transport/architecture 聚焦回归 33 passed；完整
  `tests/test_soc_*.py` + architecture 重跑 641 passed。第一次全量中的 demo stdout capture 偶发失败在
  isolated/组合重跑均通过，最终完整重跑无失败。
- 下一刀建议 `PI-04-B`：建立薄 Web 运营视图，只展示已冻结 snapshot；完整 Prometheus、SLO alerting、
  Kafka lag 与模型算力 telemetry 继续后置。

## 2026-08-02 — PI-01 Checkpoint D12-A implemented; D12-B remains data-gated

- 产品负责人确认外网环境无法访问 PingAn `search_asset_info`、`asset_to_bu` 和 UM workflow；允许先参考
  `validation/original_works/zeus/flows/disposition_tools/asset_locator.py` 实现可移植代码并用 fake
  transport 测试，之后移植内网执行真实验证。
- D12 固定拆成两个不能互相冒充的交付物：
  - `D12-A Provider implementation`：外网完成通用 action contract、PingAn adapter、Zeus
    `isec_sign` 鉴权调用边界、`searchAssetInfo -> asset_to_bu -> UM` 降级编排、fake transport 与测试；
    所有结果必须声明 `mocked=true`，状态只能是 code-complete/tested。
  - `D12-B Internal real smoke`：内网注入真实 endpoint、secret、signer、workflow runner 和 tenant
    mapping，保存成功、查无、鉴权失败、超时及端到端 `InvestigationEvidence` 报告；只有真实调用结果
    明确 `mocked=false` 后，才能作为 `PA-12` / `PI-01 real provider` 完成证据。
- 旧实现只作为接口语义参考：资产提取和角色重建继续由当前 Runtime/Skill 负责；PingAn adapter 只查询
  已提取资产并返回归属候选，不直接决定 verdict、response target、ReviewQueue、memory 或 action。
- D12-A 已实现：
  - PingAn provider 位于 `backend/soc_agent/integrations/pingan/asset_location.py`，MCP server 位于
    `backend/soc_agent/integrations/pingan/asset_mcp_server.py`；通用 Runtime/Core 不 import 平安实现；
  - 保留旧 ZEUS `/public/searchAssetInfo` 请求体、`isec_sign` 边界和
    `searchAssetInfo -> asset_to_bu -> UM` 降级顺序，但资产提取、角色判断和处置决策不进入 provider；
  - `fake` 与 `internal` 模式严格互斥；internal 配置缺失直接失败，MCP `isError=true` 在字段裁剪前
    映射为 failed action，不能产生空对象成功；
  - 多个归属候选返回 `ambiguous=true`，不沿用旧代码“取第一条即定案”的行为；原始 provider
    response 不写入结果，只输出有界归属候选和 attempt provenance；
  - fake smoke 产物位于
    `backend/.deer-flow/soc-runtime-validation/checkpoint-d/step-d12-pingan-asset-provider/d12-a-fake-smoke.json`
    （gitignored），明确记录 `mocked=true`、`provider_mode=fake`、`decision_impact=none`；
  - 运行与内网交接命令记录在 `backend/samples/mcp/pingan_asset/README.md`。
- 验证：D12/MCP 聚焦回归 41 passed；包含 core/Lead Agent/architecture 的扩展回归 153 passed；
  完整 `tests/test_soc_*.py` + architecture 回归 632 passed；changed-file Ruff、JSON config parse 和
  `git diff --check` 通过。D12-A stdio smoke 成功并保存 `mocked=true` 报告；D12-B missing-config
  smoke 以退出码 1、`SocMcpToolProviderError` 和 `external_side_effect=not_executed` fail closed；
  `codegraph sync .` 已同步新增 provider/MCP symbols，并可查询 `PingAnAssetLocatorService`。
- 当前状态：`D12-A Done / fake-only`；`D12-B Waiting / internal-network-and-credential-gated`。
  在 D12-B 保存真实 `mocked=false` smoke 前，`PA-12` 与 `PI-01 real provider` 必须继续保持未完成。

## 2026-08-02 — PI-01 Checkpoint D11 + D11.1 evidence-quality semantics

- 新增 `build_checkpoint_d_full_corpus_runtime_review.py`、聚焦测试和唯一命令
  `./scripts/soc-runtime-validation.sh checkpoint-d-full-corpus`。
- D11 严格复用 `SocAnalysisService` 的 production 九步控制流；每个 D0 payload 在同一进程执行
  两次 `StubLLMAnalyzer` Runtime，只验证全量 payload 兼容性、fail-closed 和语义稳定性，不调用
  LLM、DB/repository replay、租户处置、MCP 或 action，也不评估模型质量。
- 稳定性投影排除 `run_id`、时间戳、耗时、重复 step input hash 和 source 缺失时按摄入时间生成的
  `AlertEventRef.received_at`；实体、事实、bounded input、Skill、Analyzer、Grounding、Decision 等
  下游语义 output hash 仍全部参与比较。原始 trace 差异继续留在行摘要中供审计。
- authoritative 结果：
  - 212/212 行处理，424 次 Runtime 执行，212/212 语义稳定；
  - 0 Runtime exception、0 failed row、0 diagnostic，8 topic / 6 source family 全覆盖；
  - 两条 `evidence_unavailable` 均无 bounded evidence，并显式触发 fail-closed；
  - 212 条均为 `needs_review=true` / `automation_allowed=false`；
  - 220 条非空 stub evidence 全部 grounded；stub 不再为缺失的 optional command line/process 生成空引用；
  - 206 `unknown` / 6 `true_positive` 只是 deterministic stub 路径覆盖，不是模型准确率结论。
- D11.1 修正过宽的质量判级：
  - outer parser 成功即为 `recognized`；nested decode/repair 失败保留原字符串、typed observation 和
    warning，但不把整条 message schema 标为 degraded；
  - encoded compaction 单独存在只记账；普通 omission/truncation 且无 high-value gap 时为 `partial`；
    degraded/unsupported outer schema、high-value gap 或 ungrounded citation 才为 `degraded`，冲突仍为
    `conflicted`；Decision Policy 升级为 `soc.decision_policy.v3`；
  - corpus 分布为 343 个 recognized schema、12 个 parser-warning rows、175 个 routine-truncation rows、
    112 个 encoded-compaction rows；Decision state 为 6 conflicted / 2 degraded / 198 partial / 6 sufficient；
  - D11 acceptance 已锁定四条规则：routine truncation 不直接降级、nested warning 保持 outer
    recognized、high-value gap fail closed、encoded compaction 不产生历史 truncation review reason。
- 重建当前单样本 lineage：D0-D6 全部通过；D7 用 `deepseek-v4-pro` 真实调用一次（26,093 tokens）
  得到 9 条 evidence；D8 为 5 grounded / 4 description leakage；D9/v3 保留 `suspicious`，仅由
  ungrounded evidence 等四个现行 reason 进入 degraded/review/no-automation。旧 D5/D7/D8 hash 组合被
  validation 正确拒绝，没有复用不一致产物。
- 产物：
  `backend/.deer-flow/soc-runtime-validation/checkpoint-d/step-d11-full-corpus-runtime/full-corpus-runtime-matrix.json`
  （真实告警派生、gitignored）；仅失败/不稳定行才会生成 `diagnostics/*.json`，本次为 0。
- 验证：D11.1 聚焦 Runtime/parser/policy/service/validation 回归 179 passed；完整 SOC + Checkpoint D
 回归 629 passed；真实 D11 重跑为 212/212 stable、0 failed check；D0-D6、当前 D7、D8、D9 均按
  lineage 重建通过；changed-file Ruff、`git diff --check` 与 `codegraph sync .` 通过。
- 下一步：Checkpoint D 兼容性主线结束；进入第一项获批只读 dev/staging provider intake，优先
  CMDB/资产查询。需要真实 endpoint、认证方式、租户映射、批准 payload 样例和数据 owner；不再以
  本地 mock 代替生产集成证据。

## 2026-08-01 — PI-01 Checkpoint D10 cross-source Runtime review

- 新增 `build_checkpoint_d_cross_source_runtime_review.py`、聚焦测试与
  `./scripts/soc-runtime-validation.sh checkpoint-d-cross-source`。
- D10 从 D0 inventory 按 topic 中位数距离自动选择代表样本，不硬编码 alert ID；另将全部
  `evidence_unavailable` known gaps 纳入。每条样本调用同一个无持久化
  `SocAnalysisService`，并强制使用显式配置的真实 LLM analyzer；stub 会直接使验收失败。
- authoritative 结果：
  - 8 个 topic、6 类 source family、8 条代表样本和 2 条 known input gap；
  - 10/10 次 `deepseek-v4-pro` 调用完成相同 9-step production Runtime，消耗 150,795 input、
    16,247 output、合计 167,042 tokens；0 Runtime failure、0 failed check；
  - 模型输出为 8 `suspicious`、1 `needs_review`、1 `unknown`，不再是 deterministic stub 模板；
  - 87 条模型 evidence 中 67 grounded、20 ungrounded，其中 14 条为
    `description_context_leakage`；6 个样本产生可审计 quality finding，报告状态为
    `passed_with_quality_findings`；
  - 两条空 `zeusRawLogs` 告警无 bounded evidence，Runtime 现通过通用
    `analysis_evidence.unavailable` critical coverage gap 明确记录上游证据不可用；
  - Decision 均为 `evidence_state=degraded`、`needs_review=true`、
    `automation_allowed=false`；模型未落地引用和输入缺口都没有被静默修复。输入缺口规则不含
    PingAn/topic alias；已有 canonical/fact/scenario evidence 的合法通用输入不会误报。
- 产物：
  - `checkpoint-d/step-d10-cross-source-runtime/representative-matrix.json`；
  - `checkpoint-d/step-d10-cross-source-runtime/runs/*.runtime.json`。
- 验证：single-sample D7 真实调用通过；D10 真实批次 status
  `passed_with_quality_findings`；真实产物记录 `deepseek-v4-pro` response metadata、Parser 版本和
  token usage，且 D10 contract 禁止 stub；Checkpoint D + Runtime/LLM parser/Prompt/Grounding
  组合回归 99 passed；Ruff、shell syntax、`git diff --check` 和 `codegraph sync .` 通过。
- 下一步：
  - `PI-01 Checkpoint D11` 将同一检查扩展到 212/212，并执行 deterministic replay stability；
    先证明全量 payload/runtime 兼容性，再进入第一项真实只读 provider intake。

## 2026-08-01 — PI-01 Checkpoint D9 Decision Policy review

- 新增 deterministic D9 builder、测试和命令：
  - `validation/compact_zeus/checkpoint_d/build_checkpoint_d_decision_policy_review.py`；
  - `./scripts/soc-runtime-validation.sh checkpoint-d-decision`；
  - artifact：`checkpoint-d/step-d9-decision-policy/1965449.decision.json`。
- D9 直接消费 D5 request、D7 `AnalysisResult.v2` 和 D8 Grounding report，并校验三者 hash/alert
  lineage；不调用模型、不重新 Grounding、不写数据库、不执行租户处置策略。
- authoritative 结果：
  - execution `passed`，decision gate `guarded_review_required`；
  - D7 verdict `suspicious` 被原样保留；
  - 当前 D11.1 重建 lineage 的 D8 为 5 grounded / 4 rejected，仍形成
    `evidence_state=degraded`；
  - 当前 `soc.decision_policy.v3` review reasons 为 uncertain verdict、ungrounded evidence、raw
    confidence below threshold 和 confidence not calibrated；nested warning / routine truncation 不再
    冒充 hard degradation；
  - `needs_review=true`，`automation_allowed=false`。
- 同步架构决定：PingAn `dev/local/staging` 免处置属于版本化 tenant disposition policy，不是
  detection truth、LLM memory 或 Runtime short-circuit。Adapter 只输出通用 context candidate；完整
  Runtime 分析之后才做独立 operational disposition reconciliation，初期 shadow-only。
- 验证：
  - D9/D8 focused pytest：4 passed；
  - changed-file Ruff format/check：passed；
  - shell syntax：passed；
  - 真实保存的 D5/D7/D8 -> D9：passed。
- 下一步：
  - `PI-01 Checkpoint D10` 建立跨来源 representative matrix；该步骤随后按产品负责人要求升级为
    真实模型完整 Runtime 回放，不以单条 `1965449` 的特殊结果代替多格式验证。

## 2026-08-01 — PI-01 provider assertion / D7-D8 correction

- 修正 PingAn NDR/APT Adapter 的 source semantics：
  - `rule_name/rule_desc/attack_type/host_state/rule_labels` 只在
    `normalizers/pingan_ndr.py` 中映射为通用 provider detection assertions；
  - `host_state` 的通用语义为 `provider_detection_outcome_assertion`，不再被描述成普通 workflow
    state；Adapter 仍不直接写 Runtime verdict。
- 修正通用 Grounding：
  - 只有 exact path、`high` trust、实际进入 bounded projection 的 provider outcome assertion
    才能满足 outcome source；Core 不识别 `host_state` 等 PingAn alias；
  - 精确可见 encoded-omission marker 可 ground 值存在、encoding shape 和模型边界省略，但不能
    ground 隐藏字节、私有 sidecar hash、token 有效性/身份/权限或安全结果；
  - description audit 新增通用短端口检测，能拒绝把 `dport=80` 混入只引用目标 IP 的证据描述。
- Prompt 升级到 `soc-analysis-v8`：要求 `evidence.value` 逐字复制最小 scalar leaf，不得拼接
  `key=value`；IP/端口等多事实必须分条引用。Prompt 只提供指导，不能替代 deterministic Grounding。
- 重建与真实验证：
  - D0-D6 全部重建通过：212 unique alerts、0 blocking rows、D6 212/212 无失败；
  - 中间 `soc-analysis-v7` 运行得到 12 evidence、D8 为 11 grounded / 1 value-not-found；
  - authoritative `soc-analysis-v8` D7 structure passed，输出 10 evidence，primary scenario
    `弱口令成功登录`、stage `effect_observed`；
  - authoritative D8 execution passed / quality blocked：8 grounded、2
    `description_context_leakage`，没有旧的 false `unproven_outcome_claim`；两条拒绝分别为
    source-IP description 混入 destination IP，以及 request-body description 混入弱口令分类。
- 验证：
  - PingAn semantics / Prompt / Grounding 定向回归：56 passed；
  - Analyzer/Runtime/D8 builder 组合回归：64 passed；
  - v8 原子引用与短端口修订回归：27 passed；
  - changed-file Ruff format/check：passed。
- 结论与下一步：
  - 不通过反复付费采样追求偶然全绿；最新 blocked quality 是模型引用错误被 fail-closed 的正确证据；
  - `PI-01 Checkpoint D9` 继续消费当前 D5/D7/D8，验证 Decision Policy 输出 degraded evidence、
    human review 和 `automation_allowed=false`，不调用模型、不写数据库。

## 2026-07-31 — PI-01 Checkpoint D8 evidence Grounding (initial baseline, superseded)

- 将 production Grounding 升级为 `soc.analysis_evidence_grounding.v2`：
  - 新增 `description_context_leakage`，source/value 落地但 description 夹带其他 bounded
    fact 时仍计入 ungrounded；
  - 每项同时保留 `matched_context_paths` 与 `foreign_description_context_paths`；
  - entity mention synthetic key 和显式 disclaimer 不参与 sibling-fact 误报；
  - encoded omission marker、object-as-string 和未证实 outcome 继续 fail closed。
- 新增 D8 builder/test/命令：
  - `./scripts/soc-runtime-validation.sh checkpoint-d-grounding`;
  - 产物：
    `backend/.deer-flow/soc-runtime-validation/checkpoint-d/step-d8-evidence-grounding/1965449.grounding.json`;
  - 只消费 D5/D7，不调用 LLM、不执行 Decision、不持久化。
- 根据首轮 D8 反馈把 Prompt 升到 `soc-analysis-v6`，明确 marker 与 source redaction 规则；
  真实模型仍产生语义污染，证明不能依赖 Prompt 替代 Grounding。
- 当时的真实 D7/D8 baseline：
  - model `deepseek-v4-pro`, parser `soc-analysis-json-parser-v5`;
  - D7 structure `passed`, verdict `suspicious`, confidence `0.55`,
    stage `effect_observed`, 15 evidence；
  - D8 execution `passed` / quality `blocked`;
  - 8 grounded、4 description leakage、3 value-not-found；
  - 保留 `unproven_outcome_claim`，primary scenario 仍引用被拒绝 evidence。
- 验证：
  - 完整 SOC backend + architecture boundary 回归：611 passed；
  - D7/D8/Prompt/Grounding/public Skill 定向回归：71 passed；
  - D8 Grounding 聚焦回归：14 passed；
  - Ruff check/format、Shell syntax、`git diff --check`：passed；
  - 当时本地 D8 artifact 结果为 8 grounded / 4 leakage / 3
    value-not-found。
- 安全边界：
  - D8 的 blocked quality 是正确拒绝结果，不是脚本失败；
  - Grounding 不修复或重写模型证据；
  - 下一步 D9 必须证明现有 Decision Policy 将该报告转成 degraded evidence、human review 和
    `automation_allowed=false`。

## 2026-07-31 — PI-01 Checkpoint D7 typed Analyzer output

- 按 CodeGraph 确认现有 `AnalysisResult -> Prompt -> Parser -> Grounding ->
  SocDecisionPolicy` 调用链，没有新增第二套 Runtime。
- 审计旧 Zeus APT/NIDS/HIDS/EDR Flow 直接引用的 Prompt，只提炼告警研判方法：
  - 保留场景识别、证据检查、行为阶段、竞争解释、缺口和人工核查。
  - 明确不迁移攻击链/时间线、处置闭环、真实外部服务、邮件 Agent、NL2SQL/Chat BI。
- 新增 `AnalysisResult.v2` 与 `TriageScenarioAssessment`：
  - 开放词表场景，不要求固定 taxonomy；
  - `upstream_hint|inferred|hybrid` 来源；
  - detection/attempt/effect/impact/indeterminate 阶段；
  - 唯一 primary、evidence index、竞争解释、证据缺口和人工核查。
- Prompt/Parser：
  - 首轮 `soc-analysis-v5`，初次 D8 反馈后当时升级为 `soc-analysis-v6`；后续 correction 见上方
    2026-08-01 `soc-analysis-v8` 记录；
  - `soc-analysis-json-parser-v5`;
  - 缺失/未知字段、非法类型、越界 evidence index、重复场景和主场景数量错误均 fail closed。
- Skill 提炼：
  - Web：信息泄露、配置暴露、XXE、XSS、工具/扫描特征；
  - Network：DNS/DNSLog、代理/C2、工具特征；
  - Endpoint：暴力破解、远程访问、安全产品、蜜罐和授权运维；
  - 所有场景继续把客户白名单留在 governed tenant context。
- 新增 D7 builder、测试和命令：
  - `./scripts/soc-runtime-validation.sh checkpoint-d-live`
  - 产物：
    `backend/.deer-flow/soc-runtime-validation/checkpoint-d/step-d7-analyzer-output/1965449.analyzer-output.json`
- 真实运行：
  - model `deepseek-v4-pro`;
  - 当时 baseline verdict `suspicious`, confidence `0.55`;
  - primary scenario `内部IP向stg环境登录接口使用疑似弱口令获取会话令牌`;
  - stage `effect_observed`;
  - 15 evidence / 6 gaps / 5 manual checks;
  - parser repair `false`, D7 structure status `passed`.
- 验证：
  - 完整 SOC backend + architecture boundary 回归：606 passed；
  - D5-D7 builders + public Skill 定向回归：54 passed；
  - D7/parser 前序定向回归：21 passed；
  - D7/SOC/Skill 前序组合回归：98 passed；
  - D7 相关 Ruff check/format、Shell syntax、`git diff --check`：passed；
  - changed-file Ruff check：passed；
  - D0-D6 rerun：212/212 processed, 0 failures；
  - D7 live call：passed.
- 重要边界：
  - D7 只证明真实模型和 typed contract 能协作，不证明 evidence 已落地或 verdict 正确。
  - D8 已确认 description sibling facts、encoded omission 和未证实 outcome 风险并 fail
    closed；当前下一步为 D9 Decision Policy 审阅。

## 能力与历史切片台账

> 下表保留能力状态和历史切片。TODO 不能越过 `delivery-roadmap.md` 插入当前 Stage。

| 顺序 | 待办 | 状态 | 做什么 | 验收标准 |
|---|---|---|---|---|
| 0 | PingAn knowledge decomposition | Partial / internal-smoke-gated | 已完成 `PA-01..PA-11`；`PA-12` 的 DEV profile/signer/preflight 已就绪，等待内网真实 smoke | 通用 skill 不含平安内部知识；每条平安经验都有 target artifact、tenant scope、来源和验收方式；PA-12 不能用 mock 冒充完成 |
| 0.1 | `PA-01` PingAn capability card register | Done | 已新增 `.notes/ai_soc/capabilities/pingan/capability-cards.md`，从 APT/EDR/HIDS 三份源文档抽出 P0/P1/P2 cards | P0 card 已明确 source、场景、输入、输出、落点、风险等级、失败模式和验收要求；mock MCP 必须等 card 明确后再做 |
| 0.2 | `PA-02` APT source decomposition | Done | 已扩展 `PA-APT-001..005`：攻击方向、场景化研判、威胁情报、security tag、IP 封堵高风险边界；拆出 skill/domain handler/eval/memory/action 边界 | APT 通用方法进 public skill/domain handler；平安字段、URI 例外、内部环境、策略和阈值只进 tenant artifact |
| 0.3 | `PA-03` EDR source decomposition | Done | 已扩展 EDR cards：进程树、路径/命令行、LoginData/System、提权、UM/账号、终端处置候选 | 通用 endpoint 方法进 skill/domain handler；平安路径、账号、部门、BU 和封禁/隔离策略只进 tenant artifact 或 approval-gated action |
| 0.4 | `PA-04` HIDS source decomposition | Done | 已扩展 HIDS cards：主机事件上下文、event_type 场景化研判、误报/授权运维模式、服务器隔离候选 | 通用 host/endpoint 方法进 skill/domain handler；平安组名、账号、路径、机房、域名、隔离模板只进 tenant artifact 或 approval-gated action |
| 0.5 | `PA-05` PingAnKnowledgeCandidate register | Done | 已新增 `.notes/ai_soc/capabilities/pingan/knowledge-candidates.md`，从 APT/EDR/HIDS expanded cards 抽候选知识清单，标注 target_artifact、tenant_scope、source_doc、source_section、status、validity、review owner | 每条平安专属经验都能回答“放哪里、为什么、是否过期、由谁确认”，且默认 pending_review，不直接影响 runtime decision |
| 0.6 | `PA-06` public skills minimal revisions | Done | 已对 `skills/public/soc-*` 做最小增量修订，只吸收跨客户通用的 APT/EDR/HIDS/WAF/asset 研判方法 | `rg` 检查 public skills 不出现平安字段、内部环境、账号/组织、白名单、模板 ID、策略 ID 或处置阈值 |
| 0.7 | `PA-07` P0 read-only mock action adapters | Done / revised | 保留 `threat_intel.ip_reputation.lookup`、`security_tag.lookup` mock adapters；未经证实的进程树/主机上下文查询 mock 已删除 | 外部查询通过 `SocActionAdapterRegistry`；进程/主机上下文使用告警原生证据；不改 verdict/memory |
| 0.8 | `PA-08` PingAn eval fixtures | Done | 已新增 `backend/samples/eval/pingan/` 三条 fixture、`backend/samples/alerts/pingan_legacy_hids.json`、`backend/soc_agent/eval/pingan.py` 和 `soc eval pingan` | APT/EDR/HIDS 各 1 条脱敏 fixture；覆盖字段冲突、查不到外部事实、误报/授权标签；read-only success 写 `InvestigationEvidence` |
| 0.9 | `PA-09` PingAn memory candidate entry | Done | 已新增 `SocMemoryCandidate` contracts、`MemoryCandidateRepository` protocol、in-memory repository 和 `SocMemoryService.propose_candidate()` | 候选默认 `pending_review`，携带 source/evidence/validity/idempotency/facets/review 信息；不自动 confirmed，不影响 runtime decision |
| 0.10 | `PA-10` PingAn domain triage MVP | Done | 已新增 `SocDomainTriageRequest/Result/Finding` contract、`SocDomainTriageService`、APT/EDR/HIDS deterministic handlers 和 `soc eval pingan-domain` | 子研判只输出 finding/evidence/recommendation；消费 skill context 和 read-only evidence refs；不写 DB、不执行 action、不改 verdict |
| 0.11 | `PA-11` PingAn main orchestrator demo | Done | `SocMainOrchestratorService`、`UnifiedInvestigationReport`、`soc eval pingan-main` 已覆盖 APT/EDR/HIDS analyze -> correlation -> read-only evidence -> domain finding -> review summary | 每条当前告警命中 seeded historical run，并只复用该 historical `run_id` 的 evidence；不写 DB、不执行高风险动作 |
| 0.12 | `PA-12` real PingAn MCP/API replacement | In Progress / internal smoke | DEV profile、portable signer、preflight/direct smoke entry 已完成；内网补 Agent Platform import、approved cases 和 `mocked=false` 证据 | 评估 latency、failure、payload/result size、字段裁剪和敏感信息风险；不能用本地 mock 假装完成 |
| 0.13 | `D12-A` PingAn asset provider implementation | Done / fake-only | 已建立可移植 `asset.locate` provider、ZEUS HTTP/signing port、workflow port、stdio MCP server、显式 config 与 fake smoke | fake 输出始终 `mocked=true`；internal 缺配置 fail closed；该状态不标记 PA-12/PI-01 real provider Done |
| 0.14 | `D12-B` PingAn asset provider internal smoke | Parked / internal evidence pending | root config/模型/签名已审计，preflight、direct matrix 和 MCP evidence acceptance 已实现；按产品决定暂存，恢复时直接在内网补真实 case matrix | 成功、查无、鉴权失败、超时和 InvestigationEvidence 全部留证；真实响应为 `mocked=false`；暂存不等于完成 |
| 0.15 | `PI-01A` PingAn threat-intelligence provider | In Progress / internal smoke pending | production-shaped Provider、stdio MCP、action config、bounded field mapping、freshness/lineage 和 fake/persistence 回归已完成 | 真实 DEV hit/not-found/error/timeout；实际字段 coverage 经复核；`mocked=false` evidence 可持久化回读；不迁移旧评分/封禁规则 |
| 1 | Correlation Service MVP | Done | `SocCorrelationService` 基于 summary/evidence 输出相似告警、匹配原因和可复用证据；typed result 已进入 main report/domain/review summary | 不调用 LLM、不依赖真实 MCP、不改 decision；demo 当前告警可看到历史 run + reusable evidence |
| 1.1 | Correlation -> Unified Investigation bridge | Done | 共享 summary repository、统一 deterministic scorer、`SocDomainTriageRequest.correlation_result`、`UnifiedInvestigationReport.correlation_result` 和 review counts 已接通 | metadata count 不是证据源；historical evidence 只按 matched `run_id` 加载；移除两个无效 mock 后 APT/EDR/HIDS eval 为 3 matches / 4 evidence / 0 failure |
| 1.2 | Correlation quality baseline | Done | 已建 vendor-neutral same-incident / related-but-distinct / unrelated corpus；`soc eval correlation` 输出双任务指标、reason 分布、fan-out、evidence lineage/unrelated exposure，并支持 `--baseline-json` replay diff | scorer/report/fixture 版本显式；当前 8-pair baseline 暴露 retrieval/dedup precision 均约 0.667；`shadow_dedup_allowed=false` |
| 1.3 | [Correlation label corpus expansion](../archive/ai_soc/deferred/correlation-label-corpus-expansion.md) | Deferred / `PI-03` data-dependent | 从脱敏真实告警准备 analyst-reviewed pairs，覆盖来源、时间窗口、跨规则同事件和同规则不同事件 cohort | 不以 8 条受控 pair 代表生产分布；标签来源/rationale/version 可审计；扩充后再比较 scorer v2，不直接切换生产规则；不阻塞当前 `PI-01` |
| 2 | External Disposition Sync Contract | Done | 已新增 vendor-neutral event/status/mapping/record/result contract、generic mapper、Zeus mock fixture、`SocExternalDispositionService`、repository protocol、in-memory repository、PostgreSQL persistence、ReviewQueue context API/Web/TUI/Lead Agent visibility；已接 high-trust mapped review/correction 和 pending memory candidate | 不在 core service 写死 Zeus；未知状态/无法定位只保存 unmatched；重复事件幂等；free-text reason 只能进 pending candidate，不能进 confirmed memory |
| 3 | Memory Tracking Contract | Partial | DB-first candidate persistence、review workflow、confirmed-memory boundary、retrieval policy 与 governed activation 已完成；`SocMemoryCandidateSourceBridge` 已接 correction、domain finding、analyst feedback 和 ReviewQueue review note；Kafka/Lead Agent 自动结论来源与 prompt injection 仍后置 | 不再使用四维硬 key；缺 topic/detection/vendor alias/scenario 任意 facet 时仍可工作；wiki/OKF 只作为后期 projection |
| 3.1 | Memory candidate DB/API/ReviewQueue visibility | Done | 已新增 `soc_memory_candidates`、repository、CLI `soc memory list/get`、Gateway `/api/soc/memory/candidates`、ReviewQueue context/Web/TUI/Lead Agent bounded visibility | candidate 仍为 `pending_review` 且 `runtime_decision_allowed=false`；不注入 prompt，不影响 verdict |
| 3.2 | Memory candidate review workflow / confirmed-memory boundary | Done | 已新增 `SocMemoryCandidateReviewCommand/Result`、`SocMemoryRecord`、`soc_memory_records`、`soc memory review`、`soc memory records list/get`、Gateway review/records API 和 ReviewQueue Web 操作入口 | confirm/reject/deprecate/expire 只能走 `SocMemoryService`；`confirm` 生成 `SocMemoryRecord(retrieval_enabled=false)`；不注入 prompt，不影响 verdict |
| 3.3 | Confirmed memory retrieval policy / unified visibility MVP | Done | 已新增 `SocMemoryQuery`、`SocMemoryMatch`、`SocMemoryRetrievalResult`、`SocMemoryService.find_relevant_records()`、CLI `soc memory search`、Gateway `/api/soc/memory/search`、`InvestigationContext.relevant_memories` 和 Web/TUI/Lead Agent 可见化 | 只返回 `retrieval_enabled=true`、confirmed、未过期 record；返回 score/match reason/token estimate/hash/version；不注入 prompt，不影响 verdict |
| 3.4 | Governed confirmed-memory retrieval activation | Done | `SocMemoryRetrievalActivationCommand` 和 `SocMemoryService.set_retrieval_activation()` 统一 role/reason/expected-version/validity/review/audit 语义；CLI/API/Web/Boss Demo 均复用该入口，search 支持 baseline diff | 直接写布尔值、过期 activation、逾期 review 或无治理 metadata 的 record 均不能进入 bounded retrieval；事务失败不留下 record/audit 半写状态 |
| 4 | Domain Sub-Agent Contract | Done for PA-10 | 已固定 `SocDomainTriageRequest`、`SocDomainTriageResult`、`SocDomainFinding` 结构 | EDR/APT/HIDS 已共用同一 schema；子研判不能直接改 decision 或写 DB |
| 5 | Generic security scenario recognition | Partial | deterministic MVP 已完成：第一批场景包括反弹 shell、webshell、横向移动、命令/代码执行、恶意外联、提权、凭证滥用；未命中内部 taxonomy 但存在上游场景提示时输出 `vendor.unmapped` 候选 finding；已暴露 `SCENARIO_TAXONOMY_VERSION`/keys/snapshot；PingAn domain eval 和 vendor-neutral `soc eval scenarios` 都输出 covered/missing/unmapped 计数，`--baseline-json` 可生成 replay diff | 任何来源的告警都通过统一 `SocDomainTriageResult/Finding` 输出场景化 finding；Evidence Fusion First；未映射厂商场景不阻断研判、不改 verdict、不写 confirmed memory；eval 能作为 replay diff 基线；LLM 后续只能在 bounded context 中识别场景，不能直接改 verdict 或写 confirmed memory |
| 6 | Main SOC Agent Orchestrator MVP | Done for Phase 2 bridge | 已串起 analyze、skill context、correlation、read-only action evidence、domain triage、review summary，输出 `UnifiedInvestigationReport` | APT/EDR/HIDS demo 能看到主控用了哪些 skill、历史 match/reasons/evidence、route、finding 和 review context |
| 7 | Web/TUI visible investigation | Done for MVP | 已新增 `UnifiedInvestigationView`、`InvestigationTimelineItem`，`InvestigationContext` 聚合 correlation result、domain triage results、evidence timeline、external feedback、memory candidates 和 relevant memories；Web/TUI/Lead Agent bounded artifact 可见 | 分析师能区分 runtime decision、domain findings、read-only evidence、外部人工反馈、人工 correction、retrieval-enabled memory；视图只读，不改 verdict |
| 8 | Demo / Eval Script | Done for APT/EDR/HIDS + single-alert MVP | `soc demo run`/`soc demo alert` 保持持久化调查演示；`soc eval pingan-main` 额外验证无 DB 的 current + historical correlation 主编排链 | 可分别演示持久化 Web/TUI context 与 bounded orchestrator report；mock action evidence 明确标记，不冒充真实 PA-12 |
| 9 | Memory candidate source integration | Partial | 已新增 `SocMemoryCandidateSourceBridge`：correction 会自动生成 pending candidate 并回写 `memory_candidate_id`，domain finding 已有幂等 bridge/factory，analyst feedback 可进入 candidate content/facets/metadata，`SocReviewService.add_note()` / `soc review note` 可把 ReviewQueue review note 生成 pending candidate；Kafka daemon、Lead Agent proposal 等来源待接 | 每类来源都有 source/evidence/validity/idempotency/facet；候选默认 pending review；confirmed/retrieval gate 仍由 `SocMemoryService` 控制 |
| 10 | Normalization maintenance loop | Done for MVP | 持久化 schema baseline、主动 monitor、去重/reopen issue、SocEvent、CLI/API/Web/TUI、Kafka metric 摘要；字段重要性 registry、离线 suggestion、confidence calibration 和 repair domain guard 已落地 | 新 schema/解析降级/关键映射缺口不静默；首次观察不自批 baseline；suggestion 不自动改代码；calibration profile 不自动放行动作 |
| 11 | DeerFlow-backed live Runtime LLM | Done for MVP | 新增 `DeerFlowLLMChatClient`、`SocLLMSettings`，统一装配 analyze/replay/demo/Kafka；offline eval 和 normalize suggest 支持 live model | 显式选择模型；未知模型 fail-fast；输出过 JSON/schema/domain validation；trace 记录安全 metadata/usage；模型不能执行动作 |
| 11.1 | Deterministic decision policy / confidence guard | Done for uncalibrated MVP | 新增 `SocDecisionPolicy`，把 raw analyzer score、来源、校准状态、证据状态、结构化 review reasons 和 policy version 分开；mock/failed evidence 不参与 domain/scenario 置信度 | stub/LLM self-report 当前全部进入复核；误报、冲突、schema 降级/不支持、关键证据缺口、截断等 guard 不会被高分覆盖；summary/queue/audit 保留原因 |
| 11.2 | Runtime production hardening | Done | 显式 `skill_context` trace、共享 bounded projection、analysis evidence grounding、prompt/output/schema hard bounds、LLM concurrency/RPM admission、typed sanitized failure、Kafka retry/dead-letter 语义、run/summary/review/audit 原子 bundle | 未落地证据强制复核；可重试失败不 commit offset/不制造工单噪声；不可重试失败进入 ReviewQueue/DLQ；SQL 故障回归证明四类主写入全部回滚 |
| 11.3 | Governed confidence label set | Done for initial 5-sample baseline / 3 accepted, 2 excluded | 已完成 5 条 DeepSeek 同 scope 标签审阅：2 false positive、1 exploit-attempt true positive accepted；2 条决定性授权/内部业务上下文缺失样本保留业务真值但 excluded；validator 返回 `calibratable=true` | 标签集无 pending/重复 input hash/混合 model-prompt-pipeline scope；accepted 同时包含正负类；仅 3 条 accepted，仍只允许离线 smoke，不能生成生产阈值或自动放行 |
| 11.4 | Governed context fact lifecycle | GF-01 Done | 已完成 typed `AuthorizedActivityPayload`、append-only fact versions、role-gated lifecycle、repository protocol、in-memory/SQLAlchemy persistence、`0013` migration、CLI 和 sample | Fact 与 evidence/memory/approval/detection truth 分离；revision fail closed 并重新审批 |
| 11.5 | Authorized activity event-time matcher | AA-01 Done | 已完成 `AuthorizationQuery/AuthorizationMatchResult`、canonical query builder、历史 lifecycle version selection、source freshness/recurrence/scope matcher、`soc context match`；真实 HIDS/EDR shadow replay 均为 exact | matcher 不识别 vendor aliases、不调用 LLM、不持久化、不改 verdict/ReviewQueue/disposition；naive event time 必须显式传 IANA timezone |
| 11.6 | Authorization enrichment persistence/projection | EX-01 Done | 已完成 strict command/record/result contracts、append-only in-memory/SQL repository、`0014` migration、幂等写入、replay lineage、CLI 和 InvestigationContext/Web/TUI/Lead Agent 投影 | enrichment 保存 query hash/policy/fact refs/actor；`shadow_only=true`、`decision_impact=none`；不修改 run decision/queue/memory/disposition |
| 11.7 | Shadow disposition proposal | DP-01 Done | 已完成 generic operational disposition、detection truth snapshot、append-only proposal repository、`0015` migration、CLI 和 InvestigationContext/Web/TUI/Lead Agent 投影 | 仅 open-queue persisted exact enrichment + current true-positive 可提议 benign-TP；proposal 永远 shadow/not-applied，人工复核，不改 run/queue，不自动关单 |
| 11.8 | Shadow disposition evaluation gate | EV-01 Done | 已完成 explicit evaluation scope、hash-ranked sample manifest、append-only superseding outcome、`0016` migration、CLI、gate report 和 InvestigationContext/Web/TUI/Lead Agent 只读投影 | 指标覆盖 resolution/precision/override/sample coverage+agreement/freshness/fact fan-out；passed 只代表可进入治理评审，`auto_close_allowed=false` |
| 11.9 | Structured disposition outcome capture | EV-02 Done | authenticated API/Web、Review TUI primary/sample command 和 trusted external disposition bridge 全部复用 `SocDispositionEvaluationService` | 不从 `close_reason` 猜标签；幂等、sample membership、独立 reviewer、supersession 和 closed queue 仍由 service 校验；不启用 auto-close |
| 11.10 | Sample review campaign inbox | EV-03 Done | 新增 derived reviewer inbox、latest-outcome batch query、Gateway read API 和 Web `抽样复核` view；selected work 回到 EV-02 capture form | manifest 仍是唯一抽样真相；禁止挑样、第二写入口和 mutable campaign table；`auto_close_allowed=false` |
| W1 | Real dev/staging CMDB/EDR MCP replacement | Waiting | 等 endpoint/凭证后替换本地 fixture，运行 `soc mcp tools/smoke` 并保存 report | 评估 latency、failure、payload/result size、字段裁剪和敏感信息风险 |
| D1 | [Wiki/OKF export projection](../archive/ai_soc/deferred/wiki-okf-memory-projection.md) | Deferred | DB memory store、retrieval、review workflow 稳定后，再做 DB -> wiki/OKF export | PostgreSQL 仍是 source of truth；wiki 反向修改只能生成 proposal |
| D2 | Prometheus / operations overview | Partial | normalization 运维页、Gateway bounded metrics 和 Kafka JSONL issue 摘要已完成；全局 Kafka/review/approval/runtime/算力 Prometheus exporter 和态势面板仍后置 | 当前 maintenance issue 可见；全系统运行态势不阻塞 SOC Agent Alpha |
| D3 | High-risk real execute | Deferred | 等真实 staging adapter、审批策略、补偿和 adapter audit 成熟后再打开 | 生产 execute 前必须有 approval、dry-run、idempotency、回滚/补偿策略 |
| D4 | [Adaptive normalization/parser evolution](../archive/ai_soc/deferred/adaptive-normalization-parser-evolution.md) | Deferred / production-data-dependent | 按真实 drift cohort 离线生成 parser/mapping/test 候选并治理发布 | 当前 deterministic parser/monitoring 已工作；不得逐告警调用 LLM 或自动改 Runtime |

## Phase 1 切片计划

| 序号 | 切片 | 状态 | 验收标准 |
|---|---|---|---|
| 1 | SOC Agent 代码落点确认与骨架创建 | Done | 明确包目录、CLI 接入方式、测试目录；新增空骨架不破坏现有测试 |
| 2 | contracts + core state | Done | 定义 `AlertInput`、`AnalysisResult`、`Decision`、`AnalysisRun`、`PipelineStepTrace` 等 schema/状态 |
| 3 | fixed Runtime pipeline | Done | `normalize -> entity_extract -> fact_reconstruct -> build_analysis_input -> skill_context -> analyze -> schema_validate -> evidence_grounding -> decide` 固定执行，LLM 不能跳步 |
| 4 | CLI `soc analyze` | Done | 能读取 JSON 文件/字符串，输出结构化 JSON 结果和 step trace |
| 5 | golden alert samples | Done for Phase 1 | 覆盖批准扫描器误报、恶意 IOC、低置信未知、字段缺失、嵌套坏 JSON accepted/rejected repair 和 schema drift |
| 6 | Phase 1 最小测试 | Done | 字段缺失不崩、输出过 schema/domain validation、每步有 trace、不执行自动处置；坏 JSON repair 有字段策略/domain guard 回归 |
| 7 | replay contract | Done | `AnalysisRun` 记录 input payload/hash；`SocAnalysisService.replay()` 通过 repository 生成新 run，不覆盖旧 run |
| 8 | PostgreSQL run repository | Done | SOC ORM row + SQLAlchemy repository + Alembic migration + headless CLI `show/replay` 已完成 |
| 9 | manual correction loop | Done | `soc correct RUN_ID` 更新 operational decision，保留原 AI verdict，追加 correction record，不自动写 confirmed memory |
| 10 | decision audit log | Done | `soc_decision_audit_log` 独立表记录 analyze/replay/correct 的结构化审计记录 |
| 11 | alert summary read model | Done | `soc_alert_summaries` 保存可查询摘要，analyze/replay/correct 通过 service 维护 summary |
| 12 | legacy platform normalizer | Done | 平安旧预警平台 envelope 转 canonical `AlertInput`；APT/EDR/HIDS raw message parser registry 可提取核心实体，完整 raw payload 保留 |
| 13 | CLI summary list | Done | `soc list` 输出持久化 `AlertSummary`，用于验证 Web/TUI 列表字段 |
| 14 | ZEUS evidence input policy | Done | 平安 ZEUS/天眼 parsed raw message 最高优先级；多 message 分 primary/supplementary；nested decode 失败保留原文，保守验收的 repair 进入独立 repaired projection，拒绝/失败时使用脱敏字符串 fallback |
| 15 | fact reconstruction layer | Done | `entity_extract` 后生成 `FactReconstructionResult`，记录字段可信度、角色候选和冲突报告 |
| 16 | LLM-ready analysis request | Done | `fact_reconstruct` 后生成 `LLMAnalysisRequest`，analyzer 消费有界 primary/supplementary evidence 内容和 compact coverage summary，不接收完整 vendor payload；完整 `EvidenceCoverageReport` 留作审计 |
| 17 | Prompt Builder + SOC prompt golden tests | Done | Prompt 只能从 `LLMAnalysisRequest` 生成；覆盖 PingAn APT/EDR、raw message 缺失 fallback、字段冲突；不把完整 raw payload 无脑塞进 prompt |
| 18 | LLM JSON parser + bad JSON repair | Done | 先严格 JSON parse，再 repair，再 Pydantic/domain validation；覆盖代码块、尾逗号、半截 JSON、字段类型错误 |
| 19 | 真实 LLM analyzer behind flag | Done | 已复用 DeerFlow `create_chat_model`；默认继续走 `analyze_stub`，显式 `SOC_ANALYZER_MODE=llm` 或 CLI flag 才调用模型；输出经过 prompt builder、JSON parser、schema/domain validation |
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
| 45 | Kafka consumer adapter planning | Done | 新增并归档 `.notes/archive/ai_soc/implementation-plans/kafka-consumer-adapter-plan.md`，明确 mapper/runner/offset/dead-letter/metrics 方案和下一刀 |
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
| 76 | First concrete safe read-only adapter | Done | 先接资产归属只读 adapter，验证 descriptor、dry-run、execute preflight 与审计 payload；不接封禁/隔离等写动作 |
| 77 | Read-only adapter dispatcher / tool gateway wiring | Done | 明确 `asset.lookup` 如何通过受控 route/tool gateway 进入运行态；默认不加入 chat router 白名单；结果必须写入 action result / audit payload |
| 78 | SOC Lead Agent read-only tool proposal bridge | Done | Lead Agent 只能通过结构化 envelope 请求 `asset.lookup` 等只读能力；bridge 转成同一条 router/policy/dispatcher/registry 链路；不直接调用 adapter/MCP |
| 79 | MCP adapter bridge / real read-only data source planning | Done | 规划真实资产、TI、安全标签或其他已确认 read-only tool 如何通过 adapter descriptor 接入；write/destructive 仍走 approval |
| 80 | MCP tool provider port + fake provider adapter tests | Done | 定义 SOC MCP provider port、fake provider 和 read-only MCP adapter skeleton；不接真实 MCP server |
| 81 | MCP-backed read-only `asset.lookup` adapter config builder | Done | 用 fake provider 固定显式配置到 MCP-backed `asset.lookup` adapter registry 的构造方式；不接真实 MCP server |
| 82 | SOC action package structure hygiene | Done | 将 action adapter、proposal、MCP adapter 收口到 `backend/soc_agent/actions/`，删除根目录旧入口；架构测试防止继续往根目录新增 action-like 模块 |
| 83 | DeerFlow cached MCP provider implementation | Done | 复用 DeerFlow MCP cache/session 生命周期，实现 `SocMcpToolProviderPort`；仍不让 Lead Agent 直接调用任意 MCP tool |
| 84 | Read-only config smoke wiring | Done | 支持 JSON/YAML 显式 adapter config 加载，`soc mcp smoke` 可验证 config -> registry -> DeerFlow cached provider -> action result |
| 85 | Dev/staging read-only MCP smoke report contract | Done | `soc mcp smoke` 输出 versioned report，记录 latency、failure、payload size、result size、tool/config 和 output_fields 裁剪信息 |
| 86 | MCP smoke readiness inventory | Done | `soc mcp tools` 可安全列出 DeerFlow cached MCP tools，`soc mcp smoke/tools --report-path` 可落盘报告；无 MCP config 时 tool_count=0 |
| 87 | Local real MCP fixture and read-only smoke | Done | 本地 stdio MCP server + sample extensions/action config 可验证 `soc_dev_asset_lookup` discovery 和 `asset.lookup` execute smoke |
| 88 | Real dev/staging MCP replacement | In Progress / PI-01 | 先接真实 PingAn 资产、TI、安全标签 DEV Provider，再替换对应 fixture、保存 smoke report 并评估延迟、失败率、字段裁剪和接入风险 |
| 89 | Upstream MCP sync compatibility retest | Done | 同步 upstream/main 后，SOC MCP adapter 显式传递 `mcp.server`，并重新验证 DeerFlow MCP 前缀重叠路由、local stdio discovery 和 `asset.lookup` execute smoke |
| 90 | Asset extraction skill + asset.locate MCP mock | Done | 根据资产提取/定位原型，新增 `soc-asset-extraction` skill、`asset.locate` read-only policy、mock MCP tool/config，并让 Lead Agent proposal bridge 可通过 MCP-backed adapter 执行只读定位 |
| 91 | Read-only action result evidence bridge | Done | 新增 `InvestigationEvidence` contract、repository protocol、in-memory store；read-only action success 后可记录 evidence，ReviewQueue context / Lead Agent artifact / Web/TUI 可展示 |
| 92 | InvestigationEvidence PostgreSQL persistence / Gateway wiring | Done | 新增 `soc_investigation_evidence` migration、ORM row、SQLAlchemy repository 方法；Gateway/CLI ReviewService 和 Lead Agent read-only dispatcher 使用同一 repository 共享 evidence |
| 93 | Lead Agent evidence reuse + endpoint process-tree mock adapter | Removed / superseded 2026-08-03 | evidence reuse 保留；未经证实且高耗时的进程树查询 mock、policy 和 proposal 已删除，改用告警原生 bounded evidence |
| 94 | PingAn SOC capability onboarding | Done | 新增 `.notes/ai_soc/capabilities/pingan/onboarding.md`，固定经验 -> capability card -> skill/MCP/normalizer/domain/eval/memory 的转化流程 |
| 95 | Correlation Service + Unified Report Bridge | Done | 结构化 correlation service/CLI 基于 summary + evidence 找历史 match；typed result 已进入 main report/domain/review summary；不调用 LLM、不改 Runtime decision |
| 96 | External Disposition Sync Contract MVP | Done | 固定外部预警/工单/处置系统状态与理由同步协议；新增 mapper/service/repository MVP，Zeus 只是 mock fixture |
| 100 | External Disposition Review/Correction Integration | Done | 高可信 mapped external disposition 在唯一定位本地 target 后复用 `SocReviewService.correct()`，同步 operational correction 并关闭 review queue；低可信/未知/无法定位不改判 |
| 97 | Memory Tracking Contract | Partial | `SocMemoryCandidate` 已完成 DB/API/ReviewQueue visibility 和 review workflow；`confirm` 会生成 retrieval-disabled `SocMemoryRecord`；retrieval policy/query/result/unified visibility MVP 已完成；TUI/Web/Kafka/Lead Agent/domain/external disposition 结论先生成 candidate，不直接写生效 memory；wiki/OKF 后期只做 projection |
| 98 | PingAn Domain Triage MVP | Done | 新增 `SocDomainTriageService` 和 APT/EDR/HIDS deterministic handlers；`soc eval pingan-domain` 可验证三类样本输出 domain findings、capability card refs 和 evidence refs |
| 99 | PingAn Main Orchestrator Demo | Done | `soc eval pingan-main` 验证 APT/EDR/HIDS historical + current analyze -> correlation -> skill -> read-only evidence -> domain finding -> review summary |
| 101 | Phase 2 Correlation Eval Baseline | Done | 新增版本化 scorer ID、same/related/unrelated pair corpus、双任务 precision/recall、reason/fan-out/evidence 报告和 replay diff；不启用 dedup suppression |

## 进度记录

### 2026-07-31 — Legacy Zeus audit narrowed to alert flows

- 按用户确认，将旧实现审计范围收紧为 `validation/original_works/zeus/flows/` 的告警研判主线；
  EML/二维码、真实 CMDB/EDR/TI/标签接通、NL2SQL、Chat BI 和邮件 Agent 不再列为当前缺口。
- 重写 `capabilities/pingan/legacy-zeus-capability-extraction.md`：逐文件覆盖 controller、schema、
  description、APT/EDR/HIDS/NIDS/fallback flows 和 disposition helpers，分别标记 `Replaced`、
  `Partial/D7`、`Excluded`，并映射到当前 Runtime、Skill、correlation、evidence、policy、approval
  与 report contracts；`flows/` 的 14 个顶层 Python 文件和 12 个 disposition helper 文件共
  26/26 均已完成去向审计。这里的“入表”只表示已审阅并明确保留、替代或排除，不表示照搬或迁移
  26 个旧 Flow。
- 固定 D7 carry-forward checklist：真实 analyzer 输出必须保留场景、证据引用、尝试/效果/影响层级、
  历史上下文边界、证据缺口和人工核查，但不复制旧 LlamaIndex 控制流、攻击链/时间线或处置动作。
- 审计确认两项不能宣称已完成的边界：`LLMAnalysisRequest.v2` 尚不包含 correlation/memory，历史
  当前在分析后进入 orchestrator/review；`AnalysisResult` 尚无 typed LLM-discovered scenario
  candidate，D7 必须决定新增契约或证明后置 finding 层足够。
- 该记录当时的下一步 D7 已完成；当前权威下一步是 D8 evidence grounding，不插入旁支功能开发。

### 2026-07-31 — PI-01 Checkpoint D-5/D-6 and SOC Skill package projection completed

- 完成旧 Zeus `flows/` 一级能力盘点和迁移卫生：
  - 删除 ignored 参考目录中的 163 个 `Zone.Identifier` 与 68 个缓存文件；
  - `.gitignore` 阻止 Windows sidecar 再进入仓库；
  - 新增 `capabilities/pingan/legacy-zeus-capability-extraction.md`，固定 Skill、Adapter、MCP、
    governed context、policy/eval、report 和明确不迁移内容的归属。
- 修复通用实体契约：`ExtractedEntities.hosts` 只保留 `EntityKind.HOST`，资产 ID/组进入新增
  `assets`；样本 `1965449` 现在是 `hosts=[]`、`assets=[平安健康险]`，证据 mention/path 仍完整。
- 重构 public SOC Skill taxonomy：
  - `soc-waf-f5-triage` 改为 vendor-neutral `soc-web-application-triage`；
  - 新增 `soc-email-phishing-triage`；
  - network、endpoint、web、email 增加从旧 Zeus 泛化的场景 references，不包含平安字段、
    白名单、组织事实或绝对成功规则。
- `SocSkillContext.v2` 改为实际 Skill package 投影：Resolver 只选白名单 Skill；materializer 使用
  DeerFlow parser 校验 package，只读取 `references/runtime-guidance.md`，记录 package/guidance
  hash、来源、估算 token 与预算，不维护硬编码摘要表、不注入完整 `SKILL.md`。
- Resolver 通过首次全语料失败报告收紧：typed HTTP/email/canonical observations 驱动专业路由；
  `tentative/unresolved` 角色不再使 direction Skill 常驻；任意 IP/file/asset name 不再证明
  network/endpoint 行为。首次 D6 的 212 条 direction 全选和 9 条 asset-only endpoint 误路由均已
  修复后重跑。
- D6 人工语义审阅暂存一项非阻塞风险：部分 HIDS 告警仅因宽泛关键词 `恶意` 选择
  network/APT Skill，并因未区分端点与 Web 语境的 `命令执行` 选择 Web Skill。D6 的当前
  `passed` 只证明路由、package 投影和覆盖统计机械一致；后续应优先使用 typed
  evidence/scenario context 收紧跨域关键词，不把固定路由 confidence 当成研判概率。
- 新增并实跑：
  - D5 `1965449`: `soc-network-apt-triage`、`soc-web-application-triage`、
    `soc-alert-triage`，3 个真实 package、当次 313 estimated tokens、13 项 check 全通过；
    D7 前随 runtime guidance 增量重跑后的当前投影为 387 estimated tokens；
  - D6: 212/212 processed、0 failure、0 typed HTTP/email miss、0 package mismatch、0
    asset-only endpoint misroute；selection counts 为 alert 212、network 170、web 96、endpoint 67、
    direction 8、email 6；其中 3 条 threat-intel 告警因 typed `host.ip_addresses` 正确选择
    endpoint Skill，未把 `asset_group` 当成主机；
  - `./scripts/soc-runtime-validation.sh checkpoint-d` 可单命令确定性重跑 D0-D6，不调用 LLM。
- 验证：
  - Ruff、`git diff --check`、`bash -n scripts/soc-runtime-validation.sh` 全部通过；
  - SOC/architecture/Checkpoint D/public Skill 回归得到 657 passed，唯一 cwd 相对路径失败按
    `backend/` 模块约定重跑后 3 passed，因此 658 个唯一测试均通过；
  - Checkpoint D 编排最终 `exit=0`。
- 该记录当时要求先审阅 gitignored
  `checkpoint-d/step-d5-skill-context/1965449.skill-context.json`；D7 已完成，当前按
  `delivery-roadmap.md` 进入 D8 evidence grounding，不先扩展更多 Skill 或 mock。

### 2026-07-30 — PI-01 Checkpoint D-4 bounded analysis input completed

- D-3 已获用户确认；D-4 继续使用 canonical row `1965449/sec_guard_apt`。
- 新增 D-4 构建器：
  - 重放 D1-D3 并分别校验 normalized semantics、entities、facts hash；
  - 调用生产 `build_llm_analysis_request()`，输出完整 `LLMAnalysisRequest` 与
    `EvidenceCoverageReport`；
  - 验证 raw payload 未修改、structured fallback 未进入 message-first 分析、parsed/decoded/
    repaired 路径均被 projected/sanitized/omitted 之一覆盖；
  - 明确不运行 skill resolution、Prompt、LLM、grounding、decision 或 persistence。
- 用户明确当前是内部 PingAn SOC、由安全运营人员处理，因此本地 gitignored D-4 使用显式批准的
  `full` mode；通用 Runtime 默认仍为 `redact`。修复 full mode coverage 误报：已选字段不额外脱敏，
  `llm_sanitized_count=0`。源数据自身掩码保持不变。
- D-4 结果：25 项链路/边界/coverage check 全部通过，状态
  `passed_with_coverage_findings`；93 parsed、35 decoded、1 repaired、119 projected、0 sanitized、
  4 encoded-compacted、11 adapter-excluded omissions、0 high-value gap。`detail_info`、`vuln_desc`、
  headers、request/response body 均被投影；4 个超长 JWT 路径仅做 bounded-context compaction，
  完整值仍在 immutable raw payload。
- 可审阅产物：
  - `backend/.deer-flow/soc-runtime-validation/checkpoint-d/step-d4-bounded-analysis-input/1965449.analysis-input.json`
    （敏感、gitignored）。
- 下一步：等用户审阅 D-4；确认后进入 D-5，只运行 deterministic SOC skill resolution，不渲染
  Prompt、不调用模型、不执行 grounding/decision/persistence。

### 2026-07-30 — FieldTrust source trust / reasoning eligibility split completed

- 根据 D3 人工审阅结论重构 `FieldTrust`，不再用 `trust_level=low` 表达“该字段不参与推理”：
  - `source_trust` 只记录来源可信度；
  - `reasoning_status` 区分 `selected_evidence`、`supplementary_evidence`、
    `included_canonical_projection`、`excluded_unselected_fallback` 与
    `excluded_duplicate_projection`；
  - `participates` 明确是否作为独立事实来源，并由 schema validator 保证与 status 一致。
- canonical source/destination 从 `CanonicalFieldProvenance` 继承 source trust。样本 `1965449`
  的两个 canonical IP 现在均为 `source_trust=high`，但标记
  `excluded_duplicate_projection/participates=false`，避免同一 message 证据重复投票。
- 未选中的 Zeus structured fallback 保持
  `source_trust=unknown/excluded_unselected_fallback/participates=false`；实际 selected structured
  fallback 仍复用 evidence policy trust。
- D3 本地 artifact schema 升级为
  `soc.validation.checkpoint_d.fact_reconstruction_review.v2`，增加 reasoning-status 计数与
  provenance/source-trust 一致性检查。
- 重新生成 D1-D3：D1/D2 语义与 12 mentions 未变；D3 `passed`，4 条 FieldTrust 中仅 selected
  message 参与，状态计数为 1 selected、1 unselected fallback、2 duplicate projection。
- 验证：相关 Runtime/Parser/Prompt/Grounding/Checkpoint 测试 `90 passed`；完整
  `validation/compact_zeus` 测试 `20 passed`；Ruff check/format passed。
- 下一步保持不变：继续人工审阅更新后的 D3 JSON；确认后进入 D4 bounded analysis input 与
  `EvidenceCoverageReport`，不运行 skill/LLM/decision。

### 2026-07-30 — Compact Zeus validation source layout organized

- 将原先平铺在 `validation/compact_zeus/` 的验证代码按职责迁移到：
  - `checkpoint_d/`：D0-D3 单告警 Runtime 逐步重放与契约测试；
  - `corpus/`：canonical corpus 与压缩报告构建；
  - `audits/`：各 PingAn topic/Adapter 的批量字段流向审计；
  - `reviews/`：Checkpoint B/C 人工审阅样本构建；
  - `shared/`：受限 pickle loader 与 encoded-context 验证工具；
  - `docs/`：长期设计与审阅说明。
- 每个源码目录新增简短 `README.md`，记录职责、依赖边界、入口、输出位置与敏感数据要求；
  根 README 作为总导航。`validation/compact_zeus/data/` 与
  `backend/.deer-flow/soc-runtime-validation/` 的生成物路径保持不变。
- 修正全部 Python imports、脚本 `ROOT` 解析和活动 AGENTS/方案/进度文档命令；生产代码仍禁止
  反向导入 `validation.*`。
- 使用新路径完整重放 D0-D3：
  - D0 `passed_with_known_input_gaps`，212 rows / 212 unique IDs；
  - D1 `passed_with_parser_warnings`，message-first NDR normalization；
  - D2 `passed_with_extraction_warnings`，12 mentions；
  - D3 `passed`，36 provenance / 5 role claims / 0 conflicts。
- 产物链未漂移：D1 semantic hash 为
  `a46c94e80f40c20cfe6528e8791d1fd3b50fbb0b94f34e0bd33e70476d8b7a98`；D2 entity hash 为
  `32aa63289f959ad25236f0031bfff4da8cf1016a363270c2d4c229eba6179243`；D3 replay hash 均匹配。
- 验证：`backend/.venv/bin/python -m pytest -q validation/compact_zeus` -> `20 passed`；
  Ruff check/format passed。
- 下一步保持不变：由用户审阅 D3 JSON；确认后进入 D4 bounded analysis input 与
  `EvidenceCoverageReport`，不运行 skill/LLM/decision。

### 2026-07-30 — PI-01 Checkpoint D-3 single-alert fact reconstruction completed

- D-2 已获用户确认；D-3 继续使用 canonical row `1965449/sec_guard_apt`。
- 新增 D-3 构建器：
  - `validation/compact_zeus/checkpoint_d/build_checkpoint_d_fact_reconstruction_review.py`；
  - 重放生产 normalization/entity extraction，分别与 D1 normalized semantics、D2
    `ExtractedEntities` 做 hash 校验后调用生产 `reconstruct_facts()`；
  - 输出完整 `FactReconstructionResult`，不构建 analysis input，不运行 skill、LLM、grounding、
    decision 或 persistence。
- D-3 审阅中修复两处生产契约问题：
  - 未选中的 `fallback_input_path` 过去被错误标成参与事实重建；现在 message-first 成功时只保留
    `unknown` trust、`participates=false` 的审计记录，实际 selected structured fallback 复用唯一
    selected-input trust；
  - NDR provenance 过去可能把值不同的 `rule_desc` 误写成 canonical `rule_name` 来源；现在只有与
    canonical 选择规则一致的 message 字段才产生该 provenance，本样本伪来源已删除。
- D-3 结果：
  - 16 项 chain/policy/trust/role/automation check 全部通过；D1/D2 hash 未漂移，raw 未修改；
  - 4 条 `FieldTrust` 中仅 selected high-trust message 参与；structured fallback 与两个 canonical
    processed direction field 均不参与；
  - 5 条 claims：`source/destination` 是 `observation`、semantic confidence `0.9`；
    `attacker/victim` 是供应商断言，`impacted_asset` 是 Adapter 推导候选，三者均为 `0.5`；
  - resolutions 为 2 个 `observed`、3 个 `tentative`，全部 `automation_allowed=false`；
  - 场景仅有 tentative `web_attack`，confidence `0.72`，包含 `detail_info` 等 8 条 evidence path；
    0 conflict、0 warning；真实 message canonical provenance 为 36 条。
- 可审阅产物：
  - `backend/.deer-flow/soc-runtime-validation/checkpoint-d/step-d3-fact-reconstruction/1965449.facts.json`
    （敏感、gitignored）。
- 验证：
  - D0-D3 focused suite：`5 passed`；
  - `backend/tests/test_soc_agent_runtime.py`：`36 passed`；
  - `backend/tests/test_soc_pingan_message_parsing.py`：`37 passed`；
  - Ruff check/format passed。
- 下一步：
  - 等用户审阅 D-3；确认后进入 D-4，只构建 bounded analysis input 与
    `EvidenceCoverageReport`，重点验证 `detail_info`、`vuln_desc`、headers/body、sanitization、
    compaction 和 omission，不调用模型。

### 2026-07-29 — PI-01 Checkpoint D-2 single-alert generic entity extraction completed

- D-1 已获用户确认；D-2 继续使用同一 canonical row `1965449/sec_guard_apt`。
- 新增 D-2 构建器：
  - `validation/compact_zeus/checkpoint_d/build_checkpoint_d_entity_extraction_review.py`；
  - 通过 Runtime 公开 `inspect_alert_normalization()` 边界执行生产 normalization + generic
    deterministic entity extraction，并将 normalization 语义 hash 与 D-1 对比；
  - 完整 hash 仍保留审计；仅当上游没有接收时间时，语义比较允许
    `event.received_at` 这一项运行时生成值不同，任何其他 normalized 字段变化都会失败；
  - 不执行 fact reconstruction、analysis input、skill、LLM、decision 或 persistence。
- D-2 结果：
  - 9 项 chain integrity / raw immutability / entity report check 全部通过；本次真实样本连
    `event.received_at` 也完全一致；
  - 共 12 个 deterministic mention，全部带 `confidence=1.0` 和 evidence path：2 IP、2 domain、
    2 URL、1 asset、1 rule code、1 rule name、1 detection key、2 MITRE；
  - 去重后的实体值包括 `10.28.121.248`、`30.184.42.99`、
    `ehis-dataplus-stg.paic.com.cn`、`/api/user/sign-in`、资产组 `平安健康险`、规则与
    `TA0001/T1190`；
  - `process/user/host` 未抽取，其中生产 extractor 只对 process 生成 warning；对 NDR/APT
    网络样本这是显式 extraction gap，不等同于 Adapter 或告警失败；状态为
    `passed_with_extraction_warnings`。
- 可审阅产物：
  - `backend/.deer-flow/soc-runtime-validation/checkpoint-d/step-d2-generic-entity-extraction/1965449.entities.json`
    （敏感、gitignored）。
- 验证：
  - D-0/D-1/D-2 focused suite：`4 passed`；
  - Ruff check/format 与 `git diff --check` passed。
- 下一步：
  - 等用户审阅 D-2；确认后进入 `PI-01 Checkpoint D-3`，只运行 fact reconstruction，并再次
    停下审阅。

### 2026-07-29 — PI-01 Checkpoint D-1 single-alert canonical normalization completed

- 代表样本：
  - `alert_id=1965449`、`topic=sec_guard_apt`、expected source type `ndr`；
  - canonical row 来自权威 PKL；历史 JSON 是 `conflict_pkl_authoritative` lineage，不作为本次输入。
- 新增 D-1 构建器：
  - `validation/compact_zeus/checkpoint_d/build_checkpoint_d_normalization_review.py`；
  - 只选择一条 canonical `alert_data` 并调用生产 `normalize_alert_payload()`；
  - 输出完整本地 `normalized_alert`、parser 摘要、evidence policy、canonical provenance 和 raw
    hash checks；不读取历史 `agent_response`；
  - 不运行 generic entity extraction、fact reconstruction、analysis input、skill、LLM、decision
    或 persistence。
- D-1 结果：
  - PingAn adapter 与 `ndr` source type 选择正确；`raw_message_first/high` 选中
    `alert.hitLog[0].zeusRawLogs[0].message`，processed sibling fields 标记为 reasoning-excluded；
  - 14 项 canonical/lineage/immutability check 全部通过；输入 hash 未变化，`normalized.raw`
    与 canonical `alert_data` 完全一致；D3 provenance 校正后 canonical provenance 为 36 条；
  - parser `pingan_delimited_json/v2` 从 4,522 字符 message 提取 70 个字段；2 个 nested
    top-level field strict decoded，`rsp_body` 保守 repair accepted，`req_body` 因引入无源 key
    repair rejected；共有 4 条明确 parser warning；
  - 状态因此为 `passed_with_parser_warnings`，不是无条件 `passed`；repaired value 不冒充 strict
    decoded/source fact，原始字符串仍完整保留。
- 可审阅产物：
  - `backend/.deer-flow/soc-runtime-validation/checkpoint-d/step-d1-canonical-normalization/1965449.normalization.json`
    （敏感、gitignored）。
- 验证：
  - D-0 + D-1 focused tests：`3 passed`；
  - Ruff check/format passed。
- 下一步：
  - 等用户审阅 D-1；确认后进入 `PI-01 Checkpoint D-2`，只对同一 normalized alert 运行
    generic entity extraction，并再次停下审阅。

### 2026-07-29 — PI-01 Checkpoint D-0 corpus inventory completed

- 清理了旧 Adapter 生成的本地 SOC Runtime/Alpha/Boss Demo 产物和隔离 SQLite；保留
  DeerFlow `deerflow.db`、用户、memory 与 JWT 运行数据。
- 新增 adapter-independent D-0 构建器：
  - `validation/compact_zeus/checkpoint_d/build_checkpoint_d_corpus_inventory.py`；
  - 通过 restricted unpickler 读取权威 corpus；
  - 只检查 corpus hash、212 个唯一 ID、wrapper/payload ID、topic/source family、
    `hitLog`、`zeusRawLogs` 和非空 `message` 可用性；
  - 不解析 message，不导入或调用 PingAn Normalizer、Runtime、LLM、Decision Policy 或
    persistence；逐行记录不复制原始 message 值。
- D-0 真实语料结果：
  - `212/212` rows，`212` unique alert IDs，0 duplicate、0 global issue、0 blocking row；
  - `raw_message_available=200`、`structured_fallback_candidate=10`、
    `evidence_unavailable=2`；
  - 343 个 message 字段全部为非空 string；共 215 个 HitLog、358 个 raw event；
  - 6 个预期来源族：`nids=95, ndr=44, edr=37, hids=23, siem=10,
    threat_intel=3`；
  - 两个已知上游缺口为 `1965452/sec_guard_apt` 与 `1965795/leagsoft-edr`：均有 HitLog，
    但 `zeusRawLogs` 为空，不归因于 Adapter。
- 可审阅产物：
  - `backend/.deer-flow/soc-runtime-validation/checkpoint-d/step-d0-corpus-inventory/corpus-inventory.json`
    （gitignored）。
- 验证：
  - `backend/.venv/bin/python -m pytest -q validation/compact_zeus/checkpoint_d/test_build_checkpoint_d_corpus_inventory.py`
    -> `2 passed`；
  - Ruff check/format passed；
  - D-0 实跑状态为 `passed_with_known_input_gaps`。
- 下一步：
  - 等用户审阅 D-0；确认后进入 `PI-01 Checkpoint D-1`，只取一条
    `raw_message_available` 代表样本检查 canonical normalization，再次停下审阅。

### 2026-07-27 — PI-01 PingAn NDR/APT / HIDS Checkpoint C completed

- 真实语料覆盖：
  - NDR/APT：44 alerts / 105 messages，生成 105 network observations、63 HTTP
    observations、20 `observed_artifact` file observations；43 条具有 canonical wire
    source/destination；
  - HIDS：23 alerts / 46 messages，生成 44 process observations / 122 process nodes、21
    file observations 和 5 个事件契约限定的 network observations；
  - 两类均为 0 known high-value gap、0 raw payload mutation。
- Adapter 契约：
  - 新增 `normalizers/pingan_ndr.py` 与 `normalizers/pingan_hids.py`，字段 alias 不进入 generic
    Runtime；
  - NDR `ioc` 经全语料确认是厂商检测描述，不按值形状冒充 typed IOC；`file_name/file_md5`
    只形成网络内容 artifact，不证明终端落盘；
  - HIDS endpoint identity 与 packet direction 分离；`external_ip=1.1.1.1` 是 non-reasoning
    placeholder；只有 `bounce_shell/honeypot/malic_opera` 产生 event-scoped network
    observations，canonical source/destination 保持为空；
  - 补充消息中的 file/user/process summary 仍保留 exact provenance；HIDS `ppid` 即使没有
    parent name 也通过通用 `parent_process_id` contract 保留；
  - message 与 Zeus 外层加工字段不再混合：任意 message 确定性解析成功后，外层字段只留在
    immutable raw，不进入 canonical/fact/scenario/conflict/LLM；仅零条 message 可解析时启用
    structured fallback。
- 覆盖监控修正：
  - `EvidenceFieldImportanceRegistry` 只匹配非空 leaf，避免空字符串/null 制造假 mapping gap；
  - `LLMAnalysisRequest v2` 增加 generic `BoundedEvidenceHighlight`：首条 primary + 最多四条
    full supplementary 之外的 adapter-declared 高价值字段，仍按 exact path、敏感模式和总量
    预算进入模型上下文；重复值保留 occurrence count 与最多 5 个代表路径，完整覆盖路径只留在
    `EvidenceCoverageReport`，避免路径元数据挤占 Prompt；
  - `participates_in_reasoning=false` 已从 Prompt 约束升级为投影硬过滤，并记录
    `adapter_excluded_from_reasoning`；HIDS 默认 `1.1.1.1`、NIDS 未解释 result code 不再进入
    bounded content；
  - v3 实例级审计按 leaf name 覆盖嵌套 `_origin.*`/`payload.*`，不再因完整路径不同漏算；当前
    NDR/HIDS 8,436 个非空 parsed leaf 实例全部有 typed consumer 或 exact semantic，未分类数、
    high-value instance gap、structured-fallback violation 均为 0；后续出现任何非空未分类字段
    会直接使 corpus audit 失败并进入 mapping maintenance。
- 可复跑产物：
  - `build_pingan_ndr_hids_field_audit.py`；
  - `build_pingan_ndr_hids_review_artifacts.py` 生成 11 份敏感本地代表样本；
  - `test_build_pingan_ndr_hids_field_audit.py` 锁定 strict message-only、嵌套 HTTP/漏洞字段、
    placeholder、方向和 IOC 边界；生成数据已 gitignore。
- 本地语料布局：
  - 权威输入移动到 `datas/source/`，5 个历史 JSON 移动到 `datas/legacy_demos/`；
  - gitignored 验证产物统一分到 `validation/compact_zeus/data/{corpus,audits,reviews,compaction,exploration}/`；
  - 长期说明移动到 `validation/compact_zeus/docs/`，所有构建器、Runtime 验证脚本和报告引用
    已切换到新路径，根目录不再混放 notebook、HTML、Excel 或敏感审阅 JSON。
- 验证：
  - 212 条 corpus rebuild：`edr=37, hids=23, ndr=44, nids=95, siem=10,
    threat_intel=3`，8 个 topic 无 `other`；
  - NIDS、NDR/HIDS、EDR、TI/SIEM 四组全量 field audit 均 passed；
  - SOC tests：`584 passed`；architecture/migration：`17 passed`；compact validation fixtures：
    `15 passed`；四组真实 field audit 均 passed；
  - 新目录下重新生成 212-row corpus、四组 audit、五组 review artifacts 和 compaction 报告；
    `./scripts/soc-runtime-validation.sh core` 已从 `datas/legacy_demos/` 成功重建 Steps 01-05；
  - Ruff format/check、`git diff --check` passed；`codegraph sync .` 已纳入两个新 Adapter。
- 下一步：
  - `PI-01 first approved read-only provider`：选择真实 dev/staging CMDB、EDR 或 TI endpoint，
    不继续用新增本地 mock 冒充 provider 完成。

### 2026-07-27 — PI-01 PingAn Threat Intel / SIEM Checkpoint C completed

- Threat Intel adapter：
  - 新增 `normalizers/pingan_threat_intel.py`，3 条告警 / 4 个 message 全量重放生成 4 个独立
    network observations；nested `net.*` 作为 wire session，provider `attacker/victim` 作为独立
    vendor assertions，flattened Zeus copies 不再制造重复角色冲突；
  - 3/3 条均投影 monitored host、external IOC、malware family 和 MITRE `T1496`；
    `assets.ip` 的 CIDR/range 明确为 asset scope，不进入 host IP；`result=success`、
    `is_black_ip`、provider severity/level/score 均为 typed source semantics，不作为攻击成功或
    Runtime confidence。
- SIEM adapter 与通用 contract：
  - 新增 `normalizers/pingan_siem.py` 和 optional `EmailEntityRef/EmailObservationRef`；
    `suspicious_email` 6 alerts / 7 events 形成 6 个 email observations，deterministic extractor
    只从 canonical email 生成 email/domain/URL mentions；body 与 `llm_ans/llm_score` 保留为
    bounded upstream-model evidence，`User=system` 不成为 actor；
  - `standard_machine_copy` 4 alerts / 8 events 形成 host name/IP candidates；不生成
    source/destination/attacker/victim 或 network observations；未知 subtype 保留 selected
    structured evidence 并报告 mapping gap，不猜实体；
  - `EvidenceFieldImportanceRegistry` 增加通用 selected `raw_structured` source view，
    `structured.*` high-value rules 现在真正参与 coverage/maintenance，不再只检查 parsed message。
- 可复跑证据：
  - `build_pingan_ti_siem_field_audit.py` 重放 10 SIEM / 15 structured events 与 3 TI / 4
    messages，结果为 159 canonical provenance、0 high-value gap、0 invented SIEM direction、
    0 pipeline-actor leak、0 raw payload mutation；
  - `build_pingan_ti_siem_review_artifacts.py` 生成 TI 单/多 message、SIEM email/machine-copy
    四份 `full` 模式本地 JSON；输出位于 gitignored
    `validation/compact_zeus/data/reviews/pingan-ti-siem-checkpoint-c/`，不得提交。
- 验证：
  - PingAn parser + normalization maintenance + validation 聚焦回归：`42 passed`；
  - 完整 SOC + architecture 回归：`589 passed`；compact Zeus validation：`13 passed`；
  - 全量 TI/SIEM field audit：`status=passed`；212-row corpus rebuild：`status=passed`、
    6 source types、0 unexpected `other`；Ruff passed。
- 下一步：
  - Checkpoint C 不再继续扩张本地 mapping；按 `delivery-roadmap.md` 选择第一项经过批准的
    read-only dev/staging provider；真实 endpoint/topic、认证、tenant mapping、approved payload
    和 data owner 仍是外部输入，不能用新 mock 冒充完成。

### 2026-07-27 — PI-01 PingAn EDR Checkpoint C completed

- 语料与字段审计：
  - 重放 37 条 EDR 告警、60 个 message；其中 5 条 `edr-core-xc` 包含 14 个 message、
    21 个 nested `detailsN` 记录；31 条 `leagsoft-edr` 继续覆盖既有 flat KV 路径，另有
    1 条 message-less structured fallback；
  - Adapter 生成 30 个 process observations、39 个 process nodes 和 7 个 file
    observations；5 条信创 EDR 的 endpoint/process/user/MITRE 信息进入 canonical 与
    observation provenance，不再只对 LLM 可见；
  - 19 个合法 MD5 与 19 个合法 SHA-256 可进入实体；2+2 个短值保持在 parsed/bounded
    evidence，并通过 `invalid_process_hash` 禁止进入实体、hash mention 和 provenance；
  - flat EDR 的 40 个 message 均有合法 `str_source_ip`，但 message 内 `device__ip` 为 0；
    37 个非空 `str_attack_ip` 中 33 个等于 endpoint，`str_threat_value` 有 36 个、
    `str_activity_id` 有 38 个是 32 位 digest-shaped vendor value，证明这些字段不能直接映射
    wire destination/hash；
  - 纠偏后 canonical network source/destination 和 directional network observations 均为 0，
    endpoint IP 覆盖仍为 36/37；raw payload mutation count 为 0，EDR typed high-value gap 为 0。
- Adapter/contract：
  - 新增 `normalizers/pingan_edr.py`，集中管理 `detailsN`、历史 `process_mame` typo、
    observation、provenance、field-importance 和 source-field semantics；generic Runtime 不识别
    PingAn aliases；
  - canonical process/file 保留单值摘要，多 message/detail 使用稳定 evidence path 的
    `ProcessObservationRef` / `FileObservationRef` 回放；child process 不覆盖父进程；
  - `iplist`、`str_source_ip`、`device__ip` 只形成 endpoint host IP 与 provisional
    victim/impacted-asset claims，不生成 network source/destination；合法且不同于 endpoint 的
    `str_attack_ip` 只生成 typed IOC 与 tentative vendor attacker candidate；
    message 与 structured fallback 会在同一 raw-event observation scope 内交叉排除 endpoint，
    字段分层时不会制造假 remote IOC/attacker；
    `str_threat_value`/`str_activity_id` 不按字符串形状生成 destination/hash；
  - file/registry/task/existence/MITRE 只作为 typed investigation context，不自动证明恶意或
    攻击成功；没有显式 directional connection contract 时，EDR network observations 保持空；
  - `ProcessEntityRef`/`ProcessNodeRef` 增加 process ID 与合法 hash，`FileEntityRef` 增加
    file observations；deterministic extractor 读取标准 observation 和 typed threat IOC，不读取
    vendor 字段。
- 可复跑产物：
  - `validation/compact_zeus/data/audits/pingan-edr-field-audit.{before,after}.json`；
  - `validation/compact_zeus/data/reviews/pingan-edr-checkpoint-c/{before,after}_adapter_mapping/` 五组
    代表样本；生成数据均 gitignored、包含敏感真实告警，不提交；
  - 构建入口为 `build_pingan_edr_field_audit.py` 和
    `build_pingan_edr_review_artifacts.py`，合成 contract 回归为
    `test_build_pingan_edr_field_audit.py`。
- 验证：
  - PingAn EDR/authorization/validation 聚焦回归：`45 passed`；validation corpus/NIDS/EDR：
    `11 passed`；
  - SOC architecture boundary：`11 passed`；完整 SOC 回归：`585 passed`；Ruff passed；
  - `codegraph sync .` 完成，并可查询 `edr_attacker_candidates`、跨层 endpoint 回归测试及
    新增 EDR adapter symbols；
  - 212 条 corpus rebuild：`edr=37, hids=23, ndr=44, nids=95, siem=10,
    threat_intel=3`，无 unexpected `other`。
- 下一步：
  - 继续 `PI-01 Checkpoint C / TI + SIEM`，先区分 Threat Intel observation、内部模型输出和
    trusted structured fallback 的字段语义，再补 canonical/fact/scenario/LLM coverage；
  - 真实 CMDB/EDR/TI provider endpoint/credential 仍 data-gated，不用新增 mock 冒充完成。

### 2026-07-24 — PI-01 all-topic encoded-context production boundary

- 将长编码压缩固定在生产
  `backend/soc_agent/pipeline/encoded_context.py`；`validation` 只作为调用方，
  新增 architecture test 禁止 `backend/soc_agent` 导入 `validation.*`。
- 压缩发生在共享 primary/supplementary bounded-evidence 边界，不依赖 source type 或
  topic；代表测试覆盖 PingAn 当前 8 个 topic，包括 message-first 与 structured fallback。
- 占位符格式为
  `<ENCODED:type:length:sha256=12-char-prefix:OMITTED>`，审计侧车保留 path 与完整
  SHA-256；二者均不能成为 grounded analyzer evidence。
- 212 条真实语料重放：
  - 212/212 条完成生产 LLM projection，8/8 topic 无绕过；
  - 112 条共压缩 210 段：NIDS 180、APT 8、APT Detail 3、HIDS 19；
  - EDR/SIEM/Threat Intel 当前样本无命中，但均已执行同一检查；
  - `raw_payload_mutation_count=0`，policy contract violations 为 0。
- 验证：
  - all-topic corpus + NIDS audit：`9 passed`；
  - encoded-context/grounding/PingAn/architecture 聚焦回归：`46 passed`；
  - 完整 SOC 回归：`570 passed`；SOC architecture boundary：`11 passed`。

### 2026-07-24 — PI-01 PingAn NIDS Checkpoint C completed

- 语料与字段审计：
  - 重放 95 条 NIDS 告警、128 个 `pingan_json_object` message；95/95 canonical 五元组完整；
  - 生成 128 个独立 network observations、67 个 HTTP observations（35 条告警），15 条多五元组
    告警保持多 observation，不折叠成单一会话；
  - `query` 保留为 bounded sensor context，未伪装成 DNS/domain；当前 typed high-value gap 为 0；
  - 81/95 告警产生 deterministic scenario hypothesis，未命中 taxonomy 的文本仍进入受控 LLM evidence。
- Adapter/contract：
  - canonical network 增加 application protocol；network observation 保留 direction、community/flow ID、
    规则相对 source/target、zone 和双向 byte/packet；
  - 新增 per-message `HttpObservationRef`，映射 method/host/path/protocol/port/status/UA/referer/XFF；
  - nested `alert.signature/category` 映射 detection，sensor result/severity/signature ID 保留为显式 labels；
  - Adapter 输出 generic canonical provenance、typed field-importance rules 和
    `SourceFieldSemantic`；generic fact reconstructor 只校验/合并 contract，不识别 PingAn aliases。
  - 15 条含 `files[]` 的告警将其保留为 network-transaction metadata，并显式声明它不证明终端文件
    写入；审阅产物直接展示 source-field semantics。
- 编码边界：
  - 将 `compact_encoded_llm_context.py` 的内容检测算法提取为生产
    `soc_agent.pipeline.encoded_context`；Runtime 与验证脚本共用同一实现；
  - NIDS 子集只压缩 LLM-bound evidence，不解码、不修改 raw/parsed input；92 条告警共记录
    180 个 typed omission spans；全 topic 结果见上一节；
  - marker 保留 kind/length/短 hash，path/kind/length/完整 hash 侧车保留在 request/run
    audit 但不进入 prompt；marker 与侧车均从 evidence grounding 排除，不能成为事实。
- 可复跑产物：
  - `validation/compact_zeus/data/audits/pingan-nids-field-audit.json`；
  - `validation/compact_zeus/data/reviews/pingan-nids-checkpoint-c/{before,after}_adapter_mapping/` 四组代表样本；
  - 生成数据均 gitignored、包含敏感真实告警，不提交。
- 验证：
  - PingAn + prompt + encoded-context + grounding 聚焦测试：`38 passed`；
  - NIDS audit/corpus validation：`8 passed`；
  - 完整 SOC 回归：`570 passed`；SOC architecture boundary 当前为 `11 passed`；
  - 212 条 corpus rebuild：`edr=37, hids=23, ndr=44, nids=95, siem=10, threat_intel=3`，无
    unexpected `other`。
- 下一步：
  - 继续 `PI-01 Checkpoint C / EDR`，先审计全部 EDR parsed leaf 是否进入
    canonical/fact/scenario/LLM/audit，再只在 PingAn Adapter 补充已确认 mapping。

### 2026-07-24 — PI-01 PingAn structured fallback bounded evidence

- 用户确认的输入与信任边界：
  - `zeusRawLogs[].message` 不存在时，只把第一条 structured raw event 投影给当前分析节点；
    后续 raw events 继续完整保存在 `AlertInput.raw` / run input 中，不进入当前模型上下文。
  - structured fallback 默认 `low trust`；当前唯一例外是 exact topic
    `T_GBD_zeus_data`，作为已确认的内部 SIEM/模型来源使用 `high trust`。
  - source type、缺少 `message`、相似 topic 名称和 topic prefix 均不能自动提权。
  - parser/repair 保持原始 password/token/cookie/header/body 值；通用模型边界默认
    `redact`，当前经批准环境显式配置
    `SOC_LLM_SENSITIVE_EVIDENCE_MODE=full`。
- 实现：
  - `BoundedAnalysisEvidence v3` 新增显式 sensitive mode；structured fallback 使用逐字段、
    总预算受限的 JSON projection，`full` 模式不改写已选字段值，超预算项进入 omission。
  - `EvidenceCoverageReport v3` 增加 structured field paths，并从真实 bounded projection
    生成 projected/sanitized/omitted/truncated 结果。
  - CLI/Runtime/DeerFlow LLM settings 与 daemon compose/K8s 配置共享同一 mode；部署默认仍是
    `redact`，本地私有 `.env` 使用 `full`。
  - PingAn Adapter 使用 exact-topic trust allowlist，反向测试锁定其他 topic fallback 仍为
    `low`。
- 212 条 replay：
  - 200 条 `raw_message_first`、12 条 `structured_fallback`，policy violation 为 0。
  - 12 条 fallback 中 10 条存在首条 raw event，共投影 164 个 structured leaf fields；
    2 条 `zeusRawLogs=[]` 被显式记录为上游证据缺口，不伪造证据。
  - 3 条 bounded projection 因总预算记录 truncation/omission；完整原始 payload 未丢失。
- 下一步：
  - 进入 PingAn Adapter Checkpoint C，从 NIDS 开始逐来源审阅 parsed fields 的
    canonical/fact/scenario/LLM evidence coverage，只补 adapter mapping，不把厂商字段泄漏到通用
    Runtime。

### 2026-07-24 — PI-01 PingAn Adapter Checkpoint B implemented

- Adapter 实现：
  - 用户确认 `ptp-nids -> nids`、`sec_guard_wb -> threat_intel`、
    `T_GBD_zeus_data -> siem`；映射只存在于 PingAn Adapter；
  - 新增 `pingan_json_object.v1`，在 quoted/comma KV 前解析完整 direct JSON 或 bounded-prefix +
    JSON；拒绝数组、fragment、残缺 JSON、尾随 payload 和超界前缀；
  - 保持 delimited JSON 优先，避免改变现有 APT 语义；无 message 时继续使用完整 structured fallback。
- 212 条 replay：
  - `other` 从 108 降为 0，source type 分布为 `edr=37`、`hids=23`、`ndr=44`、`nids=95`、
    `siem=10`、`threat_intel=3`；
  - unsupported observations 从 68 降为 0；recognized 为 323，既有 nested-repair degraded 仍为 20；
  - parser 分布为 JSON object 146、delimited JSON 105、quoted KV 52、comma KV 40；
  - `raw_message_first=200`、`structured_fallback=12`，逐条 policy contract violation 为 0。
- 审阅产物：
  - 新增 `build_pingan_adapter_review_artifacts.py`，生成 direct NIDS、prefixed EDR、
    prefixed Threat Intel、no-message SIEM 四份敏感本地 JSON；
  - 产物同时包含完整 parsed fields/field schema、canonical alert、fact reconstruction、
    evidence coverage 和 bounded analysis evidence；
  - 初步暴露 EDR 128 个 schema 节点但 0 role claim、SIEM fallback 0 role claim 等 Checkpoint C
    field-use 缺口；`high_value_gaps=0` 不能被解释为字段已完整使用。
- 验证：
  - 聚焦 PingAn/Runtime/normalization/capability tests：`70 passed`；
  - validation corpus tests：`4 passed`；
  - 212 条 corpus rebuild 与四份 Checkpoint B artifact assertions：passed。
- 下一步：
  - 与用户逐份审阅四个 JSON，只在确认字段语义后补 PingAn mapping；尚未进入真实消费测试。

### 2026-07-24 — PI-01 PingAn Adapter coverage review checkpoint

- 结论：
  - 不是重写通用 SOC Runtime，而是基于 212 条真实语料增量完善 PingAn source adapter；
  - 保持 `message` 存在时 `raw_message_first`，不存在时 `structured_fallback`；当前 200/12 条策略选择
    符合该契约；
  - `ptp-nids` 的 95 条被错误归入 `other`，且 direct JSON 被 quoted-KV parser 部分命中或完全不支持；
  - `edr-core-xc` 的 14 个 message 是 syslog-prefix + JSON，`sec_guard_wb` 的 4 个 message 是
    ThreatBook-prefix + JSON，当前均 unsupported；
  - `T_GBD_zeus_data` 10 条都没有 message，fallback 正确，只需确认它作为 Zeus 模型/关联输出是否归为
    `siem`。
- 审阅产物：
  - 新增 `validation/compact_zeus/docs/pingan_adapter_rebuild_review.md`，固定不变量、完整 Topic 基线、
    代表 alert IDs、候选 source types、JSON parser 顺序和 A-D 四个 checkpoint；
  - Checkpoint B/C 要分别审阅完整 parser 输出和 parsed-leaf coverage，不能把 parser success 当成字段
    已完整用于研判。
- 当前边界：
  - 本切片未修改 PingAn Adapter；
  - 等用户确认 `ptp-nids -> nids`、`sec_guard_wb -> threat_intel`、
    `T_GBD_zeus_data -> siem` 后再进入实现。

### 2026-07-24 — PI-01 real-alert corpus intake and lineage validation

- 输入盘点：
  - `datas/source/full_alert_2026_month_forth_sample_200.pkl` 的 `alert_full_data` 是主数据；文件名虽含 200，
    实际为 210 行、210 个唯一 `alert_id`；
  - 5 个历史 JSON 中，2 个与 PKL 精确一致，1 个 ID 已存在但有 5 处网络地址字段差异，2 个 ID
    不在 PKL；
  - 210 个历史 `agent_response` 都是合法 JSON 且 ID 对齐，但它们是历史模型输出，不是人工标签；
    `ground_label` 非空数为 0。
- 实现：
  - 新增 `validation/compact_zeus/corpus/build_alert_validation_corpus.py`，使用受限 pickle loader 生成
    `soc.validation.alert_corpus.v1`；
  - 每个 `alert_id` 只保留一个 canonical row；PKL 对已有 ID 权威，精确 JSON 只增加
    `source_refs`，冲突 JSON 完整保存在 `legacy_demo_variants`，缺失 JSON 包装为
    `app_code/flow_id/alert_id/alert_data` 后追加；
  - 新增 provenance、canonical hash、lineage、response-presence 字段和不含原始字段值的 manifest；
    原始数据、统一 PKL、manifest、HTML、Excel 均按敏感本地产物忽略；
  - 新增共享 `restricted_dataframe_pickle.py`，语料构建和压缩报告使用同一窄白名单 loader，
    并用负向测试确认未授权 pickle global 会被拒绝；
  - 修正 `build_zeus_compaction_artifacts.py` 的仓库根目录/default input，使其可直接消费统一 corpus。
- 结果：
  - 输出 212 行、212 个唯一 ID；原 210 行所有原始列逐值保持不变；
  - merge 状态为 `exact_match=2`、`conflict_pkl_authoritative=1`、`appended=2`；
  - 212 条均可通过当前 SOC normalizer；source type 为 `edr=37`、`hids=23`、`ndr=44`、
    `other=108`；
  - 200 条采用 `raw_message_first`，12 条采用 `structured_fallback`；40 条告警含 unsupported
    message schema、12 条含 degraded schema、12 条无 message observation。这证明输入契约可接收，
    不证明 adapter/parser 语义覆盖完整；
  - `compact_zeus` 全量重跑：115 条命中 1,915 个长编码片段；使用含 12 位短哈希的新
    marker 后节省 3,437,530 字符，`alert_full_data` 字符压缩率 17.24%，非
    `zeusRawLogs` 字段变化数为 0。
- 验证：
  - `backend/.venv/bin/python -m pytest -q validation/compact_zeus/corpus/test_build_alert_validation_corpus.py`
    -> `4 passed`；
  - `backend/.venv/bin/python -m ruff check validation/compact_zeus/*.py` -> passed；
  - `backend/.venv/bin/python validation/compact_zeus/corpus/build_alert_validation_corpus.py` -> passed；
  - `backend/.venv/bin/python validation/compact_zeus/corpus/build_zeus_compaction_artifacts.py` -> passed。
- 下一步：
  - 仍属于 `PI-01`：先将 `T_GBD_zeus_data`、`ptp-nids`、`sec_guard_wb` 的 `other` 分类及
    unsupported/degraded message schema 转成可审阅 adapter/parser coverage register；
  - corpus 目前没有人工 ground truth，不能用于置信度校准、生产准确率声明或自动处置门槛。

### 2026-07-20 — Alpha Gate approved; Stage 4 / PI-01 started

- 具名决定：
  - 项目负责人 `yydspanda` 于 `2026-07-20T18:42:25+08:00` 明确批准 Alpha Gate；个人开发阶段临时
    兼任产品、SOC 运营、安全、平台 reviewer，并临时负责 `PI-01..05`；
  - 批准范围仅为 Stage 4 development / real-integration preparation，不批准共享部署、有限试点、生产、
    auto-close、suppression、isolation、blocking、attack simulation 或其他高风险外部副作用；
  - 进入共享环境或 pilot 前，必须由实际 security/platform/response-system owner 重新确认对应边界。
- 阶段转换：
  - `BG-03` 与 Stage 3 标记为 Done，Alpha Gate passed；`PI` Stage 4 成为唯一 Current stage；
  - `PI-01 Real providers` 成为唯一 In Progress task，先做 provider intake、contract mapping 和第一项只读
    dev/staging 集成选择；缺少 endpoint/credential/approved payload/data owner 时不得用新增 mock 冒充。
- 证据边界：
  - `soc.alpha_readiness_report.v1` 仍保留机器侧 `pending_owner_review`/`stage_transition_allowed=false`，因为
    脚本不会推断后续人工授权；`alpha-gate-review.md` 是独立的人类 gate decision 记录；
  - 当前带无关本地改动生成的开发报告不作为共享部署归档；`PI-02/PI-04` 必须在共享部署前从 clean reviewed
    checkout 重跑并归档，同时补实际环境 owner 审批。
- 下一步：
  - 执行 `PI-01 provider intake`，不提前插入 `PI-02..05` 或 Parking Lot 工作。

### 2026-07-20 — BG-03 independent Alpha Gate review completed

- 评审结论：
  - 新增 `alpha-gate-review.md`，从产品、SOC 运营、安全、平台四个视角复核 readiness 报告、完整性矩阵、
    部署/回滚和关键代码边界；四方独立意见均为“建议通过 Stage 3 技术退出”；
  - 明确该建议只允许在正式签字后进入 Stage 4 真实集成准备，不批准共享部署、有限试点、生产发布、
    auto-close 或高风险动作；
  - 评审记录区分 advisor recommendation 与 accountable sign-off，不伪造 reviewer 身份、时间、决策或
    change ticket。
- Stage 4 责任边界：
  - 为 `PI-01..05` 固定建议责任角色、第一份受控交付物和 entry condition；`Named owner` 仍为 Pending，
    本地新增 mock 不算满足真实集成输入；
  - 风险按 Act now / Track 分类，仍由 roadmap 和 completeness matrix 拥有状态，未创建平行 blocker list。
- 证据核对：
  - 技术 baseline 为 `4631f9fd2c0934891019e950e17fff9c8edbc660`；readiness gate passed，backend
    `558 passed`，architecture/migration `16 passed`，matrix `Gap=0`；
  - CodeGraph/source 复核 `SocDecisionPolicy`、`SocReviewService`、`SocMutationUnitOfWork`、
    `SocKafkaConsumerRunner` 的控制流、服务、事务审计和 ingestion ownership 落点；
  - 当前证据由带无关本地改动的 worktree 生成，只用于开发评审；共享归档仍需从通过评审的 clean checkout
    重跑。
- 下一步：
  - `BG-03 accountable sign-off`：四类具名负责人记录 approve/changes-requested，为 `PI-01..05` 指定
    具名 owner；完成前 `stage_transition_allowed=false`，路线不切到 Stage 4。

### 2026-07-20 — BG-03 Alpha readiness technical candidate passed

- 技术退出包：
  - 新增 `scripts/soc-alpha-readiness.sh all|acceptance|backend|architecture|finalize` 和
    `backend/scripts/soc_alpha_readiness.py`，复用既有 Alpha acceptance，不复制 Runtime/Kafka/Web
    业务验收逻辑；
  - 新增 `soc.alpha_test_gate.v1`，同时校验 pytest exit code、非零 passed count 和 failed/error count；
  - `soc.alpha_readiness_report.v1` 绑定 source commit/branch、acceptance report hash、backend 与
    architecture/migration gate、完整性矩阵 hash/counts、Data-gated/Deferred IDs 和 `PI-01..05`；
  - finalizer 对缺失/坏报告、测试失败、矩阵 `Gap != 0` 或 roadmap 解析失败全部 fail closed；正式归档
    还要求从 reviewed commit 的 clean checkout 重跑。
- 评审与运维边界：
  - 新增 `alpha-readiness-package.md`，明确 Alpha 可承诺/不可承诺范围、共享环境 PostgreSQL/Kafka/auth/
    model/provider/action/data 前置、部署顺序、stop-first 回滚和 Stage 4 外部输入；
  - 技术 pass 固定输出 `release_decision=pending_owner_review`、`stage_transition_allowed=false`、
    `production_ready=false`，脚本不能替产品/SOC/安全/平台负责人签字；
  - completeness matrix 仍是能力状态唯一来源，readiness report 只解析并 hash 引用，不创建平行 blocker
    list。
- 验证：
  - `./scripts/soc-alpha-readiness.sh all`：nested Alpha acceptance core/Kafka/frontend passed；
  - full SOC backend：558 passed；architecture + migration environment：16 passed；
  - authoritative matrix：Complete 34 / Gap 0 / Mock 1 / Data-gated 6 / Deferred 9 / Total 50；
  - readiness final report：`status=passed`、`alpha_candidate_ready=true`、failure reasons empty；
  - readiness focused tests：7 passed；Ruff/shell syntax/diff check 通过。
- 下一步：
  - `BG-03 owner review`：审阅本地
    `backend/.deer-flow/soc-alpha-readiness/alpha-readiness-report.json` 与评审包，接受边界、指定
    `PI-01..05` owner 并记录 approve/changes-requested；未签字前 Stage 4 不切换为 Current。

### 2026-07-20 — BG-P1-05 Alpha E2E and docs reconciliation completed

- `AC-23` 已关闭：
  - `frontend/tests/unit/core/soc/api.test.ts` 补齐 memory candidate/record/search/retrieval activation
    与 normalization list/baseline/metrics/update transport contract；
  - `frontend/tests/e2e/soc-review.spec.ts` 和 deterministic `mock-soc-api.ts` 用 Chromium 覆盖
    ReviewQueue/context、correction/close、candidate review、confirmed-memory activation、approval token+
    dry-run、manifest-selected disposition outcome 和 normalization maintenance；
  - browser fixture 只证明真实 React/请求契约，未冒充部署后的 Gateway/auth/network E2E。
- `AC-24` 已关闭：
  - 新增 `scripts/soc-alpha-acceptance.sh all|core|kafka|frontend|finalize` 和
    `backend/scripts/soc_alpha_acceptance.py`；一条命令从空输出目录运行三类 fixture；
  - core 通过公开 CLI、真实 SQL repository、registered Gateway handlers/service dependencies 验证
    feedback correction/close、exact retry、changed retry `409`、decision+mutation audit 和 linked replay；
  - Kafka 使用 ephemeral Redpanda 验证 APT `2026494`、EDR `1965810`、HIDS `HIDS-2026-0001` 的
    strict envelope、consume、commit、post-commit idle，并验证 malformed JSON DLQ+commit；
  - frontend component 自行启动/清理 auth-disabled isolated Next dev server，运行 API/full Rstest、
    Playwright 和 `pnpm check`；最终生成带 boundary、failure semantics 和 SHA-256 manifest 的
    `soc.alpha_acceptance_report.v1`。
- `AC-49` 已关闭：
  - 新增 `alpha-acceptance-runbook.md`，同步 solution、lifecycle、engineering contracts、mock/real
    register、root/backend AGENTS、backend README、delivery roadmap 和 completeness matrix；
  - 纠正旧七步/技术 Phase、Lead Agent service map、trace 字段、Kafka/SSE/Prometheus 当前/target、
    过期 CLI command 和 mock/application reachability 描述；路线图仍是唯一执行顺序。
- 验证：
  - `./scripts/soc-alpha-acceptance.sh all`：aggregate `passed`，core/Kafka/frontend 全部 passed；
  - `cd backend && ./.venv/bin/pytest -q tests/test_soc_*.py`：551 passed；
  - architecture + migration environment：16 passed；
  - frontend Rstest：72 files / 648 passed；SOC Playwright：3 passed；`pnpm check` 通过；
  - backend Ruff format/check 与 shell syntax 通过；`codegraph sync .` 后新验收 generator/test 符号可查询。
- 真实边界：
  - deterministic analyzer、local SQLite、mock read-only providers、local Redpanda、fixture external
    source 和 mocked browser HTTP transport 均保留在报告；不证明 live-model 质量、PostgreSQL/Kafka/K8s
    生产能力、真实 PingAn provider/feed 或外部高风险执行。
- 下一步：
  - `BG-03 Alpha readiness package`：冻结本次报告和全量门禁证据，整理部署/回滚、限制与 Stage 4
    data/credential inputs，完成 Alpha Gate 评审材料。

### 2026-07-20 — BG-P1-04 Governed memory activation completed

- `AC-39` 已关闭：
  - 新增 `SocMemoryRetrievalActivationCommand/Result` 和固定 policy
    `soc.memory_retrieval_activation_policy.v1`；confirm 仍只创建 retrieval-disabled record；
  - `SocMemoryService.set_retrieval_activation()` 是唯一 enable/disable 边界，要求
    `soc_memory_reviewer|soc_admin`、可信 auth source、reason、expected record version、idempotency key，
    enable 还要求 timezone-aware validity 和 mandatory review period；
  - SQL/in-memory repository 增加 expected-version CAS；record 版本迁移与
    `SocMutationAuditRecord(operation=memory_retrieval_activation)` 在同一 transaction 提交，事件只在
    commit 后发出；exact retry、changed retry、stale writer 和 fault rollback 均有回归；
  - candidate deprecate/expire 同步禁用并 version-bump linked record；retrieval 拒绝 direct/legacy
    boolean、activation expired、review overdue、非 confirmed 和 source expired record，并输出独立 counters；
  - CLI `soc memory records retrieval`、Gateway
    `POST /api/soc/memory/records/{memory_id}/retrieval`、ReviewQueue Web 和 Boss Demo 全部调用同一个
    service；`soc memory search --baseline-json` 输出 timestamp-independent before/after diff。
- 验证：
  - `cd backend && ./.venv/bin/pytest -q tests/test_soc_*.py`：546 passed；
  - architecture + migration environment：16 passed；backend Ruff format/check 通过；
  - `cd frontend && pnpm test`：643 passed；`pnpm check` 通过。
- 真实边界：
  - governed activation 只允许 confirmed memory 进入 bounded investigation context，不注入 fixed
    Runtime prompt、不改 verdict、不授权 action；automatic Kafka/Lead Agent lesson capture 仍 Deferred；
  - 无新增 migration：治理字段保存在既有 `record_payload`，并复用 `version`、
    `retrieval_enabled` 索引列；生产 PostgreSQL 迁移基线不变。
- 下一步：
  - `BG-P1-05`：关闭 `AC-23/AC-24/AC-49`，建立单命令、版本化 APT/EDR/HIDS Alpha E2E acceptance
    report，补齐 focused SOC frontend regression，并统一权威 operator docs。

### 2026-07-20 — BG-P1-03 Runtime recovery and decision provenance completed

- `AC-13` 已关闭：
  - 新增 `AnalysisRequestJournal(soc.analysis_request_journal.v1)` 和 Runtime
    `analyze_journaled()` pre-provider hook；持久化 CLI/Kafka path 在 analyzer/provider 调用前提交
    `AnalysisRun(status=running)`，final analysis bundle 再原子更新 journal 为 `completed/failed`；
  - journal 只保存 request hash/schema、source/detection 元数据、model/prompt/step、证据计数、selected
    skills、request/trace/actor 和哈希后的 idempotency key，不保存 rendered prompt、evidence values、
    provider header/response、credential/token；existing `input_payload` 继续作为受治理 replay snapshot；
  - `SocAnalysisService.recover()` / `soc recover` 使用 stale window，将 process-lost run 保留为
    `interrupted`；SQL repository 通过 expected-running 条件更新保证单赢家 claim，interrupted claim 仍受
    stale lease 保护；随后创建稳定幂等且带 `replay_of_run_id` 的新 run，普通 replay 拒绝 running run；
  - process loss、provider timeout、stale-window deny、final bundle rollback 和重复 recovery 均有 SQL 回归。
- `AC-17` 已关闭：
  - human correction 固定写 `human_confirmation`；只有经过 external disposition trust/mapping/target
    gate 的 `correct_external()` 写 `external_disposition`；入口不能自行伪造来源；
  - 删除 external fixed `0.95`；correction 数字定义为未校准 confirmation strength，使用
    `soc.correction_policy.v1`，保留 explicit/default、解释和来源，强制
    `confidence_is_calibrated=false` / `calibrated_probability=null`；
  - provenance 已贯穿 `Decision`、`CorrectionRecord`、`AlertSummary`、decision/mutation audit、timeline、
    CLI 和 Review API，frontend contract 同步新增字段。
- 验证：
  - process-loss/timeout/rollback/recovery + correction/external/summary/audit/API focused tests 通过；
  - `cd backend && ./.venv/bin/pytest -q tests/test_soc_*.py`：541 passed；
  - architecture + migration environment：16 passed；
  - `cd frontend && pnpm test`：642 passed；`pnpm check` 通过；backend Ruff format/check 通过。
- 真实边界：
  - Alpha recovery 使用同一 SOC business store 和 stale-window claim；生产多 pod worker ownership、lease/
    heartbeat 和 provider cost reconciliation 仍需 Stage 4 基础设施证据；
  - confirmation strength 不是 production calibrated probability，真实校准仍由 `AC-19/PI-03` data gate 控制。
- 下一步：
  - `BG-P1-04`：关闭 `AC-39`，实现 role/reason/audit/version-controlled memory retrieval enable/disable，
    并验证所有入口复用 service、只有 enabled confirmed record 进入 bounded retrieval。

### 2026-07-20 — BG-P1-02 API contract stabilization completed

- `AC-11` 已关闭：
  - 保留已被 Gateway/Web 使用的 `/api/soc/*` path 和 direct typed success body，不制造重复
    `/api/soc/v1/*` 或破坏性 `{data,meta}` 迁移；
  - 新增 `soc_transport.py`，所有 SOC routers 通过 `create_soc_router()` 共享
    `X-SOC-API-Version: 1`、`X-Request-Id`、`X-Trace-Id` 和 sanitized
    `SocProblemDetails(soc.api.problem.v1)`；
  - request/trace ID 进入同一个 `ServiceRequestContext`；validation error 不回显 input；Gateway
    authenticated identity 仍优先，旧草案 `X-Actor` 被正式废弃；
  - `contracts/soc_api/openapi-v1.snapshot.json` 锁定所有 SOC path/method、公共 request/response
    headers 和 error statuses；
  - frontend `SocRequestContext` 支持 request ID，`SocApiError` 保留 code/status/request/trace/retryable，
    并拒绝已声明但不支持的 API version。
- 验证：
  - backend transport/router focused：42 passed；完整 SOC suite：538 passed；architecture/migration：16 passed；
  - frontend full test：642 passed；`pnpm check` 通过；Ruff 和 diff check 通过；
  - 真实同步 `soc_review` ASGI route smoke 返回 typed body 和 v1/request headers。
- 下一步：
  - `BG-P1-03`：只关闭 `AC-13/AC-17`，实现 durable pre-model journal/recovery 和正确的
    human/external decision confidence provenance。

### 2026-07-18 — BG-P1-01 versioned ingestion and feedback completed

- `AC-04` 已关闭：
  - 新增严格 `SocAlertRawEnvelope(schema_version=soc.alert.raw.v1)`，要求 source、alert ID、dedup key、
    event time、severity 和 raw；可选 tenant/source event/version 和 bounded entities hint；
  - raw 上限 900,000 UTF-8 JSON bytes，entities hint 上限 64,000 bytes；拒绝非 JSON、extra field、坏
    version 和伪造 `_soc_ingress`；validation error 不回显 raw input；
  - Kafka mapper 校验后完整保留 vendor raw，只用 `setdefault` 补通用 transport fallback，并将 envelope
    metadata 放入保留 `_soc_ingress`；APT/EDR/HIDS 三份真实脱敏样本逐字段保持；
  - `scripts/soc_kafka_smoke.py` 现在发布 versioned envelope，不再把裸 vendor object 直接发到 alert topic。
- `AC-08` 已关闭：
  - 新增 `SocExternalDispositionIngressCommand` 和 authenticated
    `POST /api/soc/external-dispositions`；Gateway 只构造认证 context 和映射错误，所有业务写仍进入
    `SocExternalDispositionService.apply_event()`；
  - service boundary 要求 `soc_admin` 或 `external_disposition_adapter`，canonical ingress 必须提供
    `source_event_id`；完全相同的事件返回一个既有 record，同 key 改内容返回 conflict；
  - 默认未配置 status mapping 时安全保存 unmatched；真实映射由 server-side config/service injection
    提供，客户端不能提交 trust mapping；真实 Zeus/ITSM/SOAR feed、签名和凭证仍属于 `AC-09`。
- 验证：
  - `cd backend && ./.venv/bin/python -m pytest -q tests/test_soc_*.py`：532 passed；
  - architecture + migration environment：16 passed；Kafka focused：38 passed；external ingress/UoW：19 passed；
  - 本地 Redpanda：versioned alert `processed` + offset committed，summary=1，同 group 下一次 poll=idle；
    bad JSON 写入 DLQ 后 offset committed；run `RUN-8D1C57429E9F`；
  - Docker Desktop 由本次启动，Windows Engine 可用；当前 WSL distribution 仍未启用 Docker CLI integration。
- 下一步：
  - `BG-P1-02`：只关闭 `AC-11`，统一现有 SOC API contract/error/request metadata 与 frontend client，
    增加 OpenAPI snapshot/compatibility tests，不扩展业务流程。

### 2026-07-18 — BG-P0-02 transactional mutation and durable audit completed

- `AC-16` 已关闭：
  - 新增 `SocMutationUnitOfWork` 和 SQLAlchemy transaction repository；事务内部 repository
    `commit()` 只 flush，外层 command context 统一 commit/rollback；
  - `SocReviewService.correct()` 与 `SocExternalDispositionService.apply_event()` 的 run、summary、queue、
    candidate、decision audit、external disposition、eligible outcome 和 mutation audit 属于一个命令事务；
  - `SocEvent` 在事务内缓冲，只有 commit 成功才 flush；逐写入 fault injection 证明任一步失败都不留半套状态或成功事件；
  - 完全相同的 idempotent retry 返回一个既有逻辑结果，复用 key 提交不同 command 会 conflict。
- `AC-21` 已关闭：
  - 新增 `SocMutationAuditRecord` / `SocMutationOperation`、repository protocol/SQL 实现和 migration
    `0018_mutation_audit`，落表 `soc_mutation_audit_log`；
  - 覆盖 review correct/close/note、memory review、approval request submit/approve/reject/expire、
    action dry-run/execute 与 external disposition apply；
  - 审计保存 actor + `auth_source`、request、reason、operation、target、idempotency、command hash 和有界
    result projection；不保存原始 action/alert payload，敏感 key 和 inline credential 会脱敏限长；
  - authenticated API 和 Review TUI 测试证明实际入口身份/幂等信息进入同一持久审计链。
- 验证：
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_*.py -q`：516 passed；
  - architecture boundaries：10 passed；migration environment：6 passed；
  - 新增 mutation UoW/audit 聚焦测试：7 passed；Ruff 和 `git diff --check` 通过。
- 真实边界：
  - 该事务只覆盖本地 SOC 数据库状态；真实 provider/外部副作用仍需 Stage 4 compensation/verification；
  - `soc_decision_audit_log` 继续负责 verdict lineage，`soc_mutation_audit_log` 负责通用 L3 command lineage；
    generic durable `SocEvent` stream 仍是 deferred `AC-46`。
- 下一步：
  - `BG-P1-01`：关闭 `AC-04`、`AC-08`，实现严格 versioned Kafka alert envelope 和通用 external
    disposition 应用入口，并验证 bad version/malformed input、DLQ/offset 和 duplicate semantics。

### 2026-07-18 — BG-P0-01 approval integrity and L3 authorization completed

- `AC-34` 已关闭：
  - `SocAgentApprovalRequest` 状态机固定为 `pending -> approved/rejected/expired`，终态保存处理人、理由、时间、幂等键和可选 grant 引用；
  - grant command 只接受 `approval_request_id`，service 必须加载持久化 pending request，不再信任客户端回传的完整请求对象；
  - repository 使用 insert-only request create 和带 expected status 的原子 resolve；approve 在同一事务中完成 request transition + grant insert，数据库唯一约束保证一个 request 最多一个 grant；
  - 完全相同的 approve/reject/expire 重试返回原结果，理由、幂等键、过期参数或目标终态不同则 conflict；Web/TUI 增加 reject/expire。
- `AC-22` 已关闭：
  - `ActorContext.auth_source` 明确记录 session/internal/local CLI/local TUI/daemon/external adapter/test 等身份信任来源；
  - shared `require_actor_roles()` 在 core service 内拒绝 anonymous/unknown provenance，并保护 review close/note/correct、memory review、normalization mutation、governed-context lifecycle 和 approval submit/resolve/dry-run/execute；
  - Gateway 从认证状态映射 `soc_analyst`/`soc_admin` 并保留认证来源；CLI/TUI/daemon 使用显式本地身份，入口不能通过 actor header 覆盖已认证用户。
- 数据库 migration：`0017_approval_request_lifecycle`；旧的通用 `save_approval_request()` 写入口已移除，避免绕过状态机。
- 边界声明：审批终态和 execution payload 已持久化，但统一追加式 mutation audit 仍属于 `AC-21`，没有在本刀提前标记完成；真实外部副作用仍未打开。
- 验证：
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_*.py -q`：509 passed；
  - `cd frontend && pnpm test tests/unit/core/soc/api.test.ts`：16 passed；
  - backend Ruff、frontend Prettier/TypeScript checks 通过；
  - forged/stale/repeated/unauthorized、SQL lifecycle、API/Web/TUI happy/terminal paths 均有回归覆盖。
- 下一步：
  - `BG-P0-02`：关闭 `AC-16`、`AC-21`，先定义 correction/external-feedback unit of work 与统一 mutation audit，再用 fault injection 证明任一步失败都完整回滚。

### 2026-07-18 — AUD-03 completeness matrix completed; AA Gate passed; BG-P0-01 started

- 新增 `.notes/ai_soc/audits/alpha-completeness-matrix.md`，作为唯一 SOC Alpha 完整性矩阵：
  - 对 50 项能力只分类一次：`Complete=21`、`Gap=13`、`Mock=1`、`Data-gated=6`、`Deferred=9`；
  - 将 13 个代码可控 Gap 分为 4 个 P0 和 9 个 P1；Mock、外部数据/凭证条件和明确后置项不冒充当前 blocker；
  - 每个 Gap 均记录 owner boundary、影响、AUD-01/AUD-02/源码证据、可测试验收条件和目标阶段。
- Stage 3 输入已冻结为 7 个有序工作包：
  - `BG-P0-01` approval integrity and L3 authorization；
  - `BG-P0-02` transactional mutation and durable audit；
  - `BG-P1-01` versioned ingestion and external feedback；
  - `BG-P1-02` SOC API contract stabilization；
  - `BG-P1-03` Runtime recovery and decision provenance；
  - `BG-P1-04` governed memory activation；
  - `BG-P1-05` Alpha E2E and authoritative docs reconciliation。
- AA Gate 已于 2026-07-18 通过；当前阶段切换为 `BG`，且唯一进行中的任务是 `BG-P0-01`。
- 本刀仅完成审计分类、门禁和执行指针更新，没有修改业务代码。
- 验证：
  - 矩阵编号连续覆盖 `AC-01..AC-50`，状态统计合计 50；
  - `.notes/ai_soc/README.md`、`delivery-roadmap.md`、`progress.md` 已指向同一矩阵和下一工作包；
  - `git diff --check`；文档审计未重跑产品测试。
- 下一步：
  - `BG-P0-01`：先修 `AC-22`、`AC-34`，验收 forged/stale/repeated/unauthorized 请求均被拒绝，API/Web/TUI 正常审批路径通过。

### 2026-07-18 — AUD-02 consistency audit completed; AUD-03 started

- 新增 `.notes/ai_soc/audits/alpha-consistency-audit.md`，以 AUD-01 as-is 旅程为基线，对照：
  - `.notes/ai_soc/soc-agent-solution.md`；
  - `.notes/ai_soc/alert-lifecycle-flow.md`；
  - `.notes/reference-index/soc-agent-engineering-contracts.md`；
  - `.notes/ai_soc/integrations/mock-and-real-register.md`；
  - 当前 SOC/Gateway/Kafka/Web/TUI/Lead Agent 代码与测试。
- 审计结果：
  - 记录 10 项已确认一致的核心边界，包括 fixed Runtime、bounded evidence、analysis atomic bundle、
    read-only correlation、GF/AA/EX/DP/EV shadow boundary、真实可选 LLM path、mock external facts 和 no-side-effect approval boundary；
  - 记录 `CONS-01..24` 共 24 项事实差异，覆盖 application reachability、API/Kafka contract、Lead Agent/
    main orchestrator wiring、状态/持久化、approval、audit、atomicity、confidence provenance、memory activation、
    RBAC、运维目标和过时命令/台账；
  - 单独核对 real implementation、mock provider、shadow-only、service-only 和 data-gated，避免把
    “服务已实现”误写成“应用入口已接通”，也避免把真实 Runtime/SQL/governance 误写成 mock。
- 重要事实包括：
  - Gateway 当前没有 analyze/replay，external disposition 只有 service + SQL persistence 而没有应用 ingress；
  - Kafka consumer/commit/DLQ/daemon 是真实串行实现，但 versioned input envelope、result topics 和 worker pool 未落地；
  - approval request 批准后仍保持 pending，grant API 不从 repository 校验/解析 request；
  - durable decision audit 不覆盖 close/note/approval，`SocEvent` 默认 no-op；
  - correction/external disposition 不是 analysis bundle 那样的跨表原子事务；
  - human/external correction 没有写正确 `Decision.confidence_source`；
  - confirmed memory retrieval 算法真实存在，但没有受治理的 retrieval-enable 应用入口；
  - mock register 对 EX/DP/EV 和 external disposition SQL persistence 的描述已落后。
- 本刀只新增审计报告并更新索引/阶段指针，没有修改业务代码，也没有在审计过程中改写被审文档。
- 验证：
  - `codegraph status .`：index up to date，1,614 files / 33,184 nodes / 77,881 edges；
  - CodeGraph 查询 `SocExternalDispositionService`、`SocDaemonService`、`SocLeadAgentChatService`、
    `SocMainOrchestratorService`，再用源码/`rg` 补齐动态入口和 absence evidence；
  - `git diff --check`；本刀为文档审计，未重跑产品测试。
- 下一步：
  - `AUD-03 Completeness matrix + blocker register`：将 AUD-01 journey 与 `CONS-01..24` 只分类一次，
    补齐状态、优先级、owner、影响、证据、验收和目标阶段，冻结 `BG-01/BG-02` 输入；仍不在该刀修代码。

### 2026-07-18 — AUD-01 Journey inventory completed; AUD-02 started

- 新增 `.notes/ai_soc/audits/alpha-journey-inventory.md`，以当前代码而不是方案推断为基线，完成：
  - 14 组 CLI command entry、Kafka/daemon/deployment、5 组 Gateway API、Web/TUI/Lead Agent 和 service-only ingress 盘点；
  - 21 个 public/service boundary、fixed Runtime 九步主链和显式 side-service 边界盘点；
  - 11 组端到端 journey、15 个状态聚合、17 张 SOC 业务表和 Alembic metadata table 盘点；
  - CLI/Web/TUI/Lead Agent/Kafka/Demo/validation 可见产物及 source-of-truth 映射；
  - 源码、测试和 CodeGraph 证据索引。
- 本刀只写 as-is evidence，不做业务代码修改，也不提前把差异标成 P0/P1/P2。
- 明确保留的事实边界包括：
  - Gateway 当前没有 analyze/replay/governed-context/external-disposition ingress；
  - external disposition 和 domain-finding memory source 目前存在 service/test，但不是完整应用入口；
  - correlation/domain/unified view 是读取时派生结果，不是隐藏 Runtime node 或独立表；
  - high-risk execute 当前只消费 token，明确不产生外部生产副作用；
  - active Kafka runner 仍是串行，worker-pool/partition tracker 是后续并发基础。
- 已同步 `README.md` 和 `delivery-roadmap.md`，当前执行指针切换到 `AUD-02`。
- 验证：
  - `codegraph sync .` 已在盘点前完成；随后用 CodeGraph 查询核心 service，并以源码补齐动态 Gateway/TUI/Lead Agent 调用关系。
  - 对照 `db/models.py` 与 `db/migrations/versions/`，17 张业务表均有唯一 migration/writer/reader 落点。
  - 本刀无 Python/TypeScript 业务代码改动，因此未重跑产品测试；文档路径、heading、ID 和引用在本地检查。
- 下一步：
  - `AUD-02 Code/contract/docs consistency`：对照 `soc-agent-solution.md`、`alert-lifecycle-flow.md`、工程契约、mock/real register 与 AUD-01，产出一份事实差异报告；不在该刀修复代码。

### 2026-07-18 — BD-03 completed; BD Gate passed; AUD-01 started

- Docker Desktop/WSL Integration 恢复，Boss Demo `status` 返回 READY。
- 保存 live DeepSeek ReviewQueue 页面：
  `backend/.deer-flow/soc-boss-demo/review-desktop-live.png`；对应 run 为
  `RUN-8366A14C3E9B`，decision `needs_review/0.45`，grounding `6/7`，无 silent fallback。
- 通过 Web 提交明确标注的演示纠正：run 变为 `suspicious`，产生 1 条 correction 和 1 条
  `pending_review` memory candidate；candidate 未进入 confirmed memory，且不允许 Runtime decision impact。
  证据保存在 `review-context-after-feedback.json` 和 `review-feedback-live.png`。
- 完成真正 clean reset：先 `soc-boss-demo.sh stop`，再 `soc-boss-demo.sh start --reset`；生成新的
  deterministic run `RUN-7330DE4DADCC` / queue `REV-F978361476D8`，证明 ID 和数据可重复重建而非复用旧实例。
- 冷启动第一次 readiness probe 在 Gateway 启动约 5 秒时超时；约 40 秒后第二次 probe 返回 READY，
  未发现服务错误。
- 最终 deterministic context 为 open queue、`unknown/0.45/needs_review`、3 domain findings、2 条
  read-only mock evidence、1 条 relevant confirmed demo memory、0 corrections；API/CLI 结果与 Web 一致。
- 最终产物：`review-context-rehearsal.json`、`review-desktop-rehearsal.png`；服务继续运行在
  `http://localhost:2026/workspace/soc/review`。
- `BD-03` 与 BD Gate 已完成；按唯一交付路线切换到 `AUD-01 Journey inventory`。下一刀只盘点完整旅程，
  不在审计过程中修复或扩展单一模块。

### 2026-07-17 — BD-02 completed; live Boss Demo and Runtime Step 01-12 rerun

- Docker overlay 已验证 Gateway/API/Web 使用同一独立 `soc_boss_demo.db`；authenticated Review Context
  API 返回 HTTP 200，ReviewQueue 页面和 bounded Lead Agent context bridge 均已 smoke/test。
- live Boss Demo 使用 `deepseek-v4-pro` 完成 clean reset，无 silent fallback：manifest `ready`，1 个 open
  queue、3 domain findings、2 个明确标记 mock 的只读证据、1 条 demo confirmed memory 和 10 条 timeline。
- 新增 `scripts/soc-runtime-validation.sh`，统一提供 `core/live/evaluations/finalize/snapshot/all`；不再依赖
  历史手工命令重跑 Step 01-12。
- 新增 `generate_soc_context_validation.py`，可重复生成 governed fact `proposed -> active -> history/query`
  和 HIDS/EDR authorization exact shadow match；所有边界保持 read-only/no-decision-impact。
- 新增 `generate_soc_runtime_validation_report.py` 与 `runtime-validation-runbook.md`，生成本地
  `RUN-INDEX.md/latest-run.json`，明确 fixed Runtime、offline maintenance、evaluation、governance 四类轨道。
- 五条 `deepseek-v4-pro` live 样本均完成：38/49 evidence grounded，3/5 样本共 11 条引用被拒绝；
  `SocDecisionPolicy` 对这些样本正确设置 degraded review 且 `automation_allowed=false`。模型引用质量作为
  review finding 保留，不用“安全拦截成功”掩盖分析质量问题。
- 新 label set 保持 5 条 pending，`calibratable=false`；脚本写
  `label-set.rerun.pending.json`，未覆盖已有人工真值 `label-set.pending.json`。
- 总验证结果：13 个轨道 passed、1 个 expected human boundary、0 failed/missing；correlation 8-pair replay
  `changed=false`，HIDS/EDR authorization shadow 均 exact。
- 回归：聚焦 Boss/validation/Lead Agent/TUI `36 passed`；SOC + architecture suite `504 passed`
  （仅 1 条 DeerFlow MCP event-loop deprecation warning）；Ruff、format check、`bash -n`、
  `git diff --check` 通过；`codegraph sync .` 已纳入 2 个新增生成器、41 个节点。
- 当前进入 `BD-03`：仅剩 Docker Desktop/WSL Integration 恢复后补 live 页面截图、演示 note/correction
  feedback，并跑一次最终 8-10 分钟彩排；不新增产品范围。

### 2026-07-17 — BD-01 one-command Boss Demo preparation completed

- 新增 `soc demo boss`：默认使用独立
  `backend/.deer-flow/data/soc_boss_demo.db`，支持显式 `--reset`、stub/llm analyzer 选择和 Web base URL。
- 输出 `soc.boss_demo_manifest.v1`，包含本次 `run_id`/`queue_id`、调查视图计数、Web/API/TUI 入口、
  analyzer mode，以及 `real / deterministic / fixture / mock / shadow_only / disabled` 能力边界。
- 复用现有 `SocAnalysisService`、SQLAlchemy repository、ReviewQueue、action evidence、domain triage 和
  memory service；未新增第二套 Demo Runtime 或前端业务逻辑。
- 新增 `scripts/soc-boss-demo.sh` 与 `docker-compose.soc-boss-demo.yaml`，提供
  `prepare/start/status/logs/stop`，容器 Gateway 精确绑定同一 Demo DB，不修改 `.env` 或基础 Compose。
- 新增 `boss-demo-v0.1-runbook.md`，持续记录明天汇报所需命令、流程图、8 分钟话术、真实/mock 边界、
  故障处理和验收结果。
- 验证：Ruff 通过；`backend/tests/test_soc_demo_investigation.py` 为 `4 passed`；默认
  `soc demo boss --reset --pretty` 返回 `status=ready`、1 个 open queue、3 findings、2 evidence、
  1 relevant demo memory、10 timeline items。
- 当前进入 `BD-02`：Docker daemon 已恢复，接下来启动 Gateway/Web 并做真实同库 smoke。

### 2026-07-17 — Four-stage delivery roadmap frozen; Boss Demo first

- 新增唯一阶段路线 `delivery-roadmap.md`，冻结执行顺序：`Boss Demo v0.1 -> SOC Alpha
  Completeness Audit -> Close Blocking Gaps -> Real Data & Production Integration`。
- 纠正上一版把 Alpha audit 设为当前下一刀的排序。当前只执行 `BD-01`，先让老板在浏览器中看到
  一条可重复、可说明、显式标注 mock/shadow 边界的告警研判闭环；审计在 BD Gate 通过后开始。
- `Correlation label corpus expansion` 保留为 Stage 4 data-dependent TODO；当前 8-pair baseline 继续
  作为工程回归，不冒充生产质量结论，也不阻塞 Boss Demo 或 Alpha 代码完整性。
- 新需求只有三种处理方式：属于当前 task、用户明确替换当前目标、或进入 Parking Lot；不再通过
  临时聊天把质量优化、真实集成或远期能力插入当前主线。

### 2026-07-16 — Phase 2 correlation quality baseline completed

- 新增显式 `CORRELATION_SCORING_POLICY_VERSION=soc.correlation.scoring.v1`；每个
  `CorrelationResult` 和 eval report 都记录实际 scorer 版本，fixture 与当前版本不一致时 fail-fast。
- 新增 vendor-neutral `soc.correlation_eval_fixture_set.v1`，用 2 个 endpoint/network case、8 个
  labeled pairs 区分：
  - `same_incident`：检索正样本、duplicate identity 正样本；
  - `related_distinct`：检索正样本、duplicate identity 负样本；
  - `unrelated`：两项均为负样本。
- 新增 `soc eval correlation [FIXTURE] [--baseline-json PRIOR] --pretty`：
  - 分别输出 retrieval 与 offline duplicate-identity confusion matrix、precision/recall/F1；
  - 输出 match reason prefix distribution、candidate fan-out、per-pair score/reasons；
  - 分开统计跨 run 的 `evidence_lineage_leakage_count` 和无关候选的
    `unrelated_evidence_exposure_count`；
  - replay diff 忽略 `generated_at`，比较 policy/corpus、pairs、metrics、reasons、fan-out 和 evidence。
- 当前受控 baseline 不是生产质量结论：retrieval precision/recall 为 `0.667/1.0`；离线 threshold
  `130` 的 duplicate precision/recall 也是 `0.667/1.0`，因为一个 related-but-distinct endpoint
  occurrence 被高重叠分数误判为 duplicate。Evidence lineage leakage 为 `0`，但 unrelated evidence
  exposure 为 `2`。
- 安全边界保持不变：eval 使用 in-memory repository，不写业务 DB/ReviewQueue/memory，不修改 Runtime
  decision；报告固定 `shadow_dedup_allowed=false`、`decision_impact=none`。
- 本地报告：
  - `backend/.deer-flow/soc-runtime-validation/step-11-correlation-eval/correlation-baseline.json`；
  - `backend/.deer-flow/soc-runtime-validation/step-11-correlation-eval/correlation-replay-diff.json`
    （同一基线重放 `changed=false`）。
- 验证：聚焦 correlation eval/service/repository 回归通过；最终 SOC + architecture 回归为
  `499 passed, 1 warning`。warning 仍是既有 DeerFlow MCP cache
  `asyncio.get_event_loop()` deprecation。
- 下一步先扩展脱敏真实告警的 analyst-reviewed pair corpus 和 event-time/source cohorts；不能用 8 条
  受控 pair 直接调生产阈值。标签稳定后再做 scorer v2 shadow comparison，仍不自动 suppression。

### 2026-07-16 — Phase 2 correlation bridged into the unified main report

- 把相似评分提取到 vendor-neutral `soc_agent.domain.correlation.score_similar_alert()`；SQL 和新增
  `InMemoryAlertSummaryRepository` 共用一套 detection/rule/source/category/entity scoring 语义。
- `SocMainOrchestratorService` 默认使用共享 in-memory summary/evidence store；生产必须注入已经正确配置、
  共享同一 PostgreSQL repository 的 `SocAnalysisService` / `SocCorrelationService` 成对实例，避免
  summary-only 半持久化。主编排器不直接查 repository，也没有新增 LangGraph/agent runtime。
- `CorrelationResult` 现在显式进入：
  - `UnifiedInvestigationReport.correlation_result`；
  - `SocDomainTriageRequest.correlation_result`；
  - `SocOrchestratorReviewContextSummary` 的 match/reusable-evidence counts；
  - 既有 `SocReviewService` domain request。
- Domain/scenario evidence profile 优先读取 typed correlation，而不是 metadata count；所有 domain handler 和
  scenario finding 的 evidence refs 均可追溯 matched historical run 和 reusable evidence。Correlation 仍不改 Runtime decision、ReviewQueue、
  memory、approval 或 action。
- 修正 reusable-evidence 查询边界：只按 matched historical `run_id` 加载。Repository 多引用过滤是 union
  语义；若同时使用复用的 `alert_id`，相同告警 ID 的 current evidence 可能混入 historical match。
- `soc eval pingan-main --pretty` 现在每个 APT/EDR/HIDS fixture 先跑一条本地 historical run，再跑 current
  alert；验证结果保存于本地
  `backend/.deer-flow/soc-runtime-validation/step-10-correlation-bridge/pingan-main.json`：3 matches、
  6 reusable evidence、0 failures，每条 evidence 的 `run_id` 均指向 matched history。
- 聚焦回归：PingAn main + domain scenario `11 passed`；in-memory correlation `2 passed`；SQL correlation
  `2 passed`。最终完整 SOC + architecture 回归为 `493 passed, 1 warning`；warning 仍是既有 DeerFlow MCP
  cache `asyncio.get_event_loop()` deprecation。
- 下一步不是直接做自动抑制，而是 `Correlation Eval Baseline`：先量化 same-incident、related-but-distinct、
  unrelated 的 precision/recall、reason distribution 和 fan-out，再决定 shadow dedup contract。

### 2026-07-16 — EV-03 sample review inbox implemented

- 新增 `SocDispositionSampleReviewInbox/Item/Readiness`：
  - campaign progress 从 immutable manifest、proposal、ReviewQueue、latest primary/sample outcomes 派生；
  - 只有 latest sampled reviewer 与 primary analyst 独立才计入 completed；当前 reviewer 冲突、queue 未关闭、
    lineage 缺失/不一致均返回显式 readiness/blocking reason；
  - response 分页，固定 `decision_impact=none`、`auto_close_allowed=false`，不新增 migration/campaign table。
- repository 新增 `list_latest_disposition_outcomes_for_proposals()`：内存/SQL 同协议；SQL 使用 window rank，
  按 `observed_at, created_at, outcome_id` 选择每条 lane 最新版本，并对 proposal ids 分块。
- Gateway 新增只读入口：
  - `GET /api/soc/review/disposition-samples`；
  - `GET /api/soc/review/disposition-samples/{sample_id}/inbox`；
  - reviewer actor 来自 authenticated request context，不能由 query/body 指定。
- Web `/workspace/soc/review` 新增 `告警队列 / 抽样复核` mode：sample inbox 拆成独立组件，展示批次、完成度、
  reviewer conflict 和逐项 readiness；打开条目时只把服务端返回的 `sample_id/proposal_id/queue` 预填到
  EV-02 capture form，没有第二个 outcome 写入口，也不能选择 manifest 外 proposal。
- 完整 SOC + architecture backend 回归 `492 passed`；frontend `pnpm check` 和 `638 passed` 已通过；唯一
  warning 仍是既有 DeerFlow MCP cache `asyncio.get_event_loop()` deprecation。
- 当时记录的下一步是把 `SocCorrelationService` 合并进 `UnifiedInvestigationReport`；该项已在后续
  `Phase 2 correlation bridged into the unified main report` 切片完成。

### 2026-07-16 — EV-02 structured disposition outcome capture implemented

- Gateway 新增 authenticated `POST /api/soc/review/disposition-outcomes`：
  - API 固定 `source=analyst`，要求 `Idempotency-Key`；
  - actor/surface、closed queue、sample membership、independent reviewer、幂等和 append-only supersession
    全部继续由 `SocDispositionEvaluationService` 校验；
  - not-found / conflict / ineligible / unavailable 使用明确 HTTP 状态，不从 `close_reason` 猜标签。
- Review TUI 新增：
  - `/outcome DPROP-... disposition idempotency-key reason`；
  - `/sample-outcome DSAMPLE-... DPROP-... disposition idempotency-key reason`；
  - CLI 装配把现有 evaluation service 注入 TUI，没有新增第二套业务实现；`--actor-id` 为审计和独立
    reviewer 校验提供稳定身份。
- Web ReviewQueue workbench 新增结构化标签表单：
  - closed queue 才可提交；显式选择 proposal、observed disposition、primary/sample lane 和 reason；
  - sampled lane 必须填写 manifest id；已有同 lane outcome 时界面显式提交
    `supersedes_outcome_id`；关闭工单和写标签仍是两个动作。
- trusted external disposition bridge：
  - 只接受 high-trust mapped event、verified target 和唯一 lineage-matching proposal；
  - 外部 record 先持久化，再通过 evaluation service 写 `source=external_disposition` outcome；重复事件可
    幂等补写；成功/幂等/skip reason 进入 apply result、audit 和 event；
  - 只自动 supersede 先前 external-source primary；latest primary 来自 analyst/replay 时 fail closed，要求
    显式人工修订；external free-text reason 不参与 canonical status 推断。
- EV-02 未新增 migration，不修改 DP proposal，不应用 shadow proposal，不开启 auto-close；既有 high-trust
  external correction/ReviewQueue sync 仍是独立反馈边界。
- 完整 SOC + architecture backend 回归 `489 passed`；frontend `pnpm check` 和 `637 passed` 已通过。
- 下一步：`EV-03 Sample Review Inbox`，把 manifest、selected proposals、已完成/待完成独立复核组织成
  可操作 campaign/inbox；仍不进入 auto-close rollout。

### 2026-07-16 — EV-01 shadow disposition evaluation gate implemented

- 新增严格分层合同：
  - `SocDispositionEvaluationScope` 固定 tenant/environment/time window/proposal+matcher policy cohort；
  - `SocDispositionSampleManifest` 用 `sha256_rank_v1` 从完整 population 可复现抽样，只保存 seed hash；
  - `SocDispositionOutcomeRecord` 显式保存 proposed/observed disposition、review kind、来源、reviewer、sample、
    evidence 和时间；更正必须 append 并显式 `supersedes_outcome_id`，唯一 lineage key 阻止并发双 root/后继；
  - `SocDispositionEvaluationGatePolicy/Report` 计算 resolution、shadow precision、override、独立抽样 coverage/
    precision/agreement、source freshness 和 fact-version fan-out。
- 新增 `SocDispositionEvaluationService`：
  - outcome 只能绑定 lineage 一致且已关闭的 ReviewQueue；不能从 free-text `close_reason` 推断标签；
  - sampled quality review 必须来自持久化 manifest，proposal 必须在样本中，已有 primary reviewer 时要求独立 reviewer；
  - population/outcome/manifest 查询命中 limit 或 enrichment lineage 破损时，gate fail closed 为
    `insufficient_data`；
  - gate policy 显式 allowlist primary/sample outcome source，且评测阶段再次排除同 reviewer 的非独立样本；
  - 即使 `passed_shadow_evaluation`，结果也只是 `eligible_for_governed_rollout_review`，固定
    `auto_close_allowed=false`。
- 新增 append-only `soc_disposition_sample_manifests`、`soc_disposition_outcomes` 和 migration
  `0016_disposition_evaluation`；SQL 与 in-memory repository 都校验唯一 idempotency/semantic identity 和
  typed payload/index 一致性。
- 新增 CLI：`soc disposition sample create|list|get`、`soc disposition outcome record|list|get`、
  `soc disposition evaluate`。InvestigationContext、timeline/counts、Web/TUI 和 bounded Lead Agent artifact
  只读展示 outcome，不提供 apply/close/action 能力。
- 聚焦 service/repository/migration/CLI/Review projection/architecture 回归 `57 passed`；完整 SOC 回归
  `484 passed, 1 warning`；Ruff、frontend `pnpm check` 和 `636 passed` 均通过。warning 是既有 DeerFlow
  MCP cache 的 `asyncio.get_event_loop()` deprecation。
- 下一步：`EV-02` 把 Web/TUI/API 结构化标签录入和 trusted external disposition bridge 接入同一 service；
  不直接进入 auto-close rollout。

### 2026-07-16 — DP-01 shadow disposition proposal implemented

- 新增 vendor-neutral contracts：`SocOperationalDisposition`、`SocDetectionTruthSnapshot`、
  `SocDispositionProposalCommand/Record/ApplyResult`；检测真值与运营处置保持为两个独立字段。
- 新增 `SocDispositionProposalService`：
  - 只消费已持久化的 `AuthorizationEnrichmentRecord`；
  - 仅 linked ReviewQueue 仍为 open、`match_result.status=exact` 且当前 detection truth 为 `true_positive` 时允许提议
    `closed_benign_true_positive`；
  - 保存 matcher/query/fact-version refs 与 detection snapshot；相同 retry key 幂等返回原记录，相同语义 proposal
    使用不同 retry key 时显式冲突，避免返回未持久化的伪别名；
  - queue 缺失/关闭/lineage 错误、partial/conflict/expired/not_found/unavailable、非 true-positive 或无检测结论全部 fail closed。
- 新增 append-only `SocDispositionProposalRepository`、in-memory/SQLAlchemy adapters、
  `soc_disposition_proposals` 和 migration `0015_disposition_proposals`。
- 新增 CLI `soc disposition propose|list|get`；Review API、Web、TUI、统一时间线和 Lead Agent bounded
  artifact 均可只读查看 proposal。
- 安全边界固定为 `proposal_mode=shadow`、`application_status=not_applied`、
  `requires_human_review=true`、`auto_close_allowed=false`、detection/ReviewQueue impact 均为 `none`。
- 收紧 open ReviewQueue 与幂等边界后的聚焦回归 `57 passed`；完整 SOC 回归
  `476 passed, 1 warning`；frontend `pnpm check` 和 `636 passed` 均通过。
  warning 是既有 DeerFlow MCP cache 的 `asyncio.get_event_loop()` deprecation。
- 下一步：`EV-01` 建立 shadow outcome 与评测 gate；在 gate 和 rollback policy 完成前不实现 auto-close。

### 2026-07-16 — EX-01 authorization enrichment persistence/projection implemented

- 新增 strict contracts：
  - `AuthorizationEnrichmentCommand`
  - `AuthorizationEnrichmentRecord`
  - `AuthorizationEnrichmentApplyResult`
- 新增 `SocAuthorizationEnrichmentService`：
  - 只关联已存在且 alert lineage 一致的 run/queue；
  - 保存 canonical query、semantic query hash、typed result、matcher policy、fact-version refs、actor、
    idempotency key 和 replay lineage；
  - 同 key 同输入返回原记录，不同输入 fail-fast；replay append 新记录，不覆盖来源。
- 新增 `AuthorizationEnrichmentRepository`、in-memory adapter、SQLAlchemy 实现、
  `soc_authorization_enrichments` 和 migration `0014_authorization_enrichments`。
- 新增 CLI：
  - `soc context enrich RUN_ID`
  - `soc context enrichment list|get|replay`
- `SocReviewService` 已把 enrichment 投影到 InvestigationContext、UnifiedInvestigationView timeline/counts、
  Review API/Web、TUI 和 Lead Agent bounded artifact。
- 保持边界：`shadow_only=true`、`decision_impact=none`；不写 Runtime decision、ReviewQueue 状态、memory、
  disposition，也不自动关单。
- 验证：authorization/governed-context/TUI 定向 `41 passed`；相关 service/repository/API/Lead Agent/
  architecture 回归 `167 passed`；完整 SOC 回归 `470 passed, 1 warning`；frontend `pnpm check` 通过。
  warning 是既有 DeerFlow MCP cache 的 `asyncio.get_event_loop()` deprecation。全量 DeerFlow
  `tests/` 在首个 blocking-IO router test 长时间无进展后中止，本切片未改该路径。
- 下一步：`DP-01` 只消费持久化 exact enrichment，生成独立 shadow disposition proposal。

### 2026-07-16 — AA-01 deterministic authorized-activity matcher implemented

- 新增独立 `soc_agent.contracts.authorization`：固定 query、逐维度 selector explanation、fact refs 和
  `exact/partial/conflict/expired/not_found/unavailable` 结果；所有合同 `extra=forbid`。
- `AuthorizationQueryBuilder` 只消费 canonical alert/entity/fact/scenario。角色值先判定 IP、asset id 或
  agent id，未知类型不强塞进 IP；无时区 event time 必须由调用方显式给 IANA timezone 并留 warning。
- `AuthorizedActivityMatcher` 按 event time 选择追加式 fact lifecycle 历史版本，检查 lifecycle、business
  validity、source observation/freshness、跨午夜 recurrence，以及 subject/target/behavior scope；不同
  selector kind@namespace AND、同 group 值 OR，CIDR 可匹配 canonical IP。
- 新增只读 `SocAuthorizedActivityService` 和 `soc context match`。Repository 缺失、读取失败或候选截断
  都返回 `unavailable`，不把基础设施故障伪装成 `not_found/exact`。
- 定向合同/生命周期/CLI/架构测试 `35 passed`；SOC 全量加架构回归 `466 passed, 1 warning`，唯一
  warning 是 DeerFlow MCP cache 既有 asyncio deprecation。真实
  `datas/legacy_demos/hids-1965448.json` 与 `datas/legacy_demos/edr-1965810.json` shadow replay
  都为 `exact`，产物位于 gitignored
  `backend/.deer-flow/soc-runtime-validation/step-12-authorization-shadow/`。
- 当前边界：AA-01 结果不写 AnalysisRun/ReviewQueue，不进入 LLM prompt，不生成 disposition，不关单。
  下一刀 `EX-01` 只负责 enrichment persistence/audit/context projection；之后 `DP-01` 才生成
  `closed_benign_true_positive` shadow proposal，`EV-01` 再做上线 gate。

### 2026-07-16 — GF-01 governed context fact lifecycle implemented

- 在字段谱系修复基线提交 `8fbaae7f` 之后，新增独立合同模块
  `backend/soc_agent/contracts/governed_context.py`，避免继续扩张通用 `schemas.py`。首个 typed payload
  `AuthorizedActivityPayload` 明确 activity、subject/target/behavior selectors 和 recurring windows；不含
  PingAn/Zeus aliases，也不实现自然语言万能 matcher。
- 新增 `SocGovernedContextService`：
  - propose/revise 使用 proposal roles；activate/suspend/revoke 使用 context approver/admin；expire 额外
    支持 context service。
  - 稳定 `fact_id` 下每次状态变化追加不可变 `fact_version_id/version`；writer 必须携带
    `expected_latest_version`，stale update fail-fast。
  - activation 要求非空 evidence refs、未结束的 fact validity、可激活 source type 和未 stale source；
    revision 回到 proposed 并清除 reviewer；terminal fact 不可恢复。
- 新增 `GovernedContextFactRepository`、`InMemoryGovernedContextFactRepository` 和
  `SqlAlchemyAlertRepository` 实现；SQL 写入在一个 transaction 中取消 previous latest 并追加 next，
  `current_key` 和 `(fact_id, version)` 唯一约束防止并发双 latest。读取 JSON 后恢复 typed payload 并核对
  索引列。
- 新增 migration `0013_governed_context_facts`、table `soc_governed_context_facts`，并增加
  `soc context propose|revise|activate|suspend|revoke|expire|list|get` 及通用 sample。
- 验证：单独 lifecycle/DB/CLI/migration tests `13 passed`；GF-01 + architecture targeted tests 通过；
  SOC 全量加 architecture 回归 `454 passed, 1 warning`。唯一 warning 是 DeerFlow MCP cache 已有的
  asyncio deprecation，不由本切片引入。CLI/Alembic smoke 产物保存在 gitignored
  `backend/.deer-flow/soc-runtime-validation/step-11-governed-context/`。
- 明确非目标：active fact 尚未进入 `LLMAnalysisRequest`、Runtime decision、ReviewQueue 或 disposition。
  `work04/java -> chattr` 与 RemoteRegistry 业务真值仍未写死；下一刀 AA-01 通过 canonical query 和
  deterministic event-time matcher 接入，并先做 shadow replay。

### 2026-07-16 — Five-sample field-lineage repair completed

- PingAn Adapter 修复：
  - 多条 raw message 形成独立 network/process observations，并以 `observation_scope` 约束角色冲突；
    不再把不同请求的 source/destination 当作同一会话互相冲突。
  - HIDS `external_ip=1.1.1.1` 作为 `SourceFieldSemantic` 标记为青藤默认占位值，禁止进入
    canonical host IP、实体、IOC 和网络对端推理；完整保留 `systemd -> java -> chattr` 节点、PID 和每条消息。
  - `host_md5` 只保留为主机身份摘要语义，不再映射到 file hash；NDR `attack_sip/alarm_sip`
    不再冒充 packet source/destination；SOAR owner/user 只留在 legacy enrichment，不再污染事件 actor。
  - related alert、SOAR 和 workflow 上下文仍保留在完整 raw payload，并通过 coverage 明确标为 deferred
    external context；它们不会静默消失，也不会无界进入基础 Prompt。
- Vendor-neutral Runtime 修复：
  - `FactReconstructor` 仅在同一 observation 内报告角色矛盾；跨 observation 保留并列事实。
  - XFF chain 拆成独立 IP；相对路径、`.html/.php/.txt` 文件名不再误识别为 domain。
  - `BoundedAnalysisEvidence v2` 以结构化 leaf 投影保留合法 JSON，优先跨消息保留高价值字段，精确记录
    projected/sanitized/omitted paths 和 omission reasons；coverage 使用实际投影结果，不再把候选字段误报为已送模。
- LLM 边界修复：
  - Prompt `soc-analysis-v3` 要求一条 evidence 对应一个精确 source path，禁止 description 借同级字段
    引入未引用事实，并明确 HTTP 200 / workflow state 不能证明漏洞利用、命令执行或文件写入成功。
  - grounding 支持 exact `#parsed/#decoded/#repaired` 路径，拒绝 composite source，并把无结果证据支撑的
    正向成功声明标为 `unproven_outcome_claim`；否定和不确定表达不会误触发。
  - JSON parser v4 对模型偶发返回的 bounded dict/list evidence value 做有审计、有限长、无损 JSON
    scalar repair；超限对象继续 schema failure。
- 验证：`ruff format` 无变化，`ruff check` 通过，SOC 全量测试 `431 passed, 1 warning`；唯一 warning
  是 DeerFlow MCP cache 已有的 asyncio deprecation。5 条 deterministic replay 和两条 DeepSeek live replay
  保存在 gitignored `backend/.deer-flow/soc-runtime-validation/step-10-five-sample-repair/`。
- 结果边界：HIDS live model 已正确忽略 `1.1.1.1` 并识别两次不同 PID 的进程执行，但仍给出
  `suspicious`，因为“周期性内部预期行为”尚未作为 event-time governed fact 进入 bounded input。
  该业务真值不能写死在 PingAn Adapter/通用 Runtime；下一刀由 `GF-01 -> AA-01` 提供可审计匹配。

### 2026-07-16 — Governed context fact and calibration boundary decision

- 问题：逐告警人工确认授权扫描、内部服务和运维活动不可扩展；但把一次确认写成永久 IP 白名单、
  confirmed memory 或 Prompt 又会造成跨时间/目标/行为误放行。
- 决策：新增 vendor-neutral `GovernedContextFact` typed envelope，由 `SocGovernedContextService`
  管理 propose/activate/suspend/revoke/expire/version/query；`AuthorizedActivityFact` 是第一个类型，
  通过 `SocAuthorizedActivityService` + deterministic matcher 做 subject/target/behavior/event-time 匹配。
  公共 lifecycle 可以复用，但每种 fact 必须有强类型 payload 和专用 matcher，禁止万能自然语言 matcher。
- 护网扩展：新增 planned `SecurityExerciseCampaignFact`、`ExerciseParticipantFact` 和
  `ParticipantAttributionResult`。红/蓝/白队 IP 只能证明事件时间内的 participant attribution；必须再匹配
  campaign、目标、行为、禁用技术和授权，才能提议 `authorized_security_exercise` 良性真阳处置。
- 语义分离：detection truth 与 operational disposition 分开。真实攻击/操作行为仍为
  `true_positive`；在事件时间、范围和来源都 exact match 时，只提议
  `closed_benign_true_positive`，不得改写成 `false_positive`。
- 人力策略：一次人工确认先形成有时效的 fact proposal，经授权角色激活；后续 exact match 复用，
  仅 partial/new pattern/scope mismatch/expired/revoked/conflict/source unavailable 和随机审计样本进人工。
- 校准策略：业务真值已知但决定性授权事实没有出现在当次 bounded model input 的样本，保留
  `actual_verdict/actual_disposition`，但标记 `excluded_missing_decisive_context`，不进入 analyzer
  Brier/ECE/threshold fitting；该样本转入 enrichment coverage 和 end-to-end disposition eval。
- 推进顺序：当前先完成同版本 5 样本字段谱系审阅与集中修复，之后按
  `GF-01 -> AA-01 -> EX-01 -> DP-01 -> EV-01` 实现；
  auto-close 前必须经过 shadow precision、analyst override、freshness、随机抽样和 rollback gate。

### 2026-07-16 — Live sample field-lineage audit in progress

- 审阅方法固定为：`raw alert/message -> parsed/decoded/repaired -> canonical entities -> entity extraction -> fact/scenario -> bounded LLM evidence -> model decision`，不只看最终 verdict。
- 人工标签状态：
  - `apt-1965449`：业务确认 `vendor=pingan_ad`、`domain=guanbi`、HeadlessChrome 为平安内部自动化/测试客户端；模型 `suspicious`，人工真值 `false_positive`。
  - `apt-2025642`：业务确认 `paic.com.cn/pws/askbob-gpt` 为平安内部 LLM 调用；packet bytes 可独立验证 `30.116.114.150 -> 30.174.29.44:9092`；模型 `suspicious`，人工真值 `false_positive`。
  - `apt-2026494`：真实 PbootCMS/PHP 利用载荷成立，但来源是否为授权扫描/渗透测试尚未确认；需区分 `true_positive` detection 与 `closed_benign_true_positive` operational disposition，当前保持 pending。
  - `apt-2026494` 最终 detection label：两条独立 HTTP request 都包含 PbootCMS/PHP `file_put_contents` + `file_get_contents` 文件写入利用载荷，因此 exploit attempt 为 `true_positive`；Nginx/ASP.NET 返回 IIS default page，结合 `失败企图/企图` 只能证明 HTTP 200，不能证明 PHP 执行或 webshell 写入成功。是否为授权扫描仅影响 operational disposition，不影响 detection label，因此该样本 accepted。
  - `edr-1965810`：业务确认 `30.162.29.85` 为平安内部服务，并以 RemoteRegistry 已授权作为该验证样本真值；行为检测为 `true_positive`、运营处置为 `closed_benign_true_positive`。授权事实未出现在当次 bounded input，因此记录真值但以 `excluded` 排除出 analyzer confidence calibration，待 enrichment 后重跑。
  - `hids-1965448`：业务确认 `work04` 周期性 `java(3065) -> chattr` 为预期内部业务，`external_ip=1.1.1.1` 是青藤 HIDS 默认值而非网络对端/IOC；行为检测为 `true_positive`、运营处置为 `closed_benign_true_positive`。周期业务、历史人工结论和字段默认值语义未进入 bounded input，因此标记 `excluded`。
  - 只更新 gitignored `label-set.pending.json` 的 `actual_verdict/review_status/reviewer/time/reason`；尚未修改 Adapter/Runtime，也未重跑模型。
- 已确认的修复台账：
  - PingAn Adapter：移除 `host_md5 -> file.md5` 错误映射；补 rule version/MITRE aliases/资产上下文；把 PingAn 外层加工字段与 Message 原始网络观察分层；将 `packet_data` 接入通用 packet evidence contract；不得把受害/source IP 默认写成 IOC。
  - PingAn EDR Adapter：区分 asset/logged-on owner、process user、parent-process user，不能把 `WANGJIAN191`、`LOCAL SERVICE`、`SYSTEM` 压成一个 actor；保留 parent hash、process ids、MAC 和 event interval；把仅在描述中的 `ntoskrnl.exe` 标为 vendor-described process observation，而不是与 `svchost.exe` 合并。
  - Vendor-neutral Runtime：按 evidence layer/trust 解释冲突，不隐藏冲突也不让低信任 fallback 无差别推翻高信任观察；为 packet/session/proxy hop 和 per-message observation scope 提供通用证据语义；同一事件的字段 aliases 不能冒充独立佐证；修正 `代码执行 -> web_attack` 的过宽规则。
  - Entity extraction：文件名（`*.html/*.php/*.txt/*.exe`）不能误识别为 domain；XFF/proxy chain、payload-embedded IP/file/path 与真实 network peer 必须使用不同 role。
  - LLM validation：结构化证据以外的 summary/reason 也不得引入未落地地理/资产结论；HTTP 200 不等于认证、利用或写文件成功。
- `hids-1965448` 新增集中修复项：
  - `relatedAlertList` 和 SOAR enrichment 虽保留在 raw `input_payload`，但未进入 bounded context，coverage 却错误显示 `omissions=[]` / `high_value_gaps=[]`；后续要输出显式 omitted/external-context coverage，并由 correlation/investigation context 受控接入，而不是直接塞进 base Runtime prompt。
  - 历史 disposition 必须区分人工结论与 `zeusai` 自动继承结论，避免“根据历史忽略”循环自证；保留 operator/source/lineage 和独立证据计数。
  - HIDS process evidence 不能只压成 `java -> chattr`；应保留 per-message `systemd/java/chattr` 节点、PID、事件时间和稳定/变化字段，供周期模式关联。
  - `external_ip=1.1.1.1` 必须在 PingAn HIDS adapter 标为 default/placeholder 并禁止进入 canonical host IP、IOC 和 LLM 网络对端推理；source/message-header/host/UCMDB aliases 需要 typed role 或 conflict explanation。
  - Evidence grounding 不能只验证 cited value；description 使用另一个 source 的 spring/jackson 规则和“隐藏恶意文件”等推断时，必须校验 citation/claim lineage，不能因 value 存在就全部记为 grounded。
- `apt-2026494` 新增集中修复项：
  - 两条独立请求分别来自 sensor-observed `30.180.248.178/.177`，共享 XFF chain `182.16.91.214,30.185.76.57`；当前被压成一个 canonical source 加 alternative/conflict。应保留 per-message network observations、trusted-proxy hops 和 original-client candidate，不能把多请求或代理链默认当字段冲突。
  - bounded evidence 截断导致第二条请求中最关键的 request start-line 无法 grounding；投影预算应按 message 保留高价值字段，再裁剪低价值长文本，不能让第一条 message 吃完预算。
  - `news.html`、`fireworks123.php`、`shell.txt` 被误抽成 domain，`host_md5` 被误映射为 file MD5；分别修 entity parser 和 PingAn adapter mapping。
  - HTTP 200 必须标为 transport/application response，不等于模板执行、文件写入或 webshell 成功；response body/server stack、`host_state=企图`、`失败企图` 应进入 success-semantic guard。
  - `is_blocked/is_banned` 和“告警转生产”是 vendor workflow/action observation，不是攻击成功、处置完成或 analyst verdict；需要 typed provenance，不能混入 detection truth。
  - composite citation（一个 evidence item 声明多个 source path）当前 grounding 失败；contract/prompt 应要求结构化 source refs 或一条 evidence 对应一个可解析路径，description 中的推断也要单独标注。
- 实施顺序：先完成同一代码版本下的 5 条审阅，再集中修复、补回归测试、重跑 5 条并输出 before/after diff，避免边审边改导致样本不可比。
- 人力扩展原则：不要求逐告警确认。优先查询 authoritative security tag/asset/change/scan-task evidence；按 detection/scenario/entity/authorization scope 聚类，一次人工结论只在明确时间、目标和任务范围内复用；只把新模式、范围不匹配、授权过期、证据冲突和随机审计样本送人工。LLM/历史反馈只能提 suppression/memory candidate，不能直接生成永久 IP 白名单。
- 当前 label validation：5 条中 3 accepted、2 excluded、0 pending，accepted 真值为 2 `false_positive` + 1 `true_positive`，`calibratable=true` 且无 warning；样本量仍过小，只用于离线 smoke 和修复前后比较，不接生产 profile。

### 2026-07-15 — Governed confidence labels and live-model reliability follow-up

- 新增人工标签治理边界：
  - `ConfidenceCalibrationLabelSet` / `ConfidenceCalibrationSample.v2` 保存 run/input hash、
    model/prompt/pipeline 版本、预测摘要、证据落地计数和人工审阅字段，不复制 raw payload。
  - `pending_review` 不能校准；accepted 必须有确定 verdict、reviewer、时间和理由；无法确定的样本
    使用 excluded。重复 input hash 与混合 model/prompt/pipeline scope 会阻断 calibration。
  - 新增 `soc eval labels prepare PATH` 与 `soc eval labels validate LABEL_SET.json`；
    `soc eval confidence` 改为只消费完成校验的 label-set envelope，并输出 dataset hash 和 profile scope。
- 真实样本验证：
  - 使用 `deepseek-v4-pro`、`soc-analysis-v2`、`soc-runtime-v1` 成功运行
    `datas/legacy_demos/` 中 5 条
    APT/EDR/HIDS 样本。
  - gitignored 评审入口：
    `backend/.deer-flow/soc-runtime-validation/step-09-confidence-labeling/label-set.pending.json`。
  - 当前 5 条均为 pending，validation 明确 `calibratable=false`；未伪造人工 ground truth。
- 真实调用暴露并修复：
  - parser 升级为 `soc-analysis-json-parser-v3`，只允许有 repair log 的无损白名单语义修复：
    单元素 verdict 数组和单元素 evidence value 数组解包；多元素数组继续失败。
  - 新增 `SOC_LLM_CALL_TIMEOUT_SECONDS`（默认 180 秒），与 admission timeout 分离；模型调用
    超时进入 retryable `analyzer_timeout`。executor worker 数受 `SOC_LLM_MAX_CONCURRENCY` 限制。
- 已验证（聚焦）：
  - confidence labels / normalization maintenance：12 passed。
  - parser / confidence labels：16 passed；新增 evidence-value repair 后 parser：13 passed。
  - DeerFlow LLM client / compose / K8s / analyzer：17 passed。
- 下一步：
  - 分析师逐条审阅 5 条 pending labels 并给出真实结论；验证通过后运行小样本 calibration smoke。
  - 继续扩充跨来源、跨场景、包含正负样本的代表性标签集；未达到治理样本量前 profile 不接 Runtime。

### 2026-07-14 — SOC Runtime production hardening completion

- 问题：
  - LLM evidence 只做了非空校验，模型可以引用 bounded input 中不存在的值；skill resolver 隐藏在
    `build_analysis_input` 内，trace 不可见；prompt/output 缺少完整硬上限。
  - Runtime failure 只有 step error 字符串，Kafka 会把失败分析当 processed 并 commit；run/summary/
    review/audit 分别 commit，任一中间失败会留下半套业务状态。
- 实现：
  - Runtime pipeline 升级为 `soc-runtime-v1`：增加显式 `skill_context` 和 `evidence_grounding` step；
    `soc-analysis-v2` prompt 和 grounding 共用同一 `project_analysis_context()`。
  - 新增 `AnalysisEvidenceGroundingReport`，逐条验证 evidence source/path 和 value；未落地证据进入
    `ungrounded_analysis_evidence` review reason。`AnalysisResult`、evidence、knowledge candidate、prompt
    context 和 model response 均增加硬上限。
  - 新增 `SocLLMAdmissionController` 及 concurrency/RPM/admission-timeout 环境配置；容量饱和、超时、
    provider unavailable、bad output、input limit 等进入 typed/sanitized `RuntimeFailure`。
  - Kafka worker 对 retryable failure 不 commit；non-retryable failure 进入 DLQ。可重试 failed run 允许
    同 idempotency key 重新执行且不立即创建人工工单；不可重试 failure 进入 ReviewQueue。
  - 新增 `AnalysisPersistence.save_analysis_bundle()`；SQLAlchemy 将 run/summary/optional review/audit
    作为一个事务提交，Normalization Monitor 保持 fail-open 后置处理。
- 验证：
  - 完整 SOC + architecture regression：`422 passed`；仅 1 条既有 DeerFlow MCP cache asyncio
    deprecation warning。Ruff format/check 和 `git diff --check` 通过。
  - 新增 evidence hallucination、source mismatch、composite evidence、response oversize、admission budget、
    retry/no-commit、non-retry review、idempotent retry 和 SQL bundle rollback 覆盖。
  - 5 条 `datas/legacy_demos/` 均按 `normalize -> entity_extract -> fact_reconstruct -> build_analysis_input ->
    skill_context -> analyze_stub -> schema_validate -> evidence_grounding -> decide` 完成，均无 failure，
    stub evidence 全部落地。
  - `deepseek-v4-pro` live smoke 完成并进入 `needs_review`；当前 grounding 对保存的 live analysis 重检为
    `6 grounded / 1 ungrounded`，未落地项是模型合并的 role-resolution 自然语言，不允许高分掩盖。
    本地 DeerFlow `config.yaml` 已从 v9 安全升级到 v24，并保留 `config.yaml.bak`。
- 下一步：
  - 用 5 条 `datas/legacy_demos/` 先完成技术 smoke，再由用户逐条审阅真实 LLM 输出建立人工 label set；只有标签量和
    calibration 指标达到要求后，才讨论 versioned profile，当前继续全量人工复核。

### 2026-07-14 — Deterministic decision policy and confidence guard

- 问题：
  - Runtime 原先直接用 `AnalysisResult.confidence < 0.75` 决定是否复核，模型自报分数没有来源、
    校准状态或 policy version；高分 false positive 可能绕过 ReviewQueue。
  - domain/scenario 研判可能把成功 mock 或失败 action payload 当成真实外部事实，抬高 finding confidence。
- 实现：
  - 新增 public `DecisionPolicy` protocol 和 `SocDecisionPolicy`，Runtime 固定 `decide` 节点只调用该策略；
    `Decision` 新增 confidence provenance、calibration、evidence state、structured review reasons 和 policy version。
  - 当前 stub heuristic 与 LLM self-report 一律标记 uncalibrated 并进入人工复核；false positive、fact
    conflict、schema degraded/unsupported、high-value gap、evidence truncation 和低 raw score 分别保留原因。
  - `AlertSummary`、`ReviewQueueItem`、timeline 和 decision audit 继承结构化原因，不再只剩一个自由文本原因。
  - `InvestigationEvidence` 新增顶层 `mocked`；domain/scenario 仅采信 `status=success && mocked=false`
    的只读证据。成功 mock 只用于 demo/audit 可见性，failed/denied 不进入 finding 已采信证据集合。
- 验证：
  - 完整 SOC + architecture regression：`410 passed`；仅 1 条既有 DeerFlow MCP cache asyncio
    deprecation warning。
  - 回归覆盖真实 LLM uncalibrated review、false-positive confirmation、summary/queue/audit persistence、
    mock/failed evidence eligibility 和 scenario confidence。
- 下一步：
  - 增加 LLM evidence grounding contract/validator，检查 evidence item 是否能回指 bounded input；随后用
    5 条 `datas/legacy_demos/` 建立人工标注集并运行离线 calibration，不打开自动处置。

### 2026-07-14 — DeerFlow-backed live SOC Runtime LLM

- 问题：
  - `JsonLLMAnalyzer` 之前只有 client protocol 和 fake/replay test，没有调用 DeerFlow 模型注册表的生产 client；
    CLI/Kafka 仍实际构造 deterministic stub。
  - `soc normalize suggest` 只能读取 replay response，不能使用已配置模型生成候选。
- 实现：
  - 新增 `DeerFlowLLMChatClient`，复用 `deerflow.models.create_chat_model()`、模型配置、API key 解析和 tracing；
    按模型名缓存实例，只记录 allowlisted response metadata 和 token usage。
  - 新增 `SocLLMSettings`：`SOC_ANALYZER_MODE=stub|llm`、`SOC_LLM_MODEL`、thinking/tracing 开关；
    CLI `analyze/replay/demo alert/daemon` 支持 `--analyzer-mode` / `--model-name` 覆盖。
  - `soc llm status` 输出无 secret 的模型解析状态；未知模型 fail-fast，不静默 fallback。
  - `soc eval offline --live-llm` 可对样本集调用真实模型；与 replay JSONL 互斥。
  - `soc normalize suggest --live-llm` 可调用真实模型，仍校验 observed path/canonical whitelist，
    `auto_apply_allowed=false`，并记录 duration/usage/safe response metadata。
  - Compose/K8s daemon 模板增加 analyzer/model 配置；K8s Secret 预留 provider key，daemon 启动前验证模型注册。
- 真实验证：
  - DeerFlow 配置识别 `deepseek-v4-flash` 和 `deepseek-v4-pro`；本轮显式使用 `deepseek-v4-pro`。
  - `datas/legacy_demos/apt-1965449.json` 真实分析成功：`analyze_llm` parser 无 repair，最终
    `needs_review`、
    `automation_allowed=false`；约 18.75 秒、8,949 input tokens、1,064 output tokens。
  - live normalization suggestion 成功，返回 31 条 governed candidate，全部仍需人工评审且不可自动应用。
- 边界：
  - `asset/EDR/HIDS/TI/security-tag/Zeus disposition` 仍等待真实 endpoint/凭证；LLM 不得伪造这些外部事实。
  - 根据 `.notes/ai_soc/README.md` 指向的 mock/real 权威台账复核代码：高风险响应动作目前只完成
    proposal/policy/approval/grant/dry-run/preflight，尚未执行真实外部副作用；已补入台账。
  - 明确 deterministic normalizer/entity/fact 节点、disabled-mode Kafka adapter、SOC SQLite 和 DeerFlow
    Lead Agent 的性质，避免把“不调用 LLM”或“本地模式”误判为未完成 mock。
- 验证：
  - 完整 SOC regression：`406 passed`；仅 1 条既有 DeerFlow MCP cache asyncio deprecation warning。
  - Ruff check/format、daemon shell syntax 和 Compose overlay config 通过；overlay 服务列表包含 `soc-daemon`。
- 下一步：
  - 用 5 条 `datas/legacy_demos/` 建人工标注集，评审 verdict/evidence/recommended action，确定 token 裁剪、并发和 confidence calibration 策略。

### 2026-07-14 — Normalization maintenance and calibration loop

- 问题：
  - 原有 `normalize drift --schema-baseline` 只能人工离线比较，Runtime 即使发现 unsupported/degraded/
    high-value gap 也不会形成可领取、可去重、可关闭的维护工作。
  - hardcoded high-value 字段规则无法扩展到新供应商；LLM 辅助 mapping 和 confidence 阈值也缺少
    离线、不可自动生效的工程边界。
- 实现：
  - 新增 versioned `NormalizationSchemaBaseline` 和 deduplicated `NormalizationMaintenanceIssue`，Alembic
    `0012_normalization_maintenance` 持久化两类对象；基线仅 `soc_engineer/soc_admin` 可接受，新版本
    supersede 旧版本并关闭 covered missing/novel issue，resolved/ignored issue 复发会 reopen。
  - `SocAnalysisService` 在业务 run/summary/review/audit 写入后调用 maintenance monitor；监控失败 fail-open，
    只写 `NormalizationMonitoringResult.warnings`。CLI/Kafka 持久化入口已注入 monitor。
  - 新增 Gateway `/api/soc/normalization/{baselines,issues,metrics}`、CLI baseline/issues/update、Review TUI
    `/normalization` / `/norm-update`，以及 Web `/workspace/soc/normalization`。
  - Kafka process result/JSONL metric 带 normalization issue count/IDs/warnings，不带 raw message。
  - `EvidenceFieldImportanceRegistry` 取代 coverage 内硬编码检查；PingAn HTTP User-Agent/XFF 已映射到
    canonical HTTP entity，adapter 可通过 typed extension 增加供应商规则。
  - `soc normalize suggest` 构造不含值的离线路径 prompt，LLM replay 结果必须通过 observed source path 和
    canonical target whitelist；所有输出不可 auto apply。
  - `soc eval confidence` 输出 accuracy/Brier/ECE/bins/versioned review threshold；小样本/单一类别 warning，
    `auto_action_allowed=false`。
  - nested JSON repair 增加 field-specific root、depth、node、key/value source-evidence domain guard。
  - 新增 `backend/scripts/generate_soc_normalization_maintenance_validation.py`，可从
    `datas/legacy_demos/` 一次重生成
    gitignored Step 2-5 contract 快照；Step 5 显示每条真实样本触发的 maintenance issue，便于逐步审阅。
- 验证：
  - backend Ruff check passed；完整 SOC suite `396 passed`。
  - frontend `pnpm check` passed；Alembic head 在全新 SQLite 文件升级成功。
  - `cd backend && UV_CACHE_DIR=/tmp/deer-flow-uv-cache uv run python -m scripts.generate_soc_normalization_maintenance_validation`
    已重生成 5 条 `datas/legacy_demos/` 样本的 Step 2-5 工件；`apt-1965449` 的 canonical/LLM request 已包含
    User-Agent，且 `high_value_gaps=[]`。
  - 已运行 `codegraph sync .`，新增验证脚本和本轮最终符号进入本地 CodeGraph 索引。
- 下一步：
  - 继续按 `datas/legacy_demos/` 做 analyze/validate/decide 原始 contract 审阅；收集分析师标签后运行第一版
    confidence calibration，不将 provisional threshold 写入生产配置。

### 2026-07-14 — Message schema drift and evidence coverage report

- 背景：
  - PingAn message 外层结构可以解析，但嵌套 body 可能损坏；同时“解析出了字段”不代表 canonical、事实重建、场景识别和 LLM 都实际使用了它。
  - 新厂商/新版本结构必须可发现，但不能在没有已验收 baseline 时把任意首次观察误报成新 schema。
- 变更：
  - 新增 `MessageSchemaObservation` 和 `MessageSchemaStatus`，按 message 记录 parser/version、结构 fingerprint、field count、recognized/degraded/unsupported 和 warnings。
  - `NormalizationReport` / drift report 聚合 schema fingerprint/status；`SocNormalizationService.drift()` 支持 accepted fingerprint baseline，CLI 新增 `--schema-baseline`，novel fingerprint 进入 `suspicious_samples`。
  - 新增 `EvidenceCoverageReport`：记录 parsed/decoded、canonical/fact/scenario、bounded LLM projection、sanitization、truncation、omission 和 high-value gap。
  - `ParsedRawMessageEvidence` 升级到 v2：新增 `repaired_fields` 和
    `NestedJsonRepairObservation`；PingAn parser version 升到 v2。repair 仅在根类型、非空结构和 key
    source evidence 校验通过后 accepted。
  - strict nested JSON 失败继续保留原始字符串；保守验收的 `json_repair` 结果进入独立
    `repaired_fields`，拒绝/失败时 bounded evidence 使用脱敏字符串 fallback；repair 永不冒充
    strict-decoded source fact。
  - 完整 coverage 留在 Runtime request/验证产物；Prompt Builder 只提供不含 vendor field path 的紧凑摘要。
  - 权威方案和工程契约新增 confidence taxonomy：evidence trust、schema status、heuristic scenario/role score、LLM verdict confidence、memory confidence 不得跨层混算。
- 真实样本：
  - 已重生成 gitignored Step 2，并新增 `backend/.deer-flow/soc-runtime-validation/step-04-build-analysis-input/`。
  - `apt-1965449` schema 为 degraded；错误造键的 `req_body` repair 被拒绝并以脱敏字符串参与分析，
    截断的 `rsp_body` repair 被接受、结构化并脱敏；coverage 同时暴露 parsed/decoded/repaired 路径和
    尚未映射 canonical HTTP entity 的 User-Agent gap。
- 验证：
  - 定向 parser/runtime/prompt tests：`48 passed`；Ruff format/check passed。
  - 完整 SOC regression：`383 passed`；仅 1 条既有 DeerFlow MCP cache asyncio deprecation warning。
  - `datas/legacy_demos/` baseline replay：5 个样本、6 个 accepted fingerprints、0 个 novel fingerprint；7 个
    recognized message observations、1 个 degraded observation，只有 nested body 损坏的
    `apt-1965449` 进入 suspicious samples。
  - parser v2 会有一次预期 fingerprint 变化；使用 v2 样本重建 baseline 后 replay 仍为 0 个 novel
    fingerprint。
- 下一步：
  - 运行完整 SOC regression 和 CodeGraph sync；用户继续按真实 contract 输出审阅 Step 2/3/4，不提前评判最终 verdict。

### 2026-07-14 — Conflict-aware Fact Reconstruction v2 and Step 3 output snapshots

- 背景：
  - Step 2 实测发现旧规则把 `attacker != source`、`victim != destination` 一律视为冲突，会把反弹
    Shell、恶意外联、C2、横向移动、代理/NAT/XFF 等合法方向关系误报。
  - 旧实现即使报告冲突仍会输出看似确定的 role assignment，并把 evidence trust 与角色语义置信度混为一谈。
- 变更：
  - `FactReconstructionResult` 升级到 v2：引入 `RoleClaim`、`ScenarioSignal`、
    `ScenarioHypothesis`、`RoleResolution`、`CanonicalFieldProvenance`；角色状态明确区分
    observed/tentative/conflicted/confirmed/unresolved。
  - PingAn 字段别名只在 `normalizers/pingan_evidence.py` 内转换成 vendor-neutral claims；generic
    fact reconstructor 不再识别 `attack_sip` / `alarm_sip` / `str_attack_ip` 等字段。
  - 反弹 Shell 识别为 `reverse_connection`，采用 `source=victim`、`destination=attacker` 约束；删除全局
    `attacker_source_mismatch` / `victim_destination_mismatch` 假设。
  - 主 message、supplementary messages、structured fallback 全部参与 claim/conflict；冲突输出暂定值、
    支持/反对 claims、证据缺口、人工核查清单和 automation guard。fact layer 永不确定 response target，
    也不允许自动处置。
  - PingAn parser 新增 allowlisted nested JSON、HTTP header、XFF chain 解码；bounded evidence 用脱敏
    decoded projection 替换 raw body/header，token/cookie/password 不进入模型上下文。
  - normalization quality rule 改为 source-aware，HIDS 不再因为缺少 network source/destination 被误报。
- 真实样本结果：
  - `apt-2025642`：识别 `reverse_connection`；暂定 source/victim/impacted asset=`30.116.114.150`，
    destination/attacker=`30.174.29.44`；保留 message 与 structured fallback 的真实方向冲突，不产生伪
    `attacker_source_mismatch`。
  - `apt-2026494`：保留 source/attacker 多候选冲突；`edr-1965810` 识别 outbound C2 + lateral movement；
    `hids-1965448` 识别 command execution 并暂定受影响主机，网络方向保持 unresolved。
  - 本地、gitignored 原始契约输出快照位于
    `backend/.deer-flow/soc-runtime-validation/step-03-fact-reconstruction/`；每份样本的
    `fact_reconstruction` 都是 `FactReconstructionResult.model_dump(mode="json")` 的直接结果，
    不包含审阅聚合、中文解释或人工结论，不提交真实告警衍生数据。
- 验证：
  - 定向 parser/prompt/runtime tests：`43 passed`；nested/supplementary/provenance/reverse-shell regression
    tests 通过。
  - `PYTHONPATH=. .venv/bin/python -m pytest tests/test_soc*.py tests/architecture/test_soc_agent_boundaries.py -q`：
    `379 passed`；仅 1 条既有 DeerFlow MCP cache asyncio deprecation warning。
- 下一步：
  - 用户逐条验证 Step 3 原始输出；确认后把状态改为 passed，再进入 analyzer/decision Step 4。

### 2026-07-14 — PingAn raw-message parsing and evidence-priority repair

- 背景：
  - 使用 `datas/legacy_demos/` 的 3 条 APT、1 条 EDR、1 条 HIDS 样本逐步测试 Runtime 时发现：旧实现只把
    `message` 路径标成 high trust，没有解析正文，也没有把正文放进 `LLMAnalysisRequest`。
  - HIDS 的 IP、host 和 process tree 因此完全漏提取；Fact Reconstruction 还会把 Zeus
    structured fallback 错误继承为 raw-message high trust。
- 变更：
  - 新增 `soc_agent.normalizers.pingan_messages` parser registry，支持 delimited JSON、quoted KV、
    comma-delimited KV 和 loose KV；解析结果写入 `ParsedRawMessageEvidence`，不复制或替换原文。
  - PingAn adapter 使用 parsed message 字段覆盖同语义 Zeus 加工字段；保留平台 `ruleCode` 作为
    detection identity；新增 HIDS host IP 和 `java -> chattr` process-tree 映射。
  - 多 message 记录 deterministic primary/supplementary paths；`LLMAnalysisRequest` 新增限长的
    `BoundedAnalysisEvidence` 内容。
  - nested body 严格 JSON 解析失败时保留原始字符串和 warning；后续 repair projection 切片补充为：
    保守验收成功的 repair 单独保存并进入 bounded evidence，拒绝/失败时使用脱敏字符串 fallback。
  - 修正 Fact Reconstruction 优先级：parsed raw message/high > Zeus structured fallback/medium-low
    > processed canonical/low，并为解析失败输出显式 warning。
  - 原始输入继续完整保存在 `AlertInput.raw` 和 `AnalysisRun.input_payload`，供持久化、审计和 replay。
- 验证：
  - `PYTHONPATH=. .venv/bin/python -m pytest tests/test_soc*.py -q`：`367 passed`，仅 1 条既有 MCP
    event-loop deprecation warning。
  - 定向 Runtime/Prompt/parser 测试：`41 passed`；Ruff checks passed；`git diff --check` passed。
  - `datas/legacy_demos/` 5/5 样本解析成功：APT -> `pingan_delimited_json`，EDR -> `pingan_comma_kv`，HIDS ->
    `pingan_quoted_kv`；HIDS 现可提取 host IP、host name、`java/chattr` process；多日志进入
    supplementary evidence；所有样本 `raw_preserved=true`。
  - 已使用当前代码重生成
    `backend/.deer-flow/soc-runtime-validation/step-02-message-parsing/`：每份文件中的
    `normalization_inspection` 都直接等于 `SocNormalizationService.inspect().model_dump(mode="json")`；
    `alert.extensions.parsed_raw_messages[].fields/decoded_fields` 展示完整第一层解析和受控二次解码结果。
- 下一步：
  - 先逐条验证 Step 2 message 解析和 canonical normalization，再继续 Step 3 的 role claims、
    attack/victim direction 和 conflict reports；不在这些事实语义确认前继续评判 stub/LLM verdict。

### 2026-07-08 — Management-facing Runtime/LLM control strategy doc

- 背景：
  - 用户希望有一份 `.md` 用来向老板解释：SOC Agent 为什么不是让 Lead Agent / LLM 完全自主控制流程，而是采用 Runtime-first + bounded LLM。
- 变更：
  - 新增 `.notes/ai_soc/runtime-and-llm-control-strategy.md`，面向管理层说明业务风险、架构取舍、LLM 使用位置、PingAn/未来客户兼容性、阶段策略和成功指标。
  - 更新 `.notes/ai_soc/README.md`，把该文档加入 Start Here、Directory Map 和 Document Roles。
- 验证：
  - 文档口径已对齐 `.notes/ai_soc/soc-agent-solution.md` 与 `.notes/ai_soc/alert-lifecycle-flow.md`：Runtime 掌握主流程，LLM/Lead Agent 只做受控研判和结构化建议。
- 下一步：
  - 后续如果老板认可该方向，继续按 `progress.md` 当前待办推进 Alpha 演示链路和 Web/TUI 可见化。

### 2026-07-08 — SOC solution review baseline rewrite

- 背景：
  - 用户要求 `.notes/ai_soc/soc-agent-solution.md` 成为后续 review 的主方案文档，需要中英文术语对照、结构化流程图、模块说明和清晰边界。
  - 旧文档混合了历史计划、阶段流水账、路线争论和已完成状态，内容很全但不适合作为评审入口。
- 变更：
  - 重写 `.notes/ai_soc/soc-agent-solution.md`，定位为 `Active review baseline`。
  - 新增中英文术语对照：Alert、Evidence Layer、Field Trust、Review Queue、SOC Lead Agent、Skill、MCP、Approval、Memory Candidate 等。
  - 新增 Mermaid 架构图、端到端流程图、runtime pipeline、Lead Agent/Skill/MCP/Approval、memory lifecycle、PingAn capability onboarding 等 review 图。
  - 明确模块边界：entry layer、core service、runtime、normalizer/evidence、review/approval/external feedback、memory、PingAn capability layer。
  - 把进度流水账职责移回本文件，把详细生命周期职责指向 `.notes/ai_soc/alert-lifecycle-flow.md`。
- 验证：
  - `rg` 检查主文档和索引文档不再引用旧的 `.notes/ai_soc/*` 平铺文档路径。
  - `git diff -- .notes/ai_soc/soc-agent-solution.md` 人工检查主文档从 2517 行混合草案收敛为 750 行 review baseline。
- 下一步：
  - 后续 review 以 `.notes/ai_soc/soc-agent-solution.md` + `.notes/reference-index/soc-agent-engineering-contracts.md` 为主，具体进度继续写入本台账。

### 2026-07-08 — AI SOC notes structure cleanup

- 背景：
  - 用户要求整理 `.notes/ai_soc`，避免主线目录继续堆放平行方案、专项计划和源资料。
- 变更：
  - 保留根目录主入口：`README.md`、`soc-agent-solution.md`、`progress.md`、`alert-lifecycle-flow.md`。
  - 平安专项移动到 `capabilities/pingan/`：
    - `onboarding.md`
    - `knowledge-decomposition.md`
    - `capability-cards.md`
    - `knowledge-candidates.md`
    - `source-docs/`
  - 外部系统与 mock/real 台账移动到 `integrations/`。
  - 记忆方案移动到 `memory/memory-tracking.md`。
  - Agent/Skill/MCP 治理移动到 `governance/agent-profile-governance.md`。
  - 重写 `.notes/ai_soc/README.md`，新增目录地图和使用路径。
  - 更新 `.notes/README.md`、`soc-agent-solution.md`、工程契约和进度台账里的旧路径引用。
- 验证：
  - `rg` 检查旧路径/旧文件名无残留。
  - `git diff --check -- .notes/ai_soc .notes/README.md .notes/reference-index/soc-agent-engineering-contracts.md`
- 下一步：
  - 后续新增 SOC 文档优先放入现有主题目录；只有真正的主入口才留在 `.notes/ai_soc/` 根目录。

### 2026-07-08 — Review note memory source and single-alert demo

- 背景：
  - 用户要求把剩余三刀做完：ReviewQueue review note -> pending memory candidate、单告警 demo/runbook 命令、review context 展示优化。
- 变更：
  - 新增 `ReviewNoteCommand` / `ReviewNoteResult`，并新增 `SocMemoryCandidateSourceType.REVIEW_NOTE`。
  - `SocMemoryCandidateSourceBridge` 新增 review note factory/bridge，生成 `pending_review` candidate，带 queue/run/alert/scenario/domain/finding facets、evidence refs 和幂等 key。
  - `SocReviewService.add_note()` 成为 ReviewQueue note -> memory candidate 的 service 边界；入口层不直接写 memory repository。
  - CLI 新增 `soc review note QUEUE_ID --note ...`，并为 `soc review context` 增加 `--summary` compact view。
  - CLI 新增 `soc demo alert PATH|--json`，可持久化一条 alert，输出 run/queue/review summary/domain findings/memory candidates/next commands；可选 `--review-note` 验证 note candidate 链路。
- 验证：
  - `env UV_CACHE_DIR=/tmp/deer-flow-uv-cache uv run pytest backend/tests/test_soc_agent_service.py -q`
  - `env UV_CACHE_DIR=/tmp/deer-flow-uv-cache SOC_DATABASE_URL=sqlite:////tmp/soc-demo-alert.db uv run python -m soc_agent.cli demo alert samples/alerts/pingan_legacy_apt.json --init-db --pretty`
  - `env UV_CACHE_DIR=/tmp/deer-flow-uv-cache SOC_DATABASE_URL=sqlite:////tmp/soc-demo-alert-note.db uv run python -m soc_agent.cli demo alert samples/alerts/pingan_legacy_apt.json --init-db --review-note 'Analyst says raw message direction wins over derived fields.' --scenario-key network.malicious_outbound --domain apt --pretty`
- 结果：
  - 服务测试 `76 passed`。
  - 单告警 demo 能输出 ReviewQueue summary、3 个 domain/scenario findings、evidence gaps 和 next commands。
  - 带 review note 的 demo 生成 `source_type=review_note` 的 pending memory candidate，且 `runtime_decision_allowed=false`。
- 下一步：
  - 继续把这条 single-alert demo/review summary 链路映射到 Web/TUI 操作面，或接 Lead Agent/Kafka 结论来源到同一 `SocMemoryCandidateSourceBridge`。

### 2026-07-08 — Vendor-neutral scenario eval and replay diff

- 背景：
  - 上一刀已把 taxonomy coverage 接入 PingAn domain eval，但这仍绑定 PingAn fixture；用户要求继续按台账推进，所以本刀把 taxonomy coverage/replay diff 抽成通用 eval 入口。
- 变更：
  - 新增 `backend/soc_agent/eval/scenarios.py`：
    - `ScenarioEvalReport` / `ScenarioEvalSampleResult` / `ScenarioEvalFinding` / `ScenarioEvalDiff`。
    - `run_scenario_eval(samples, baseline=None)` 直接消费任意 alert JSON 样本，走 `SocAnalysisService` + `SocDomainTriageService`，输出 taxonomy coverage、missing、unmapped 和 per-sample findings。
    - `load_scenario_eval_report(path)` 加载历史报告用于 replay diff。
  - 新增 CLI：`soc eval scenarios PATH --glob '*.json' [--baseline-json REPORT] [--pretty]`。
  - `--baseline-json` 只生成 diff，不自动失败、不写业务库、不生成 memory。
  - 新增 `backend/tests/test_soc_scenario_eval.py` 覆盖通用 coverage、baseline diff 和 CLI 输出。
  - 更新 `.notes/ai_soc/soc-agent-solution.md`、`.notes/reference-index/soc-agent-engineering-contracts.md`。
- 边界：
  - 本 eval 不依赖 PingAn fixture/action evidence；没有只读证据时 confidence 可能低于 PingAn domain eval，这是预期现象。
  - Eval 只读样本，不写 ReviewQueue、audit、memory 或 DB。
- 验证：
  - `PYTHONPATH=backend backend/.venv/bin/python -m ruff check backend/soc_agent/eval/scenarios.py backend/soc_agent/eval/__init__.py backend/soc_agent/cli.py backend/tests/test_soc_scenario_eval.py`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_scenario_eval.py backend/tests/test_soc_pingan_capability_eval.py backend/tests/test_soc_domain_scenarios.py -q`
  - `PYTHONPATH=backend backend/.venv/bin/python -m soc_agent.cli eval scenarios backend/samples/alerts --glob 'pingan_legacy_*.json' --pretty`
- 下一步：
  - 继续接 Kafka/Lead Agent/review note 等 memory candidate 来源，或开始 bounded LLM scenario recognizer / custom taxonomy 的受控设计。

### 2026-07-08 — Scenario taxonomy eval baseline + analyst feedback candidate bridge

- 背景：
  - 用户要求下一步按台账推进 scenario taxonomy / replay diff / analyst feedback -> pending memory candidate，让“识别到了场景”可以评测、反馈、沉淀。
- 变更：
  - `backend/soc_agent/domain/scenarios.py` 暴露 `SCENARIO_TAXONOMY_VERSION`、`scenario_taxonomy_keys()` 和 `scenario_taxonomy_snapshot()`，作为 deterministic scenario taxonomy 的稳定评测快照。
  - `backend/soc_agent/eval/pingan.py` 的 `PingAnDomainTriageEvalReport` 新增 `scenario_taxonomy_version`、`scenario_finding_count`、`unmapped_vendor_scenario_count`、`scenario_taxonomy_keys`、`covered_scenario_keys`、`missing_scenario_taxonomy_keys`，让 `soc eval pingan-domain` 能直接输出 replay-diff baseline。
  - 收紧 `web.webshell` deterministic 关键词，移除过宽的 `jsp/php/upload` 单词匹配，避免普通 Web 路径误命中 WebShell 场景。
  - `SocMemoryCandidateSourceBridge.propose_from_domain_finding()` / `propose_from_domain_triage_result()` 支持 `analyst_feedback`，把分析师对 domain/scenario finding 的补充意见写入 pending candidate content/facets/metadata。
  - 增强测试：PingAn domain eval 验证 taxonomy coverage/missing/unmapped；domain finding candidate 测试验证 analyst feedback 进入 candidate 且仍 `runtime_decision_allowed=false`。
- 边界：
  - Eval baseline 只读，不写业务库，不生成 confirmed memory。
  - Analyst feedback 只生成 `pending_review` candidate，不绕过 `SocMemoryService.review_candidate()`，不影响 runtime verdict。
- 验证：
  - `PYTHONPATH=backend backend/.venv/bin/python -m ruff check backend/soc_agent/domain backend/soc_agent/eval/pingan.py backend/soc_agent/memory/sources.py backend/tests/test_soc_pingan_capability_eval.py backend/tests/test_soc_agent_service.py backend/tests/test_soc_domain_scenarios.py`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_pingan_capability_eval.py backend/tests/test_soc_agent_service.py::test_memory_source_bridge_proposes_domain_finding_candidate_idempotently backend/tests/test_soc_domain_scenarios.py -q`
  - `PYTHONPATH=backend backend/.venv/bin/python -c "from soc_agent.eval import DEFAULT_PINGAN_CAPABILITY_EVAL_DIR, load_pingan_capability_eval_fixtures, run_pingan_domain_triage_eval; r=run_pingan_domain_triage_eval(load_pingan_capability_eval_fixtures(DEFAULT_PINGAN_CAPABILITY_EVAL_DIR)); print({'scenario_finding_count': r.scenario_finding_count, 'covered': r.covered_scenario_keys, 'missing': r.missing_scenario_taxonomy_keys, 'unmapped': r.unmapped_vendor_scenario_count})"`
- 下一步：
  - 继续把 taxonomy/replay baseline 提升到 vendor-neutral eval，或接 Kafka/Lead Agent/review note 等 memory candidate 来源。

### 2026-07-08 — Vendor unmapped scenario fallback

- 背景：
  - 用户追问“如果不在场景识别器里会怎么样”，明确不能让内部场景库未覆盖导致 SOC Agent 停止研判或强行映射到错误场景。
- 变更：
  - `backend/soc_agent/domain/scenarios.py` 在 deterministic scenario rule 未命中、但上游/canonical alert 存在 vendor scenario hints 时，输出 `scenario_key=vendor.unmapped` 的候选 `SocDomainFinding`。
  - 该 finding 保留 `vendor_scenarios`、`evidence_profile`、`current_conclusion`、`limitations` 和 `human_checklist`，推荐人工复核并作为 taxonomy / capability card / memory candidate 的后续候选。
  - 命中内部场景规则时不会额外生成 `vendor.unmapped`，避免把已识别场景和厂商未映射提示混在一起。
  - 更新 `.notes/ai_soc/soc-agent-solution.md` 和 `.notes/reference-index/soc-agent-engineering-contracts.md`，固定未映射厂商场景的边界。
- 边界：
  - `vendor.unmapped` 不改 operational verdict，不写 confirmed memory，不执行 action，不替代 domain handler 的基础 finding。
  - 这只是 deterministic fallback；后续 taxonomy 扩展、replay diff、analyst feedback -> pending memory candidate 仍是独立切片。
- 验证：
  - `PYTHONPATH=backend backend/.venv/bin/python -m ruff check backend/soc_agent/domain/scenarios.py backend/tests/test_soc_domain_scenarios.py`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_domain_scenarios.py backend/tests/test_soc_pingan_capability_eval.py -q`
- 下一步：
  - 继续补 scenario taxonomy / replay diff / analyst feedback -> pending memory candidate，或者先把 domain finding bridge 显式接入合适的 service/entry。

### 2026-07-08 — Generic scenario recognition deterministic MVP

- 背景：
  - 用户指出“下一步查什么/调什么工具/交给谁复核”不能建立在客户自动化能力完整的假设上；历史相似预警、外部运营反馈和 confirmed memory 不是工具缺失后的降级项，而是每次研判都应参与的常规 evidence input。
- 变更：
  - 扩展 `SocDomainFinding` contract：
    - 新增 `scenario_key`、`scenario_name`、`vendor_scenarios`。
    - 新增 `SocEvidenceProfile`：记录 raw、similar alerts、external feedback、confirmed memory、memory candidates、read-only evidence 和工具证据状态。
    - 新增 `SocFindingConclusion`：每条 finding 都必须给出当前结论、risk/certainty、recommended action/queue 和 rationale，且 `automation_allowed=false`。
    - 新增 `human_checklist`，让证据不足时仍有可执行人工核查清单。
  - `SocDomainTriageService` 追加 deterministic scenario recognizer：
    - 第一批场景：`execution.reverse_shell`、`web.webshell`、`lateral_movement`、`execution.suspicious_command`、`network.malicious_outbound`、`privilege_escalation`、`credential_abuse`。
    - 场景识别从 canonical alert、raw message、vendor scenario hints、entity/summary、历史/反馈/memory metadata 和 read-only action evidence 做 evidence fusion。
  - `SocReviewService.get_investigation_context()` 先生成 relevant memory，再把 similar alerts、correlation、external feedback、memory candidate、relevant memory 和 available action routes 传入 domain triage metadata。
  - ReviewQueue Web/TUI、unified timeline 和 memory candidate content/facets 展示/携带 scenario、current conclusion 和 evidence gaps。
- 边界：
  - 本刀不引入 LLM recognizer，不启用 prompt injection，不写 confirmed memory，不改变 operational verdict。
  - 工具证据缺失只进入 evidence gaps 并降低 certainty，不阻断 finding 输出。
- 验证：
  - `PYTHONPATH=backend backend/.venv/bin/python -m ruff check backend/soc_agent/domain/triage.py backend/soc_agent/contracts/schemas.py backend/soc_agent/contracts/__init__.py backend/soc_agent/core/service.py backend/soc_agent/memory/sources.py backend/soc_agent/eval/pingan.py backend/soc_agent/tui/render.py backend/tests/test_soc_pingan_capability_eval.py backend/tests/test_soc_agent_service.py`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_pingan_capability_eval.py backend/tests/test_soc_agent_service.py -q`
  - `pnpm --dir frontend check`
  - `codegraph sync .`
- 下一步：
  - 补 scenario taxonomy / replay diff / analyst feedback -> pending memory candidate；继续接 Kafka/Lead Agent/review note 等 memory 来源。

### 2026-07-08 — Memory candidate source bridge + correction integration

- 背景：
  - Candidate store、review workflow、retrieval gate 和统一调查视图已经存在，但候选记忆来源如果继续散落在 correction、domain、Kafka、Lead Agent 等模块里，后续会重复实现 source/evidence/facet/idempotency 逻辑。
- 变更：
  - 新增 `backend/soc_agent/memory/sources.py`：
    - `SocMemoryCandidateSourceBridge` 统一把稳定 SOC 来源转成 `SocMemoryCandidateCreateCommand`，再通过 `SocMemoryService.propose_candidate()` 写入。
    - 已支持 `CorrectionRecord` 和 `SocDomainTriageResult/SocDomainFinding` 两类来源。
    - correction candidate 带 `correction/run/alert/review_queue` evidence refs、corrected/previous verdict facets 和 correction idempotency key。
    - domain finding candidate 使用稳定 hash key，避免同一 finding 重放重复写入。
  - `SocReviewService.correct()` 在注入 `MemoryCandidateRepository` 时自动生成 pending candidate，并把 `memory_candidate_id` 写回 `CorrectionRecord`、audit payload 和 review corrected event。
  - `CorrectionRecord` 新增 `memory_candidate_id`，用于把 operational correction 和 pending memory candidate 串起来。
- 边界：
  - 本刀不自动确认 memory，不启用 prompt injection，不改变 runtime verdict。
  - `InvestigationEvidence` 仍只是 evidence ref，不直接触发 memory 写入。
  - Domain finding bridge 已完成，但后续 entry/service 还需要显式调用它。
- 验证：
  - `PYTHONPATH=backend backend/.venv/bin/python -m ruff check backend/soc_agent/memory backend/soc_agent/core/service.py backend/soc_agent/contracts/schemas.py backend/tests/test_soc_agent_service.py backend/tests/test_soc_external_disposition.py backend/tests/test_soc_agent_repository.py`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_agent_service.py backend/tests/test_soc_external_disposition.py backend/tests/test_soc_agent_repository.py -q`
  - `codegraph sync .`
- 下一步：
  - 做 generic security scenario recognition，优先把反弹 shell、webshell、横向移动、命令执行、恶意外联、提权、凭证滥用等通用场景落到 `SocDomainTriageResult/Finding`；Kafka/Lead Agent/review note 等记忆来源按同一 bridge 逐步接入。

### 2026-07-08 — Persistent demo script MVP

- 背景：
  - Web/TUI visible investigation 已能聚合 correlation、domain finding、read-only evidence、memory candidate 和 relevant memory，但还缺一条可重复命令把演示数据真实写入 SOC repository，导致分析师不方便直接打开 Web/TUI 看完整效果。
- 变更：
  - 新增 `backend/soc_agent/demo/`：
    - `run_pingan_investigation_demo()` 调用现有 `SocAnalysisService`、`SocAgentActionDispatcher`、`SocMemoryService`、`SocReviewService`，不绕过 service 直接拼 view。
    - 用 PingAn APT/EDR/HIDS 脱敏 fixture 生成持久化 `AnalysisRun`、`ReviewQueueItem`、read-only `InvestigationEvidence`、confirmed retrieval memory 和 `UnifiedInvestigationView`。
    - 分析 run、action evidence、memory candidate 都使用 demo idempotency key；重复执行会复用已有链路，不无限堆重复数据。
  - 新增 CLI：
    - `soc demo run [all|apt|edr|hids] --database-url ... --init-db --pretty`。
    - 输出 run ids、queue ids、next commands、view counts、timeline kinds 和 action evidence id。
  - 新增测试：
    - 验证 `soc demo run apt` 后能通过 `soc review context QUEUE_ID` 看到 action evidence、domain findings 和 relevant memories。
    - 验证重复执行同一 HIDS demo 会复用既有 run/queue/evidence。
- 边界：
  - 本 MVP 使用本地 mock read-only adapters 和脱敏样例；不代表真实 PingAn PA-12 完成。
  - 本 MVP 暂不种 external disposition，避免 high-trust external mapping 自动 correction/close 影响 open ReviewQueue 演示可见性。
  - Confirmed memory 在 demo 中显式打开 `retrieval_enabled=true`，用于验证检索和统一视图；生产记忆仍必须走人工 review/gate。
- 验证：
  - `PYTHONPATH=backend backend/.venv/bin/python -m ruff check backend/soc_agent/demo backend/soc_agent/cli.py backend/tests/test_soc_demo_investigation.py`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_demo_investigation.py -q`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_pingan_capability_eval.py -q`
  - `PYTHONPATH=backend backend/.venv/bin/python -m soc_agent.cli demo run all --database-url sqlite:////tmp/soc_demo_investigation_smoke.db --init-db --pretty`
- 下一步：
  - 做 memory candidate 来源闭环：把 correction、external disposition、domain finding、Lead Agent proposal、Kafka daemon 等重要结论统一生成 pending candidate，并保持 confirmed-memory/retrieval gate 不被绕过。

### 2026-07-08 — Web/TUI visible investigation MVP

- 背景：
  - Correlation、domain triage、read-only evidence、external feedback、memory candidate 和 relevant memory 都已经有各自的 read model，但分析师在 Web/TUI 打开一个 ReviewQueue item 时仍要分散查看，缺少统一调查视图和 evidence timeline。
- 变更：
  - 新增统一调查视图 contract：
    - `InvestigationTimelineItem`：把 analysis、decision、correlation、domain finding、read-only evidence、external disposition、memory candidate、relevant memory、audit、correction 统一为只读时间线项。
    - `UnifiedInvestigationView`：聚合 runtime verdict/confidence、只读计数、`CorrelationResult`、`SocDomainTriageResult`、timeline 和 boundary notes。
  - `SocReviewService.get_investigation_context()` 现在聚合：
    - `correlation_result`：通过 `SocCorrelationService` 基于 summary/evidence 生成。
    - `domain_triage_results`：通过 `SocDomainTriageService` 使用 bounded skill context 和现有 action evidence 生成。
    - `investigation_view`：从 run/summary/audit/evidence/external/memory/correlation/domain 生成只读分析师视图。
  - ReviewQueue Web 新增“统一调查视图”区块，展示 runtime summary、关键计数、领域发现、调查时间线和 Top 关联告警。
  - SOC Review TUI 新增 unified view 计数和 timeline 摘要。
  - Lead Agent bounded artifact 新增 compact `investigation_view` payload；不提供新增权限。
- 边界：
  - `UnifiedInvestigationView` 是展示投影，不是 source of truth。
  - 本刀不写 DB、不执行 action、不改 verdict、不启用 prompt injection。
- 验证：
  - `PYTHONPATH=backend backend/.venv/bin/python -m ruff check ...`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_agent_service.py -k "investigation_context or context_includes_similar_alerts or context_includes_action_evidence or relevant_memory" -q`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_review_router.py -q`
  - `pnpm --dir frontend check`
- 下一步：
  - 做 Demo / Eval Script：一条命令生成或装载样例、ReviewQueue item、只读 evidence、domain finding、relevant memory 和 unified investigation view，方便直接打开 Web/TUI/TUI 看完整效果。

### 2026-07-08 — Confirmed memory retrieval policy / unified visibility MVP

- 背景：
  - Candidate review workflow 已能把候选推进到 `SocMemoryRecord`，但 confirmed record 默认 `retrieval_enabled=false`，系统还缺少可审计的检索 query/result、retrieval gate 和统一调查上下文展示。
- 变更：
  - 新增 retrieval contract：
    - `SocMemoryQuery`：支持 memory type/status/tenant、可选 facets、text terms、evidence refs、limit、min score、max token budget。
    - `SocMemoryMatch` / `SocMemoryRetrievalResult`：返回 score、match reasons、matched facets、token estimate、memory id/version/hash 和 skipped counters。
  - 新增 service / repository 能力：
    - `SocMemoryService.find_relevant_records()` 只返回 `retrieval_enabled=true`、`status=confirmed`、未过期的 records。
    - `MemoryRecordRepository.list_memory_records()` 支持 `retrieval_enabled` 过滤。
    - `SocReviewService.get_investigation_context()` 在存在 record repository 时生成 `InvestigationContext.relevant_memories`。
  - 新增入口和可见化：
    - CLI：`soc memory search` 和 `soc memory records list --retrieval-enabled true|false|all`。
    - Gateway：`POST /api/soc/memory/search`，`GET /api/soc/memory/records?retrieval_enabled=...`。
    - ReviewQueue Web/TUI/Lead Agent bounded artifact 展示 relevant memories、token budget 和 skipped retrieval-disabled counters。
  - 文档同步：
    - `.notes/ai_soc/soc-agent-solution.md`
    - `.notes/ai_soc/alert-lifecycle-flow.md`
    - `.notes/ai_soc/memory/memory-tracking.md`
    - `.notes/reference-index/soc-agent-engineering-contracts.md`
- 验证：
  - `PYTHONPATH=backend backend/.venv/bin/python -m ruff check ...`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_agent_service.py -k "memory or investigation_context" -q`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_agent_repository.py -q`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_memory_router.py -q`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_review_router.py -q`
  - `PYTHONPATH=backend backend/.venv/bin/python -m soc_agent.cli db upgrade --database-url sqlite:////tmp/soc_memory_retrieval_policy_check.db`
  - `PYTHONPATH=backend backend/.venv/bin/python -m soc_agent.cli memory search --facet tenant=pingan --term feedback --database-url sqlite:////tmp/soc_memory_retrieval_policy_check.db --pretty`
  - `pnpm --dir frontend check`
  - `git diff --check`
  - `codegraph sync .`
- 下一步：
  - 做 Web/TUI visible investigation：把 correlation result、domain triage findings、main orchestrator report、evidence timeline、external feedback 和 relevant memories 收敛到分析师统一调查视图；仍不让 Web/TUI 复制业务逻辑。

### 2026-07-07 — Memory candidate review workflow / confirmed-memory boundary

- 背景：
  - 候选记忆已经能从 external disposition、domain finding、correction 等路径进入 DB/API/Web/TUI/Lead Agent context，但还缺少统一的人工评审状态机和 confirmed-memory 边界。
- 变更：
  - 新增 memory review contract：
    - `SocMemoryCandidateReviewDecision` / `SocMemoryCandidateReviewCommand` / `SocMemoryCandidateReviewResult`。
    - `SocMemoryRecord` / `SocMemoryRecordStatus`，并固定 `retrieval_enabled=false` 作为当前硬边界。
  - 新增 service/repository/DB：
    - `SocMemoryService.review_candidate()` 统一处理 `confirm_candidate`、`confirm`、`reject`、`deprecate`、`expire`。
    - `confirm` 从 candidate 派生 `SocMemoryRecord(status=confirmed, retrieval_enabled=false)`；非法状态迁移 fail-fast。
    - 新增 `MemoryRecordRepository`、in-memory store、SQLAlchemy repository、ORM row 和 migration `0011_memory_records`。
  - 新增入口：
    - CLI：`soc memory review`、`soc memory records list/get`。
    - Gateway：`POST /api/soc/memory/candidates/{candidate_id}/review`、`GET /api/soc/memory/records`、`GET /api/soc/memory/records/{memory_id}`。
    - ReviewQueue Web：候选记忆卡片支持填写评审理由并确认/驳回/废弃/过期。
  - 文档同步：
    - `.notes/ai_soc/soc-agent-solution.md`
    - `.notes/ai_soc/alert-lifecycle-flow.md`
    - `.notes/ai_soc/memory/memory-tracking.md`
    - `.notes/reference-index/soc-agent-engineering-contracts.md`
- 验证：
  - `PYTHONPATH=backend backend/.venv/bin/python -m ruff check ...`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_agent_service.py -k memory -q`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_agent_repository.py -q`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_memory_router.py -q`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_review_router.py -q`
  - `PYTHONPATH=backend backend/.venv/bin/python -m soc_agent.cli db upgrade --database-url sqlite:////tmp/soc_memory_review_workflow_migration_check.db`
  - `PYTHONPATH=backend backend/.venv/bin/python -m soc_agent.cli memory records list --status "" --database-url sqlite:////tmp/soc_memory_review_workflow_migration_check.db`
  - `pnpm --dir frontend check`
- 下一步：
  - 做 confirmed memory retrieval policy / unified investigation visibility：补 `SocMemoryQuery`、retrieval-enabled gate、score、match reason、token budget 和 ReviewQueue/Web/TUI/Lead Agent 可见化；仍不让 pending candidate 或 retrieval-disabled record 影响 verdict。

### 2026-07-07 — Memory candidate DB/API/ReviewQueue visibility

- 背景：
  - External Disposition、correction、domain finding 和 Lead Agent 后续都会产生候选经验；之前 `SocMemoryCandidate` 主要停留在 in-memory/test boundary，分析师无法通过统一 ReviewQueue context 看到这些候选。
- 变更：
  - 新增 `soc_memory_candidates` 持久化：
    - 新增 migration `0010_memory_candidates`、`SocMemoryCandidateRow`、SQLAlchemy repository 方法。
    - `MemoryCandidateRepository` 增加 idempotency lookup 和 run/alert/queue 过滤。
    - `SocMemoryService.propose_candidate()` 按 `idempotency_key` 复用既有 candidate，避免重复写入。
  - 新增查询入口：
    - Gateway 新增 `/api/soc/memory/candidates` 和 `/api/soc/memory/candidates/{candidate_id}`。
    - CLI 新增 `soc memory list/get`。
  - ReviewQueue context 可见：
    - `SocReviewService.get_investigation_context()` 聚合相关 `memory_candidates`。
    - `InvestigationContext`、Lead Agent bounded artifact、TUI render 和 ReviewQueue Web 页面新增候选记忆展示。
    - Web/TUI/Lead Agent 只能把它展示为 `pending_review` candidate，不作为 confirmed fact 或 active lesson。
  - 文档同步：
    - `.notes/ai_soc/soc-agent-solution.md`
    - `.notes/ai_soc/alert-lifecycle-flow.md`
    - `.notes/reference-index/soc-agent-engineering-contracts.md`
- 验证：
  - `PYTHONPATH=backend backend/.venv/bin/python -m ruff check ...`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_agent_service.py -k memory -q`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_agent_repository.py -q`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_memory_router.py -q`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_review_router.py -q`
  - `PYTHONPATH=backend backend/.venv/bin/python -m soc_agent.cli db upgrade --database-url sqlite:////tmp/soc_memory_candidate_migration_check.db`
  - `pnpm --dir frontend check`
  - `git diff --check`
- 下一步：
  - 做 memory candidate review workflow / confirmed-memory boundary：补 confirm/reject/deprecate/expire 状态机、review audit 和 confirmed memory record 设计；仍不把 pending candidate 注入 prompt。

### 2026-07-07 — External Disposition PostgreSQL/API/ReviewQueue visibility

- 背景：
  - External Disposition 已能同步 high-trust review/correction，并把外部 reason 变成 pending memory candidate；下一步需要让外部处置历史、理由、correction id 和 memory candidate id 被分析师、Web/TUI 和 SOC Lead Agent 看到。
- 变更：
  - 更新 `backend/soc_agent/contracts/schemas.py`：
    - `InvestigationContext` 新增 `external_dispositions`。
    - `SocLeadAgentReviewContextArtifact` 新增 bounded `external_dispositions`。
  - 新增 `backend/soc_agent/db/migrations/versions/0009_external_dispositions.py`，并更新 ORM / SQLAlchemy repository：
    - 新增 `soc_external_dispositions` 表。
    - `SqlAlchemyAlertRepository` 实现 `save_external_disposition()`、`find_external_disposition_by_idempotency_key()`、`list_external_dispositions()`。
  - 更新 `SocReviewService.get_investigation_context()`：
    - 通过 `SocExternalDispositionRepository` 聚合外部处置反馈。
  - 更新 Gateway/CLI/TUI/Lead Agent context bridge/Web：
    - `/api/soc/review/items/{queue_id}/context` 返回外部反馈。
    - `soc review context/tui` 和 `soc chat tui --lead-agent` 使用同一 repository 读取外部反馈。
    - ReviewQueue Web 页面新增“外部处置反馈”区块。
  - 更新文档：
    - `.notes/ai_soc/integrations/external-disposition-sync.md`
    - `.notes/ai_soc/alert-lifecycle-flow.md`
    - `.notes/reference-index/soc-agent-engineering-contracts.md`
- 验证：
  - `PYTHONPATH=backend backend/.venv/bin/python -m ruff format ...`
  - `PYTHONPATH=backend backend/.venv/bin/python -m ruff check ...`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_agent_repository.py -q`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_review_router.py -q`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_external_disposition.py -q`
  - `pnpm --dir frontend check`
  - `PYTHONPATH=backend backend/.venv/bin/python -m soc_agent.cli db upgrade --database-url sqlite:////tmp/soc_external_disposition_migration_check.db`
  - `git diff --check`
  - `codegraph sync .`
- 下一步：
  - 做 DB-first memory candidate persistence：把 `SocMemoryCandidate` 从 in-memory 测试边界推进到 PostgreSQL/API/ReviewQueue 可评审队列，仍保持 `pending_review` 不影响 runtime decision。

### 2026-07-07 — External Disposition memory candidate integration

- 背景：
  - External Disposition 已能同步 high-trust review/correction；下一步需要把外部系统里的人工处置理由沉淀为可评审候选，但不能直接污染 confirmed memory。
- 变更：
  - 更新 `backend/soc_agent/core/external_disposition.py`：
    - `SocExternalDispositionService` 新增可选 `memory_service`。
    - mapped、可定位、带 `external_reason` 的事件通过 `SocMemoryService.propose_candidate()` 生成 `SocMemoryCandidate(status=pending_review)`。
    - low-trust mapped event 可以生成候选，但不能改判；unknown/unmatched/no reason 不生成候选。
    - candidate 固定带 `source_id=disposition_id`、run/alert/queue/correction refs、idempotency key、facets、trust/apply metadata 和 `candidate-only` 标签。
    - external disposition record 和 audit payload 记录 `memory_candidate_id`。
  - 更新 `backend/tests/test_soc_external_disposition.py`：
    - 覆盖 high-trust mapped event 同时 correction + pending memory candidate。
    - 覆盖 duplicate event 不重复生成 candidate。
    - 覆盖 low-trust mapped event 只生成 candidate、不改判。
    - 覆盖 unknown/unmatched event 不生成 candidate。
  - 更新 `.notes/ai_soc/integrations/external-disposition-sync.md`、`.notes/ai_soc/soc-agent-solution.md`、`.notes/ai_soc/alert-lifecycle-flow.md`、`.notes/ai_soc/integrations/mock-and-real-register.md` 和工程契约：
    - 固定 external reason 的安全边界：只能 pending review，不进入 confirmed memory。
- 验证：
  - `PYTHONPATH=backend backend/.venv/bin/python -m ruff format backend/soc_agent/core/external_disposition.py backend/tests/test_soc_external_disposition.py`
  - `PYTHONPATH=backend backend/.venv/bin/python -m ruff check backend/soc_agent/core/external_disposition.py backend/tests/test_soc_external_disposition.py`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_external_disposition.py -q`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_agent_service.py -k 'review_service_correct or memory_service or correlation' -q`
- 下一步：
  - 做 External Disposition PostgreSQL/API/ReviewQueue visibility，让分析师能看到外部处置历史、理由和 memory candidate id；或者先做 DB-first memory candidate persistence，避免候选长期停留在 in-memory 测试边界。

### 2026-07-07 — External Disposition Review/Correction integration

- 背景：
  - External Disposition Contract MVP 已能记录、映射、定位和审计外部状态；下一步需要把高可信外部人工结论同步到本地 operational correction / review close，同时继续防止低可信或无法定位事件改判。
- 变更：
  - 更新 `backend/soc_agent/contracts/schemas.py`：
    - `SocExternalDispositionApplyResult` 新增 `correction_applied`。
  - 更新 `backend/soc_agent/core/external_disposition.py`：
    - `SocExternalDispositionService` 新增可选 `summary_repository`。
    - 对 `trust_level=high`、`apply_status=mapped`、目标唯一且 canonical status 可映射到 verdict 的事件，复用 `SocReviewService.correct()`。
    - correction reason 固定带外部系统、case id、canonical status 和外部 reason。
    - 外部 disposition audit payload 记录 `correction_id`。
    - 低可信、未知状态、无法定位、非 verdict 类状态仍只记录，不改判。
  - 更新 `backend/tests/test_soc_external_disposition.py`：
    - 覆盖 high-trust mapped event 会写 correction、关闭 review、写 correction/external 两类 audit。
    - 覆盖 duplicate event 不重复 correction。
    - 覆盖 low-trust mapped event 不改判、不关闭 review。
  - 更新 `.notes/ai_soc/integrations/external-disposition-sync.md`：
    - 切片 4 Review/Correction integration 标记 Done；下一步切到 memory candidate integration。
- 验证：
  - `PYTHONPATH=backend backend/.venv/bin/python -m ruff format backend/soc_agent/core/external_disposition.py backend/tests/test_soc_external_disposition.py backend/soc_agent/contracts/schemas.py`
  - `PYTHONPATH=backend backend/.venv/bin/python -m ruff check backend/soc_agent/core/external_disposition.py backend/tests/test_soc_external_disposition.py backend/soc_agent/contracts/schemas.py`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_external_disposition.py -q`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_agent_service.py -k 'review_service_correct or memory_service or correlation' -q`
  - `git diff --check`
  - `codegraph sync .`
- 下一步：
  - 做 External Disposition memory candidate integration：外部 reason 只能生成 `SocMemoryCandidate(status=pending_review)`，不能写 confirmed memory。

### 2026-07-07 — External Disposition Sync Contract MVP

- 背景：
  - `PA-12` 真实 PingAn MCP/API 替换等待 endpoint/凭证；按规划回到 External Disposition Sync，让 Zeus/ITSM/SIEM-SOAR/客户自研 SOC 的人工状态和理由能先通过 vendor-neutral feedback lane 回流。
- 变更：
  - 更新 `backend/soc_agent/contracts/schemas.py` 和 `backend/soc_agent/contracts/__init__.py`：
    - 新增 `SocExternalDispositionEvent`、`SocExternalDispositionCanonicalStatus`、`SocExternalDispositionApplyStatus`。
    - 新增 `SocExternalDispositionStatusMapping`、`SocExternalDispositionMappingConfig`、`SocExternalDispositionAdapterConfig`。
    - 新增 `SocExternalDispositionRecord`、`SocExternalDispositionApplyResult`。
    - 扩展 `AuditAction.EXTERNAL_DISPOSITION` 和 `SocEventType.EXTERNAL_DISPOSITION_RECEIVED`。
  - 更新 `backend/soc_agent/protocols.py`：
    - 新增 `SocExternalDispositionRepository` protocol。
  - 新增 `backend/soc_agent/external_disposition/`：
    - `build_external_disposition_event()`：通过 field-path config 把外部 payload 映射成 canonical event。
    - `resolve_external_disposition_status()`：通过 mapping config 把外部状态映射成 canonical status。
    - `build_external_disposition_idempotency_key()`：固定幂等键形态。
    - `InMemoryExternalDispositionRepository`：用于 service tests 和本地 smoke。
  - 新增 `backend/soc_agent/core/external_disposition.py` 并导出 `SocExternalDispositionService`：
    - `apply_event()` 支持 schema validation、状态映射、目标定位、幂等、unmatched、audit 和 event emission。
    - 当前不自动 correction、不关闭 review、不写 memory candidate。
  - 新增 `backend/samples/external_disposition/zeus_status_update.json`：
    - 脱敏 Zeus 状态/理由 mock payload，用 adapter config 映射，不在 core 写死 Zeus。
  - 新增 `backend/tests/test_soc_external_disposition.py`：
    - 覆盖 mapper、状态映射、幂等、unmatched、audit 和 service repository 边界。
  - 更新 `.notes/ai_soc/integrations/external-disposition-sync.md`：
    - 标记切片 1-3 Done，下一步切到 Review/Correction integration。
- 验证：
  - `PYTHONPATH=backend backend/.venv/bin/python -m ruff format backend/soc_agent/contracts/schemas.py backend/soc_agent/contracts/__init__.py backend/soc_agent/protocols.py backend/soc_agent/core/__init__.py backend/soc_agent/core/external_disposition.py backend/soc_agent/external_disposition/__init__.py backend/soc_agent/external_disposition/mapping.py backend/soc_agent/external_disposition/repository.py backend/tests/test_soc_external_disposition.py`
  - `PYTHONPATH=backend backend/.venv/bin/python -m ruff check backend/soc_agent/contracts/schemas.py backend/soc_agent/contracts/__init__.py backend/soc_agent/protocols.py backend/soc_agent/core/__init__.py backend/soc_agent/core/external_disposition.py backend/soc_agent/external_disposition/__init__.py backend/soc_agent/external_disposition/mapping.py backend/soc_agent/external_disposition/repository.py backend/tests/test_soc_external_disposition.py`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_external_disposition.py -q`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_agent_service.py -k 'review_service_correct or memory_service or correlation' -q`
  - `git diff --check`
  - `codegraph sync .`
- 下一步：
  - 做 External Disposition Review/Correction integration：高置信 mapped external disposition 在唯一定位本地 target 后，同步 operational correction / review close/update；仍不写 confirmed memory。

### 2026-07-07 — PA-11 PingAn main orchestrator demo

- 背景：
  - `PA-10` 已把 APT/EDR/HIDS capability cards、skill context 和 read-only evidence 收口成 domain findings；下一步需要一个主控服务把单条预警的固定分析、只读调查、子研判和复核上下文合成统一报告，让后续 Web/TUI/Lead Agent 有稳定输入。
- 变更：
  - 更新 `backend/soc_agent/contracts/schemas.py` 和 `backend/soc_agent/contracts/__init__.py`：
    - 新增 `SocOrchestratorActionSpec`、`SocOrchestratorRouteStep`、`SocOrchestratorReviewContextSummary`、`SocMainOrchestratorRequest`、`UnifiedInvestigationReport`。
  - 新增 `backend/soc_agent/core/orchestrator.py` 并导出 `SocMainOrchestratorService`：
    - 串起 `SocAnalysisService.analyze()`、`SocAgentCapabilityRouter`、`SocAgentActionDispatcher`、`InvestigationEvidenceRepository`、`SocDomainTriageService`。
    - 只处理显式 read-only action specs；结果进入 `InvestigationEvidence`，再进入 domain triage 和 review summary。
    - report metadata 固定 `handler_output_only=true`、`writes_db=false`、`executes_high_risk_actions=false`。
  - 更新 `backend/soc_agent/eval/pingan.py` 和 `backend/soc_agent/eval/__init__.py`：
    - 新增 `run_pingan_main_orchestrator_eval()` 和 `PingAnMainOrchestratorEvalReport`。
  - 更新 `backend/soc_agent/cli.py`：
    - 新增 CLI：`soc eval pingan-main [path] --pretty`。
  - 更新 `backend/tests/test_soc_pingan_capability_eval.py`：
    - 覆盖 APT/EDR/HIDS report schema、route/evidence/finding/review context、read-only metadata 和 CLI 输出。
  - 更新 `.notes/ai_soc/capabilities/pingan/onboarding.md`、`.notes/ai_soc/soc-agent-solution.md`、`.notes/ai_soc/alert-lifecycle-flow.md`、`.notes/reference-index/soc-agent-engineering-contracts.md`：
    - 固定 PA-11 已完成、PA-12 等真实 endpoint/凭证，不用 mock 假装完成真实替换。
- 验证：
  - `PYTHONPATH=backend backend/.venv/bin/python -m ruff format backend/soc_agent/contracts/schemas.py backend/soc_agent/contracts/__init__.py backend/soc_agent/core/__init__.py backend/soc_agent/core/orchestrator.py backend/soc_agent/eval/__init__.py backend/soc_agent/eval/pingan.py backend/soc_agent/cli.py backend/tests/test_soc_pingan_capability_eval.py`
  - `PYTHONPATH=backend backend/.venv/bin/python -m ruff check backend/soc_agent/contracts/schemas.py backend/soc_agent/contracts/__init__.py backend/soc_agent/core/__init__.py backend/soc_agent/core/orchestrator.py backend/soc_agent/eval/__init__.py backend/soc_agent/eval/pingan.py backend/soc_agent/cli.py backend/tests/test_soc_pingan_capability_eval.py`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_pingan_capability_eval.py -k 'main_orchestrator or pingan_main' -q`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_pingan_capability_eval.py -q`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_action_adapters.py -q`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_agent_service.py -k 'correlation or read_only or action_policy_treats_asset_locate or memory' -q`
  - `PYTHONPATH=backend backend/.venv/bin/python -m soc_agent.cli eval pingan-main`
  - `git diff --check`
  - `codegraph sync .`
- 下一步：
  - `PA-12` 等真实 PingAn MCP/API endpoint/凭证；真实接口未就绪前，下一刀回到 External Disposition Sync / Memory Tracking / Web-TUI visible investigation。

### 2026-07-07 — PA-10 PingAn domain triage MVP

- 背景：
  - `PA-09` 已建立 pending review memory candidate 入口；下一步需要把 PingAn APT/EDR/HIDS 的 capability cards、skill context 和 read-only evidence 收口为可审阅 domain findings，为 `PA-11` Main Orchestrator demo 铺路。
- 变更：
  - 更新 `backend/soc_agent/contracts/schemas.py` 和 `backend/soc_agent/contracts/__init__.py`：
    - 新增 `SocDomainName`、`SocDomainFindingSeverity`、`SocDomainFindingDisposition`。
    - 新增 `SocDomainTriageRequest`、`SocDomainFinding`、`SocDomainTriageResult`。
  - 新增 `backend/soc_agent/domain/`：
    - `SocDomainTriageService` 根据 source type / skill context 路由到 APT、EDR、HIDS deterministic handler。
    - APT handler 消费方向冲突、threat intel、security tag evidence，输出 network/apt finding。
    - EDR handler 消费 process-tree evidence 和 risk tags，输出 endpoint finding。
    - HIDS handler 消费 host-event context 和 authorization tag evidence，输出 HIDS finding。
    - result metadata 明确 `writes_db=false`、`executes_actions=false`。
  - 更新 `backend/soc_agent/eval/pingan.py` 和 `backend/soc_agent/eval/__init__.py`：
    - 新增 `run_pingan_domain_triage_eval()` 和 `PingAnDomainTriageEvalReport`。
    - 复用 PA-08 action evidence 生成链路，再把 `InvestigationEvidence` 喂给 domain triage。
  - 更新 `backend/soc_agent/cli.py`：
    - 新增 CLI：`soc eval pingan-domain [path] --pretty`。
  - 更新 `backend/tests/test_soc_pingan_capability_eval.py`：
    - 覆盖 APT/EDR/HIDS 三类 domain、handler id、capability card refs、evidence refs 和 CLI 输出。
- 验证：
  - `PYTHONPATH=backend backend/.venv/bin/python -m ruff format backend/soc_agent/contracts/schemas.py backend/soc_agent/contracts/__init__.py backend/soc_agent/domain/__init__.py backend/soc_agent/domain/triage.py backend/soc_agent/core/__init__.py backend/soc_agent/eval/__init__.py backend/soc_agent/eval/pingan.py backend/soc_agent/cli.py backend/tests/test_soc_pingan_capability_eval.py`
  - `PYTHONPATH=backend backend/.venv/bin/python -m ruff check backend/soc_agent/contracts/schemas.py backend/soc_agent/contracts/__init__.py backend/soc_agent/domain/__init__.py backend/soc_agent/domain/triage.py backend/soc_agent/core/__init__.py backend/soc_agent/eval/__init__.py backend/soc_agent/eval/pingan.py backend/soc_agent/cli.py backend/tests/test_soc_pingan_capability_eval.py`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_pingan_capability_eval.py`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_action_adapters.py`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_agent_service.py -k 'correlation or read_only or action_policy_treats_asset_locate or memory'`
  - `PYTHONPATH=backend backend/.venv/bin/python -m soc_agent.cli eval pingan-domain --pretty`
- 下一步:
  - 执行 `PA-11`：接 Main Orchestrator demo，串起 analyze -> read-only evidence -> domain triage -> unified investigation report。仍不让 domain handler 写 DB、执行 action 或确认 memory。

### 2026-07-07 — PA-09 PingAn memory candidate entry

- 背景：
  - `PA-08` 已把 PingAn APT/EDR/HIDS read-only capability 固定成可回放 eval；下一步需要把 PingAn 专属经验、误报模式、identity pattern、外部理由等先收口到统一候选入口，避免直接污染 confirmed memory 或 public skill。
- 变更：
  - 更新 `backend/soc_agent/contracts/schemas.py` 和 `backend/soc_agent/contracts/__init__.py`：
    - 新增 `SocMemoryCandidate`、`SocMemoryCandidateCreateCommand`、`SocMemoryCandidateSource`、`SocMemoryCandidateValidity`。
    - 新增 candidate status/type/target artifact/source type/decision impact 枚举。
    - candidate 固定 `runtime_decision_allowed=False`、`review_required=True`，默认 `pending_review`。
    - candidate 包含 source surface、source doc/section/run/alert/queue/eval refs、evidence refs、validity、idempotency key、confidence、facets、review owner 和 audit metadata。
  - 更新 `backend/soc_agent/protocols.py`：
    - 新增 `MemoryCandidateRepository` 协议。
  - 新增 `backend/soc_agent/memory/`：
    - 新增 `InMemoryMemoryCandidateRepository`，用于本地 smoke 和 service 测试。
  - 更新 `backend/soc_agent/core/service.py`：
    - `SocMemoryService.propose_candidate()` 只写 pending review candidate，并发出 `MEMORY_UPDATED` 事件。
    - `get_candidate()`、`list_candidates()` 通过 repository 协议读取。
    - `list_facts()` 仍保持未实现，避免把 confirmed memory store 提前做半套。
  - 更新 `backend/tests/test_soc_agent_service.py`：
    - 覆盖无 repository fail-fast、PingAn candidate pending_review、source/evidence/validity/idempotency/facets/review 字段、tenant/status 查询和事件输出。
- 验证：
  - `PYTHONPATH=backend backend/.venv/bin/python -m ruff format backend/soc_agent/contracts/schemas.py backend/soc_agent/contracts/__init__.py backend/soc_agent/protocols.py backend/soc_agent/core/service.py backend/soc_agent/memory/__init__.py backend/soc_agent/memory/candidates.py backend/tests/test_soc_agent_service.py`
  - `PYTHONPATH=backend backend/.venv/bin/python -m ruff check backend/soc_agent/contracts/schemas.py backend/soc_agent/contracts/__init__.py backend/soc_agent/protocols.py backend/soc_agent/core/service.py backend/soc_agent/memory/__init__.py backend/soc_agent/memory/candidates.py backend/tests/test_soc_agent_service.py`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_agent_service.py -k memory`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_agent_service.py -k planned_services`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_pingan_capability_eval.py`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_action_adapters.py`
  - `PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_soc_agent_service.py -k 'correlation or read_only or action_policy_treats_asset_locate or memory'`
  - `git diff --check`
  - `codegraph sync .`
- 下一步：
  - 执行 `PA-10`：接 APT / EDR / HIDS domain triage MVP。domain handler 读取 capability cards / skill context / read-only evidence refs，输出 domain findings，不直接写 DB、不执行动作、不确认 memory。

### 2026-07-07 — PA-08 PingAn eval fixtures

- 背景：
  - `PA-07` 已补齐 P0 read-only mock action adapters；需要把这些能力固定成可回放 eval，确保后续 Lead Agent/router/domain handler 改动不会让 PingAn 能力退化或污染通用 skill。
- 变更：
  - 新增 `backend/soc_agent/eval/pingan.py`：
    - 定义 `PingAnCapabilityEvalFixture`、`PingAnCapabilityEvalReport` 等结构。
    - 读取 `backend/samples/eval/pingan/` fixture。
    - 通过 `SocAgentCapabilityRouter` + `SocAgentActionDispatcher` + `SocActionAdapterRegistry` 执行 read-only action。
    - 成功 action 必须写入 `InvestigationEvidence`。
  - 更新 `backend/soc_agent/eval/__init__.py` 和 `backend/soc_agent/cli.py`：
    - 新增 CLI：`soc eval pingan [path] --pretty`。
  - 新增 `backend/samples/alerts/pingan_legacy_hids.json`：
    - 补齐脱敏 HIDS 样本。
  - 新增 `backend/samples/eval/pingan/*.json`：
    - APT fixture 覆盖字段冲突、威胁情报命中、security tag 查不到。
    - EDR fixture 覆盖 process tree 命中、威胁情报查不到。
    - HIDS fixture 覆盖 host event context 命中、授权维护标签命中。
  - 更新 `backend/soc_agent/normalizers/pingan_platform.py`：
    - PingAn HIDS envelope 识别为 canonical `source_type=hids`。
  - 新增 `backend/tests/test_soc_pingan_capability_eval.py`：
    - 覆盖默认 fixtures、report 聚合和 CLI 输出。
  - 更新 `.notes/ai_soc/soc-agent-solution.md`、`.notes/ai_soc/capabilities/pingan/onboarding.md`、`.notes/ai_soc/capabilities/pingan/capability-cards.md`、`.notes/ai_soc/alert-lifecycle-flow.md` 和本进度台账：
    - `PA-08` 标记 Done。
    - 下一刀改为 `PA-09` memory candidate 入口。
- 验证：
  - `env UV_CACHE_DIR=/home/yydspei/projects/deer-flow/.tooling/uv-cache uv run ruff format backend/soc_agent/eval/pingan.py backend/soc_agent/eval/__init__.py backend/soc_agent/cli.py backend/soc_agent/normalizers/pingan_platform.py backend/tests/test_soc_pingan_capability_eval.py`
  - `env UV_CACHE_DIR=/home/yydspei/projects/deer-flow/.tooling/uv-cache uv run ruff check backend/soc_agent/eval/pingan.py backend/soc_agent/eval/__init__.py backend/soc_agent/cli.py backend/soc_agent/normalizers/pingan_platform.py backend/tests/test_soc_pingan_capability_eval.py`
  - `env UV_CACHE_DIR=/home/yydspei/projects/deer-flow/.tooling/uv-cache uv run pytest backend/tests/test_soc_pingan_capability_eval.py`
  - `env UV_CACHE_DIR=/home/yydspei/projects/deer-flow/.tooling/uv-cache uv run pytest backend/tests/test_soc_action_adapters.py backend/tests/test_soc_agent_offline_eval.py backend/tests/test_soc_pingan_capability_eval.py`
  - `env UV_CACHE_DIR=/home/yydspei/projects/deer-flow/.tooling/uv-cache uv run pytest backend/tests/test_soc_agent_service.py -k 'read_only or action_policy_treats_asset_locate'`
- 下一步：
  - 执行 `PA-09`：接 PingAn tenant memory candidate 入口，保持 `pending_review`，不自动写 confirmed memory。

### 2026-07-07 — PA-07 P0 read-only mock action adapters

- 背景：
  - 用户指出应先按 `capabilities/pingan/onboarding.md` 做好 PingAn 专项，而不是继续切到 External Disposition。
  - `PA-07` 的目标是补齐 P0 read-only mock adapters：`host.event_context.lookup`、`threat_intel.ip_reputation.lookup`、`security_tag.lookup`。
- 变更：
  - 更新 `backend/soc_agent/contracts/schemas.py` 和 `backend/soc_agent/contracts/__init__.py`：
    - 新增 `SocHostEventContextRecord`、`SocThreatIntelReputationRecord`、`SocSecurityTagRecord`。
  - 更新 `backend/soc_agent/actions/adapters.py`：
    - 新增 `HOST_EVENT_CONTEXT_LOOKUP_ACTION`、`THREAT_INTEL_IP_REPUTATION_LOOKUP_ACTION`、`SECURITY_TAG_LOOKUP_ACTION`。
    - 新增对应 descriptor 和 in-memory/mock adapters。
    - 默认 mock 数据使用 vendor-neutral host/account/IP/tag，不写平安内部知识。
    - 将旧 endpoint process-tree mock 中的 `UM001` 收敛为 `enterprise-user-1`。
  - 更新 `backend/soc_agent/core/service.py`：
    - 将三条新 action 加入 `SocAgentActionPolicy.READ_ONLY_ACTIONS`。
  - 更新 `backend/soc_agent/cli.py`：
    - `soc chat tui --lead-agent` 默认 read-only adapter registry 增加三条新 mock adapters。
  - 更新 `backend/soc_agent/lead_agent.py`：
    - 增加三条 read-only action proposal 示例。
    - 将 `BU` 表述收敛为通用 business ownership。
  - 更新测试：
    - `backend/tests/test_soc_action_adapters.py` 覆盖三条 mock adapters 的 descriptor 和 execute。
    - `backend/tests/test_soc_agent_service.py` 覆盖新 read-only policy 和 `security_tag.lookup` 成功写入 `InvestigationEvidence`。
  - 更新 `.notes/ai_soc/soc-agent-solution.md`、`.notes/ai_soc/capabilities/pingan/onboarding.md`、`.notes/ai_soc/capabilities/pingan/capability-cards.md`、`.notes/ai_soc/alert-lifecycle-flow.md` 和本进度台账：
    - `PA-07` 标记 Done。
    - 下一刀改为 `PA-08` eval fixtures。
- 验证：
  - `env UV_CACHE_DIR=/home/yydspei/projects/deer-flow/.tooling/uv-cache uv run ruff format --check backend/soc_agent/contracts/schemas.py backend/soc_agent/contracts/__init__.py backend/soc_agent/actions/adapters.py backend/soc_agent/core/service.py backend/soc_agent/cli.py backend/soc_agent/lead_agent.py backend/tests/test_soc_action_adapters.py backend/tests/test_soc_agent_service.py`
  - `env UV_CACHE_DIR=/home/yydspei/projects/deer-flow/.tooling/uv-cache uv run ruff check backend/soc_agent/contracts/schemas.py backend/soc_agent/contracts/__init__.py backend/soc_agent/actions/adapters.py backend/soc_agent/core/service.py backend/soc_agent/cli.py backend/soc_agent/lead_agent.py backend/tests/test_soc_action_adapters.py backend/tests/test_soc_agent_service.py`
  - `env UV_CACHE_DIR=/home/yydspei/projects/deer-flow/.tooling/uv-cache uv run pytest backend/tests/test_soc_action_adapters.py`
  - `env UV_CACHE_DIR=/home/yydspei/projects/deer-flow/.tooling/uv-cache uv run pytest backend/tests/test_soc_agent_service.py -k 'read_only or action_policy_treats_asset_locate'`
  - `env UV_CACHE_DIR=/home/yydspei/projects/deer-flow/.tooling/uv-cache uv run pytest backend/tests/test_soc_agent_lead_agent.py backend/tests/test_soc_lead_agent_chat.py -k 'profile or process_tree or asset_locate or action'`
- 下一步：
  - 执行 `PA-08`：为 APT/EDR/HIDS 每类至少建立 1 条脱敏 eval fixture，覆盖字段冲突、查不到外部事实、误报/授权标签。

### 2026-07-07 — Correlation Service MVP

- 背景：
  - Phase 2 需要先把“历史相似告警 + 可复用 evidence”做成稳定 service，而不是继续让 ReviewQueue 自己零散拼字段。
  - 当前目标是 deterministic MVP：不调用 LLM、不依赖真实 MCP、不修改 DeerFlow core。
- 变更：
  - 新增 `backend/soc_agent/core/correlation.py`：
    - `SocCorrelationService.correlate()` 以 `run_id` 为入口。
    - 从 `AlertSummaryRepository` 读取 subject summary。
    - 复用 `find_similar_alert_summaries()` 得到历史相似告警和匹配原因。
    - 从 `InvestigationEvidenceRepository` 读取每个历史 match 的 read-only evidence refs。
  - 更新 `backend/soc_agent/contracts/schemas.py` 和 `backend/soc_agent/contracts/__init__.py`：
    - 新增 `CorrelationQuery`、`CorrelationEvidenceRef`、`CorrelationMatch`、`CorrelationResult`。
  - 更新 `backend/soc_agent/core/__init__.py`：
    - 导出 `SocCorrelationService`。
  - 更新 `backend/soc_agent/cli.py`：
    - 新增 `soc correlate RUN_ID`。
    - 支持 `--limit`、`--candidate-limit`、`--evidence-limit`、`--pretty` 和 `--database-url`。
  - 更新测试：
    - `backend/tests/test_soc_agent_service.py` 覆盖 in-memory service correlation 和禁用 evidence loading。
    - `backend/tests/test_soc_agent_repository.py` 覆盖 SQLAlchemy repository 下的 correlation + reusable evidence。
- 验证：
  - `env UV_CACHE_DIR=/home/yydspei/projects/deer-flow/.tooling/uv-cache uv run ruff format --check backend/soc_agent/contracts/schemas.py backend/soc_agent/contracts/__init__.py backend/soc_agent/core/correlation.py backend/soc_agent/core/__init__.py backend/soc_agent/cli.py backend/tests/test_soc_agent_service.py backend/tests/test_soc_agent_repository.py`
  - `env UV_CACHE_DIR=/home/yydspei/projects/deer-flow/.tooling/uv-cache uv run ruff check backend/soc_agent/contracts/schemas.py backend/soc_agent/contracts/__init__.py backend/soc_agent/core/correlation.py backend/soc_agent/core/__init__.py backend/soc_agent/cli.py backend/tests/test_soc_agent_service.py backend/tests/test_soc_agent_repository.py`
  - `env UV_CACHE_DIR=/home/yydspei/projects/deer-flow/.tooling/uv-cache uv run pytest backend/tests/test_soc_agent_service.py -k 'correlation_service'`
  - `env UV_CACHE_DIR=/home/yydspei/projects/deer-flow/.tooling/uv-cache uv run pytest backend/tests/test_soc_agent_repository.py -k 'correlation_service or finds_similar_alert_summaries'`
- 下一步：
  - 当时建议执行 `External Disposition Sync Contract`；后续用户明确要求先做好 PingAn 专项，因此当前已改为继续 `PA-07` / `PA-08`。

### 2026-07-07 — PA-06 public skills minimal revisions

- 背景：
  - `PA-05` 已经把平安专属经验放进 pending candidates；下一步需要把 cards 中跨客户通用的研判方法补进 public skills。
  - 目标是增强 SOC Lead/Sub Agent 的通用研判能力，同时避免平安字段、内部环境、账号/组织、白名单、模板 ID、策略 ID 或处置阈值污染 public skill。
- 变更：
  - 更新 `skills/public/soc-alert-triage/SKILL.md`：
    - 增加 evidence review buckets 和 domain routing hints。
  - 更新 `skills/public/soc-network-apt-triage/SKILL.md`：
    - 增加网络/APT 通用事实重建、方向判断、攻击成功信号和 read-only query 建议。
  - 更新 `skills/public/soc-endpoint-triage/SKILL.md`：
    - 增加 endpoint/HIDS 通用 execution chain、风险维度、可疑/降风险指标和 read-only query 建议。
    - 移除 public skill 中偏租户化的 `UM-like` 表述，改为 generic enterprise account identifiers。
  - 更新 `skills/public/soc-waf-f5-triage/SKILL.md`：
    - 增加 HTTP 路径重建、代理链归因、Web 攻击成功信号和 read-only query 建议。
  - 更新 `skills/public/soc-asset-direction/SKILL.md`：
    - 增加 direction method 和 conflict handling。
  - 更新 `skills/public/soc-asset-extraction/SKILL.md`：
    - 把 `UM`、`BU/company code` 等租户/组织语义收敛为 tenant-specific identity、business ownership、organization code。
  - 更新 `.notes/ai_soc/soc-agent-solution.md`、`.notes/ai_soc/capabilities/pingan/onboarding.md`、`.notes/ai_soc/capabilities/pingan/capability-cards.md` 和本进度台账：
    - `PA-06` 标记 Done。
    - 整体下一刀回到 `Correlation Service MVP`。
- 验证：
  - `rg -n 'PingAn|平安|天眼|ZEUS|Zeus|zeus|rule_code|templateId|operateType|UM|BU|PA code|pa_code|company code|策略 ID|模板 ID|内部域名|部门|白名单|封堵策略|机房|青藤' skills/public/soc-*` 无匹配。
  - 待本切片结束执行 `git diff --check`。
- 下一步：
  - 开始 `Correlation Service MVP`：新增结构化 correlation contract/service/CLI，基于 `soc_alert_summaries` 和 `soc_investigation_evidence` 输出相似告警、匹配原因和可复用证据。

### 2026-07-07 — PA-05 PingAnKnowledgeCandidate register

- 背景：
  - APT/EDR/HIDS capability cards 已经展开，但其中仍包含大量平安专属经验、误报模式、身份模式、处置策略候选和环境事实。
  - 这些内容不能直接进入 public skill、runtime memory 或 Lead Agent prompt，否则会污染通用产品能力。
- 变更：
  - 新增 `.notes/ai_soc/capabilities/pingan/knowledge-candidates.md`：
    - 建立 `PingAnKnowledgeCandidate` schema。
    - 把 APT/EDR/HIDS cards 中的专属经验整理为 `PA-KC-*` candidate register。
    - 每条 candidate 标注 type、target artifact、tenant scope、source、status、validity、review owner 和验收要求。
    - 明确所有 candidate 默认 `pending_review`，不能直接影响 runtime decision。
  - 更新 `.notes/README.md` 和 `.notes/ai_soc/README.md`：
    - 增加 PingAn knowledge candidates 入口。
  - 更新 `.notes/ai_soc/capabilities/pingan/onboarding.md`：
    - `PA-05` 标记 Done。
    - 当时推进到 `PA-06`；后续已由 “PA-06 public skills minimal revisions” 记录完成。
  - 更新 `.notes/ai_soc/soc-agent-solution.md`、`.notes/ai_soc/capabilities/pingan/knowledge-decomposition.md`、`.notes/ai_soc/capabilities/pingan/capability-cards.md`、`.notes/ai_soc/alert-lifecycle-flow.md`：
    - 同步 PA-05 完成状态。
    - 明确下一刀是 `PA-06` public skill 最小修订。
- 验证：
  - 待本切片结束执行 `git diff --check`。
- 下一步：
  - 执行 `PA-06`：只把跨客户通用的 APT/EDR/HIDS 研判方法补进 `skills/public/soc-*`，并用 `rg` 确认没有平安字段、内部环境、账号/组织、白名单、模板 ID、策略 ID 或处置阈值进入 public skills。

### 2026-07-07 — PA-04 HIDS source decomposition

- 背景：
  - 用户要求继续执行 PingAn capability card 拆解；当前轮到 `PA-HIDS-SRC`。
  - HIDS 文档中的平安特定内容非常多，包括内部机房、域名、组名、账号、脚本路径、内部安全工具和服务器隔离模板，必须避免污染 public skill。
- 变更：
  - 更新 `.notes/ai_soc/capabilities/pingan/capability-cards.md`：
    - `PA-HIDS-SRC` 标记为 `PA-04 expanded`。
    - `PA-HIDS-001..003` 标记为 `Expanded`，`PA-HIDS-004` 标记为 `Boundary defined`。
    - 新增 `PA-04 HIDS Source Decomposition`：
      - 拆出 HIDS source content 到 host/endpoint skill、domain handler、tenant memory/config、read-only action、approval policy、eval fixture 的 artifact matrix。
      - 明确 HIDS public skill 只能包含通用主机事件方法，不能包含平安机房、内部域名、内部网段、组名、账号、脚本路径、工具名、operateType 或 templateId。
      - 草拟 `HidsTriageRequest` / `HidsTriageResult` 边界，要求 domain handler 只输出 findings/proposals，不直接调用 MCP、不写 DB、不改 verdict。
      - 固定 read-only actions：`host.event_context.lookup`、`asset.locate`、`security_tag.lookup`、`endpoint.process_tree.lookup`。
      - 固定 high-risk `host.isolate_server` 只作为 approval-gated proposal。
      - 列出 HIDS tenant memory candidates 和 eval fixture candidates。
  - 更新 `.notes/ai_soc/capabilities/pingan/onboarding.md`：
    - `PA-04` 标记 Done。
    - 当时推进到 `PA-05`；后续已由 “PA-05 PingAnKnowledgeCandidate register” 记录完成。
  - 更新当前待办：
    - 下一刀为 `PA-05` PingAnKnowledgeCandidate register。
- 验证：
  - 待本切片结束执行 `git diff --check`。
- 下一步：
  - 从 APT/EDR/HIDS expanded cards 抽 PingAn tenant candidates，默认 `pending_review`，并标注 source、validity、target artifact 和 review owner。

### 2026-07-07 — PA-03 EDR source decomposition

- 背景：
  - 用户确认 SOC Agent 必须保持通用化，不能和平安环境焊死。
  - 用户追问此前平安字段 adapter 的代码位置，明确后续其他客户/供应商也应通过对应 adapter 接入 canonical SOC schema。
- 代码位置确认：
  - 平安旧平台字段 adapter 在 `backend/soc_agent/normalizers/pingan_platform.py`。
  - 统一 normalize 入口在 `backend/soc_agent/normalizers/alert.py::normalize_alert_payload()`。
  - `normalize_alert_payload()` 先识别 `alert.hitLog[].zeusRawLogs[]` 这种平安旧平台 envelope，命中后交给 `normalize_pingan_platform_payload()`；否则走 generic normalizer。
- 变更：
  - 更新 `.notes/ai_soc/capabilities/pingan/capability-cards.md`：
    - `PA-EDR-SRC` 标记为 `PA-03 expanded`。
    - `PA-EDR-001..004` 标记为 `Expanded`，`PA-EDR-005` 标记为 `Boundary defined`。
    - 新增 `PA-03 EDR Source Decomposition`：
      - 拆出 EDR source content 到 endpoint skill、domain handler、tenant memory/config、read-only action、approval policy、eval fixture 的 artifact matrix。
      - 明确 EDR public skill 只能包含通用 endpoint 方法，不能包含平安安全路径、内部部门、管理员组、UM/外包账号格式、BU/PA code 或处置模板。
      - 草拟 `EdrTriageRequest` / `EdrTriageResult` 边界，要求 domain handler 只输出 findings/proposals，不直接调用 MCP、不写 DB、不改 verdict。
      - 固定 read-only actions：`endpoint.process_tree.lookup`、`asset.locate`、`security_tag.lookup`、`host.event_context.lookup`。
      - 固定 high-risk actions：`account.disable_um`、`endpoint.isolate_host`、`endpoint.isolate_ip` 只作为 approval-gated proposals。
      - 列出 EDR tenant memory candidates 和 eval fixture candidates。
  - 更新 `.notes/ai_soc/capabilities/pingan/onboarding.md`：
    - `PA-03` 标记 Done。
    - `PA-04` 当时进入后续待办；后续已由 “PA-04 HIDS source decomposition” 记录完成。
  - 更新当前待办：
    - 下一刀当时为 `PA-04` HIDS source decomposition；后续已推进到 `PA-05`。
- 验证：
  - 待本切片结束执行 `git diff --check`。
- 下一步：
  - 展开 `PA-HIDS-SRC`：HIDS 主机事件上下文、event_type 场景化研判、误报/授权运维模式、服务器隔离候选。

### 2026-07-07 — PA-02 APT source decomposition

- 背景：
  - 用户追问为什么要先做 capability cards，而不是直接做 skill prompt 或 MCP。
  - 结论是 cards 是分拣和验收层，用来决定哪些内容进入 public skill、tenant memory/config、adapter/normalizer、read-only MCP/action、approval-gated action 或 eval fixture；skill 和 MCP 是 cards 之后的具体落点，不应直接承接混合经验。
- 变更：
  - 更新 `.notes/ai_soc/capabilities/pingan/capability-cards.md`：
    - `PA-APT-SRC` 标记为 `PA-02 expanded`。
    - `PA-APT-001..004` 标记为 `Expanded`，`PA-APT-005` 标记为 `Boundary defined`。
    - 新增 `PA-02 APT Source Decomposition`：
      - 拆出 APT source content 到 skill、domain handler、tenant memory/config、read-only action、approval policy、eval fixture 的 artifact matrix。
      - 明确 APT public skill 只能包含通用方向重建和攻击成功证据方法，不能包含平安内部 host/URI/网段/策略 ID/模板 ID/BU/PA code。
      - 草拟 `AptTriageRequest` / `AptTriageResult` 边界，要求 domain handler 只输出 findings/proposals，不直接调用 MCP、不写 DB、不改 verdict。
      - 固定 read-only actions：`threat_intel.ip_reputation.lookup`、`security_tag.lookup`、`asset.locate`。
      - 固定 high-risk `response.block_ip` 只作为 approval-gated proposal。
      - 列出 APT tenant memory candidates 和 eval fixture candidates。
  - 更新 `.notes/ai_soc/capabilities/pingan/onboarding.md`：
    - `PA-02` 标记 Done。
    - `PA-03` 当时进入后续待办；后续已由 “PA-03 EDR source decomposition” 记录完成。
    - 修正 P0 card ID：`PA-APT-002` 是 APT 场景化研判，`PA-APT-003` 是威胁情报，`PA-APT-004` 是 security tag。
  - 更新当前待办：
    - 下一刀当时为 `PA-03` EDR source decomposition；后续已推进到 `PA-04`。
- 验证：
  - 待本切片结束执行 `git diff --check`。
- 下一步：
  - 展开 `PA-EDR-SRC`：EDR 进程树、路径/命令行、LoginData/System、提权、UM/账号、终端处置候选。

### 2026-07-07 — PA-01 PingAn capability card register

- 背景：
  - 用户指出 PingAn 方案推进顺序混乱，不能直接跳到 mock MCP；必须先把 `.notes/ai_soc/capabilities/pingan/source-docs/` 拆成明确 TODO 和 capability cards。
  - 当前目标是让平安经验先成为可审计、可实现、可评测的 cards，再进入 skill、tenant memory、MCP/action、policy/config、domain handler 或 eval。
- 变更：
  - 新增 `.notes/ai_soc/capabilities/pingan/capability-cards.md`：
    - 固定 source register：`PA-APT-SRC`、`PA-EDR-SRC`、`PA-HIDS-SRC`。
    - 建立 card register：`PA-COM-*`、`PA-APT-*`、`PA-EDR-*`、`PA-HIDS-*`、`PA-RESP-*`。
    - 展开 P0 card：历史关联漏斗、资产提取/归属、APT 方向、APT 场景化研判、威胁情报、security tag、EDR 进程树、HIDS 主机事件、HIDS event_type 研判。
    - 明确 guardrails：不复制原 prompt、不污染 public skill、不以 rule_code 为必需主键、不把 read-only evidence 自动写 confirmed memory、不让 high-risk action 进入自由 tool call。
  - 更新 `.notes/ai_soc/capabilities/pingan/onboarding.md`：
    - `PA-01` 标记 Done。
    - `PA-02` 当时进入后续待办；后续已由 “PA-02 APT source decomposition” 记录完成。
  - 更新 `.notes/README.md` 和 `.notes/ai_soc/README.md`：
    - 将 capability card register 纳入 active notes。
  - 更新当前待办：
    - 下一刀当时为 `PA-02` APT source decomposition；后续已推进到 `PA-03`。
- 验证：
  - 待本切片结束执行 `git diff --check`。
- 下一步：
  - 扩展 `PA-APT-001..004`，把 APT 通用方法、平安专属 tenant artifact、read-only action、eval fixture 边界拆实。

### 2026-07-07 — PingAn knowledge decomposition boundary

- 背景：
  - 用户指出 PingAn APT/EDR/HIDS 文档中的很多历史 prompt 实际不是 prompt，而是平安运营经验、环境知识、误报模式、字段映射、工具能力或处置策略。
  - 用户进一步指出现有 `skills/public/soc-*` 也需要明确边界：哪些是通用研判 skill，哪些是平安知识，哪些进 memory，哪些进 MCP/action。
- 变更：
  - 新增 `.notes/ai_soc/capabilities/pingan/knowledge-decomposition.md`：
    - 固定 PingAn docs -> skill / tenant memory / adapter / MCP/action / policy/config / eval fixture 的拆解矩阵。
    - 明确通用 skill 只能保存跨客户研判方法，平安内部域名、账号、部门、路径、规则码、模板 ID、策略 ID、误报模式等进入 tenant-scoped memory/config/adapter/eval。
    - 规划第一批拆解任务：补 skill boundary、抽 P0 capability cards、设计 knowledge candidates、mock read-only adapters、建 eval fixtures、接 memory candidate。
  - 给现有六个 SOC skill 增加 `Knowledge Boundary`：
    - `soc-alert-triage`
    - `soc-asset-direction`
    - `soc-asset-extraction`
    - `soc-endpoint-triage`
    - `soc-network-apt-triage`
    - `soc-waf-f5-triage`
  - 更新 `.notes/ai_soc/memory/memory-tracking.md`：
    - 新增 `benign_pattern`、`identity_pattern`、`response_policy_hint` memory types。
    - 增加 PingAn prompt decomposition memory 映射。
  - 更新 `.notes/reference-index/soc-agent-engineering-contracts.md`：
    - 明确历史 prompt 原文不得整体复制进 Lead Agent prompt、analysis node prompt 或 public skill。
    - 平安字段名仅能进入 adapter/normalizer/mapping tests/fixture；core、public skill、Lead Agent prompt 消费 canonical fields。
- 下一步：
  - 从 PingAn docs 抽第一批 P0 `PingAnKnowledgeCandidate` / capability cards：APT 方向、EDR 进程树、HIDS 主机事件、资产归属、外部查询、处置动作。
  - 随后开始 mock `host.event_context.lookup`、`threat_intel.ip_reputation.lookup`、`security_tag.lookup`。
- 验证：
  - `rg -n 'paic|平安|Zeus|天眼|RPAADM|strategyId|operateType|pa_code|biz_group|company_code|detail_|str_' skills/public/soc-*` 无命中，确认通用 skill 不含平安/Zeus 专属字段或规则。
  - `cd backend && ./.venv/bin/python -m pytest tests/test_lead_agent_skills.py tests/test_skills_validation.py tests/test_skills_loader.py`

### 2026-07-06 — SOC notes active set cleanup

- 背景：
  - 用户指出 `.notes/ai_soc` 又开始堆积文档，需要删除/移走低价值内容，保留真正有用的入口，并让使用者知道怎么读这些文档。
- 变更：
  - 重写 `.notes/ai_soc/README.md`，按使用场景说明先看哪份、再看哪份、什么时候更新哪份。
  - 同步 `.notes/README.md`，只列当前主线文档，不再把已完成切片和暂缓项作为 active docs。
  - 将已完成或低频实现计划移到 `.notes/archive/ai_soc/implementation-plans/`：
    - `action-adapter-registry-plan.md`
    - `kafka-consumer-adapter-plan.md`
    - `mcp-adapter-bridge-plan.md`
  - 将当前暂缓项移到 `.notes/archive/ai_soc/deferred/`：
    - `kafka-worker-pool-concurrency-plan.md`
    - `operations-overview-deferred.md`
  - 将已被主方案/工程契约吸收的背景参考移到 `.notes/archive/ai_soc/reference/`：
    - `normalization-drift-strategy.md`
    - `zeus-alert-flow-and-field-trust.md`
  - 将生产运行 runbook 移到 `.notes/archive/ai_soc/runbooks/`，等进入部署阶段再提升为 active。
- 当前 active `.notes/ai_soc` 顶层只保留：
  - `README.md`
  - `soc-agent-solution.md`
  - `progress.md`
  - `alert-lifecycle-flow.md`
  - `capabilities/pingan/onboarding.md`
  - `integrations/external-disposition-sync.md`
  - `memory/memory-tracking.md`
  - `governance/agent-profile-governance.md`
- 验证：
  - active docs 已无旧 `.notes/ai_soc/<archived-doc>.md` 路径引用。

### 2026-07-05 — Alert lifecycle roadmap refresh

- 背景：
  - 用户希望看到完整 SOC Agent + EDR/APT/HIDS/F5 domain sub-agent 研判效果，需要把当前 lifecycle 和接下来实现路线讲清楚。
  - 真实 dev/staging MCP endpoint/凭证暂不可用，继续扩展 mock tool 收益下降。
- 追加决策：
  - 用户明确提出还需要把平安 SOC tool/MCP/skill 经验嵌入项目，才能让系统真正像生产 AI SOC 跑起来。
  - 因此新增 PingAn SOC capability onboarding 作为 Slice 0：每条内部经验先整理成 capability card，再分类落到 skill、MCP/action adapter、normalizer、domain handler、eval case 或 memory candidate。
  - 用户进一步提出后续需要按 topic/rule_code/场景做记忆追踪，并将 SOC TUI / Kafka 工作流中的重要结论更新到记忆，保持经验最新。
  - 随后澄清：`rule_code` 只是平安等平台的 vendor alias；topic、detection、scenario 等也不能变成硬主键。Memory 需要采用 typed record + facets + retrieval policy，DB 先做稳，wiki/OKF 后期作为展示/审阅 projection。
  - 用户提出现实工作流里分析师仍会在老 Zeus 预警系统处理告警，需要 Zeus 更新状态/理由后同步到 SOC Agent，并在空闲时把理由沉淀成候选记忆或 skill 优化候选。该能力必须做成市场化、可扩展、可插拔协议，不能写死平安 Zeus。
- 变更：
  - `.notes/ai_soc/alert-lifecycle-flow.md` 重写为当前 As-Is 生命周期 + To-Be Main SOC Agent / domain sub-agent 可见链路。
  - 新增 `.notes/ai_soc/capabilities/pingan/onboarding.md`，记录平安 SOC 经验注入流程、capability card 模板、P0 能力 backlog 和用户提供信息格式。
  - 新增 `.notes/ai_soc/memory/memory-tracking.md`，记录 DB-first typed memory store、facets retrieval、写入来源、状态机、DB/wiki 一致性和实现路线。
  - 新增 `.notes/ai_soc/integrations/external-disposition-sync.md`，固定 external disposition feedback lane：外部系统状态/理由更新通过 adapter 转成 `SocExternalDispositionEvent`，再由 service 同步 audit/review/correction，并生成 pending memory / skill improvement candidate。
  - 明确下一阶段路线：PingAn SOC capability onboarding -> `SocCorrelationService` -> External Disposition Sync Contract -> Memory Tracking Contract -> domain sub-agent contract -> EDR/APT/HIDS/F5 MVP handlers -> Main SOC Agent orchestrator -> unified investigation report -> Web/TUI 可见化 -> demo/eval script。
  - 当时进度台账把下一刀切到 Phase 2 最小 Correlation Service，并把 External Disposition Sync Contract 和 Memory Tracking Contract 加入后续待办。
- 下一步：
  - 实现 Correlation Service MVP，先让 review context / CLI 能看到结构化相似告警、匹配原因和可复用 investigation evidence。
  - 之后实现 External Disposition Sync Contract，再实现 Memory Tracking Contract，接 TUI/Web correction、Kafka repeated pattern、external status/reason 和 domain triage 的候选记忆写入。
  - PingAn capability card 收集已由 `PA-01` 承接到 `.notes/ai_soc/capabilities/pingan/capability-cards.md`；后续按 `PA-02/03/04` 逐源展开。

### 2026-07-05 — Lead Agent evidence reuse + endpoint process-tree mock adapter

- 背景：
  - 用户确认当前没有真实 dev/staging CMDB/EDR MCP endpoint/凭证，因此不能继续把真实 MCP smoke 作为当前下一刀。
  - 为了继续验证 SOC Lead Agent 的安全运营能力，先扩展不依赖外部服务的 read-only mock adapter，并让 Lead Agent 明确复用已有 action evidence。
- 变更：
  - `backend/soc_agent/contracts/schemas.py`：
    - 新增 `SocEndpointProcessNode`、`SocEndpointNetworkConnection`、`SocEndpointProcessTreeRecord`。
  - `backend/soc_agent/actions/adapters.py`：
    - 新增 `endpoint.process_tree.lookup` read-only descriptor 和 `InMemoryEndpointProcessTreeLookupActionAdapter`。
    - 默认 mock 返回 endpoint process tree、suspicious PowerShell、network connection 等结构化 EDR 证据。
  - `backend/soc_agent/core/service.py` / `backend/soc_agent/cli.py`：
    - `endpoint.process_tree.lookup` 纳入 read-only policy。
    - `soc chat tui --lead-agent` 默认本地 registry 同时包含 `asset.lookup` 和 `endpoint.process_tree.lookup`；显式 `--mcp-action-config` 仍只使用配置中的 MCP adapter。
  - `backend/soc_agent/lead_agent.py` / `backend/soc_agent/context_bridge.py`：
    - SOC Lead Agent profile 和 bounded context instructions 明确：已有 `action_evidence` 要先复用，避免重复查同类只读工具。
    - 增加 `endpoint.process_tree.lookup` proposal 示例。
  - 测试：
    - 覆盖 endpoint process-tree adapter descriptor/execute/not-found。
    - 覆盖 policy 把 `endpoint.process_tree.lookup` 识别为 read-only。
    - 覆盖 Lead Agent proposal -> router/policy/dispatcher -> mock adapter -> evidence repository。
- 下一步：
  - 继续不依赖真实 MCP 时，可补 Web/TUI evidence 过滤/详情体验或更多 read-only mock adapter。
  - 真实 dev/staging MCP smoke 等 endpoint/凭证可用后再执行。

### 2026-07-05 — InvestigationEvidence PostgreSQL persistence / Gateway wiring

- 背景：
  - 上一刀 `InvestigationEvidence` 只在同进程内存中可复用，Web、TUI、daemon、Lead Agent 不能跨进程共享只读查询结果。
  - 在接真实 CMDB/EDR MCP 前，先把 evidence 落到 SOC business store，避免真实工具结果只停留在一次 stream 里。
- 变更：
  - `backend/soc_agent/db/models.py`：
    - 新增 `SocInvestigationEvidenceRow`，保存 route/action/status、queue/run/alert/thread/proposal/context 索引和完整 `evidence_payload`。
  - `backend/soc_agent/db/migrations/versions/0008_investigation_evidence.py`：
    - 新增 `soc_investigation_evidence` 表和常用查询索引。
  - `backend/soc_agent/db/repositories.py`：
    - `SqlAlchemyAlertRepository` 实现 `save_evidence()` / `list_evidence()`。
  - Gateway / CLI wiring：
    - `app.gateway.routers.soc_review.get_soc_review_service()` 将同一 repository 注入 `evidence_repository`。
    - `soc review context`、`soc review tui`、`soc chat tui --lead-agent` 使用 PG repository 聚合/写入 evidence。
  - 测试：
    - 增加 SQLAlchemy evidence persistence、ReviewService context 聚合、Gateway context action_evidence 覆盖。
- 已验证：
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_repository.py tests/test_soc_review_router.py tests/test_soc_agent_service.py tests/test_soc_lead_agent_chat.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/db soc_agent/core soc_agent/contracts soc_agent/protocols.py soc_agent/context_bridge.py soc_agent/cli.py soc_agent/tui/render.py app/gateway/routers/soc_review.py tests/test_soc_agent_repository.py tests/test_soc_review_router.py tests/test_soc_agent_service.py tests/test_soc_lead_agent_chat.py`
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/db soc_agent/core soc_agent/contracts soc_agent/protocols.py soc_agent/context_bridge.py soc_agent/cli.py soc_agent/tui/render.py app/gateway/routers/soc_review.py tests/test_soc_agent_repository.py tests/test_soc_review_router.py tests/test_soc_agent_service.py tests/test_soc_lead_agent_chat.py --check`
  - `cd backend && ./.venv/bin/python -m soc_agent.cli db upgrade --database-url sqlite:////tmp/soc_agent_evidence_migration_smoke.db`
  - `cd frontend && pnpm check`
- 下一步：
  - 真实 dev/staging MCP server 尚不可用时，继续做 mock read-only adapter 和 evidence reuse 体验。
  - endpoint/凭证可用后，再跑 `soc mcp tools` / `soc mcp smoke` 并保存 `soc.mcp_action_smoke_report.v1`。

### 2026-07-05 — Read-only action result evidence bridge

- 背景：
  - `asset.lookup` / `asset.locate` 已能通过 Lead Agent proposal -> policy -> dispatcher -> adapter/MCP 产生 `soc.action_result`。
  - 如果结果只停留在 stream 事件里，分析师重新打开工单、Web 页面或后续 Lead Agent turn 无法复用“已经查过的资产定位结果”。
- 变更：
  - `backend/soc_agent/contracts/schemas.py`：
    - 新增 `InvestigationEvidence`，表示只读 action/tool 产生的调查证据。
    - `InvestigationContext` 和 `SocLeadAgentReviewContextArtifact` 增加 `action_evidence`。
  - `backend/soc_agent/protocols.py` / `backend/soc_agent/core/evidence.py`：
    - 新增 `InvestigationEvidenceRepository` 协议和 `InMemoryInvestigationEvidenceRepository`。
  - `backend/soc_agent/core/service.py`：
    - `SocAgentActionDispatcher` 在 read-only adapter 成功执行后可选写入 evidence，并把 `evidence_id` 回填到 action result payload。
    - `SocReviewService.get_investigation_context()` 可选聚合 action evidence。
  - `backend/soc_agent/context_bridge.py`：
    - Lead Agent bounded review artifact 带入最近 action evidence，数量受限。
  - `backend/soc_agent/cli.py`：
    - `soc chat tui --lead-agent` 同一进程中共享 in-memory evidence repository。
  - `backend/soc_agent/tui/render.py` / `frontend/src/components/workspace/soc/soc-review-queue-workbench.tsx`：
    - Review context / Web review 页面展示只读查询证据摘要和 result payload。
- 已验证：
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_agent_service.py tests/test_soc_lead_agent_chat.py`
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/contracts soc_agent/protocols.py soc_agent/core/evidence.py soc_agent/core/service.py soc_agent/context_bridge.py soc_agent/cli.py soc_agent/tui/render.py tests/test_soc_agent_service.py tests/test_soc_lead_agent_chat.py --check`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/contracts soc_agent/protocols.py soc_agent/core/evidence.py soc_agent/core/service.py soc_agent/context_bridge.py soc_agent/cli.py soc_agent/tui/render.py tests/test_soc_agent_service.py tests/test_soc_lead_agent_chat.py`
  - `cd frontend && pnpm check`
- 下一步：
  - 做 `InvestigationEvidence` PostgreSQL repository / migration / Gateway wiring，让 evidence 跨进程共享。
  - PG 持久化完成后，再用真实 dev/staging CMDB/EDR MCP config 替换本地 mock 并保存 smoke report。

### 2026-07-05 — Asset extraction skill + asset.locate MCP mock

- 背景：
  - 用户提供的 `资产提取器.py` / `资产定位器.py` 原型分别代表两类能力：提取资产和角色应沉淀为 skill；远程资产归属/BU 定位应走 MCP/tool 边界。
  - 真实 Zeus/CMDB/asset_to_bu 远程调用暂不接入，本切片用 deterministic mock 结果验证协议和 proposal bridge。
- 变更：
  - 新增 `skills/public/soc-asset-extraction/SKILL.md`：
    - 指导 Lead Agent 提取 IP、DOMAIN、WEB/URL、HOST、USER、UM 等资产。
    - 明确 role assignment、disposal target、recommended lookup order。
    - 明确 skill 不执行远程调用；需要定位归属时只能提出 `asset.locate` proposal。
  - `backend/soc_agent/skills.py`：
    - 新增 `SOC_ASSET_EXTRACTION_SKILL`，加入 SOC Lead Agent skills 和 resolver。
    - 当告警包含资产实体、用户/UM/host/domain/url 关键词或字段冲突时，选择资产提取 skill。
  - `backend/scripts/soc_dev_mcp_server.py`：
    - 新增 MCP tool `asset_locate`，模拟远程资产归属/BU 定位，返回 `company_code`、`biz_group`、`search_results`、`mocked=true`。
  - `backend/samples/mcp/soc_dev_action_adapters.json`：
    - 新增 `asset.locate -> soc_dev_asset_locate` read-only adapter config。
  - `backend/soc_agent/core/service.py` / `backend/soc_agent/lead_agent.py`：
    - 将 `asset.locate` 纳入 read-only action policy。
    - SOC Lead Agent profile 增加 `asset.locate` proposal 示例。
  - `backend/soc_agent/cli.py`：
    - `soc chat tui --lead-agent --mcp-action-config PATH` 可显式注入 MCP-backed read-only action registry。
    - 不传 config 时仍使用本地 `InMemoryAssetLookupActionAdapter`。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format scripts/soc_dev_mcp_server.py soc_agent/skills.py soc_agent/lead_agent.py soc_agent/cli.py soc_agent/core/service.py tests/test_soc_mcp_adapters.py tests/test_soc_agent_lead_agent.py tests/test_soc_agent_service.py tests/test_soc_lead_agent_chat.py --check`
  - `cd backend && ./.venv/bin/python -m ruff check scripts/soc_dev_mcp_server.py soc_agent/skills.py soc_agent/lead_agent.py soc_agent/cli.py soc_agent/core/service.py tests/test_soc_mcp_adapters.py tests/test_soc_agent_lead_agent.py tests/test_soc_agent_service.py tests/test_soc_lead_agent_chat.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_mcp_adapters.py tests/test_soc_agent_lead_agent.py tests/test_soc_lead_agent_chat.py tests/test_soc_agent_service.py::test_agent_action_policy_treats_asset_locate_as_read_only`
  - `cd backend && SOC_DEV_MCP_PYTHON=/home/yydspei/projects/deer-flow/backend/.venv/bin/python SOC_DEV_MCP_SERVER=/home/yydspei/projects/deer-flow/backend/scripts/soc_dev_mcp_server.py DEER_FLOW_EXTENSIONS_CONFIG_PATH=/home/yydspei/projects/deer-flow/backend/samples/mcp/soc_dev_extensions_config.json ./.venv/bin/python -m soc_agent.cli mcp tools --include-schema --pretty`
  - `cd backend && SOC_DEV_MCP_PYTHON=/home/yydspei/projects/deer-flow/backend/.venv/bin/python SOC_DEV_MCP_SERVER=/home/yydspei/projects/deer-flow/backend/scripts/soc_dev_mcp_server.py DEER_FLOW_EXTENSIONS_CONFIG_PATH=/home/yydspei/projects/deer-flow/backend/samples/mcp/soc_dev_extensions_config.json ./.venv/bin/python -m soc_agent.cli mcp smoke samples/mcp/soc_dev_action_adapters.json --route asset.locate --json '{"asset_key":"10.10.1.5","asset_type":"IP","role":"target","context_refs":{"thread_id":"SOC-THREAD-1"}}' --pretty`
- 下一步：
  - 将 `asset.locate` 真实 dev/staging MCP 参数替换 mock config，保存 smoke report。
  - 如果要进 Web/TUI 正式体验，再给 `soc chat tui --lead-agent --mcp-action-config ...` 做一条可复现实操验收脚本。

### 2026-07-05 — Upstream MCP sync compatibility retest

- 背景：
  - 同步 `upstream/main` 后，DeerFlow MCP core 新增了按 source server 分组路由的修复，避免 `web` / `web_scraper` 这类 server 名前缀重叠时误路由。
  - SOC MCP adapter 原本已经有 `mcp.server` 配置字段，但 execute / smoke one-shot 路径仍主要依赖 tool name 前缀推断 server，需要与 upstream 的新路由语义对齐。
- 变更：
  - `backend/soc_agent/actions/mcp.py`：
    - `SocMcpToolProviderPort.invoke()` 增加可选 `server_name` keyword。
    - `SocMcpToolActionAdapter` 保存 `mcp_server` 并在 execute 时传给 provider。
    - one-shot MCP smoke 路径优先使用显式 `server_name` 定位连接，并只在未提供 server 时 fallback 到最长前缀推断。
    - MCP inventory server 字段优先读取 metadata，缺失时按已配置 server 的最长前缀做只读展示推断。
  - `backend/tests/test_soc_mcp_adapters.py`：
    - 覆盖 config-built adapter 把 `server_name=cmdb` 传入 provider。
    - 覆盖 prefix overlap 时 explicit server 优先。
  - 更新工程契约和主方案：`mcp.server` 是执行路由绑定，不只是展示字段。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/actions/mcp.py tests/test_soc_mcp_adapters.py --check`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/actions/mcp.py tests/test_soc_mcp_adapters.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_mcp_adapters.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_mcp_session_pool.py::test_mcp_tools_routed_to_source_server_with_prefix_overlap`
  - `cd backend && SOC_DEV_MCP_PYTHON=/home/yydspei/projects/deer-flow/backend/.venv/bin/python SOC_DEV_MCP_SERVER=/home/yydspei/projects/deer-flow/backend/scripts/soc_dev_mcp_server.py DEER_FLOW_EXTENSIONS_CONFIG_PATH=/home/yydspei/projects/deer-flow/backend/samples/mcp/soc_dev_extensions_config.json ./.venv/bin/python -m soc_agent.cli mcp tools --include-schema --pretty`
  - `cd backend && SOC_DEV_MCP_PYTHON=/home/yydspei/projects/deer-flow/backend/.venv/bin/python SOC_DEV_MCP_SERVER=/home/yydspei/projects/deer-flow/backend/scripts/soc_dev_mcp_server.py DEER_FLOW_EXTENSIONS_CONFIG_PATH=/home/yydspei/projects/deer-flow/backend/samples/mcp/soc_dev_extensions_config.json ./.venv/bin/python -m soc_agent.cli mcp smoke samples/mcp/soc_dev_action_adapters.json --route asset.lookup --json '{"asset_key":"10.10.1.5","context_refs":{"thread_id":"SOC-THREAD-1"}}' --pretty`
- 下一步：
  - 仍然是拿真实 dev/staging CMDB/EDR MCP 参数替换本地 fixture，再跑同一组 `tools/smoke` 并保存 `soc.mcp_action_smoke_report.v1`。

### 2026-07-05 — Local real MCP fixture and read-only smoke 切片

- 背景：
  - 前一刀 `soc mcp tools` 在未配置 MCP 时只能返回 `tool_count=0`。
  - 为了验证真实 MCP 协议链路，而不是继续用 fake provider，需要一个无凭证、无外部副作用的本地 read-only MCP server。
- 变更：
  - 新增 `backend/scripts/soc_dev_mcp_server.py`：
    - 最小 MCP JSON-RPC stdio server。
    - 暴露 `asset_lookup` 只读工具。
    - 返回 MCP `structuredContent`，用于验证真实 MCP result 归一化。
  - 新增样例配置：
    - `backend/samples/mcp/soc_dev_extensions_config.json`
    - `backend/samples/mcp/soc_dev_action_adapters.json`
  - `backend/soc_agent/actions/mcp.py`：
    - `DeerFlowCachedMcpToolProvider` 支持 smoke execute one-shot MCP session 调用。
    - 继续先通过 DeerFlow cached MCP inventory 验证 exact tool 可见。
    - 归一化 MCP `structuredContent`，让 `output_fields` 能裁剪业务字段。
  - `backend/soc_agent/cli.py`：
    - `soc mcp smoke` 使用 one-shot read-only invocation，避免 DeerFlow stdio workspace snapshot 阻塞数据查询类 MCP smoke。
  - `backend/tests/test_soc_mcp_adapters.py` 增加 `structuredContent` 归一化覆盖。
  - 更新主方案、MCP bridge plan 和工程契约。
- 本机 smoke：
  - `soc mcp tools --include-schema --pretty` 在样例 MCP config 下返回 `tool_count=1`、tool=`soc_dev_asset_lookup`。
  - `soc mcp smoke samples/mcp/soc_dev_action_adapters.json --route asset.lookup --json '{"asset_key":"10.10.1.5","context_refs":{"thread_id":"SOC-THREAD-1"}}' --pretty` 返回 `status=success`、`asset_found=true`。
- 边界：
  - 本地 fixture 不是生产 CMDB/EDR。
  - 样例 `extensions_config` 使用 `$SOC_DEV_MCP_PYTHON` / `$SOC_DEV_MCP_SERVER` 传绝对路径，避免 DeerFlow stdio tool 执行时切换 cwd 后相对路径失效。
  - 不开放 high-risk MCP execute。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff check scripts/soc_dev_mcp_server.py soc_agent/actions/mcp.py soc_agent/cli.py tests/test_soc_mcp_adapters.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_mcp_adapters.py`
  - `cd backend && SOC_DEV_MCP_PYTHON=/home/yydspei/projects/deer-flow/backend/.venv/bin/python SOC_DEV_MCP_SERVER=/home/yydspei/projects/deer-flow/backend/scripts/soc_dev_mcp_server.py DEER_FLOW_EXTENSIONS_CONFIG_PATH=/home/yydspei/projects/deer-flow/backend/samples/mcp/soc_dev_extensions_config.json ./.venv/bin/python -m soc_agent.cli mcp tools --include-schema --pretty`
  - `cd backend && SOC_DEV_MCP_PYTHON=/home/yydspei/projects/deer-flow/backend/.venv/bin/python SOC_DEV_MCP_SERVER=/home/yydspei/projects/deer-flow/backend/scripts/soc_dev_mcp_server.py DEER_FLOW_EXTENSIONS_CONFIG_PATH=/home/yydspei/projects/deer-flow/backend/samples/mcp/soc_dev_extensions_config.json ./.venv/bin/python -m soc_agent.cli mcp smoke samples/mcp/soc_dev_action_adapters.json --route asset.lookup --json '{"asset_key":"10.10.1.5","context_refs":{"thread_id":"SOC-THREAD-1"}}' --pretty`
- 下一步：
  - 拿真实 dev/staging CMDB/EDR MCP 参数替换本地 fixture，再跑同一组 `tools/smoke`。
  - 或先把 MCP-backed `asset.lookup` registry 注入 `SocLeadAgentActionProposalBoundary` 的 read-only dispatcher，默认仍 behind config。

### 2026-07-05 — MCP smoke readiness inventory 切片

- 背景：
  - 真实 dev/staging read-only MCP smoke 需要 DeerFlow cached MCP tools 可见。
  - 当前本机只有 `extensions_config.example.json`，没有启用的 `extensions_config.json` / `mcp_config.json`；直接列 DeerFlow cached MCP tools 得到 `tool_count=0`。
- 变更：
  - `backend/soc_agent/actions/mcp.py` 新增：
    - `SocMcpToolInventoryItem`
    - `SocMcpToolInventoryReport`
    - `inspect_mcp_tool_inventory()`
  - `backend/soc_agent/cli.py` 新增：
    - `soc mcp tools`
    - `soc mcp tools --include-schema`
    - `soc mcp tools --report-path PATH`
    - `soc mcp smoke --report-path PATH`
  - `backend/tests/test_soc_mcp_adapters.py` 增加 inventory success/failure、CLI tools 和 report-path 覆盖。
- 本机 readiness：
  - `soc mcp tools --pretty` 输出 `status=success`、`tool_count=0`。
  - 说明当前还不能跑真实 `asset.lookup` / EDR process tree smoke，需要先配置 dev/staging MCP server。
- 边界：
  - inventory 只列 tool name/server/description，默认不输出 input schema；`--include-schema` 才输出 schema。
  - 不调用 MCP tool。
  - 不打印 secret。
  - 不接生产 MCP server。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/actions/mcp.py soc_agent/cli.py tests/test_soc_mcp_adapters.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_mcp_adapters.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/actions soc_agent/cli.py soc_agent/lead_agent.py soc_agent/lead_agent_chat.py tests/test_soc_action_adapters.py tests/test_soc_mcp_adapters.py tests/test_soc_lead_agent_chat.py tests/test_soc_agent_service.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/actions soc_agent/cli.py soc_agent/lead_agent.py soc_agent/lead_agent_chat.py tests/test_soc_action_adapters.py tests/test_soc_mcp_adapters.py tests/test_soc_lead_agent_chat.py tests/test_soc_agent_service.py tests/architecture/test_soc_agent_boundaries.py --check`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_mcp_adapters.py tests/test_soc_action_adapters.py tests/test_soc_lead_agent_chat.py tests/test_soc_agent_service.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m soc_agent.cli mcp tools --pretty`
  - `codegraph sync .`
- 下一步：
  - 创建/启用真实 dev/staging `extensions_config.json` 或 `mcp_config.json`，确认 `soc mcp tools` 能看到目标 read-only tool。
  - 写对应 `soc_action_adapters.yaml/json`，运行 `soc mcp smoke CONFIG --route asset.lookup --json ... --report-path ... --pretty`。

### 2026-07-05 — Dev/staging read-only MCP smoke report contract 切片

- 背景：
  - `soc mcp smoke` 已能跑通 config -> registry -> provider -> action result，但真实 dev/staging 验证还需要稳定的 metrics/report 输出。
  - 当前本机没有真实 CMDB/EDR dev/staging MCP 参数，因此本切片只固定 report contract，不冒充 live MCP 已连通。
- 变更：
  - `backend/soc_agent/actions/mcp.py` 新增：
    - `SocMcpActionSmokeReport`
    - `run_mcp_action_adapter_smoke()`
  - smoke report 字段包括：
    - `duration_ms`
    - `action_payload_bytes`
    - `action_result_bytes`
    - `mcp_result_bytes`
    - `adapter_id / adapter_kind / mcp_server / tool_name / timeout_seconds`
    - `output_fields / output_filter_applied / mcp_result_keys`
    - `error_type / error_message`
    - `action_result`
  - `backend/soc_agent/cli.py` 的 `soc mcp smoke` 改为输出 `soc.mcp_action_smoke_report.v1`，失败也返回结构化 JSON report。
  - `backend/tests/test_soc_mcp_adapters.py` 增加 smoke success metrics、config failure 和 CLI report 覆盖。
- 边界：
  - 不接生产 MCP server。
  - 不把 smoke report 接入默认 chat/daemon。
  - 不开放 high-risk execute。
  - 不让 Lead Agent 直接调用 MCP tool。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/actions/mcp.py soc_agent/cli.py tests/test_soc_mcp_adapters.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_mcp_adapters.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/actions soc_agent/cli.py soc_agent/lead_agent.py soc_agent/lead_agent_chat.py tests/test_soc_action_adapters.py tests/test_soc_mcp_adapters.py tests/test_soc_lead_agent_chat.py tests/test_soc_agent_service.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/actions soc_agent/cli.py soc_agent/lead_agent.py soc_agent/lead_agent_chat.py tests/test_soc_action_adapters.py tests/test_soc_mcp_adapters.py tests/test_soc_lead_agent_chat.py tests/test_soc_agent_service.py tests/architecture/test_soc_agent_boundaries.py --check`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_mcp_adapters.py tests/test_soc_action_adapters.py tests/test_soc_lead_agent_chat.py tests/test_soc_agent_service.py tests/architecture/test_soc_agent_boundaries.py`
  - `codegraph sync .`
- 下一步：
  - 准备一份真实 dev/staging MCP config，运行 `soc mcp smoke CONFIG --route asset.lookup --json ... --pretty`，把 report 记录到台账或 runbook，评估延迟、失败率、payload size 和字段裁剪。

### 2026-07-05 — Read-only MCP config smoke wiring 切片

- 背景：
  - `DeerFlowCachedMcpToolProvider` 已能复用 DeerFlow MCP cache，但还没有固定本地显式 adapter config 的加载方式。
  - 下一步接 dev/staging MCP 前，需要一个可重复 smoke 入口验证 `config -> registry -> provider -> SocAgentActionResult.payload`。
- 变更：
  - `backend/soc_agent/actions/mcp.py` 新增：
    - `load_mcp_action_adapter_configs()`：加载 `.json/.yaml/.yml`，只接受顶层 list 或 `adapters: [...]`。
    - `build_mcp_action_adapter_registry_from_file()`：从显式 allowlist config 构造 registry。
  - `backend/soc_agent/cli.py` 新增 `soc mcp smoke`：
    - 显式传入 config、`--route`、可选 `--action` 和 `--json` action payload。
    - 默认使用 `DeerFlowCachedMcpToolProvider`。
    - `--dry-run` 只验证 adapter/tool 可用性，不调用 MCP tool。
  - `backend/tests/test_soc_mcp_adapters.py` 增加配置加载、registry-from-file 和 CLI smoke 覆盖。
  - 更新 MCP bridge plan、action adapter plan、主方案、工程契约和进度台账。
- 边界：
  - 不接生产 MCP server。
  - 不把 MCP config 自动接入 chat TUI / daemon 默认链路。
  - 不开放 write/destructive execute。
  - 不让 Lead Agent 直接选择 MCP tool；仍只能走 SOC route/action。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/actions/mcp.py soc_agent/cli.py tests/test_soc_mcp_adapters.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_mcp_adapters.py`
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/actions soc_agent/cli.py soc_agent/lead_agent.py soc_agent/lead_agent_chat.py tests/test_soc_action_adapters.py tests/test_soc_mcp_adapters.py tests/test_soc_lead_agent_chat.py tests/test_soc_agent_service.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/actions soc_agent/cli.py soc_agent/lead_agent.py soc_agent/lead_agent_chat.py tests/test_soc_action_adapters.py tests/test_soc_mcp_adapters.py tests/test_soc_lead_agent_chat.py tests/test_soc_agent_service.py tests/architecture/test_soc_agent_boundaries.py --check`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_mcp_adapters.py tests/test_soc_action_adapters.py tests/test_soc_lead_agent_chat.py tests/test_soc_agent_service.py tests/architecture/test_soc_agent_boundaries.py`
  - `codegraph sync .`
- 下一步：
  - 用真实 dev/staging MCP server 配置一个 read-only `asset.lookup` 或 `endpoint.process_tree.lookup` smoke，记录延迟、失败率、payload size 和敏感字段裁剪结果。

### 2026-07-05 — DeerFlow cached MCP provider implementation 切片

- 背景：
  - MCP adapter skeleton 和 explicit config builder 已完成，但 provider 仍是测试 fake provider。
  - 下一步需要复用 DeerFlow MCP cache/session 生命周期，同时不能让 LangChain `BaseTool` 或 MCP SDK 类型扩散到 core/API/TUI/Web。
- 变更：
  - `backend/soc_agent/actions/mcp.py` 新增 `DeerFlowCachedMcpToolProvider`：
    - 默认 lazy import `deerflow.mcp.cache.get_cached_mcp_tools()`。
    - 对外仍实现 `SocMcpToolProviderPort`。
    - `list_tools()` 把 cached `BaseTool` 转成 `SocMcpToolDescriptor`，包括 name、description、input schema。
    - `invoke()` 按 exact tool name 调用 `BaseTool.invoke()`，不做 fuzzy match。
    - provider 层执行 timeout，并把 loader failure、missing tool、tool failure、timeout 映射为 `SocMcpToolProviderError` / `SocMcpToolNotFoundError`。
    - 将 dict、content+artifact tuple、Pydantic/model dump、文本等 tool result 归一为 `Mapping`，避免 raw LangChain/MCP result 类型进入 action result。
  - `backend/tests/test_soc_mcp_adapters.py` 增加 fake cached tool 覆盖：
    - monkeypatched DeerFlow cache loader。
    - descriptor/input schema。
    - exact invoke payload。
    - content+artifact result normalization。
    - missing tool。
    - cache loader failure。
    - config builder + `DeerFlowCachedMcpToolProvider` 组合执行 `asset.lookup`。
  - 更新 MCP bridge plan、action adapter plan、主方案、工程契约和进度台账。
- 边界：
  - 不接真实生产 MCP server。
  - 不让 Lead Agent 直接使用 cached MCP tools。
  - 不开放 write/destructive execute。
  - 不修改 DeerFlow MCP core。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/actions soc_agent/cli.py soc_agent/lead_agent_chat.py tests/test_soc_action_adapters.py tests/test_soc_mcp_adapters.py tests/test_soc_lead_agent_chat.py tests/test_soc_agent_service.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/actions soc_agent/cli.py soc_agent/lead_agent_chat.py tests/test_soc_action_adapters.py tests/test_soc_mcp_adapters.py tests/test_soc_lead_agent_chat.py tests/test_soc_agent_service.py tests/architecture/test_soc_agent_boundaries.py --check`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_mcp_adapters.py tests/test_soc_action_adapters.py tests/test_soc_lead_agent_chat.py tests/test_soc_agent_service.py tests/architecture/test_soc_agent_boundaries.py`
- 下一步：
  - 做 read-only live smoke / config wiring：用 dev/staging MCP server 或本地 fake MCP server 验证 `config -> registry -> DeerFlowCachedMcpToolProvider -> SocAgentActionResult.payload`，并固定显式 adapter config 加载方式。

### 2026-07-05 — SOC action package structure hygiene follow-up

- 背景：
  - 项目仍处早期，可以接受删除旧入口，不需要为了尚未稳定的 SOC internal import 保留兼容层。
- 变更：
  - 删除根目录旧入口：
    - `backend/soc_agent/action_adapters.py`
    - `backend/soc_agent/action_proposals.py`
    - `backend/soc_agent/mcp_adapters.py`
  - 架构测试从“允许兼容 wrapper”改为“根目录不允许 action-like modules”。
  - 更新主方案、MCP bridge plan 和工程契约，明确新代码必须使用 `soc_agent.actions.*`。
- 边界：
  - 不改变 runtime 行为。
  - 不接真实 MCP provider。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/actions soc_agent/cli.py soc_agent/lead_agent_chat.py tests/test_soc_action_adapters.py tests/test_soc_mcp_adapters.py tests/test_soc_lead_agent_chat.py tests/test_soc_agent_service.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/actions soc_agent/cli.py soc_agent/lead_agent_chat.py tests/test_soc_action_adapters.py tests/test_soc_mcp_adapters.py tests/test_soc_lead_agent_chat.py tests/test_soc_agent_service.py tests/architecture/test_soc_agent_boundaries.py --check`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_mcp_adapters.py tests/test_soc_action_adapters.py tests/test_soc_lead_agent_chat.py tests/test_soc_agent_service.py tests/architecture/test_soc_agent_boundaries.py`
- 下一步：
  - 做 DeerFlow cached MCP provider implementation；真实 provider 放在 `backend/soc_agent/actions/` 下。

### 2026-07-05 — SOC action package structure hygiene 切片

- 背景：
  - `backend/soc_agent` 顶层已经出现 action/tool 横向堆文件苗头，下一步真实 MCP provider 如果继续写在根目录会变得难维护。
  - 需要在继续接真实 provider 前先固定 action/tool 代码归属，避免重复 openclaw/hermes 那类后期难拆的结构问题。
- 变更：
  - 新增 `backend/soc_agent/actions/` package：
    - `actions/adapters.py`：原 `action_adapters.py`，承载 adapter registry、dry-run-only adapter 和本地 `asset.lookup` adapter。
    - `actions/mcp.py`：原 `mcp_adapters.py`，承载 MCP provider port、read-only MCP adapter 和 explicit config builder。
    - `actions/proposals.py`：原 `action_proposals.py`，承载 Lead Agent action proposal boundary。
    - `actions/__init__.py`：轻量 package marker，不重导出子模块，避免隐性加载 core/proposal 依赖。
  - 根目录旧入口后续 follow-up 已删除；新代码必须 import `soc_agent.actions.*`。
  - SOC 内部代码和测试 import 切到新路径。
  - `backend/tests/architecture/test_soc_agent_boundaries.py` 增加架构测试，防止未来新增 root-level action-like modules。
  - 更新主方案、MCP bridge plan、action adapter plan 和工程契约。
- 边界：
  - 不改变 runtime 行为。
  - 不接真实 MCP provider。
  - 不移动 Lead Agent / skill / profile 相关文件；后续单独评估是否收口到 `soc_agent/agent/`。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/action_adapters.py soc_agent/action_proposals.py soc_agent/mcp_adapters.py soc_agent/actions soc_agent/cli.py soc_agent/lead_agent_chat.py tests/test_soc_action_adapters.py tests/test_soc_mcp_adapters.py tests/test_soc_lead_agent_chat.py tests/test_soc_agent_service.py tests/architecture/test_soc_agent_boundaries.py`
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/action_adapters.py soc_agent/action_proposals.py soc_agent/mcp_adapters.py soc_agent/actions soc_agent/cli.py soc_agent/lead_agent_chat.py tests/test_soc_action_adapters.py tests/test_soc_mcp_adapters.py tests/test_soc_lead_agent_chat.py tests/test_soc_agent_service.py tests/architecture/test_soc_agent_boundaries.py --check`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_mcp_adapters.py tests/test_soc_action_adapters.py tests/test_soc_lead_agent_chat.py tests/test_soc_agent_service.py tests/architecture/test_soc_agent_boundaries.py`
- 下一步：
  - 做 DeerFlow cached MCP provider implementation；真实 provider 放在 `backend/soc_agent/actions/` 下，不回到根目录。

### 2026-07-05 — MCP-backed read-only `asset.lookup` adapter config builder 切片

- 背景：
  - MCP adapter skeleton 已经固定 provider port 和 read-only execution contract。
  - 下一步接真实 MCP provider 前，需要先把 `route/action -> mcp.server/tool` 的显式配置边界固定下来，避免 Lead Agent、dispatcher 或自然语言推断 tool name/payload。
- 变更：
  - `backend/soc_agent/mcp_adapters.py` 新增：
    - `SocMcpToolBindingConfig`：承载 `mcp.server/tool/timeout/input_mapping/output_fields/result_schema_version`。
    - `SocMcpActionAdapterConfig`：承载 SOC action descriptor、owner/environment、payload schema、metadata 和 MCP binding。
    - `build_mcp_action_adapter()`：从单个 enabled config 构造 `SocMcpToolActionAdapter`。
    - `build_mcp_action_adapter_registry()`：从配置列表构造 `SocActionAdapterRegistry`，跳过 disabled config，重复 route/action fail-fast。
  - `SocMcpActionAdapterConfig` 当前只允许 `risk_level=read_only`、`adapter_kind=mcp`、`external_side_effect=read`、`execute_supported=True`、`idempotency_required=False`。
  - `backend/tests/test_soc_mcp_adapters.py` 增加 explicit config -> registry -> `asset.lookup` execute、disabled skip、direct disabled build reject、duplicate route/action reject、non-read-only config reject 覆盖。
  - 更新 MCP bridge plan、action adapter plan、工程契约和主方案。
- 边界：
  - 不接真实 DeerFlow cached MCP provider。
  - 不接真实 MCP server。
  - 不修改 DeerFlow MCP core。
  - 不开放 write/destructive MCP execute。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/mcp_adapters.py tests/test_soc_mcp_adapters.py`
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/mcp_adapters.py tests/test_soc_mcp_adapters.py --check`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_mcp_adapters.py`
- 下一步：
  - 做 DeerFlow cached MCP provider implementation：复用 `get_cached_mcp_tools()`，但对外仍只暴露 `SocMcpToolProviderPort`，按 tool name 精确查找并把失败映射为 adapter failure。

### 2026-07-05 — MCP tool provider port + fake provider adapter tests 切片

- 背景：
  - MCP bridge 规划已完成，但还没有 SOC 自己的 provider port 和 adapter skeleton。
  - 直接接 DeerFlow cached MCP provider 会引入外部状态；先用 fake provider 固定 contract 更稳。
- 变更：
  - 新增 `backend/soc_agent/mcp_adapters.py`：
    - `SocMcpToolDescriptor`
    - `SocMcpToolProviderPort`
    - `SocMcpToolProviderError` / `SocMcpToolNotFoundError`
    - `mcp_read_only_adapter_descriptor()`
    - `SocMcpToolActionAdapter`
  - `SocMcpToolActionAdapter` 当前只支持 read-only MCP invocation：
    - descriptor 必须是 `adapter_kind=mcp`、`risk_level=read_only`、`external_side_effect=read`、`execute_supported=True`。
    - dry-run 校验 route/action、required payload、required context refs、tool availability，但不调用 provider `invoke()`。
    - execute 通过 `input_mapping` 构造 MCP tool payload，通过 `output_fields` 裁剪返回结果。
    - provider exception 被映射为 `SocAgentActionResult(status="failed")`，不向上泄漏外部异常。
  - 新增 `backend/tests/test_soc_mcp_adapters.py`：
    - fake provider list/invoke。
    - dry-run 不调用 provider。
    - execute payload mapping、timeout、output filtering。
    - missing context refs、missing MCP tool、provider error、非 read-only descriptor 拒绝。
  - 更新 MCP bridge plan、action adapter plan、主方案、工程契约。
- 边界：
  - 不接真实 DeerFlow MCP cache。
  - 不接真实 MCP server。
  - 不修改 DeerFlow MCP core。
  - 不开放 high-risk/write/destructive MCP execute。
- 已验证：
  - `cd backend && ./.venv/bin/python -m ruff check soc_agent/mcp_adapters.py tests/test_soc_mcp_adapters.py tests/test_soc_action_adapters.py tests/test_soc_lead_agent_chat.py`
  - `cd backend && ./.venv/bin/python -m ruff format soc_agent/mcp_adapters.py tests/test_soc_mcp_adapters.py tests/test_soc_action_adapters.py tests/test_soc_lead_agent_chat.py --check`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_mcp_adapters.py tests/test_soc_action_adapters.py tests/test_soc_lead_agent_chat.py`
  - `cd backend && ./.venv/bin/python -m pytest tests/test_soc_mcp_adapters.py tests/test_soc_action_adapters.py tests/test_soc_agent_service.py tests/test_soc_lead_agent_chat.py tests/test_soc_tui_chat_runtime.py`
  - `codegraph sync .`
- 下一步：
  - 做 MCP-backed read-only `asset.lookup` adapter config builder：用 fake provider 固定显式配置到 adapter registry 的构造方式，继续不接真实 MCP server。

### 2026-07-05 — MCP adapter bridge / real read-only data source planning 切片

- 背景：
  - Lead Agent 已能通过显式 read-only proposal 请求 `asset.lookup`。
  - 但真实资产系统、EDR 只读查询或 MCP readonly tool 还没有接入边界；如果直接让 Lead Agent 用 DeerFlow `tool_search` 发现和调用 MCP tool，会绕开 SOC route/action、审计和 approval policy。
- CodeGraph 结论：
  - `deerflow.tools.tools.get_available_tools()` 会加载 config tools、built-in tools、cached MCP tools 和 ACP tools，并按 tool name 去重。
  - MCP tools 来自 `deerflow.mcp.cache.get_cached_mcp_tools()`，该 cache 会根据 `extensions_config.json` / `mcp_config.json` 的 mtime 自动 reset/lazy initialize。
  - `ExtensionsConfig.from_file()` / `get_enabled_mcp_servers()` 是 DeerFlow MCP server 配置入口。
  - `tool_search` 是 DeerFlow 的 deferred MCP tool discovery 机制，不是 SOC action execution boundary。
- 变更：
  - 新增并归档 `.notes/archive/ai_soc/implementation-plans/mcp-adapter-bridge-plan.md`：
    - 固定 SOC MCP bridge 是 `SocActionAdapter` 具体实现，不是新的 agent tool runtime。
    - 固定 `route/action -> MCP server/tool` 映射只能存在于 adapter/config 层。
    - 规划 `SocMcpToolProviderPort`、payload mapping、result mapping、超时、脱敏、审计字段。
    - 明确 read-only 先接，write/destructive 继续走 approval grant + execute preflight + idempotency。
    - 拆分后续接入顺序：fake provider tests -> read-only config -> DeerFlow cached MCP provider -> live smoke -> high-risk preflight。
  - `.notes/README.md` 增加 MCP bridge 文档入口。
  - 更新 action adapter plan、工程契约和主方案。
- 边界：
  - 不新增代码 adapter。
  - 不接真实 MCP server。
  - 不修改 DeerFlow MCP core。
  - 不让 Lead Agent 直接 tool_search 后执行 SOC action。
- 已验证：
  - `CodeGraph: get_available_tools / get_cached_mcp_tools / ExtensionsConfig / tool_search` 源码查询。
  - `git diff --check`
- 下一步：
  - 做 MCP tool provider port + fake provider adapter tests：先用 fake provider 固定 `SocMcpToolActionAdapter` 的 read-only contract、timeout/error mapping 和 result payload。

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
  - 新增并归档 `.notes/archive/ai_soc/implementation-plans/action-adapter-registry-plan.md`，记录后续 dry-run integration、execute preflight、只读查询 adapter 和 MCP bridge 顺序。
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
  - `.notes/archive/ai_soc/deferred/kafka-worker-pool-concurrency-plan.md`
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
  - Prometheus exporter 暂缓，方案记录在归档的 `kafka-consumer-adapter-plan.md`；下一步进入 production overlay planning。

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
  - `.notes/archive/ai_soc/runbooks/soc-daemon-production-runbook.md`
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
  - `.notes/archive/ai_soc/implementation-plans/kafka-consumer-adapter-plan.md`
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
  - 新增 `.notes/ai_soc/governance/agent-profile-governance.md`。
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

- 根据同事反馈的上游日志方向不可靠、加工字段冲突问题，新增并归档 `.notes/archive/ai_soc/reference/zeus-alert-flow-and-field-trust.md`：
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
  - `.notes/archive/ai_soc/reference/normalization-drift-strategy.md`
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
  - `.notes/archive/ai_soc/reference/normalization-drift-strategy.md`
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
  - `.notes/archive/ai_soc/reference/normalization-drift-strategy.md`
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
  - `.notes/archive/ai_soc/reference/normalization-drift-strategy.md`
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
