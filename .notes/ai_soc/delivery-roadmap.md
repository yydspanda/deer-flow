# SOC Agent Delivery Roadmap / 阶段性交付路线

> Status: **Authoritative / 权威执行顺序**  
> Current stage: **Stage 4 - Real Data & Production Integration**
> This document owns stage order and stage gates. `soc-agent-solution.md` owns the product and
> architecture design; `progress.md` records current execution status.

## 1. Delivery Decision / 交付决策

后续严格按以下四阶段推进，不跳步、不并行发散：

```mermaid
flowchart LR
    BD["🎬 Stage 1<br/>Boss Demo v0.1<br/>老板可看"]
    AA["🔍 Stage 2<br/>SOC Alpha Completeness Audit<br/>完整性审计"]
    BG["🧱 Stage 3<br/>Close Blocking Gaps<br/>补齐阻塞缺口"]
    PI["🏭 Stage 4<br/>Real Data & Production Integration<br/>真实数据与生产集成"]

    BD -->|"BD Gate 通过"| AA
    AA -->|"唯一缺口清单确认"| BG
    BG -->|"Alpha Gate 通过"| PI
```

这四个结果不能混为一谈：

| Result / 结果 | Definition / 定义 | Does not mean / 不代表 |
|---|---|---|
| Boss Demo v0.1 | 老板可在浏览器中看到一条代表性告警完成受控研判闭环 | SOC Alpha 已完整、生产已就绪 |
| SOC Alpha audit | 全链路能力被逐项审阅并分类，阻塞项有唯一清单 | 审计时顺手修完所有问题 |
| SOC Alpha complete | 本地/测试环境中的产品闭环可重复，代码可控的 P0/P1 阻塞项已清零 | 已接真实平安系统或允许自动处置 |
| Production integration | 真实数据源、凭证、基础设施、运营指标和治理门槛得到验证 | 自动等同正式 GA 或开放高风险动作 |

## 2. Stage Overview / 阶段总览

| Stage | Status | Primary outcome / 核心产出 | Exit gate / 退出门槛 |
|---|---|---|---|
| `BD` Boss Demo v0.1 | **Done** | 一条 8-10 分钟、浏览器优先、可重复的 golden path | `BD-01..03` 与 BD Gate 已通过 |
| `AA` SOC Alpha Completeness Audit | **Done / AA Gate Passed** | 唯一的 Complete/Gap/Mock/Data-gated/Deferred 矩阵 | 审计矩阵和 P0/P1 阻塞清单已于 2026-07-18 确认 |
| `BG` Close Blocking Gaps | **Done / Alpha Gate Passed 2026-07-20** | P0/P1、Alpha readiness technical gate、独立评审与具名批准已完成 | `BG-03` 已批准进入 Stage 4 integration preparation |
| `PI` Real Data & Production Integration | **Current / External product-complete; real debt open** | 外网可实现的产品流和 `PI-01G1..G3` DeerFlow 原生 SOC 专家委派已完成；真实 PingAn/infra/quality/telemetry/rollout 仍按独立债务验收 | 外网完整性不再新增仿真切片；fresh real evidence、具名 owner、cohort enforcement 与可执行 rollback 到位后再通过 Pilot readiness review；仿真不关闭真实 gate |

Boss Demo v0.1 和 Alpha 完整性审计已于 2026-07-18 分别通过 BD Gate、AA Gate；冻结的
`BG-P0-01..BG-P1-05` 与 `BG-03` 已于 2026-07-20 关闭。`PI-04-A SOC Operations Snapshot` 已完成。产品负责人于 2026-08-05 决定：当前无法在外网访问的 PingAn DEV 能力统一使用显式 mock 走完产品流程，并把真实 `mocked=false` 验收单独登记为 `Real Integration Debt`，不再阻塞后续切片。`D12-B`、`PI-01A/B1` 的 production-shaped Provider/MCP 和内网验收工具保留；`PI-01B2/C` 无稳定来源契约，仍为 data-gated，仿真只覆盖现有 vendor-neutral service boundary，不虚构供应商接口。`PI-01D1-D4`、`PI-01E` 5/50 条 external simulation 和 `PI-05B` 仿真汇总已通过，但 completion report 不是项目完工声明：随后审计出的 Web/Gateway Lead Agent 审批入口由 `PI-01F` 补齐，memory candidate 来源完整性继续由 `PI-03F` 收口。所有 mock 必须带 `mocked=true`，所有真实 gate 均保持开放。当前 DEV 使用独立本地 SQLite；真实 Kafka/K8s/PostgreSQL 参数继续停放。

2026-08-07 产品负责人进一步决定：暂不等待内网，优先完成仍可在外网实现的完整 SOC 产品流。
`AC-30` 因此从 Parking Lot 重开为 `PI-01G`，并已通过 `G1..G3` 关闭。该工作复用 DeerFlow
原生 custom subagent registry、`task` executor、model inheritance 和 task events；不新增第二套
LangGraph Runtime。四个 specialist 本身无 tool、无动态 Skill 读取，只分析由 server 投影的
bounded ReviewQueue 证据和已评审 `runtime-guidance.md`。专家回答是 advisory reasoning，
不是证据、verdict、memory、approval 或 action authority。任何专家看到的 fake Provider 结果
仍必须保留 `mocked=true`，并继续由统一 Mock/Real 台账追踪内网替换验收。

