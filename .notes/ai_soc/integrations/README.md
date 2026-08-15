# SOC Agent Integration Work Index / 外部接入工作索引

本文件只回答“`integrations/` 里哪些已经完成、哪些仍未完成、未完成项归属哪个权威任务”。它不是
第二份路线图，不改变执行顺序；当前任务和先后顺序始终以
[`../delivery-roadmap.md`](../delivery-roadmap.md) 与 [`../progress.md`](../progress.md) 为准。

## 1. Complete Crosswalk / 完整交叉台账

| Capability / 能力 | Current nature / 当前性质 | Authoritative task / 归属 | Planned implementation / 计划落点 |
|---|---|---|---|
| Legacy model/ZEUS/status/path audit | **Done / reference** | 已完成审计 | [`pingan-legacy-source-audit.md`](pingan-legacy-source-audit.md)；只保留协议和租户边界，不迁移旧控制流 |
| Internal LiteLLM connectivity | **Code-complete / internal smoke pending** | D12-B environment gate | loopback-only fixed-prompt `chat.completions` smoke 已实现；内网启动 `sec_know_model` 后保存不含正文/凭证的 `soc.pingan_litellm_smoke.v1` pass report |
| D12-A asset provider | **Done / fake-only** | D12-A | PingAn provider、portable signer、fallback、MCP/action 契约已完成；不能代替真实验收 |
| Real `asset.locate` | **Parked / internal evidence pending** | `D12-B` | 自包含 ZEUS signer + Agent Platform HTTP client、旧源码 `YHSYS` PRD private-profile preparer、固定 legacy operator、Apple Silicon 离线 backend toolchain、preflight/direct seven-case runner 与 MCP/Dispatcher/evidence/shared-context acceptance runner 已实现；恢复后只核对剩余环境配置/cases，再做 confirmed matrix -> real evidence report -> deployed Web/TUI smoke |
| `asset.lookup` simple-record route | **Local scaffold / disabled for PingAn PI-01E** | `PI-01D` route consolidation | 它与 ownership-oriented `asset.locate` 语义不同；PingAn real-only 示例选择 `asset.locate`，paired evaluator 将选中 `asset.lookup` 视为 blocking failure；其他 tenant 若保留它仍须独立真实 adapter/result schema |
| Real threat intelligence | **Code-complete / internal smoke pending** | `PI-01A` | PingAn `/public/indicatorSearch` typed Provider + stdio MCP + action/evidence 已实现；generic 层只认识 `threat_intel.ip_reputation.lookup`；仍需真实 DEV `mocked=false` 验收 |
| Real security-tag lookup | **Code-complete / internal smoke pending** | `PI-01B1` | PingAn `/public/searchTagContent` typed Provider + stdio MCP + action/evidence 已实现；保留 validity/scope/source observation，缺失 expiry 默认 fail closed；仍需真实 DEV 字段与 `mocked=false` 验收 |
| Authoritative authorized-activity facts | **Data-gated / fixture replacement** | `PI-01B2` | change/scanner/maintenance/exercise-roster source adapter -> existing Governed Context lifecycle；不能由 tag lookup 自动冒充完成 |
| External disposition core | **Done / source missing** | 已完成 canonical service | Contract、Gateway ingress、UoW、SQL、Review/Correction/Memory candidate、Web/TUI 已完成 |
| Real Zeus status/reason feed | **Data-gated / source contract absent** | `PI-01C` | 已知旧轮询 endpoint/status enum 不足以定义稳定事件、reason、版本和乱序语义；拿到真实 feed contract 后才实现 source adapter -> `SocExternalDispositionIngressCommand` |
| Automatic read-only investigation | **D1-D4 implemented; not a mock** | `PI-01D` | Planner/Plan、strict default-off composition、durable execution/attempt/evidence、逐次 result-mode 校验、retry/recovery/replay、recomputable shadow report/addendum 与 Kafka/internal-batch opt-in 已实现；报告无 Provider 调用或第二套状态 |
| External simulation -> internal shadow | **Simulation Done / real integration debt open** | `PI-01E` | Runtime/investigation batch 分离；`soc.pingan_shadow_acceptance.v2` 按 evidence class 校验 tenant、composition/action/extensions、mock/real、P95/review/schema/gaps 与零越权。外网 5/50 条已通过并允许产品轨继续；内网 `run_pingan_internal_shadow.py`、真实 5 条与 hit mapping 保留为独立债务，不阻塞 PI-03..05 |
| Deployed Review/Operations Web + Gateway/auth smoke | **Frontend implementation done / deployed evidence data-gated** | `PI-01E` / `PI-04-B` | Review/Normalization/Operations React 与 Playwright transport fixture 已完成；仍须在真实 Gateway、身份、网络和 SOC store 上验证 readback，fixture 不冒充部署证据 |
| Historical EDR path catalog | **Done / investigation-only** | 已完成当前目标 | exact path + optional MD5、lineage、freshness、MCP/action/evidence 已完成；始终不是 allowlist |
| PingAn software-path fast disposition policy | **Done / default-off tenant policy** | `PI-03D` 旧通用 promotion 草案已关闭 | catalog/MCP 本身仍是 investigation-only；只有显式开启、精确或受控路径族匹配并通过 server-owned tenant policy 时才形成 `ignored`，且保留独立 decision lineage |
| Feedback-derived Skill backlog | **Simulation Done / real source classification debt open** | `PI-03C` | typed feedback -> distinct-source aggregation -> versioned SQL candidate -> RBAC/audit/replay 已完成；真实 external reason/analyst correction 必须经 server-owned classifier 生成 Skill/scenario/failure facet，不能按自由文本自动聚类；不自动改或发布 Skill |
| Labels and model calibration | **PI-03A/B simulation done / real labels pending** | `PI-03A/B` | immutable corpus manifest、review-source separation、manifest-bound calibration 和 composed quality replay 已实现；8 alert + 4 synthetic labels 的四组件 gate 与 stable replay 已通过。simulation 固定禁止真实质量、profile 发布、rollout 和 automation；真实 reviewer/rationale/ground truth 仍为独立输入 gate |
| Correlation pair expansion | **Deferred / data-gated** | `PI-03B` | `same_incident` / `related_distinct` / `unrelated` 人工标签及 scorer replay diff |
| Adaptive parser evolution | **Deferred / data-gated** | `PI-03E` 或独立治理切片 | 先 drift cohort + candidate bundle，再 dual-run/replay/approval/canary/rollback；禁止线上单告警自改 parser |
| Real Kafka/PostgreSQL/K8s | **Parked / inputs absent** | `PI-02` | 真实 ACL/TLS/lag/DLQ、PostgreSQL migration/recovery、K8s deployment/rollback；当前 DEV SQLite 不冒充验收 |
| Kafka worker concurrency | **Deferred / measurement-gated** | `PI-02` 子任务 | 真实串行瓶颈成立后，先 bounded queue，再 partition-aware commit 和 backpressure |
| Operations Web/telemetry/SLO | **PI-04A/B done / real telemetry debt open** | `PI-04-B/C` | Snapshot CLI/API + `/workspace/soc/operations` 已完成；Web 不重算健康并显式展示 local/test 与 `not_measured`。真实 lag、LLM/Provider telemetry、Prometheus/SLO 仍待接入 |
| Governed rollout and completion | **PI-05A/B simulation done / real rollout debt open** | `PI-05A/B/C` | `soc rollout rehearse` 已冻结 owner/gate/rollback；`soc rollout completion` 已将 PI-01E、PI-03B/C、PI-04、PI-05A 六个 artifact 汇总为五组件 fail-closed report。两者只证明仿真可复跑，固定真实 transition/effect=0 与 Pilot/Production=false；真实 feature flag/cohort enforcement、owner approval、telemetry 和 deployed rollback 归 PI-05C |
| Wiki/OKF memory projection | **Deferred / optional** | `PI-03` 之后 | DB source of truth -> versioned read-only export；Wiki 编辑只能回流 proposal |
| Real high-risk actions | **Deferred / governance-gated** | `PI-05` | 真实 EDR/F5/SOAR adapter、审批/grant、幂等、结果核验、补偿和回滚；默认人工审批 |

