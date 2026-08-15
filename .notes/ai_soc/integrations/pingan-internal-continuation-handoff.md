# PingAn SOC Internal Continuation Handoff / 平安内网续作交接单

> Type: temporary transfer artifact / 临时复制交接文件
> Reconciled: 2026-08-10
> Status: `Real Integration Debt / parked`; this is no longer the current product-development pointer
> Resume action: when approved PingAn DEV is available, inject environment secrets/cases, pass live MCP inventory, and run fresh paired `internal_real` stage 5

本文件只保留**真实内网接入尚未完成**的工作，便于未来复制到内网 Mac 后恢复验证。它不是新的权威路线，也不阻塞当前 PI-03..05 仿真产品流程；外网仓库仍以 `.notes/ai_soc/delivery-roadmap.md`、`.notes/ai_soc/progress.md` 和工程契约为准。内网结果回传后，应把状态和验收证据更新回权威文档，再删除或归档本文件。

真实 URL、App Key、Token、账号密码、企业 CA、IP、UM、未脱敏告警和完整响应可以写入已确认 Git-ignored 的 `*.local` / `.deer-flow/` 文件供本地运行，但不得进入 commit。Tracked sample 已准备完毕；当前 ignored `.env.soc-dev.local` 可由 legacy-profile preparer 原位迁移：它删除旧 import/operator 字段，从已审阅源码导入 `YHSYS` PRD profile，并保留其他本地值。剩余 ZEUS/model/fault-case 配置仍须核对。首轮采用直接访问，不预配代理、自定义 CA 或客户端证书。

## 1. Baseline / 已完成与已删除边界

以下内容已经完成，不在本交接单中重做：

- `D0-D11.1`：通用 SOC Runtime、LLM、Grounding、Decision Policy 和 212 条 corpus 稳定性验证。
- `D12-A`：PingAn `asset.locate` 生产形态代码、fake transport、stdio MCP、fallback 编排和 fail-closed；结果仍为 `mocked=true`。
- `D12-B 外网准备`：内网模型 profile、固定无业务数据的 LiteLLM chat smoke、无旧依赖 ZEUS signer、自包含 Agent Platform HTTP client、DEV-only preflight 和 direct-provider smoke 脚本已实现；尚未产生内网 LiteLLM pass 或 Provider `mocked=false` 证据。
- `PI-01A 外网实现`：`/public/indicatorSearch` typed Provider、stdio MCP、action/evidence 和 fake/persistence 回归已完成；尚未产生真实 DEV `mocked=false` 证据。
- `PI-01B1 外网实现`：`/public/searchTagContent` typed Provider、stdio MCP、validity/scope mapping 和 fake/persistence 回归已完成；尚未产生真实 DEV `mocked=false` 证据。
- `PI-01D1/D2/D3`：versioned `SocEnrichmentPolicy/Plan`、deterministic Planner、strict default-off composition、durable execution/attempt/evidence、逐次 mock/real 校验、bounded retry/recovery/replay 与 Kafka/internal-batch opt-in 已实现；默认仍只跑固定 Runtime。
- `PingAn EDR 路径目录`：旧 XLSX 已编译为版本化、精确匹配、只读的本地 SQLite 目录；可经 MCP/action 写入调查证据，但不是 allowlist，不能改变 verdict。
- `PI-04-A`：`soc.operations_snapshot.v1`、CLI/API 和精确持久化计数。

以下能力已经明确删除，后续不得恢复旧 Mock，也不属于待完成项：

- `endpoint.process_tree.lookup`
- `host.event_context.lookup`

进程树、父子进程、命令行、登录账号和主机事件继续从告警自身的 PingAn normalizer、canonical facts 和 bounded native evidence 获取，不依赖外部查询 Provider。

### 1.1 Transfer bundle / 内网迁移包

外网仓库根目录执行：

```bash
backend/.venv/bin/python \
  backend/scripts/soc_pingan_prepare_legacy_workflow_profile.py --apply
git status --short
python3 scripts/build_pingan_internal_transfer.py --include-private-overlay
backend/.venv/bin/python scripts/build_pingan_macos_offline_bundle.py
```

第一条命令只静态解析旧源码并更新 `0600` 的 Git-ignored env；输出必须是
`credential_present=true`、`secret_in_output=false`，不得出现 secret。旧源码本身被 source bundle 排除，
实际凭证只随 private overlay 进入内网。

最终交接包要求 `git status --short` 无输出。构建器默认拒绝 dirty worktree，保证 source archive 可以对应到唯一
commit。`--allow-dirty` 只供开发阶段临时验包；该报告会明确
`dirty_override_used=true`、`final_handoff_eligible=false`，禁止作为最终内网交付。
启用 `--include-private-overlay` 时，构建器还会在写出任何 archive 前检查两个 local config 均为
`0600`、不含 `/Users/...` 硬编码、不含旧 import/operator 字段，并要求 LiteLLM/ZEUS/workflow/fault-case
的关键变量都存在且不是占位值。未先执行 profile preparer 的旧 `.env.soc-dev.local` 会被明确拒绝，
这是预期保护，不是打包器故障。

两个脚本会在 Git-ignored 的 `backend/.deer-flow/internal-transfer/` 中生成四类文件：