## 3. Stage 1 - Boss Demo v0.1

### 3.1 Audience and story / 受众与故事

主要受众是老板和产品/安全负责人。主入口是 Web，终端命令只负责准备环境：

1. 导入一条有代表性的 APT/反弹 Shell 告警。
2. Runtime 展示规范化、事实重建、受控 LLM 研判和决策理由。
3. 调查上下文展示实体、攻击方向、场景、证据、证据缺口和相似历史告警。
4. ReviewQueue 展示待复核工单；Lead Agent 可围绕该工单对话。
5. 分析师提交 note/correction，系统产生可审阅 memory candidate。
6. 所有 mock、shadow-only、未接真实 provider 的能力在页面或演示清单中明确标记。

### 3.2 Work packages / 工作包

| ID | Work / 工作 | Deliverable / 产出 | Acceptance / 验收 |
|---|---|---|---|
| `BD-01` | Scope freeze + one-command golden path / 范围冻结与一键准备 | **Done**: 独立可重置 Boss Demo SQLite、`soc demo boss`、结构化 launch manifest 和聚焦测试 | 不手改数据库；输出 Web URL、`run_id`、`queue_id`、analyzer mode、真实/mock 边界和下一步命令；失败不得静默降级 |
| `BD-02` | Web + Lead Agent coherent journey / 连贯可见链路 | **Done**: Docker Gateway/API/Web 共用独立 Demo DB；authenticated API、ReviewQueue 页面和 bounded Lead Agent context bridge 已验证 | 一个浏览器旅程可完成查看与复核；前端不复制业务逻辑；mock/shadow 状态显式可见 |
| `BD-03` | Rehearsal + acceptance / 演练与验收 | **Done**: live DeepSeek 页面、Web correction -> pending memory candidate、deterministic clean reset、authenticated API/CLI context 和最终截图均已保存 | 同一输入可再次演示；live/deterministic 模式可辨认；关键 API/UI/持久化断言通过；演示验收结果已保存 |

### 3.3 Scope freeze / 当前非目标

Stage 1 不做以下工作，除非它被实测证明直接阻塞 golden path：

- 真实 CMDB/EDR/Zeus/MCP 凭证接入。
- 扩充 correlation 人工标签集或设计 scorer v2。
- Prometheus 全局运营态势面板和生产压测。
- 新增更多攻击场景或完整自治 Sub Agent 群。
- Wiki/OKF/GraphRAG/PageIndex 投影。
- 封禁、隔离等高风险真实执行。
- 为演示重写 DeerFlow Runtime、Web 或 Lead Agent 基础设施。

### 3.4 BD Gate / 演示门禁

- [x] 一条命令或一组明确的启动命令可准备独立 demo 数据库。
- [x] 无需人工编辑 JSON/数据库即可在 manifest 得到本次 `run_id`/`queue_id` 入口。
- [x] 老板主要在 Web 完成查看，CLI 不是演示主体。
- [x] Runtime conclusion、evidence、scenario、correlation、review context 可见且边界明确。
- [x] live LLM、deterministic、mock provider、shadow-only 的边界均显式显示。
- [x] correction/note 至少一条人工反馈闭环可演示。
- [x] reset 后可重复运行；自动 smoke 和演示脚本通过。

## 4. Stage 2 - SOC Alpha Completeness Audit

Stage 2 是 time-boxed 审计，不在审计过程中纵向优化单个模块。

| ID | Work / 工作 | Deliverable / 产出 | Acceptance / 验收 |
|---|---|---|---|
| `AUD-01` | Journey inventory / 完整旅程盘点 | **Done**: [`audits/alpha-journey-inventory.md`](audits/alpha-journey-inventory.md) 已逐一定位 CLI/Kafka/Runtime/persistence/correlation/action/domain/Review/Web/TUI/Lead Agent/feedback/memory/audit/replay | 入口、21 个服务边界、15 个状态聚合、17 张业务表及用户可见产物已有唯一落点 |
| `AUD-02` | Code/contract/docs consistency / 一致性审计 | **Done**: [`audits/alpha-consistency-audit.md`](audits/alpha-consistency-audit.md) 已记录 10 项确认一致边界、24 项事实差异和 mock/real/shadow/reachability 核对 | 未把 target/service-only 冒充 application-complete；未把 mock 或 shadow-only 冒充 production real；审计过程未修改业务代码或被审文档 |
| `AUD-03` | Completeness matrix + blocker register / 完整性矩阵与阻塞台账 | **Done**: [`audits/alpha-completeness-matrix.md`](audits/alpha-completeness-matrix.md) 已将 50 项能力唯一分类，记录 13 个 Gap、P0/P1 优先级和 7 个冻结工作包 | 每个 Gap 有 owner、影响、证据、验收方式和目标阶段；只有代码可控 P0/P1 进入 Stage 3 |

### AA Gate / 审计门禁

- [x] 全链路只有一份完整性矩阵，不新增平行路线图。
- [x] P0/P1 阻塞项与非阻塞质量优化明确分开。
- [x] mock、凭证缺失、真实数据缺失和代码缺口明确分开。
- [x] Stage 3 的任务集合由审计结果冻结，不靠聊天临时决定。

**AA Gate: Passed on 2026-07-18.**

## 5. Stage 3 - Close Blocking Gaps

