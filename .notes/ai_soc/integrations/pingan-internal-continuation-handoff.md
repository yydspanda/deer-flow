# PingAn SOC Internal Continuation Handoff / 平安内网续作交接单

> Type: temporary transfer artifact / 临时复制交接文件
> Reconciled: 2026-08-03
> Current pointer: `PI-01 / D12-B Internal Real Asset Provider`
> Next action: DEV profile/preflight, then direct ZEUS and `asset.locate` real smoke

本文件只保留**尚未完成**的工作，便于复制到内网 Mac 后继续开发和验证。它不是新的权威路线；外网仓库仍以 `.notes/ai_soc/delivery-roadmap.md`、`.notes/ai_soc/progress.md` 和工程契约为准。内网结果回传后，应把状态和验收证据更新回权威文档，再删除或归档本文件。

任何真实 URL、App Key、Token、账号密码、企业 CA、IP、UM、未脱敏告警和完整响应都只能留在内网，不得写入 Git 或复制回外网。

## 1. Baseline / 已完成与已删除边界

以下内容已经完成，不在本交接单中重做：

- `D0-D11.1`：通用 SOC Runtime、LLM、Grounding、Decision Policy 和 212 条 corpus 稳定性验证。
- `D12-A`：PingAn `asset.locate` 生产形态代码、fake transport、stdio MCP、fallback 编排和 fail-closed；结果仍为 `mocked=true`。
- `PI-04-A`：`soc.operations_snapshot.v1`、CLI/API 和精确持久化计数。

以下能力已经明确删除，后续不得恢复旧 Mock，也不属于待完成项：

- `endpoint.process_tree.lookup`
- `host.event_context.lookup`

进程树、父子进程、命令行、登录账号和主机事件继续从告警自身的 PingAn normalizer、canonical facts 和 bounded native evidence 获取，不依赖外部查询 Provider。

## 2. Remaining Execution Order / 剩余执行顺序

```text
D12-B 真实 asset.locate
  -> PI-01A 真实 threat_intel.ip_reputation.lookup
  -> PI-01B 真实 security_tag.lookup
  -> PI-01C Zeus 状态/理由回流 source adapter
  -> PI-02 真实 Kafka/PostgreSQL/K8s（当前暂停）
  -> PI-03 人工标签、评测与校准
  -> PI-04B+ Web 运营视图、Telemetry、Prometheus/SLO
  -> PI-05 Shadow -> Limited Pilot -> Controlled Rollout
```

项目不新增 `D13` 编号。D12-B 完成后继续使用 `PI-01..PI-05`。

当前内网 DEV 只使用：

```text
backend/.deer-flow/data/soc_agent_dev.db
```

本轮不收集或配置 Kafka、K8s、PostgreSQL；这些能力保留在 PI-02，不能阻塞 D12-B。

## 3. D12-B - Internal Real Asset Provider / 真实资产定位

### 3.1 Target flow / 目标链路

```text
SOC Runtime asset candidate
  -> Lead Agent / Action Dispatcher proposes asset.locate
  -> allowlisted SocActionAdapterRegistry
  -> PingAn stdio MCP
  -> ZEUS POST /public/searchAssetInfo
  -> not found: asset_to_bu workflow
  -> still not found and UM available: UM workflow
  -> InvestigationEvidence persistence
  -> ReviewQueue / Web / TUI / Lead Agent context
```

资产 Provider 只补充调查事实。它不能直接修改 verdict、关闭 ReviewQueue、写 confirmed memory、决定处置对象或授权响应动作。

### 3.2 Known contracts / 已知契约

下列内容已从旧实现确认，只需在内网 smoke 时核对是否漂移：

| Contract | Current value |
|---|---|
| Signer import | `util.util_tools:isec_sign` |
| Signer call | `isec_sign(data=..., app_id=..., app_key=...)` |
| Workflow runner | `model.agent_platform.util_tools:run_workflow` |
| Asset endpoint | `POST /public/searchAssetInfo` |
| ZEUS config keys | `ZEUS_SYSTEM_URL`、`ZEUS_APP_ID`、`ZEUS_APP_KEY` |
| Terminal workflow | `1087710` |
| Datacenter workflow | `1087787` |
| User/UM workflow | `1092332` |
| Workflow app ID | `YHSYS` |
| Generic action route | `asset.locate` |

现有实现位置：

- `backend/soc_agent/integrations/pingan/asset_location.py`
- `backend/soc_agent/integrations/pingan/asset_mcp_server.py`
- `backend/scripts/soc_pingan_asset_mcp_server.py`
- `backend/samples/mcp/pingan_asset/`