- `deer-flow-pingan-source-*.tar.gz`：当前 clean commit 对应的 tracked 源码；明确排除凭证、PKL、XLSX、SQLite、Git 元数据、虚拟环境和生成物。
- `deer-flow-pingan-private-overlay-*.tar.gz`：仅包含 `.env.soc-dev.local`、`config.pingan-dev.local`、当前 PKL、历史 EDR XLSX 及其已编译路径目录；只能走获批的内部传输通道。
- `transfer-report-*.json`：两个包的 SHA-256、大小、文件数、Git commit/branch/dirty 状态；不含 secret 内容。
- `deer-flow-pingan-macos-arm64-offline-*.tar.gz` 与同 timestamp report：项目私有 CPython `3.12.3`、`uv` 和当前 `backend/uv.lock --extra pingan-dev` 的 macOS arm64 离线缓存。目标机器无需公网、公司 PyPI、管理员权限或预装 Python 3.12。

构建前还会核对冻结的关键源码入口，覆盖 PingAn DEV profile、D12-B、TI、Security Tag、
external/internal shadow、paired evaluator、RID 台账和交接文档。任一入口缺失都会 fail closed，不生成
看似完整但无法续作的迁移包。

外网冻结前先运行不触网的迁移专项回归：

```bash
PYTHONPATH=. backend/.venv/bin/pytest -q \
  scripts/test_build_pingan_internal_transfer.py \
  scripts/test_build_pingan_macos_offline_bundle.py \
  backend/tests/test_soc_pingan_agent_workflow.py \
  backend/tests/test_soc_pingan_legacy_workflow_profile.py \
  backend/tests/test_soc_pingan_dev_validation.py \
  backend/tests/test_soc_pingan_litellm_smoke.py \
  backend/tests/test_soc_pingan_local_paths.py
```

不要把本 tracked 文档中的文件名或 hash 当作当前包清单：把 archive 自身的 SHA 写回 archive 内文档
会形成不可稳定的自引用。每次构建后，以同目录、同 timestamp 的 `transfer-report-*.json` 为唯一外部
清单，核对 source/private archive 的文件名、SHA-256、大小和文件数；再分别运行 `--inspect`，要求
`manifest_valid=true`、`safe_member_paths=true`。archive 与 report 均必须为 mode `0600`。

复制前分别验包：

```bash
python3 scripts/build_pingan_internal_transfer.py --inspect \
  backend/.deer-flow/internal-transfer/deer-flow-pingan-source-<timestamp>.tar.gz
python3 scripts/build_pingan_internal_transfer.py --inspect \
  backend/.deer-flow/internal-transfer/deer-flow-pingan-private-overlay-<timestamp>.tar.gz
backend/.venv/bin/python scripts/build_pingan_macos_offline_bundle.py --inspect \
  backend/.deer-flow/internal-transfer/deer-flow-pingan-macos-arm64-offline-<timestamp>.tar.gz
```

内网 Mac 先叠加源码与私有配置，再安装离线 backend toolchain。默认 checkout 为当前用户的
`$HOME/deer-flow`；对当前开发者它自然解析到已确认路径，对其他同事无需修改脚本或配置：

```bash
TRANSFER_ROOT="$HOME/soc-transfer"
TARGET_REPO="$HOME/deer-flow"
mkdir -p "$TRANSFER_ROOT"
tar -xzf /approved/path/deer-flow-pingan-source-<timestamp>.tar.gz -C "$TRANSFER_ROOT"
tar -xzf /approved/path/deer-flow-pingan-private-overlay-<timestamp>.tar.gz -C "$TRANSFER_ROOT"
mv "$TRANSFER_ROOT/deer-flow-pingan-internal" "$TARGET_REPO"

mkdir -p "$TRANSFER_ROOT/toolchain"
tar -xzf /approved/path/deer-flow-pingan-macos-arm64-offline-<timestamp>.tar.gz \
  -C "$TRANSFER_ROOT/toolchain"
"$TRANSFER_ROOT/toolchain/deer-flow-pingan-macos-arm64-offline/install-offline.sh" \
  "$TARGET_REPO"

cd "$TARGET_REPO"
eval "$(backend/.venv/bin/python backend/scripts/soc_pingan_local_paths.py --shell)"
source ./.env.soc-dev.local
stat -f '%Lp %N' .env.soc-dev.local config.pingan-dev.local \
  datas/source/full_alert_2026_month_forth_sample_200.pkl
```

首次安装不访问任何软件源。后续确需在平安内网解析新增依赖时，本项目使用 `uv` 而不是 Poetry；只对该次
维护命令 source `backend/samples/pingan_dev/uv-index.env.example`。不要把 PingAn HTTP index 写进根
`pyproject.toml`，也不要未经评审提交含内网 registry 的 `uv.lock`。

两个 archive 在外网均为 `0600`；私有覆盖包内的文件也强制为 `0600`。源码包和私有包保留独立 manifest/README，叠加解压不会相互覆盖。先核对 `transfer-report` 中的 SHA-256，再删除或隔离中转副本。
独立源码包有意排除 `.git/`，所以以上 Mac 解包流程使用 `stat` 验证私有文件权限，输出应以 `600`
开头；不要在独立解包目录执行 `git check-ignore`。只有将私有覆盖包叠加到一个现有 Git clone 时，才额外
使用 `git check-ignore -v ...` 确认本地文件不会进入提交。

## 2. Parked Real-Integration Order / 已停放的真实接入顺序

```text
PI-01D1-D4 + PI-01E external simulation（Done）
  -> PI-01E 内网 shadow 全链路（Parked Real Integration Debt）
  -> PI-02 真实 Kafka/PostgreSQL/K8s（Parked / inputs absent）

当前产品完成轨已经完成 PI-03A/B/C、PI-04A/B 与 PI-05A/B，并在 Simulation Completion Gate 收口；这些任务不在
本内网交接单内，也不等待本节债务完成。PI-05C 只在真实部署输入到位后恢复，不在外网实现假控制器。

D12-B 真实 asset.locate（Parked，可独立恢复）
  -> 仍需原 direct/MCP/persistence/Web/TUI gate，不由 PI-01A 替代

PI-01A 真实 threat_intel.ip_reputation.lookup（Code-complete / internal evidence pending）
  -> 仍需 hit/not-found/error/timeout/actual-field/persistence gate，不由 PI-01B1 替代

PI-01B1 真实 security_tag.lookup（Code-complete / internal evidence pending）
  -> 仍需 exact/expired/inactive/no-expiry/not-found/error/persistence gate

PI-01B2 / PI-01C（Data-gated）
  -> 等真实权威活动来源与稳定状态/理由事件协议，不用 fixture 或旧枚举猜测实现
```