| ID | Work / 工作 | Deliverable / 产出 | Acceptance / 验收 |
|---|---|---|---|
| `BG-01` | Close P0 blockers / 修复 P0 | **Done 2026-07-18**: `BG-P0-01..02` 已关闭审批/RBAC、事务化变更和持久审计缺口 | `AC-16/21/22/34` 均有回归与故障注入证据 |
| `BG-02` | Close P1 + E2E acceptance / 修复 P1 与端到端验收 | **Done 2026-07-20**: `BG-P1-01..05` 已完成；`soc.alpha_acceptance_report.v1` 覆盖 APT/EDR/HIDS 的 CLI/Kafka/SQL/Gateway/Web/feedback/audit/replay | 结果可重复；失败语义、offset、review、audit 和 memory boundary 符合契约 |
| `BG-03` | Alpha readiness package / Alpha 就绪包 | **Done 2026-07-20**: `soc.alpha_readiness_report.v1` 绑定版本化验收、全量回归、矩阵/路线 hash、部署/回滚和 Stage 4 输入；`alpha-gate-review.md` 记录 `yydspanda` 的具名范围批准与临时 PI owner | Alpha Gate 仅批准 Stage 4 integration preparation；共享部署、试点、生产和高风险动作仍未批准 |

Stage 3 的唯一拆分与验收条件见
[`audits/alpha-completeness-matrix.md`](audits/alpha-completeness-matrix.md) Section 6，执行顺序固定为：

```text
BG-P0-01 -> BG-P0-02 -> BG-P1-01 -> BG-P1-02 -> BG-P1-03 -> BG-P1-04 -> BG-P1-05 -> BG-03
```

Stage 3 不负责解决真实凭证、生产标签数量或企业基础设施未准备等外部条件。

## 6. Stage 4 - Real Data & Production Integration

| ID | Work / 工作 | Deliverable / 产出 | Acceptance / 验收 |
|---|---|---|---|
| `PI-01` | Providers and governed investigation / 能力源与受控调查 | **External product flow complete / Real Debt Open**: D1-D4、external 5/50、PI-01F/F2 和 PI-01G1..G3 passed；D12-B、TI、security-tag 的真实 gate 保留；B2/C data-gated | 仿真证明 workflow reachability；Web/Gateway/TUI proposal 与专家委派均进入统一治理边界；真实 `mocked=false` 证据另行关闭 Provider gate；任何入口不得越权修改基础 Runtime |
| `PI-02` | Infrastructure / 基础设施 | **Local Simulation Done / Real Debt Parked**: SQLite、local Redpanda/Kafka、worker/DLQ/幂等已有 Alpha 证据；真实 Kafka/PostgreSQL/K8s 参数暂缺 | 当前只要求本地流程可重复；生产吞吐、ACL/TLS、恢复、连接池和 K8s gate 保持开放 |
| `PI-03` | Labels, learning and calibration / 标签、学习与校准 | **PI-03A/B/C Simulation Done / Real Feedback Debt Open**: corpus、统一质量 replay 和反馈型 Skill backlog 均已走通；真实人工标签和 source classifier 仍开放 | 仿真可以验证治理代码，但不能生成真实准确率声明；任何 profile/Skill/parser promotion 仍需人工批准与真实标签 |
| `PI-04` | Operations and security / 运维与安全 | **PI-04A/B Done / Real Telemetry Debt Open**: Snapshot CLI/API 与薄 Web 已完成；本地/仿真数据性质、无 overall health 和 `not_measured` 缺口显式可见 | Web 只消费冻结 snapshot；Playwright fixture 不冒充 deployed Gateway 或真实 lag/算力/Prometheus/SLO |
| `PI-05` | Governed rollout / 受治理上线 | **PI-05A/B Simulation Done / PI-05C Real Debt Open**: rollout rehearsal 与五组件 Simulation Completion Gate 均已完成；真实控制器不在外网伪造 | completion report 必须保持 7 个 real gate 为 open、真实 transition/effect 为 0、`pilot_ready=false`、`production_ready=false`；只有 PI-05C 的真实环境证据可推进 stage |

### 6.1 PI-01 Execution Order / 真实能力与调查主线

真实 Provider “可以被 MCP/Action 调到”不等于告警消费链已经使用它。`PI-01D1/D2/D3` 后，
`SocEnrichmentPlanner`、`soc.enrichment_composition.v1`、严格 Registry 绑定和 durable workflow 已能
按 tenant 精确规划并执行 typed read-only action。Kafka daemon 与内网 PKL 批跑只有在显式传入
composition/action 配置后才进入该桥；默认仍只执行固定 `SocAnalysisService`。D4 已能从持久化
ledger/evidence 重建 shadow report、telemetry 和 addendum；它不能把“已接线”冒充真实 Provider
质量或 Pilot readiness。当前 external simulation 已完成，产品完成轨继续 PI-03..05；真实 PingAn
shadow 证据留在独立债务轨，恢复时继续使用已经准备好的同一套验收入口。`PI-01F2` 已补 direct Web
`soc-triage` 的 authenticated ReviewQueue context binding；它关闭产品入口缺口，但不改变任何真实 gate。