### 3.3 Inputs to prepare inside DEV / 内网准备项

- [ ] 脱敏审阅 `root_config` 和直接依赖的环境加载代码，确认如何选择 `dev/stg/prd`。
- [ ] 确认 DEV 不会缺省连接 PRD；SOC preflight 必须拒绝未知环境和隐式 PRD。
- [ ] 确认当前虚拟环境可 import `util.util_tools`、`util.root_config`、`model.agent_platform.util_tools`。
- [ ] 若不能直接 import，在本地配置 `SOC_PINGAN_PROVIDER_IMPORT_PATHS`，不把真实绝对路径提交 Git。
- [ ] 确认 `run_workflow` 是同步、异步还是可能返回 awaitable。
- [ ] 在本地 secret 文件中配置 DEV ZEUS base URL、App ID、App Key 和 workflow operator。
- [ ] 确认企业 CA、代理、客户端证书、来源 IP 白名单和 `companyCode: all` 要求。
- [ ] 准备已知命中、确定查无、UM fallback、ambiguous、鉴权失败和 timeout 测试值。
- [ ] 核对 workflow ID、旧 ownership override 和错误码是否仍有效。

建议只在内网创建并 gitignore：

```text
.env.soc-dev.local
backend/.deer-flow/soc-internal-validation/d12b/test-cases.local.yaml
backend/.deer-flow/soc-internal-validation/d12b/reports/
```

### 3.4 Pending code slice / 尚需实现的代码

- [ ] 增加 DEV-only environment profile/preflight：显式检查环境、imports、必需配置和 fake/internal 互斥，不输出 secret。
- [ ] preflight 必须在请求发出前阻止未知环境、隐式 PRD、fake transport 混入 internal mode。
- [ ] 增加薄的 internal direct-provider smoke 脚本或等价测试入口，直接调用当前 PingAn Provider，并按 case 输出结构化报告；该入口当前尚不存在，不能把下面的 MCP smoke 当成全部 D12-B 验收。
- [ ] 报告区分 `found`、`not_found`、`ambiguous`、`authentication_failed`、`timeout`、`provider_unavailable` 和 `invalid_response`。
- [ ] 保留原始响应仅用于内网审计；投影结果使用当前类型化 contract，不把完整内部响应传给 LLM。

### 3.5 Direct provider and fallback verification / 直接接口与降级链

- [ ] 已知资产由 `searchAssetInfo` 命中时，不调用 workflow。
- [ ] ZEUS 正常查无后，才调用 terminal/datacenter `asset_to_bu` workflow。
- [ ] 前两步正常查无且输入包含 UM 时，才调用 user workflow。
- [ ] `not_found` 与上游失败严格分开；网络失败、鉴权失败、超时不能进入下一层伪装成查无。
- [ ] 多个有效归属返回 `ambiguous=true`，不能默认选第一条。
- [ ] 全部能力失败时 fail closed，绝不切换 fake transport。
- [ ] 每个 attempt 记录阶段、状态、耗时和安全错误分类，不记录 secret/header/full response。

### 3.6 Existing MCP commands / 已存在、可直接执行的 MCP 命令

先从 `backend/` 执行数据库初始化：

```bash
unset SOC_DATABASE_URL
# config.yaml -> database.backend: sqlite automatically resolves to
# backend/.deer-flow/data/soc_agent_dev.db; migration creates missing parent dirs.
./.venv/bin/python -m soc_agent.cli db upgrade
```

然后配置内网本地环境。真实值不要写进本文：

```bash
export SOC_PINGAN_ASSET_MCP_PYTHON="$PWD/.venv/bin/python"
export SOC_PINGAN_ASSET_MCP_SERVER="$PWD/scripts/soc_pingan_asset_mcp_server.py"
export SOC_PINGAN_PROVIDER_IMPORT_PATHS="<internal-import-root>"
export SOC_PINGAN_ZEUS_BASE_URL="<dev-only>"
export SOC_PINGAN_ZEUS_APP_ID="<dev-only>"
export SOC_PINGAN_ZEUS_APP_KEY="<dev-only>"
export SOC_PINGAN_WORKFLOW_OPERATOR="<dev-service-identity-or-um>"
export DEER_FLOW_EXTENSIONS_CONFIG_PATH="$PWD/samples/mcp/pingan_asset/extensions.internal.example.json"
export D12B_REPORT_DIR="$PWD/.deer-flow/soc-internal-validation/d12b/reports"
mkdir -p "$D12B_REPORT_DIR"
```

工具发现：