项目不新增 `D13` 编号。D12-B 与 PI-01A/B1 的内网证据门槛没有被 D4 通用代码关闭，必须在 PI-01E/Pilot readiness 前恢复并关闭。

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
| Legacy signer source | `util.util_tools:isec_sign` |
| Portable signer import | `soc_agent.integrations.pingan.zeus_signing:isec_sign` |
| Signer call | `isec_sign(data=..., app_id=..., app_key=...)` |
| Workflow transport | 本项目 `HttpPingAnAgentWorkflowPort`，复现旧 auth -> async create -> poll contract |
| Asset endpoint | `POST /public/searchAssetInfo` |
| ZEUS config keys | `ZEUS_SYSTEM_URL`、`ZEUS_APP_ID`、`ZEUS_APP_KEY` |
| Terminal workflow | `1087710` |
| Datacenter workflow | `1087787` |
| User/UM workflow | `1092332` |
| Workflow app ID | `YHSYS`，即旧三条归属 workflow 的 Agent Platform 应用/租户身份 |
| Workflow operator | 旧三条归属 workflow 固定 `message.by=WANGWENBIN520`；Adapter 不接受 env 覆盖 |
| Reviewed STG endpoint | `https://agents-api-stg-new.paic.com.cn` |
| Reviewed PRD endpoint | `https://agents-api-sze.paic.com.cn`；必须显式 production confirmation |
| YHSYS source coverage | 旧配置只在 PRD branch 包含该 app；当前验证使用该 reviewed PRD profile，不虚构 STG secret |
| Generic action route | `asset.locate` |

现有实现位置：

- `backend/soc_agent/integrations/pingan/asset_location.py`
- `backend/soc_agent/integrations/pingan/agent_workflow.py`
- `backend/soc_agent/integrations/pingan/legacy_workflow_profile.py`
- `backend/soc_agent/integrations/pingan/zeus_signing.py`
- `backend/soc_agent/integrations/pingan/dev_validation.py`
- `backend/soc_agent/integrations/pingan/d12b_acceptance.py`
- `backend/soc_agent/integrations/pingan/d12b_evidence_acceptance.py`
- `backend/soc_agent/integrations/pingan/asset_mcp_server.py`
- `backend/scripts/soc_pingan_asset_mcp_server.py`
- `backend/scripts/soc_pingan_dev_preflight.py`
- `backend/scripts/soc_pingan_prepare_legacy_workflow_profile.py`
- `backend/scripts/soc_pingan_asset_direct_smoke.py`
- `backend/scripts/soc_pingan_d12b_matrix.py`
- `backend/scripts/soc_pingan_d12b_evidence.py`
- `backend/samples/pingan_dev/`
- `backend/samples/mcp/pingan_asset/`

### 3.3 Inputs to prepare inside DEV / 内网准备项

- [x] 已审阅 `root_config` 和 LOCAL/DEV 环境选择；本地模型 gateway 为 OpenAI-compatible loopback endpoint。
- [x] LiteLLM chat smoke 与不含正文/凭证的 `soc.pingan_litellm_smoke.v1` 报告已实现。
- [ ] 启动内网模型服务后取得 `litellm-smoke.json -> outcome=passed`；`GET /models` 不能替代该验收。
- [x] preflight 强制 `SOC_PINGAN_ENV=dev`，并要求 ZEUS 与 Agent Platform 都使用显式 HTTPS host allowlist；不读取旧 `env_profile`。
- [x] ZEUS signer 已在本项目内实现，不需要 import 整个旧 `util.util_tools`。
- [x] Agent Platform wire contract 已提取为本项目自包含 HTTP client，不需要旧 Python 包、`PYTHONPATH`、Redis token manager 或 `run_workflow` import。
- [x] 通过 legacy-profile preparer 从旧源码静态导入 PRD base URL、allowlist、`YHSYS` app secret；不 import/执行旧项目，也不在输出中暴露 secret。
- [x] `message.by` 按旧源码固定为 `WANGWENBIN520`，不再要求操作人环境变量。
- [x] PRD 只有在 environment/URL/allowlist/secret 全部显式切换且设置 `SOC_PINGAN_WORKFLOW_PRD_CONFIRMATION=CALL_PINGAN_PRD` 时才允许构造 client。
- [x] 已对 Git-ignored `.env.soc-dev.local` 执行 profile preparer，旧 import/operator 字段已删除，权限与无网络 preflight 已通过。
- [ ] 补齐 `D12B_INVALID_ZEUS_APP_KEY`、`D12B_TIMEOUT_ZEUS_BASE_URL`、`D12B_TIMEOUT_ZEUS_ALLOWED_HOSTS` 三个 approved 负例测试值；它们不能从旧源码推导。
- [x] 首轮不配置代理、自定义 CA 或客户端证书；只有 smoke 的实际连接/TLS 错误才能触发该配置。
- [ ] 确认来源 IP 白名单和 `companyCode: all` 要求。
- [ ] 准备已知命中、确定查无、UM fallback、ambiguous、鉴权失败和 timeout 测试值。
- [ ] 核对 workflow ID、旧 ownership override 和错误码是否仍有效。