| Order | ID | Work / 工作 | Implementation boundary / 实现边界 | Exit evidence / 退出证据 |
|---|---|---|---|---|
| 1 | `D12-B` | Real asset provider / 真实资产定位 | 仅在 PingAn integration 内接 ZEUS + workflow/UM 降级链；generic 层仍只认识 `asset.locate`；现有 acceptance runner 复用 Dispatcher/Review Context，不新增 Runtime 节点 | direct + MCP success/not-found/auth/timeout/ambiguous；至少一项 `mocked=false`；持久化证据可从共享 Review/Lead Agent context 回读且基础 Run/Review 不变；deployed Web/TUI smoke 单独通过 |
| 2 | `PI-01A` | Real threat intelligence / 真实威胁情报 | 复用 ZEUS 鉴权，PingAn adapter 映射 `/public/indicatorSearch`；generic route 保持 `threat_intel.ip_reputation.lookup` | hit/not-found/provider-failure/timeout、裁剪、freshness、lineage 和持久化证据通过 |
| 3 | `PI-01B` | Real security tags and governed facts / 安全标签与治理事实 | `/public/searchTagContent` 只输出 typed provider result；授权扫描、护网、红蓝队、维护窗口等权威事实再映射到现有 Governed Context 生命周期 | valid/expired/out-of-scope/conflict/not-found/error 均可解释；标签不能直接判安全或关单 |
| 4 | `PI-01C` | Real external disposition source / 真实状态理由回流 | Zeus/工单 source adapter 只生成 `SocExternalDispositionIngressCommand`，继续走现有 authenticated ingress/service/UoW | 幂等、乱序、重放、更正、未知状态和 trust mapping 通过；reason 只生成待评审知识候选 |
| 5 | `PI-01D` | Governed read-only investigation orchestration / 受控只读调查编排 | application-level planner/service；D1 只消费 typed entity/role + tenant policy，后续只有新增 typed trigger contract 后才可消费 scenario/gap；复用现有 dispatcher/registry/evidence repository | asset/TI/tag 自动调查可回放；`asset.lookup` 与 `asset.locate` 完成 route consolidation；Provider 失败与正常查无可区分；基础 `AnalysisRun` 不可变且所有副作用保持关闭 |
| 6 | `PI-01E` | External rehearsal -> internal shadow / 外网仿真到内网影子 | 两种环境均分开运行 Runtime compatibility 与 persisted investigation；外网 `external_simulation` 要求 fake/`mocked=true`，内网 `internal_real` 要求 internal/`mocked=false`；先 5 后 50 | 两类报告均覆盖 provider hit/not-found/error、有效证据率、P95、LLM/tool cost、review rate 和越权计数；外网 pass 仅允许进入下一档，不关闭真实 gate |
| 7 | `PI-01F` | Interactive Lead Agent governance bridge / 交互式主控治理桥 | DeerFlow 增加 operator-owned per-agent middleware 扩展；SOC Web/Gateway profile 使用审批 middleware，SOC TUI 保留外层 service bridge；两者共用 proposal/policy/approval service | 结构化 proposal 可稳定 replay；模型不能伪造 ID/actor/context；高风险只入 Approval Inbox，不直接执行或作为 unrestricted MCP tool 暴露 |
| 8 | `PI-01F2` | Direct Web ReviewQueue context bridge / Web 工单上下文桥 | 客户端只传 queue identity hint；Gateway 通过 ReviewService 重建 bounded artifact、写 owner-scoped immutable thread binding；profile middleware transient injection + message provenance | 首轮新线程和后续重开均可用；每轮 fresh rebuild；48k hard cap；acceptance 匹配 queue/binding/message snapshot；不改 verdict/close/memory/action authority |
| 9 | `PI-01G1` | Native SOC specialist profiles / 原生专家配置 | **Done**: 在 `soc_agent` 中声明 capability-oriented network、endpoint、web、email profiles，直接生成 DeerFlow `CustomSubagentConfig`；显式 installer 只合并这些 root `config.yaml` entries，冲突默认 fail closed | DeerFlow registry/doctor 可解析全部 profile；`tools=[]`/`skills=[]`，只继承父模型；不能递归 task、读写文件、运行 shell 或调用 MCP/action；CLI dry-run/apply 与保留 operator config 回归通过 |
| 10 | `PI-01G2` | Governed delegation contract / 受控委派协议 | **Done**: SOC Lead Agent profile 增加 lead-only middleware；只允许 SOC specialist names、trusted ReviewQueue context、1,200-char narrow task、32K server projection、每 chat run 最多两个不同专家、stable lineage；specialist output 仅作为 advisory artifact | general-purpose/bash/未知 subagent、未绑定 context、超预算、重复专家、action marker 和 stopped/capped output 均 fail closed；专家结果不能写 evidence/verdict/memory/approval，Lead Agent 必须重新综合并引用系统证据 |
| 11 | `PI-01G3` | Product surfaces and replay / 产品入口与回放 | **Done**: Web/TUI 复用 DeerFlow native `task_*` events/subtask state；stable delegated identity 回归通过；NIDS network 与 EDR endpoint 代表样本使用真实 `deepseek-v4-flash` 完成 | 两个 smoke 都是一个预期专家 completed、0 failed/capped、advisory provenance 存在；外部 fake evidence 保留 `mocked=true`、`provider_acceptance_claimed=false`；无专家时基础 Runtime/Lead Agent 仍可独立给结论 |