```bash
./.venv/bin/python -m soc_agent.cli mcp tools \
  --include-schema \
  --report-path "$D12B_REPORT_DIR/mcp-tools.json" \
  --pretty
```

真实成功 case，先将测试值放入当前 shell：

```bash
export D12B_ASSET_KEY="<approved-internal-test-value>"

./.venv/bin/python -m soc_agent.cli mcp smoke \
  samples/mcp/pingan_asset/action_adapters.json \
  --route asset.locate \
  --json "{\"asset_key\":\"$D12B_ASSET_KEY\",\"asset_type\":\"IP\",\"role\":\"victim\",\"context_refs\":{\"thread_id\":\"D12-B-SUCCESS\"}}" \
  --report-path "$D12B_REPORT_DIR/d12-b-success-mcp.json" \
  --pretty
```

至少分别保存 success、not-found、UM fallback、ambiguous、authentication failure 和 timeout 报告。真实成功结果必须包含：

```json
{
  "mocked": false,
  "provider_mode": "internal",
  "decision_impact": "none"
}
```

### 3.7 Business-chain verification / 业务链路验收

- [ ] 通过 `SocActionAdapterRegistry`/Action Dispatcher 调用，不允许业务入口直接调用 Provider。
- [ ] 真实结果持久化为 `InvestigationEvidence`，并保留 route/action/provider/trace provenance。
- [ ] 同一证据可从 ReviewQueue investigation context 读取。
- [ ] Web、Review TUI 和 Lead Agent bounded context 能看到经过裁剪的资产归属结果。
- [ ] 验证 Provider 没有修改 Runtime verdict、ReviewQueue 状态、memory 或 action approval。
- [ ] 验证失败结果不会提高 finding confidence，也不会触发自动关闭或自动响应。

### 3.8 Real-alert end-to-end verification / 真实告警端到端

- [ ] 选择一条 approved APT/NDR 告警，完整运行 normalization -> fact reconstruction -> LLM -> grounding -> decision。
- [ ] 对受害资产或相关资产调用真实 `asset.locate`，再读取补充后的 investigation context。
- [ ] 选择一条 approved EDR/HIDS 告警验证 host 或 UM 资产归属；只使用告警原生进程/主机证据，不恢复已删除查询。
- [ ] 保存脱敏的输入摘要、run ID、evidence ID、ReviewQueue ID、模型/策略版本和最终守卫状态。
- [ ] 对比调用前后：资产归属只能丰富调查上下文，不应偷偷改变检测真值。

### 3.9 Performance and security acceptance / 性能与安全

- [ ] 记录每层 latency、timeout、failure rate、payload/result bytes 和 attempt count。
- [ ] 验证请求超时、有限重试、限流和错误分类；不得无限重试。
- [ ] 检查日志、trace、报告和 LLM projection 中没有 secret、认证头或完整内部响应。
- [ ] 记录 trace/request ID，确保 Action、MCP、Provider 和 InvestigationEvidence 可关联。
- [ ] 验证 Provider 不可用时 Runtime/ReviewQueue 仍 fail closed 且可人工处理。

### 3.10 D12-B Done gate / 完成门槛

只有同时满足以下条件，才能将 D12-B 和第一个 PI-01 real provider 标记为 Done：

- [ ] preflight 和直接 Provider case matrix 全部留证。
- [ ] MCP tools/smoke 证明真实工具可发现、可调用。
- [ ] success、not-found、authentication failure、timeout 和 ambiguous 语义已验证。
- [ ] 至少一个真实结果明确 `mocked=false`、`provider_mode=internal`。
- [ ] `InvestigationEvidence` 持久化及 Web/TUI/Lead Agent 回读已验证。
- [ ] 敏感字段、裁剪、延迟、错误和审计检查通过。
- [ ] 没有 fake fallback、没有 verdict/memory/close/action 越权副作用。

## 4. PI-01 - Remaining Real Read-only Providers / 其他真实只读能力

D12-B 通过后按下面顺序推进。每项都复用 generic action、typed result、InvestigationEvidence、审计和失败契约；PingAn 字段与鉴权只能存在于 `backend/soc_agent/integrations/pingan/`。

| Order | Generic route / boundary | PingAn source | Current state | Completion evidence |
|---|---|---|---|---|
| `PI-01A` | `threat_intel.ip_reputation.lookup` | `POST /public/indicatorSearch` | in-memory mock | real DEV hit/not-found/error smoke + persisted evidence |
| `PI-01B` | `security_tag.lookup` | `POST /public/searchTagContent` | in-memory mock | valid/expired/not-found/error smoke + governed evidence |
| `PI-01C` | external disposition canonical ingress | Zeus status/reason feed | canonical service real, source feed fixture | authenticated real source adapter + idempotency/order/replay evidence |