建议只在内网创建并 gitignore：

```text
.env.soc-dev.local
backend/.deer-flow/soc-internal-validation/d12b/test-cases.local.yaml
backend/.deer-flow/soc-internal-validation/d12b/reports/
```

### 3.4 Pending code slice / 尚需实现的代码

- [x] DEV-only environment profile/preflight 已实现：显式检查环境、必需配置、tracked HTTP client 和 fake/internal 互斥，不输出 secret。
- [x] preflight 在请求发出前阻止未知环境、未确认 PRD、fake transport、未 allowlist ZEUS/Agent Platform host 和非 loopback model endpoint。
- [x] direct-provider smoke 脚本已实现，不能用 MCP smoke 替代这层验收。
- [x] 报告区分 `found`、`not_found`、`ambiguous`、`authentication_failed`、`timeout`、`provider_unavailable`、`invalid_response`、`preflight_failed` 和 `invalid_configuration`。
- [x] 七类 direct-provider case matrix runner 已实现：`--plan-only` 不发请求，live 必须显式 `--confirm-live`、使用 `0600` 的 `*.local.yaml|yml|json` 并指定 report path。
- [x] aggregate report 使用 `soc.pingan_asset_case_matrix_report.v1`，只保留 query hash、预期/实际 outcome、attempt stage/status、latency 和 error class；不含 raw query/UM、Provider body 或环境 override value。
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

从仓库根目录加载本地 profile。真实值可以直接保存在这两个 ignored 文件：

```bash
cp backend/samples/pingan_dev/config.example.yaml config.pingan-dev.local  # only when absent
cp backend/samples/pingan_dev/env.example .env.soc-dev.local              # only when absent
mkdir -p backend/.deer-flow/soc-internal-validation/d12b/reports
cp backend/samples/pingan_dev/d12b-test-cases.example.yaml \
  backend/.deer-flow/soc-internal-validation/d12b/test-cases.local.yaml   # only when absent
chmod 600 backend/.deer-flow/soc-internal-validation/d12b/test-cases.local.yaml
stat -f '%Lp %N' config.pingan-dev.local .env.soc-dev.local \
  backend/.deer-flow/soc-internal-validation/d12b/test-cases.local.yaml
# Resolve this checkout instead of hardcoding one developer path, then fill/verify real DEV values:
eval "$(backend/.venv/bin/python backend/scripts/soc_pingan_local_paths.py --shell)"
source ./.env.soc-dev.local
export D12B_REPORT_DIR="$SOC_REPO_ROOT/backend/.deer-flow/soc-internal-validation/d12b/reports"
export D12B_ASSET_KEY="<approved-internal-test-value>"
mkdir -p "$D12B_REPORT_DIR"
```

运行无网络预检和一个 approved direct case：

```bash
backend/.venv/bin/python backend/scripts/soc_pingan_litellm_smoke.py \
  --confirm-live \
  --report-path "$SOC_INTERNAL_VALIDATION_ROOT/model/litellm-smoke.json"

backend/.venv/bin/python backend/scripts/soc_pingan_dev_preflight.py \
  --report-path "$D12B_REPORT_DIR/preflight.json"

backend/.venv/bin/python backend/scripts/soc_pingan_asset_direct_smoke.py \
  --query "$D12B_ASSET_KEY" \
  --asset-type IP \
  --role victim \
  --report-path "$D12B_REPORT_DIR/direct-success.json"
```

先只检查七类 coverage，不发网络请求；确认 private matrix 中所有 placeholder 已替换后，再显式执行真实 DEV matrix：

```bash
backend/.venv/bin/python backend/scripts/soc_pingan_d12b_matrix.py \
  --cases backend/.deer-flow/soc-internal-validation/d12b/test-cases.local.yaml \
  --plan-only

backend/.venv/bin/python backend/scripts/soc_pingan_d12b_matrix.py \
  --cases backend/.deer-flow/soc-internal-validation/d12b/test-cases.local.yaml \
  --confirm-live \
  --report-path "$D12B_REPORT_DIR/direct-provider-cases.json"
```

`--confirm-live` 会发真实内网 DEV 请求。它会拒绝非 `.local` 文件名、group/world-readable 权限、未替换 placeholder、缺失 fault-injection 环境变量或缺失 report path；不能为通过验收而跳过这些门禁。

Preflight 不发网络请求。外网只会因为真实内网 URL/credential 仍是占位值而失败；不存在“缺少旧
`run_workflow` 包”的前置条件。内网必须先让 preflight 完整通过，不能跳过后强行请求。

然后进入 `backend/` 初始化独立 SQLite 并执行 MCP：

```bash
cd backend
unset SOC_DATABASE_URL
# DEER_FLOW_CONFIG_PATH -> database.backend: sqlite automatically resolves to
# backend/.deer-flow/data/soc_agent_dev.db; migration creates missing parent dirs.
./.venv/bin/python -m soc_agent.cli db upgrade
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

- [x] 验收执行器已固定通过 `SocActionAdapterRegistry`/Action Dispatcher 调用；业务入口不直接调用 Provider。
- [x] 验收执行器已实现 `InvestigationEvidence` 持久化、ReviewQueue investigation context 回读、Lead Agent bounded artifact 回读和基础 Run/Review 不变式检查。
- [ ] 在内网用真实成功 case 运行执行器，保存 `mocked=false` 报告。
- [ ] Web/Review TUI 使用同一 investigation context 的 deployed render smoke 通过；执行器只证明共享服务契约，不冒充浏览器/TUI 渲染。
- [ ] 验证 Provider 没有修改 Runtime verdict、ReviewQueue 状态、memory 或 action approval。
- [ ] 验证失败结果不会提高 finding confidence，也不会触发自动关闭或自动响应。

先从同一 SOC SQLite 选择一个已有的 open ReviewQueue 工单，再选择 private matrix 中
`expected_outcome=found` 的 case。下面命令会真实调用一次内网 MCP；不接受 negative/fault-injection case：

```bash
cd backend
./.venv/bin/python -m soc_agent.cli review list --pretty