Current status / 当前状态：`D12-B` 为 `Parked / internal evidence pending`；其代码、私有 case matrix 和验收门槛全部保留。`PI-01A` 已完成 production-shaped PingAn Provider、stdio MCP、generic action config、显式 fake smoke，以及 hit/not-found/partial/error/freshness/trim/lineage/persistence 回归；真实内网 `mocked=false` smoke 与实际响应字段复核仍是退出门槛，因此不能标记 Done。`PI-01B1` 已完成 `/public/searchTagContent` Provider/MCP/action 的外网可验证部分，保留 active/expired/inactive/conflicted/unknown/out-of-scope/unusable/not-found 语义；真实 DEV 响应、对象类型、无过期时间语义与持久化 `mocked=false` 证据仍待内网验收。

`PI-01G1..G3` 的防守产品链已完成，不再是当前进行项。其实时模型报告只证明
DeerFlow Lead Agent -> native task -> specialist -> advisory synthesis 可运行；它们不证明
PingAn Provider 已接通，也不提供生产准确率声明。

`PI-01B` 包含两个不能互相冒充的子 gate：`PI-01B1` 是按实体查询安全标签的 request/response
Provider；`PI-01B2` 是 change/scanner/maintenance/exercise-roster 等权威来源向 Governed Context
同步带版本、有效期和 scope 的事实。完成 B1 不代表 B2 完成。若 DEV 暂无 B2 来源，必须显式记录
`data-gated` 并保持授权型 disposition/automation 关闭，不能用测试 fixture 补齐。

旧 ZEUS 源码只证明状态枚举和 `/public/getAlertBrief` 轮询形态，没有给出可审阅的稳定 source event
ID、版本、reason、乱序/更正规则；现有材料也没有 change/scanner/maintenance/exercise-roster 的权威
source contract。因此 `PI-01B2` 与 `PI-01C` 当前均为 `Data-gated`。它们的 generic lifecycle/service
已存在，但 source adapter 不能靠猜测实现，也不阻塞可独立完成的 `PI-01D`。

`PI-01D` 的固定结构是：

```text
immutable base Runtime run
  -> deterministic SocEnrichmentPlanner
  -> versioned allowlisted read-only action plan
  -> SocAgentActionDispatcher / SocActionAdapterRegistry
  -> persisted InvestigationEvidence
  -> correlation + domain triage + Review/Web/TUI/Lead Agent context
```

Kafka/批处理不允许让 LLM 自由发现并执行任意工具。交互式 Lead Agent 可以提出候选动作，但仍须经过
同一个 route/action/policy/adapter 边界。若外部证据需要形成更新后的调查结论，应新增带版本和
Grounding 的 investigation addendum，而不是覆写原始 Runtime run。

`PI-01D` 固定拆成四刀，避免再次把 planner、provider 和 daemon 混成一项：

| Slice | Status | Scope / 范围 | Exit / 退出条件 |
|---|---|---|---|
| `PI-01D1` | **Done** | `SocEnrichmentPolicy/Plan`、确定性 Planner、Main Orchestrator 可选注入；typed entity/role、tenant/CIDR、预算、去重、冲突保留 | 默认关闭；只生成 exact allowlisted read-only action；同 run/policy 可稳定重放；基础 run 不变 |
| `PI-01D2` | **Done** | `soc.enrichment_composition.v1`、严格 config loader/composition root、policy-route/registry 启动校验、`asset.lookup`/`asset.locate` tenant 级二选一 | 默认关闭；exact route/action/adapter ID/kind；只读与 Planner input fail-fast；`mock_only`/`real_only`/`runtime_declared` provenance 隔离；无 tool discovery |
| `PI-01D3` | **Done** | Kafka/内网 batch 的独立 durable investigation workflow；持久化 immutable plan/execution/attempt/evidence，补跨进程幂等、bounded retry、stale recovery、linked replay 和逐次 result-mode 校验 | Runtime batch 可独立；Provider failure 不伪装 miss；重复消息不重复已完成查询/证据；Kafka retryable failure 不提交 offset；migration/CLI/回归通过 |
| `PI-01D4` | **Done** | `soc.investigation_shadow_report.v1`、`soc.investigation_addendum.v1`、只读 reporting service、Review/Web/TUI/Lead Agent 投影、CLI 与内网 batch 聚合 | 可测 hit/not-found/error/retry/action-attempt latency/evidence coverage；未接来源的 Provider 网络耗时与 cost 明确 `not_measured`；addendum 不产生新结论，仍无 verdict overwrite、auto-close、memory confirm 或高风险动作 |

`PI-01F` 已补齐交互式入口治理：通用 DeerFlow 只增加可信 per-agent middleware 装配能力，SOC
middleware、proposal parser、policy 和 approval persistence 仍留在 `backend/soc_agent/`。标准 Web/Gateway
custom-agent path 使用 profile middleware，SOC TUI 保留既有外层 service bridge；二者不会重复处理同一
proposal。该切片只关闭“proposal 能否进入治理边界”的缺口，不代表真实 Provider、生产副作用或整个产品
已经完成。

`PI-01F2` 已补齐 direct Web ReviewQueue context：URL/request 中的 queue ID 不是 artifact，只用于服务端
定位。Gateway 要求 authenticated `lead_agent/soc-triage`，校验业务 lineage，把 thread 一次性绑定到
queue/run/alert，并在每轮从 ReviewService 重建 context。Middleware 的注入只存在于 model request，AI
message 保存 exact provenance；Web acceptance 采纳该 snapshot 而不是 mutation 后的新 hash。现有 profile
必须显式 `soc agent install-profile --overwrite` 才获得 context + approval 两个 middleware。