### 4.1 PI-01A Threat intelligence / 威胁情报

- [ ] 复用 ZEUS DEV base URL、App ID/App Key 和 `isec_sign`，不要复制认证逻辑到 generic Runtime。
- [ ] 核对 `ipAnalyseReport`、`ipReputationReport`、时间、来源和过期语义。
- [ ] 实现 PingAn typed provider/MCP adapter，generic Runtime 只认识 `threat_intel.ip_reputation.lookup`。
- [ ] 不迁移旧代码里的硬编码风险评分、地理规则或封禁规则；Provider 返回事实，不直接给 verdict。
- [ ] 验证 approved hit、not-found、invalid response、auth failure、timeout 和多来源结果。
- [ ] 真实证据经 `InvestigationEvidence` 回流并可被 Grounding 引用；完整内部响应不得传给 LLM。

### 4.2 PI-01B Security tags / 安全标签

- [ ] 复用 ZEUS 认证，核对 IP/host/UM/domain 等可查询对象类型。
- [ ] 明确 `label`、`tagCode`、`tagType`、`isValid`、`expireTime`、时区和永久有效语义。
- [ ] 实现 PingAn typed provider/MCP adapter，generic Runtime 只认识 `security_tag.lookup`。
- [ ] 验证有效、过期、查无、auth failure、timeout 和多个冲突标签。
- [ ] 授权扫描、护网/红蓝队、维护窗口和白名单只能成为 governed context/evidence；不能直接判安全或关闭告警。
- [ ] 标签需要保留 scope、source、version、validity 和 freshness；过期或超范围标签不得参与 tenant disposition。

### 4.3 PI-01C Zeus status/reason feedback / 状态理由回流

这不是默认 MCP 工具。真实 source adapter 应把 Zeus/工单系统事件转换成已有 `SocExternalDispositionIngressCommand`，再调用：

```text
POST /api/soc/external-dispositions
```

- [ ] 确认 DEV transport：Webhook、HTTP polling、数据库视图或其他方式；当前不使用 Kafka。
- [ ] 明确外部 event ID、case/alert ID、状态、理由、actor、event time、version/sequence 和 tenant。
- [ ] 配置 vendor mapping、信任等级、签名、重放保护和字段裁剪。
- [ ] 验证重复事件幂等、乱序更新、状态回退、更正事件和未知状态 fail closed。
- [ ] 区分人工确认理由与自动流程输出；只有符合现有 correction policy 的输入才能进入 correction/memory-candidate 路径。
- [ ] source adapter 不得直接写 repository、ReviewQueue 或 memory。

### 4.4 PI-01 exit gate / 阶段门槛

- [ ] 资产、TI、安全标签三个真实只读 Provider 均有 `mocked=false` DEV smoke 和持久化证据。
- [ ] 可获得的 Zeus 状态/理由 source feed 已通过 canonical ingress；若 DEV 无入口，明确记录 data-gated，而不是以 fixture 标记 Done。
- [ ] Provider success/not-found/failure、敏感信息、延迟、审计和 schema drift 均可见。
- [ ] 至少一组 APT/NDR 与一组 EDR/HIDS 真实告警完成 Runtime + provider + ReviewQueue/Lead Agent 回读。

## 5. PI-02 - Real Infrastructure / 真实基础设施（当前暂停）

恢复条件：拿到正式 DEV/测试基础设施参数，并由平台负责人确认可测试。内网本轮仍使用 SQLite，下面内容不阻塞 PI-01。

- [ ] Kafka：真实 topic、ACL/TLS、consumer group、DLQ、offset、重放、背压和故障演练。
- [ ] PostgreSQL：migration、连接池、事务、隔离级别、备份恢复和故障注入；SQLite 结果不能冒充此验收。
- [ ] K8s：worker deployment、ServiceAccount、secret、资源限制、副本、health/readiness、滚动升级和回滚。
- [ ] 以约一万条/天及高峰窗口验证吞吐、端到端延迟、幂等、重试和 DLQ 恢复。
- [ ] 形成部署、容量、恢复、回滚和凭证轮换报告后，才可关闭 PI-02。

## 6. PI-03 - Real Labels and Calibration / 真实标签与校准