## 2. Explicit Non-backlog / 明确不作为待办

- `endpoint.process_tree.lookup` 与 `host.event_context.lookup` 已删除，不因旧 Mock 或文档提及而恢复。
- bounded LLM entity enrichment 和 correlation LLM rerank 只是可选扩展，不是当前已承诺任务；没有人工标签
  证明确定性基线不足前，不进入路线图。
- 完整多 Agent 自治、攻击链/时间线展示和自动高风险处置不属于当前 PI-01 完成条件。
- 旧 ZEUS `status != 1 -> skip AI`、旧路径模糊 allowlist 和旧硬编码风险评分明确不迁移。

## 3. Document Roles / 文档分工

| Document | Role / 作用 |
|---|---|
| [`mock-and-real-register.md`](mock-and-real-register.md) | 判断某项当前是 real、mock、fixture、local smoke、gap 还是 data-gated |
| [`pingan-dev-information-collection.md`](pingan-dev-information-collection.md) | 进入内网前后需要准备的非敏感契约、真实测试值和环境条件 |
| [`pingan-internal-continuation-handoff.md`](pingan-internal-continuation-handoff.md) | 内网执行命令、case matrix、验收 checklist 和结果带回边界 |
| [`external-disposition-sync.md`](external-disposition-sync.md) | 外部状态/理由 canonical contract、学习边界和 Skill 候选计划 |
| [`pingan-legacy-source-audit.md`](pingan-legacy-source-audit.md) | 已完成的旧实现审计结论，不作为新的执行队列 |

## 4. Update Rule / 更新规则

- 新增或替换真实 Provider 时，同步本索引和 `mock-and-real-register.md`。
- 任一内网依赖先用同一 production code + fake transport 完成外网 simulation package；无稳定 contract
  的能力保持 data-gated，不为仿真虚构 Provider。
- 任一项从 queued/deferred 进入执行前，必须先在 `delivery-roadmap.md` 获得 task ID，并更新
  `progress.md` 当前指针。
- 内网验收完成后，把 evidence 摘要写回权威文档；交接单完成使命后归档，不长期复制状态。