### 6.2 PI-03 Decomposition / 标签与学习工作包

PI-03 仿真产品轨已完成 A/B/C。每个切片都先用明确的 `simulation` data class 走通，再保留真实标签 gate：

| ID | Work / 工作 | First slice / 第一刀 | Gate / 门槛 |
|---|---|---|---|
| `PI-03A` | Human label foundation / 人工标签基础 | **Done (simulation)**: immutable corpus manifest、reviewer/rationale/provenance/supersede contract；CLI `prepare -> seal -> verify` 已用 5 条 Runtime 产物通过 | `simulation` 始终 `mocked=true`、`real_quality_claim_allowed=false`；没有来源和 reviewer 的标签不得用于质量声明 |
| `PI-03B` | Runtime/model/correlation evaluation / Runtime、模型与关联评测 | **Done (simulation)**: `soc.quality_evaluation_report.v1` 复用 offline/scenario/correlation/confidence 四条评测链；8 alert、4 synthetic labels 和稳定 replay diff 已通过 | simulation 固定禁止 real-quality/profile/rollout/automation；报告保留 Grounding、taxonomy 和 correlation gap，不把 self-confidence 当概率 |
| `PI-03C` | Feedback-derived Skill candidates / 反馈型 Skill 候选 | **Done (simulation)**: typed observation、distinct-source threshold、Skill package hash、scenario/failure facet、SQL backlog、RBAC/audit/state machine 和 aggregation replay 已完成 | 4 条 synthetic feedback 生成一个 `mocked=true` pending candidate；不自动编辑、激活或发布 Skill。真实 correction/external reason 到 typed facet 的 server-owned classifier 仍为 Real Integration Debt |
| `PI-03D` | Tenant knowledge promotion / 租户知识治理升级 | 对路径目录等 candidate knowledge 生成独立 promotion proposal，补 scope/validity/owner 和标签 replay | 默认保持 investigation-only；目录更新本身永不获得 decision impact |
| `PI-03E` | Adaptive parser governance / 自适应解析治理 | drift cohort report + candidate bundle；之后才允许 dual-run/replay/approval/canary/rollback | 禁止单告警 LLM 解析和 Runtime 自修改；无稳定 cohort 不启动 |
| `PI-03F` | Governed memory candidate source completion / 记忆候选来源收口 | **Done**: F1 已完成 CLI/TUI 显式采纳；F2 已完成 authenticated Gateway/Web server-side message resolution；F3 已完成默认关闭的 Kafka/batch typed observation、固定窗口聚合、5/5 双门槛、冻结候选和只读 replay | 禁止逐告警、逐 finding、模型自说自话地写 candidate；所有来源带 typed provenance/idempotency/scope，保持 `pending_review`，confirmed/retrieval 仍需人工治理；重复出现不等于真假、授权、影响或处置结论 |

### 6.3 PI-05 Decomposition / 受治理上线工作包

| ID | Work / 工作 | Status / 状态 | Gate / 门槛 |
|---|---|---|---|
| `PI-05A` | Rollout contract and rehearsal / 上线契约与演练 | **Done (simulation)**: `soc.rollout_plan.v1`、`soc.rollout_rehearsal_report.v1`、5 类 owner、7 个真实 gate、3 档虚拟推进和完整 6 步回滚已实现；CLI `soc rollout rehearse` 可稳定 replay | 报告固定 `mocked=true`、真实 transition/effect 为 0，所有 real gate 均未关闭；不能调用 Provider、broker、feature flag、Zeus 或响应动作 |
| `PI-05B` | Simulation Completion Gate / 仿真完成门禁 | **Done (simulation)**: `soc rollout completion` 只读汇总 PI-01E、PI-03B/C、PI-04 与 PI-05A 六个 artifact；五组件 typed check、artifact/semantic hash、稳定 replay 和 7 项 real debt 已落地 | 本地报告 `SCG-6EEDC5DC3417` 五组件 passed、replay `changed=false`；缺失/坏 artifact 或仿真越权声明 fail closed，固定 `pilot_ready=false`、`production_ready=false` |
| `PI-05C` | Real rollout control / 真实上线控制 | **Real Integration Debt**: 在真实 telemetry、owner、feature flag/cohort enforcement 和 deployed runtime 到位后另行实施 | 不在外网用断开的假 state machine 冒充；任何真实 stage transition 必须有 fresh gate evidence、独立批准、审计和可执行回滚 |

PI-05B 的可复跑命令和 artifact 生成顺序见
`backend/samples/rollout/README.md`。该 Gate 结束产品仿真实现轨，不会自动切换到 PI-05C；只有真实环境输入到位后才恢复对应 integration debt。

### PI Gate / 生产集成门禁

Stage 4 的退出结果是 **Pilot Ready / 可试点**。正式 GA、自动关单和高风险自动处置仍需独立治理审批。

## 7. Parking Lot / 后续项

以下事项有价值，但不能插入当前 `PI-01E`：

- [Correlation pair corpus expansion 和 scorer v2](../archive/ai_soc/deferred/correlation-label-corpus-expansion.md)。
- 完整多 Sub Agent 并行自治与跨域攻击尝试。
- Knowledge RAG、GraphRAG、PageIndex，以及
  [DB memory -> Wiki/OKF projection](../archive/ai_soc/deferred/wiki-okf-memory-projection.md)。