export D12B_QUEUE_ID="<existing-open-review-queue-id>"
export D12B_CASE_ID="search-hit"

./.venv/bin/python scripts/soc_pingan_d12b_evidence.py \
  --cases .deer-flow/soc-internal-validation/d12b/test-cases.local.yaml \
  --case-id "$D12B_CASE_ID" \
  --queue-id "$D12B_QUEUE_ID" \
  --confirm-live \
  --report-path "$D12B_REPORT_DIR/evidence-readback.json"
```

通过条件包括：`provider_mode=internal`、`mocked=false`、
`evidence_boundary=investigation_only`、`decision_impact=none`、
`raw_response_included=false`、证据带同一 `request_id/trace_id`、可从 Review Context/Lead Agent artifact 读回，并且
AnalysisRun/ReviewQueue 序列化哈希前后一致。报告不保存 raw query、UM 或 Provider body。

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
- [ ] `InvestigationEvidence` 持久化、共享 Review/Lead Agent context 回读和 deployed Web/TUI render 已分别验证。
- [ ] 敏感字段、裁剪、延迟、错误和审计检查通过。
- [ ] 没有 fake fallback、没有 verdict/memory/close/action 越权副作用。

## 4. PI-01 - Remaining Real Read-only Providers / 其他真实只读能力

D12-B 已按产品决定暂存，PI-01A/B1 已完成外网可实现代码，PI-01D1-D4 的 planner、composition、durable workflow 与 read-only reporting 也已完成；当前主线进入需要真实内网参数和批准数据的 PI-01E shadow end-to-end。每个真实 Provider 仍复用 generic action、typed result、InvestigationEvidence、审计和失败契约；PingAn 字段与鉴权只能存在于 `backend/soc_agent/integrations/pingan/`。D12-B 与 PI-01A/B1 仍须在 PI-01E/Pilot readiness 前恢复并通过各自真实门槛。

| Order | Generic route / boundary | PingAn source | Current state | Completion evidence |
|---|---|---|---|---|
| `PI-01A` | `threat_intel.ip_reputation.lookup` | `POST /public/indicatorSearch` | production-shaped Provider/MCP + fake/persistence regression complete; internal evidence pending | real DEV hit/not-found/error smoke + persisted evidence |
| `PI-01B1` | `security_tag.lookup` | `POST /public/searchTagContent` | production-shaped Provider/MCP + fake/persistence regression complete; internal evidence pending | exact/expired/inactive/unknown/out-of-scope/conflict/not-found/error smoke + persisted evidence |
| `PI-01B2` | authorized-activity fact source | change/scanner/maintenance/exercise roster | lifecycle/matcher real, source facts are fixture | real source version/scope/freshness sync or explicit data-gated status with disposition automation disabled |
| `PI-01C` | external disposition canonical ingress | Zeus status/reason feed | canonical service real; source contract data-gated | authenticated real source adapter + idempotency/order/replay evidence |
| `PI-01D` | governed read-only investigation orchestration | existing action dispatcher/registry/evidence | D1-D4 done; daemon/batch explicit opt-in, default Runtime-only; reporting is recomputable and read-only | deterministic allowlisted plan + persisted/idempotent workflow evidence + immutable base run + shadow telemetry/addendum |
| `PI-01E` | internal shadow end-to-end | real Runtime + PI-01 providers | current | `5 -> 50 -> all` investigation report with latency/cost-or-explicit-gap/error/review/no-side-effect gates |

### 4.1 PI-01A Threat intelligence / 威胁情报

- [x] 复用 ZEUS DEV base URL、App ID/App Key 和 portable `isec_sign`，没有复制认证逻辑到 generic Runtime。
- [ ] 核对 `ipAnalyseReport`、`ipReputationReport`、时间、来源和过期语义。
- [x] 实现 PingAn typed provider/MCP adapter，generic Runtime 只认识 `threat_intel.ip_reputation.lookup`。
- [x] 不迁移旧代码里的硬编码风险评分、地理规则或封禁规则；Provider 返回事实，不直接给 verdict。
- [ ] 验证 approved hit、not-found、invalid response、auth failure、timeout 和多来源结果。
- [ ] 真实证据经 `InvestigationEvidence` 回流并可被 Grounding 引用；完整内部响应不得传给 LLM。

内网从仓库根目录 source `.env.soc-dev.local`，再进入 `backend/` 执行：

```bash
export PI01A_TI_IP="<approved-dev-ip>"
export DEER_FLOW_EXTENSIONS_CONFIG_PATH="$PWD/samples/pingan_dev/extensions.example.json"

./.venv/bin/python -m soc_agent.cli mcp tools --include-schema --pretty
./.venv/bin/python -m soc_agent.cli mcp smoke \
  samples/mcp/pingan_threat_intel/action_adapters.json \
  --route threat_intel.ip_reputation.lookup \
  --json "{\"ip\":\"$PI01A_TI_IP\",\"context_refs\":{\"thread_id\":\"PI-01A-TI-SMOKE\"}}" \
  --pretty