- [ ] 建立脱敏、人工标注、版本化的 scenario/verdict/evidence corpus，记录 reviewer、来源和理由。
- [ ] 覆盖 APT/NDR、EDR、HIDS、SIEM/TI、不同场景、反例、脏数据和 schema drift。
- [ ] 评估 Runtime/LLM 的 verdict、scenario、evidence grounding、manual checks 和 review routing。
- [ ] 校准 confidence，但不把模型自报分数当概率，也不以单一 tenant 的分布代表通用产品。
- [ ] 扩充人工标注的 correlation pair corpus，区分 `same_incident`、`related_distinct`、`unrelated`。
- [ ] 记录成本、延迟、人工接管率、错误类型和 provider contribution。
- [ ] 评测通过后只允许进入 shadow review；不得直接开放 auto-close、抑制或高风险动作。

## 7. PI-04 - Operations, Observability and Security / 运营与可观测性

`PI-04-A Operations Snapshot` 已完成，不重做。剩余：

- [ ] `PI-04-B` 薄 Web 运营视图，只消费现有 `soc.operations_snapshot.v1`，不复制后端判断逻辑。
- [ ] 接真实 Kafka lag、队列深度、吞吐、处理延迟和失败/DLQ telemetry。
- [ ] 接 LLM 调用量、并发、排队、耗时、失败、token 和成本 telemetry。
- [ ] 接 Provider 成功率、not-found、超时、schema drift、payload/result size 和 dependency health。
- [ ] Prometheus metrics、dashboard、SLO、告警规则和审计留存。
- [ ] 未测量信号必须明确 `not_measured`，不能用默认值推断整体健康。

## 8. PI-05 - Governed Rollout / 受治理上线

### 8.1 Shadow

- [ ] 真实告警运行完整 Runtime 和只读 Provider；结果只供运营查看。
- [ ] 不自动改变 Zeus 状态，不自动关闭，不自动执行响应动作。
- [ ] 收集人工 outcome、override、证据缺口、延迟和失败。

### 8.2 Limited Pilot

- [ ] 仅指定告警源、场景、运营人员和时间窗口。
- [ ] 明确值班、升级、回滚、数据保留和事故响应责任人。
- [ ] 达到 PI-03 质量门槛和 PI-04 运营/SLO 门槛。

### 8.3 Controlled Rollout

- [ ] 分 tenant/source/scenario 扩大范围，使用版本化策略和 feature flag。
- [ ] 只读查询可按治理策略自动执行；任何状态修改继续走明确 service boundary。
- [ ] 每个 rollout cohort 都能独立停用和回滚。

### 8.4 Approval-gated actions

- [ ] 封禁 IP、隔离主机、禁用账号等真实 Provider 另行接入。
- [ ] 必须复用现有 approval/grant/idempotency/audit contract，并补结果验证、失败补偿和回滚。
- [ ] 默认保持人工审批；任何自动化范围扩大都需要独立评审，不由模型或单次评测决定。

## 9. Internal Evidence Package / 内网结果包

每一阶段在 `backend/.deer-flow/soc-internal-validation/` 保存 gitignored 结构化报告。建议：

```text
soc-internal-validation/
├── d12b/
│   ├── preflight.json
│   ├── direct-provider-cases.json
│   ├── mcp-tools.json
│   ├── mcp-smoke-cases.json
│   ├── evidence-persistence.json
│   └── e2e-alert-cases.json
├── pi01-threat-intel/
├── pi01-security-tags/
├── pi01-external-disposition/
├── pi02-infrastructure/
├── pi03-evaluation/
├── pi04-observability/
└── pi05-rollout/
```

可以带回外网的内容必须先脱敏，只保留：

- contract/schema 变化；
- error/status 枚举；
- 字段语义和裁剪规则；
- 不含内部地址或业务数据的代码修复；
- 聚合后的延迟、大小和成功率；
- 已审查的脱敏样本。

不得带回：secret、认证头、完整内部响应、真实 IP/UM、未脱敏告警、内网绝对路径和企业 CA 私钥。

## 10. Resume Pointer / 下次继续位置

```text
Current: PI-01 / D12-B Internal Real Asset Provider
First:   collect/review redacted root_config and DEV environment-selection contract
Then:    implement DEV-only profile/preflight and direct-provider smoke entry
Next:    run direct ZEUS/fallback cases and existing MCP tools/smoke
Gate:    persist mocked=false InvestigationEvidence and verify Web/TUI/Lead Agent readback
After:   PI-01A real threat intelligence provider
```

不要因为接口暂时不可用而增加新的 fake Provider。不可获得的输入应明确标记 `data-gated`；已有真实能力只替换 adapter/provider/config，不改变通用 Runtime 控制流和核心服务契约。