- Prometheus 全局系统态势和管理驾驶舱。
- [Adaptive normalization/parser evolution](../archive/ai_soc/deferred/adaptive-normalization-parser-evolution.md)、
  更多 vendor/scenario adapter、自动 skill 优化和长期记忆治理。
- 高风险 response adapter、补偿事务和自动化 rollout。

只有当前阶段 Gate 通过，或用户明确决定替换当前目标，Parking Lot 项才可进入执行。

## 8. Anti-Drift Rules / 防跑偏规则

1. 同一时间只有一个 Stage 为 `Current`，只有一个 task 为 `In Progress`。
2. 每个实现切片必须携带路线编号，例如 `BD-01`；没有编号的需求先进入 Parking Lot。
3. 新想法不能悄悄插队：要么用户明确替换当前目标，要么记录到后续 Stage/TODO。
4. `progress.md` 的“当前下一刀”必须引用本文件中的任务编号。
5. 未满足 Stage Gate，不得把下一阶段改成 `Current`。
6. 每个切片都写清 `real / mock / fixture / shadow-only / data-gated`，不允许静默降级。
7. 代码、测试、演示产物和文档在同一切片更新；不再创建另一份完整路线图。
8. 研究和参考项目查询必须服务当前 task 的明确决策，不以“继续研究”替代交付。
9. CodeGraph 在切片明确后用于找复用点；完成代码修改后执行 `codegraph sync .`。
10. 每次阶段转换都在 `progress.md` 留下 Gate 证据、未完成项去向和下一任务。
11. 任何 PingAn DEV 依赖先交付显式 simulation implementation + evidence；仿真 gate 通过后产品完成轨可以继续，真实 `mocked=false` gate 进入独立债务轨，不得用它反向阻塞无关产品切片。
12. 仿真只能覆盖已经冻结的 contract；无稳定 contract 的能力保持 data-gated，不用 mock 猜测。所有报告同时给出 simulation status 和 real-integration status，禁止把两者压成一个 Done。
13. Transfer archive / 迁移包不是切片完成产物。只有用户明确要求打包或已进入实际内网交接窗口时才生成；普通实现、仿真 gate 或阶段报告完成后不得自动打包。过时 archive 立即清理，源码与可复现脚本才是长期交付基础。

## 9. Current Execution Pointer / 当前执行指针