```

分别使用 approved hit 和 definite miss；鉴权失败与 timeout 必须使用获批的 DEV negative profile，不能指向生产。`status=success + reputation_found=false` 才是正常查无，MCP/action `status=failed` 是 Provider 失败，两者不得合并。

### 4.2 PI-01B Security tags and authorized facts / 安全标签与授权事实

#### PI-01B1 Security-tag lookup / 安全标签查询

- [ ] 复用 ZEUS 认证，核对 IP/host/UM/domain 等可查询对象类型。
- [ ] 明确 `label`、`tagCode`、`tagType`、`isValid`、`expireTime`、时区和永久有效语义。
- [x] 实现 PingAn typed provider/MCP adapter，generic Runtime 只认识 `security_tag.lookup`。
- [ ] 验证有效、过期、查无、auth failure、timeout 和多个冲突标签。
- [x] 授权扫描、护网/红蓝队、维护窗口和白名单只能成为 investigation evidence；输出固定 `authorization_fact_created=false`，不能直接判安全或关闭告警。
- [x] 外网契约已保留 exact scope、source path、observed response hash、validity 和 unknown freshness；过期、失效、冲突或超范围标签不产生 active match。真实 provider version/freshness 仍待内网字段确认。

内网从仓库根目录 source `.env.soc-dev.local`，再进入 `backend/` 执行：

```bash
export PI01B1_TAG_ENTITY="<approved-dev-ip-host-domain-or-account>"
export PI01B1_TAG_ENTITY_TYPE="ip"
export DEER_FLOW_EXTENSIONS_CONFIG_PATH="$PWD/samples/pingan_dev/extensions.example.json"

./.venv/bin/python -m soc_agent.cli mcp tools --include-schema --pretty
./.venv/bin/python -m soc_agent.cli mcp smoke \
  samples/mcp/pingan_security_tag/action_adapters.json \
  --route security_tag.lookup \
  --json "{\"entity_key\":\"$PI01B1_TAG_ENTITY\",\"entity_type\":\"$PI01B1_TAG_ENTITY_TYPE\",\"context_refs\":{\"thread_id\":\"PI-01B1-TAG-SMOKE\"}}" \
  --pretty
```

分别使用 approved exact-hit、expired、inactive/no-expiry、definite miss 和 provider-mismatch 值；鉴权失败与 timeout 只能使用获批 DEV negative profile。`status=success + lookup_status=not_found` 才是正常查无；`out_of_scope/unusable/conflicted/unknown` 是可审计的 fail-closed 结果，MCP/action `status=failed` 才是 Provider 调用失败。Provider 兼容旧客户端未校验顶层 `code` 的响应，但当 `code` 存在时只接受 `200`，并且只有明确的 `data: []` 才能表示查无；`data: null`、缺少 `data` 或非成功 `code` 都必须失败。内网 smoke 仍需保存脱敏响应，确认该业务码契约。缺失 `expireTime` 默认不能算 active；只有 ZEUS owner 明确确认其永久有效语义后，才能在 Git-ignored 本地配置启用 `SOC_PINGAN_SECURITY_TAG_ALLOW_OPEN_ENDED_VALIDITY=true`。

#### PI-01B2 Authoritative fact source / 权威事实来源

- [ ] 确认可用来源：change、scanner、maintenance、exercise roster、CMDB 或其他权威系统；不得用
  `security_tag.lookup` 的存在自动声称这些来源已接入。
- [ ] source adapter 生成 vendor-neutral `GovernedContextFact` command，复用现有生命周期、版本、撤销、
  event-time 和 matcher 服务；PingAn 字段不进入 generic matcher。
- [ ] 验证护网红/蓝/白队身份、授权目标/行为、扫描器、变更窗口和维护窗口的 scope 组合；身份命中不
  自动等于当前行为已授权。
- [ ] 若 DEV 没有真实来源，记录 `data-gated`，继续保持 authorization enrichment shadow-only、
  `auto_close_allowed=false`，不能用 validation fixture 关闭 gate。

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

旧源码状态码已经确认：`0 已忽略`、`1 待审阅`、`2 退回中`、`3 待确认`、`4 处理中`、`5 待复核`、`6 待关闭`、`7 子单处理中`、`8 子单已关闭`、`9 已关闭`、`10 编辑`。旧实现的“status != 1 就跳过 AI”不得迁移；状态码本身不等于 true/false verdict。具体映射边界见 `pingan-legacy-source-audit.md`。

### 4.4 PI-01D Governed read-only investigation / 受控只读调查

- [x] 新增 vendor-neutral `SocEnrichmentPlan` 和 deterministic planner；输入只使用 canonical typed entities、role resolutions、completed run status 和 tenant policy。
- [x] 首版只允许 exact registered `asset.lookup|asset.locate|threat_intel.ip_reputation.lookup|security_tag.lookup`，禁止自然语言拼接任意 tool name/payload。
- [x] PingAn PI-01E 选择从 tenant allowlist 禁用 `asset.lookup`：tracked simulated/real composition 均改用 `asset.locate`，paired evaluator 将任何 `asset.lookup` 选中视为 blocking failure；内网不再现场创建另一份临时 composition。
- [x] 复用 `SocAgentActionDispatcher`、`SocActionAdapterRegistry` 和 `InvestigationEvidenceRepository`；通用 Runtime 没有 PingAn 分支或外部 IO。
- [x] Provider failure、normal not-found、result-mode contract failure、denied 和 interrupted 是不同状态；base `AnalysisRun` 保持不可变。
- [x] Kafka/PKL 调查模式显式开启，受 action/retry budget 限制且可 linked replay；默认 Runtime compatibility batch 继续不调用 MCP。
- [x] `PI-01D4` 已增加 recomputable shadow report、Provider/plan telemetry 和 analyst-visible deterministic investigation addendum；只测 action-attempt latency，Provider 网络耗时/cost/SLO 无来源时明确 `not_measured`。

### 4.5 PI-01E External simulation -> Internal shadow / 外网仿真到内网影子

- [x] paired evaluator 已升级为显式 `external_simulation|internal_real`；同时封存同 cohort、tenant、composition/action/extensions 指纹、deterministic pre-LLM compatibility、real/mock、evidence、P95/review/schema/measurement gap 与零越权计数。
- [x] 外网 5 条 rehearsal 已通过：11 次 asset/tag fake MCP 调用、11 条 `mocked=true` evidence、0 failure/missing evidence/越权副作用；报告明确不能关闭真实 gate。
- [x] 同一外网批次已扩至 50：50/50 paired completion、157/157 fake evidence、0 failure/missing evidence/越权副作用；Provider 全部 not-found，因此真实 hit mapping 未被该报告证明。
- [x] 已新增固定薄编排入口 `run_pingan_internal_shadow.py`：默认仅验证两组静态计划；live 时依次执行环境预检、实际 MCP inventory、隔离 SQLite migration、Runtime-only、persisted investigation 和 paired gate，任一步失败即停止。
- [ ] 在内网直接使用 tracked `pingan-internal-shadow.yaml` 与 `pingan_shadow/extensions.internal.json`，只注入环境变量和 approved cases，通过该入口运行 `internal_real` 5 条。
- [ ] 保存两类报告的 provider hit/not-found/error、有效证据率、P95 latency、LLM/tool cost、review rate 和 schema drift；不得混合统计。
- [ ] 验证 verdict 覆写、自动关单、confirmed memory 写入和高风险 side effect 均为 0。
- [ ] 只有人工标签才能进入 PI-03 质量结论；批跑完成本身不是准确率证明。

内网根目录先运行默认静态计划；确认 source/hash、5 次模型调用、固定 internal composition 和输出目录后，
再追加三个 live 确认参数：

```bash
eval "$(backend/.venv/bin/python backend/scripts/soc_pingan_local_paths.py --shell)"
source ./.env.soc-dev.local
export PI01E_ROOT="$PWD/backend/.deer-flow/soc-internal-validation/internal-real/pingan-dev-001"

backend/.venv/bin/python \
  validation/compact_zeus/internal_batch/run_pingan_internal_shadow.py \
  --source /approved/path/alerts-5000.pkl \
  --output-root "$PI01E_ROOT" --ramp-stage 5

backend/.venv/bin/python \
  validation/compact_zeus/internal_batch/run_pingan_internal_shadow.py \
  --source /approved/path/alerts-5000.pkl \
  --output-root "$PI01E_ROOT" --ramp-stage 5 \
  --execute --confirm-live --confirm-investigation
```

首次 live 的 `--output-root` 必须不存在或为空；非空目录会在任何 preflight 前被拒绝，避免覆盖或混入
旧证据。中断后只在完全相同的第二条命令末尾追加 `--resume`；续跑目录必须保留匹配当前 stage 的
`orchestration-<stage>.json`。不得更换 source、root、model、tenant 或 tracked 配置后复用旧目录。

### 4.6 PingAn EDR software-path context / 路径调查知识（已实现）

旧 XLSX 已编译为 Git-ignored SQLite 目录。它保留源文件 SHA、源告警行、历史 disposition、出现次数、时间范围、规则码及可关联的 MD5；不保存原始日志正文。查询只允许精确规范化路径及可选 MD5，不使用旧代码的 basename、版本通配、前缀或删目录段模糊匹配。

在内网仓库根目录构建并查询：

```bash
backend/.venv/bin/python backend/scripts/soc_pingan_software_path_catalog.py build
backend/.venv/bin/python backend/scripts/soc_pingan_software_path_catalog.py query \
  'D:\\ps\\psexec.exe'
```

加载 `.env.soc-dev.local` 后，统一 `backend/samples/pingan_dev/extensions.example.json` 同时注册资产和路径 MCP。执行 action smoke：

```bash
cd backend
./.venv/bin/python -m soc_agent.cli mcp smoke \
  samples/mcp/pingan_software_path/action_adapters.json \
  --route endpoint.software_path.lookup \
  --json '{"path":"D:\\ps\\psexec.exe","context_refs":{"thread_id":"PATH-CONTEXT-SMOKE"}}' \
  --pretty
```

验收边界：结果必须保持 `mocked=false`、`provider_mode=local_catalog`、`candidate_only=true`、`allowlist=false`、`evidence_boundary=investigation_only`、`decision_impact=none` 和 `automation_eligible=false`。`D:`、用户可写和临时目录即使命中历史忽略记录也仍为 `high` attention。该能力已完成代码与本地数据编译，不属于 D12-B 外部真实资产 Provider gate，也不能据此关闭 `PA-12`。

### 4.7 Internal Runtime batch / 内网 5000+ 告警批跑

批跑入口复用生产 `SocAnalysisService`，不是第二套 Runtime。默认不调用 MCP；只有显式提供 composition、一个或多个 action config、`--persist` 和 `--confirm-investigation` 才在基础 run 后执行 D3 只读调查。完整用法见 `validation/compact_zeus/internal_batch/README.md`。先加载 DEV 配置并只做 Runtime 计划：

```bash
eval "$(backend/.venv/bin/python backend/scripts/soc_pingan_local_paths.py --shell)"
source ./.env.soc-dev.local
backend/.venv/bin/python \
  validation/compact_zeus/internal_batch/run_pingan_runtime_batch.py \
  --source /approved/path/alerts-5000.pkl \
  --analyzer-mode llm --model-name deepseek-v4-flash \
  --limit 5 --plan-only