```text
Completed:    BD - Boss Demo v0.1 (BD Gate passed 2026-07-18)
Completed:    AA - SOC Alpha Completeness Audit (AA Gate passed 2026-07-18; AUD-01..03 done)
Completed:    BG - Close Blocking Gaps (Alpha Gate passed 2026-07-20)
Completed:    BG-P0-01 - approval integrity and L3 authorization (AC-22, AC-34)
Completed:    BG-P0-02 - transactional mutation and durable audit (AC-16, AC-21)
Completed:    BG-P1-01 - versioned ingestion and feedback (AC-04, AC-08)
Completed:    BG-P1-02 - API contract stabilization (AC-11)
Completed:    BG-P1-03 - Runtime recovery and decision provenance (AC-13, AC-17)
Completed:    BG-P1-04 - Governed memory activation (AC-39)
Completed:    BG-P1-05 - Alpha E2E and docs reconciliation (AC-23, AC-24, AC-49)
Completed:    BG-03 - Alpha readiness package and scoped accountable approval
Current Stage: PI - Real Data & Production Integration
Current:      PI - external product-complete; no additional mock-only product slice is open
Completed:    PI-01 Checkpoint D-0 - 212-row adapter-independent corpus inventory
Completed:    PI-01 Checkpoint D-1 - alert 1965449 canonical normalization (parser warnings explicit)
Completed:    PI-01 Checkpoint D-2 - alert 1965449 generic deterministic entity extraction
Completed:    PI-01 Checkpoint D-3 - alert 1965449 fact reconstruction and role-resolution review
Completed:    PI-01 Checkpoint D-4 - alert 1965449 bounded analysis input and EvidenceCoverageReport
Completed:    PI-01 Checkpoint D-5 - alert 1965449 bounded Skill-package context (3 selected, 387 estimated tokens)
Completed:    PI-01 Checkpoint D-6 - 212-row Skill route coverage (212/212 processed, 0 failed/missed/misrouted)
Completed:    PI-01 Checkpoint D-7 - live AnalysisResult.v2 and typed scenario contract (deepseek-v4-pro, no repair)
Completed:    PI-01 Checkpoint D-8 - current production Grounding lineage (5 grounded, 4 description leakage, quality blocked)
Completed:    PI-01 Checkpoint D-9 - soc.decision_policy.v3 (degraded from ungrounded evidence, review required, no automation)
Completed:    PI-01 Checkpoint D-10 - live deepseek-v4-pro 8-topic / 6-source Runtime matrix (10 calls, 167,042 tokens, quality findings fail closed)
Completed:    PI-01 Checkpoint D-11/D11.1 - 212-row two-pass Runtime compatibility plus evidence-quality semantics (424 runs, 212 stable, 0 failures)
Completed:    PI-01 Checkpoint D12-A - PingAn asset provider code + fake MCP smoke (`mocked=true`; not real-provider evidence)
Parked:       PI-01 Checkpoint D12-B - internal real asset-provider smoke (`mocked=false` still required)
Pending evidence: PI-01A - provider/MCP/evidence code verified externally; internal `mocked=false` smoke pending
Pending evidence: PI-01B1 - provider/MCP/evidence code verified externally; internal expiry/schema and `mocked=false` smoke pending
Data-gated:   PI-01B2 - no authoritative change/scanner/maintenance/exercise-roster source contract
Data-gated:   PI-01C - no stable Zeus/ITSM source event, reason, version and ordering contract
Completed:    PI-01D1 - versioned deterministic enrichment plan + optional Main Orchestrator bridge
Completed:    PI-01D2 - strict enrichment composition, exact registry binding and mock/real provenance validation
Completed:    PI-01D3 - durable investigation ledger, opt-in Kafka/internal-batch bridge, retry/recovery/replay and per-result mode enforcement
Completed:    PI-01D4 - recomputable shadow report, deterministic investigation addendum, context/CLI/batch projections
Completed:    PI-01E tooling - dual-mode paired gate, exact composition/action/extensions fingerprints, tenant scope, per-route result coverage and zero-side-effect validation
Completed:    PI-01E external simulation stage 5 - same 5-row live-LLM cohort; 11 fake MCP calls/evidence, 0 failures/missing evidence/unauthorized side effects; report explicitly cannot close real gates
Completed:    PI-01E external simulation stage 50 - 50/50 paired completion; 157 fake MCP calls/evidence, 0 failures/missing evidence/unauthorized side effects; no Provider hit observed and real gates remain open
Completed:    PI-01E internal-real operator entry - static-by-default, dual-confirm live orchestration of environment/MCP preflight, isolated SQLite, paired batches and gate; no internal evidence produced externally
Completed:    PI-01F - operator-owned per-agent middleware plus SOC Web/Gateway approval bridge; TUI keeps its outer bridge; stable server IDs and no high-risk auto-execution
Completed:    PI-01F2 - authenticated direct Web ReviewQueue context, immutable owner-scoped thread binding, per-run fresh bounded artifact, transient middleware injection and exact assistant provenance
Completed:    PI-01G1 - capability-oriented network/endpoint/web/email profiles, explicit root-config installer and fail-closed runtime doctor
Completed:    PI-01G2 - lead-only bounded delegation middleware, server-owned case/Skill projection, two-distinct-specialist limit, stable lineage and advisory authority guard
Completed:    PI-01G3 - native task-event/replay regression plus real-model NIDS network and EDR endpoint representative smoke; Provider acceptance remains false
Completed:    PI-03F1 - explicit CLI/TUI analyst acceptance of one stable Lead Agent message -> pending review-note candidate; no automatic model-output persistence
Completed:    PI-03F2 - authenticated Web acceptance resolves the latest terminal SOC assistant message from server-owned current checkpoint state; client sends no model text and result remains pending review
Completed:    PI-03F3 - opt-in Kafka/batch immutable observations, strongest-dimension cohorts, canonical source-event-time windows, 5/5 thresholds, one frozen pending candidate, manual-only supersession and read-only replay
Completed:    PI-03A simulation label foundation - five live-LLM runs prepared, sealed and verified with immutable manifest; mocked=true and no real-quality claim
Completed:    PI-03B simulation quality gate - composed offline/scenario/correlation/manifest-bound confidence evaluation; 8/8 alerts passed and replay semantic diff is stable
Completed:    PI-03C simulation Skill backlog - four typed synthetic feedback observations produced one versioned pending candidate; SQL/RBAC/audit/freeze/replay passed, with every mutation/activation/quality claim disabled
Completed:    PI-04-A - SOC Operations Snapshot contract, exact persisted counters, Kafka readiness projection, CLI/API
Completed:    PI-04-B - thin Web consumer, explicit local/simulation evidence, not_measured gaps, desktop/mobile Playwright overflow and screenshot evidence
Completed:    PI-05A - vendor-neutral rollout plan/gate/rollback contract, 16-step virtual rehearsal and stable replay; 0 real transitions/effects
Completed:    PI-05B - five-component fail-closed completion report SCG-6EEDC5DC3417; stable replay, seven real gates open, pilot_ready/production_ready false
Completed:    UP-01 - post-upstream compatibility gate on current HEAD: SOC/architecture 842, Lead/Subagent 387, frontend unit 1014 and browser 18 passed; network/endpoint real-model specialist smokes passed while Provider acceptance remained false
Next:         Freeze external Mock completeness and migration readiness across PI-01..PI-05; fill only demonstrated product/contract/runbook gaps, not another disconnected mock capability
Internal handoff: After the external deliverable is frozen and copied into PingAn DEV, close D12-B / PI-01A / PI-01B1 and the remaining RID items with `mocked=false` evidence
Quality next: In the internal stage, run PI-03 real labeled evaluation when approved human-reviewed corpus/correlation pairs are supplied; include network direction/role calibration
Parallel debt: Keep D12-B / PI-01A / PI-01B1 explicitly mocked and open during external development even when partial interface details are known
Next evidence: PI-05C only after deployed telemetry, accountable owners, cohort enforcement and executable rollback exist; do not implement a disconnected fake controller
Real Integration Debt: D12-B asset, PI-01A TI, PI-01B1 security-tag and PI-01E internal-real shadow remain open; no local transfer archive is retained or regenerated until explicit internal handoff
Data-gated:   PI-01B2/C source contracts, real feedback-to-typed-Skill-facet classification, and real Kafka/PostgreSQL/K8s inputs remain separately parked
```