```

再按 `5 -> 50 -> all` 逐步扩大；第一次 live 必须显式 `--confirm-live`，后续使用同一 `--output-dir --resume`。每行完整 `AnalysisRun`、紧凑 `results.jsonl` 和批次 manifest 写入 mode `0700/0600` 的 Git-ignored：

```text
backend/.deer-flow/soc-internal-validation/runtime-batches/<batch>/
```

DEV 默认不持久化；需要验证 ReviewQueue/审计/维护问题时，先执行 `soc db upgrade`，从首批开始固定加入 `--persist --workers 1`，数据库仍为独立 `backend/.deer-flow/data/soc_agent_dev.db`。5,000+ live 运行前先审阅 5/50 条的输入完整性、Grounding、Decision guard、失败率、延迟和 token；批跑完成只证明技术执行完成，不证明模型准确率。

路径目录不在 automatic Planner allowlist，仍需通过 Lead Agent/Action Dispatcher 显式调用。资产、TI 和安全标签可由 D3 的 exact composition opt in，但不会被偷偷塞进固定 Runtime；结果必须通过 Dispatcher 并持久化为 `InvestigationEvidence`。批次 manifest 会锁定 composition/action-config hash，重复完成项不会重复 Provider 调用。

### 4.8 PI-01 exit gate / 阶段门槛

- [ ] 资产、TI、安全标签三个真实只读 Provider 均有 `mocked=false` DEV smoke 和持久化证据。
- [ ] 授权活动权威来源已完成真实 source sync；若 DEV 不可获得，明确记录 `PI-01B2 data-gated`，且授权型 disposition/automation 继续关闭。
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
- [x] `PI-03C` 通用 simulation 已形成可追溯、可回放、人工评审的 `SkillImprovementCandidate`，且不得自动改 Skill。
- [ ] 真实 external reason/analyst correction 仍需 server-owned classifier 映射目标 Skill/version、scenario 与 failure facet；不得按自由文本自动聚类。
- [x] `PI-03D` 旧通用 promotion 草案已关闭：catalog/MCP 永久保持 investigation-only；业务需要的快速忽略由独立、默认关闭的 PingAn software-path tenant policy 承担，并保留显式开关和 decision lineage。
- [ ] parser 漂移只有形成稳定 cohort 后才进入 `PI-03E` candidate bundle/dual-run/replay/approval，禁止 Runtime 自修改。
- [ ] 记录成本、延迟、人工接管率、错误类型和 provider contribution。
- [ ] 评测通过后只允许进入 shadow review；不得直接开放 auto-close、抑制或高风险动作。

## 7. PI-04 - Operations, Observability and Security / 运营与可观测性

`PI-04-A Operations Snapshot` 已完成，不重做。剩余：

- [x] `PI-04-B` 薄 Web 运营视图已完成，只消费现有 `soc.operations_snapshot.v1`，不复制后端判断逻辑；本地 Playwright fixture 不冒充 deployed/production evidence。
- [ ] 接真实 Kafka lag、队列深度、吞吐、处理延迟和失败/DLQ telemetry。
- [ ] 接 LLM 调用量、并发、排队、耗时、失败、token 和成本 telemetry。
- [ ] 接 Provider 成功率、not-found、超时、schema drift、payload/result size 和 dependency health。
- [ ] Prometheus metrics、dashboard、SLO、告警规则和审计留存。
- [ ] 未测量信号必须明确 `not_measured`，不能用默认值推断整体健康。

## 8. PI-05 - Governed Rollout / 受治理上线

外网 `PI-05A` 已完成 vendor-neutral simulation rehearsal：`soc rollout rehearse` 能虚拟演练三档推进和
完整回滚，但固定保持 `mocked=true`、真实 transition/effect 为 0。下面的 checkbox 仍代表真实内网/
部署环境验收，不能因为 PI-05A 通过而勾选。`PI-05B` completion gate 也已通过：它只聚合既有仿真
artifact，仍固定 Pilot/Production=false。真实执行继续归 `PI-05C Real Integration Debt`。

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
├── model/
│   └── litellm-smoke.json
├── d12b/
│   ├── preflight.json
│   ├── direct-provider-cases.json
│   ├── mcp-tools.json
│   ├── mcp-smoke-cases.json
│   ├── evidence-persistence.json
│   └── e2e-alert-cases.json
├── runtime-batches/       # PKL 5 -> 50 -> all Runtime batch artifacts
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

## 10. Real-Debt Resume Pointer / 真实接入恢复位置

```text
Status:  Parked Real Integration Debt; PI-05A/B product simulation track is complete
Ready:   PI-01D1-D4, dual-mode paired evaluator, tracked simulated/internal asset.locate + security-tag profiles; asset.lookup is a blocking failure
Passed:  external simulation stages 5 and 50; stage 50 has 157/157 `mocked=true` evidence, 0 failures/unauthorized side effects and no observed Provider hit; not real-provider evidence
First:   source `.env.soc-dev.local`, run `run_pingan_internal_shadow.py` without `--execute`, and review the static plan
Next:    rerun the same source/root with `--execute --confirm-live --confirm-investigation`; the fixed sequence performs MCP inventory before LLM and seals `internal_real` stage 5
Pending internal evidence: D12-B asset, PI-01A TI, PI-01B1 security-tag gates
Data-gated: PI-01B2 authoritative activity source, PI-01C stable status/reason feed contract
Does not block: completed PI-03/04/05A/05B simulation work
```

已确认存在的内网能力必须先用同一 production Provider/MCP/action 加显式 fake transport 完成外网仿真；该前置门槛已完成。不可获得且 contract 未冻结的输入仍标记 `data-gated`，不得发明新 Provider。进入内网只切换 adapter/provider 配置和 secret，不改变通用 Runtime 控制流与核心服务契约。
