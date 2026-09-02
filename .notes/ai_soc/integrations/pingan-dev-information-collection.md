# PingAn SOC DEV Information Collection / 平安内网 DEV 信息收集说明

> Scope: `PI-01 / D12-B` and the next read-only PingAn integration slices
> Environment: internal Mac + DEV services
> Database: isolated local SQLite
> Explicitly out of scope: Kafka, K8s, DEV PostgreSQL, endpoint process-tree lookup, host-event-context lookup, and real response actions

## 1. Purpose / 目的

这份文档用于在内网一次性收集 SOC Agent 接入 DEV 所需的信息。外网先完成可移植代码、类型化配置、预检和 smoke 脚本；项目复制到内网 Mac 后，只填本地配置并执行验证，不再来回复制修改业务代码。

执行顺序固定为 `external_simulation 5 -> external_simulation 50 -> internal_real 5`。前两档已通过；
当前只缺本文件中的 endpoint/secret/approved case 和内网执行。内网直接使用 tracked secret-free
composition/extensions，真实值只通过环境变量注入，不再现场增加临时 Mock 或修改核心业务代码。

配置分成两类：

- **Tracked / 可提交**：模块结构、配置键名、接口路径、请求/响应结构、状态枚举、脱敏样例和失败语义。
- **Local / 不提交**：真实 URL、App Key、Token、账号密码、企业 CA、IP/UM、测试 case 和完整内部响应。它们可以直接写入 `.env.soc-dev.local`、`config.pingan-dev.local` 及 `.deer-flow/` 验收文件，只要先确认 Git 忽略规则生效。

不要把 secret 写进 tracked 文档、sample 或 commit；本地 runnable config 不必用占位符。

### Quick collection checklist / 本次最小收集包

旧项目源码审计和外网代码准备已经完成。复制到内网后只需补完以下运行输入：

- [x] `root_config`、LOCAL/DEV 环境选择和本地 OpenAI-compatible model endpoint 已从源代码确认。
- [x] ZEUS signer 已提取为本项目内的无旧依赖实现，不再要求 import 整个 `util.util_tools`。
- [x] Agent Platform wire contract 已从旧源码提取到本项目自包含 HTTP client，不再 import 旧项目的 `run_workflow`。
- [x] 当前内网 Apple Silicon Mac 已准备 Python `3.12.7`、uv、Node `24`、pnpm 内网源与 nginx `1.23`，
  可使用无 Docker Host DEV；CPython `3.12.3` 离线工具链保留为备用。
- [x] 三个 PKL 与 Workbench payload SQLite 已单独放在内网 `$HOME/Downloads/source|corpus`；
  private overlay 只携带匹配的 manifest/index，解压后由 staging 脚本校验和落位。
- [x] 已提供项目自有 OpenAI-compatible 模型网关和固定无业务数据的 `chat.completions` smoke；报告不保存 key、响应 ID 或模型原文。
- [ ] 在内网由 Host DEV 启动项目模型网关后执行 smoke，并保存 `outcome=passed` 的 `0600` 报告。
- [x] Agent Platform 的 `YHSYS` PRD URL、credential 与固定 `message.by=WANGWENBIN520` 已从旧源码确认；迁移器只把 secret 写入 Git-ignored `0600` env，真实调用仍需显式 PRD confirmation 和 `--confirm-live`。
- [x] 首轮按直接访问 DEV/STG 设计，不配置代理、自定义 CA 或客户端证书；只有真实 smoke 明确报出网络前置条件时才补。
- [ ] 确认 DEV 服务是否存在来源 IP 白名单，以及当前 Mac 是否已放行。
- [ ] 确认是否能在内网准备资产命中、查无、UM fallback、ambiguous、鉴权失败和 timeout 测试 case；真实测试值留在内网。

D12-B 之后再收集：TI 和安全标签的脱敏成功/查无/错误响应，以及可用时的 Zeus 状态理由回流协议。

当前**不需要**收集 Kafka、K8s、PostgreSQL 或真实响应动作信息。内网 DEV/STG 演练数据库不需要账号密码，项目会从 DeerFlow `database.backend: sqlite` 按 Runtime profile 自动使用独立的 `soc_agent_dev.db` / `soc_agent_stg.db`。

## 2. Already Known / 不需要重复收集

以下内容已经从旧 ZEUS 代码确认，除非内网 smoke 证明已变更，否则不再要求手工说明：

| Item | Known contract |
|---|---|
| ZEUS signer | 旧协议来自 `util.util_tools:isec_sign`；可移植实现为 `soc_agent.integrations.pingan.zeus_signing:isec_sign` |
| Workflow transport | 本项目 `HttpPingAnAgentWorkflowPort`；复现旧 auth -> create -> poll wire contract，不依赖旧 Python 包 |
| Shared ZEUS config | 旧代码通过 `util.root_config` 读取 `ZEUS_SYSTEM_URL`、`ZEUS_APP_ID`、`ZEUS_APP_KEY` |
| Asset API | `POST /public/searchAssetInfo`，签名鉴权，`companyCode` header |
| Asset workflow chain | `searchAssetInfo -> asset_to_bu -> UM`，查无才进入下一步 |
| Workflow IDs | terminal `1087710`、datacenter `1087787`、user `1092332` |
| Workflow app ID | `YHSYS`；这是旧三条归属 workflow 的 Agent Platform 应用/租户身份，不是模型名或操作人 |
| Workflow operator | 旧三条归属 workflow 固定 `message.by=WANGWENBIN520`；平安 Adapter 不接受环境覆盖 |
| Agent Platform STG | 旧 LOCAL/STG profile 均为 `https://agents-api-stg-new.paic.com.cn` |
| Agent Platform PRD | 旧 PRD profile 为 `https://agents-api-sze.paic.com.cn`；新代码要求显式 PRD confirmation |
| YHSYS credential coverage | 旧源码只在 PRD branch 登记 `YHSYS`；当前真实验证因此使用该 reviewed PRD profile，不虚构 STG credential |
| Threat intelligence | `POST /public/indicatorSearch`，与资产接口共用 ZEUS App ID/App Key |
| Security tags | `POST /public/searchTagContent`，与资产接口共用 ZEUS App ID/App Key |
| Success parsing | `searchAssetInfo` 和 `run_workflow` 的成功响应结构按旧代码实现并做兼容解析 |
| Local model endpoint | 本项目在 `http://127.0.0.1:4001/v1/` 暴露 OpenAI-compatible gateway；公共 alias 为 `deepseek-v4-flash`，内部 EAGW scene/upstream model 由 private overlay 配置 |
| ZEUS status map | `0..10` 对应已忽略、待审阅、退回中、待确认、处理中、待复核、待关闭、子单处理中、子单已关闭、已关闭、编辑 |

SOC Runtime 不实现 `endpoint.process_tree.lookup` 或 `host.event_context.lookup`。进程树、命令行、登录账号和主机事件只使用告警自身携带的 bounded native evidence。

完整源码结论和 safe-path 数据边界见 `pingan-legacy-source-audit.md`。

## 3. Priority A - Required Before D12-B / D12-B 前必须收集

### 3.1 DEV environment selection / DEV 环境选择

该项已完成源码审计：

- 旧模块通过 `env_profile` 选择 profile；新项目不读取该全局变量。D12-B 首轮使用 `SOC_PINGAN_ENV=dev`，验证后可通过受治理命令切到 `stg`；Agent Platform 与 ZEUS 上游目标始终由各自变量独立显式选择。
- DeerFlow 的模型 endpoint、ZEUS endpoint 和 workflow runner 分别显式配置，不依赖旧 `root_config` 的隐式全局读取。
- `soc_pingan_dev_preflight.py` 会在发请求前拒绝未知环境、非 internal provider、未 allowlist 的 ZEUS/Agent Platform host、非 loopback model endpoint，以及未显式确认的 ZEUS/Workflow PRD target。
- 实际 DEV URL/App ID/App Key 可以直接写入 Git-ignored `.env.soc-dev.local`。

已提供：

```text
backend/samples/pingan_dev/config.example.yaml
backend/samples/pingan_dev/env.example
config.pingan-dev.local               # Git ignored; real values allowed
.env.soc-dev.local                    # Git ignored; real values allowed
```

### 3.2 Existing corpus staging / 已有语料落位

大语料不再重复进入 source/private archive。内网 checkout 解压后，先确认以下四个文件：

```text
$HOME/Downloads/source/full_alert_2026_month_forth_sample_200.pkl
$HOME/Downloads/corpus/full_alert_validation_corpus.pkl
$HOME/Downloads/corpus/full_alert_dams_labeled_merged.pkl
$HOME/Downloads/corpus/full_alert_dams_labeled_merged.workbench-payloads.sqlite
```

```bash
python3.12 scripts/soc_pingan_stage_internal_corpus.py
python3.12 scripts/soc_pingan_stage_internal_corpus.py --apply
```

第一条是无写入 dry-run；第二条只在四项均通过随包 manifest/index 的文件名、大小与 SHA-256
验证后，才以 mode `0600` 原子替换 canonical target。脚本使用 `Path.home()`，不会写死
`/Users/zhangjianming627`，同事机器可直接复用。它不反序列化 PKL，也不读取 SQLite 业务表。

### 3.3 Offline Python toolchain / 离线 Python 工具链

当前已准备好系统工具的内网 Mac 优先执行：

```bash
python3.12 scripts/soc_pingan_macos_host_dev.py check
python3.12 scripts/soc_pingan_macos_host_dev.py install
```

该路径只访问已批准的平安 PyPI/NPM 源，不需要 Docker。若另一台内网机器没有 Python/uv 或可用内部源，
仍可使用独立离线包，无需预装 Python `3.12.3` 或访问公司 PyPI：

- 外网构建 `deer-flow-pingan-macos-arm64-offline-<timestamp>.tar.gz`。
- 包内固定 Apple Silicon CPython `3.12.3`、`uv` 和当前 `backend/uv.lock` 的 `pingan-dev` 依赖缓存。
- 安装器只写项目内的 `backend/.deer-flow/toolchain/`、`backend/.deer-flow/offline/` 和 `backend/.venv/`。
- 安装过程强制 `--offline --no-python-downloads`，不使用 `sudo`，不修改系统 Python，也不依赖其他项目的虚拟环境。
- checkout 路径由 `backend/scripts/soc_pingan_local_paths.py` 基于脚本位置解析，不写死某位同事的 `/Users/...`。
- 平安 Maven/PyPI 镜像只用于离线安装之后的可选依赖维护。项目使用 `uv` 而不是 Poetry；
  `backend/samples/pingan_dev/uv-index.env.example` 提供 `UV_DEFAULT_INDEX` 和精确 host:port 的
  `UV_INSECURE_HOST`。它不进入仓库全局配置，也不替代当前 lock 对应的离线包。

### 3.4 Local-only ZEUS settings / 只留内网的 ZEUS 配置

下列实际值直接写入 `.env.soc-dev.local`，该文件必须保持 Git ignored：

```text
SOC_PINGAN_ENV=dev
SOC_PINGAN_ZEUS_PRD_BASE_URL=https://isec-gw.paic.com.cn
SOC_PINGAN_ZEUS_PRD_ALLOWED_HOSTS=isec-gw.paic.com.cn
SOC_PINGAN_ZEUS_PRD_APP_ID=SEC-MODEL
SOC_PINGAN_ZEUS_PRD_APP_KEY=<written by the legacy-profile preparer>
SOC_PINGAN_ZEUS_STG_BASE_URL=https://isec-gw-stg.paic.com.cn
SOC_PINGAN_ZEUS_STG_ALLOWED_HOSTS=isec-gw-stg.paic.com.cn
SOC_PINGAN_ZEUS_STG_APP_ID=SEC-MODEL
SOC_PINGAN_ZEUS_STG_APP_KEY=<written by the legacy-profile preparer>
SOC_PINGAN_ZEUS_ENV=prd
SOC_PINGAN_ZEUS_BASE_URL=https://isec-gw.paic.com.cn
SOC_PINGAN_ZEUS_ALLOWED_HOSTS=isec-gw.paic.com.cn
SOC_PINGAN_ZEUS_APP_ID=SEC-MODEL
SOC_PINGAN_ZEUS_APP_KEY=<written by the legacy-profile preparer>
SOC_PINGAN_ZEUS_PRD_CONFIRMATION=CALL_PINGAN_ZEUS_PRD
SOC_PINGAN_WORKFLOW_ENV=prd
SOC_PINGAN_WORKFLOW_BASE_URL=https://agents-api-sze.paic.com.cn
SOC_PINGAN_WORKFLOW_ALLOWED_HOSTS=agents-api-sze.paic.com.cn
SOC_PINGAN_WORKFLOW_APP_ID=YHSYS
SOC_PINGAN_WORKFLOW_APP_SECRET=<written by the legacy-profile preparer>
SOC_PINGAN_WORKFLOW_PRD_CONFIRMATION=CALL_PINGAN_PRD
```

在外网准备 checkout、构建 private overlay 之前运行：

```bash
backend/.venv/bin/python \
  backend/scripts/soc_pingan_prepare_legacy_model_gateway_profile.py --apply
backend/.venv/bin/python \
  backend/scripts/soc_pingan_prepare_legacy_workflow_profile.py --apply
```

两个脚本只用 AST 静态读取已审阅旧源码，不 import/执行旧项目。第一个选择 STG
`DeepSeek_V4_Flash` 模型路由、迁移 loopback key/旧 ingress app-key、生成
`.secrets/eagw-private-key.der`，同时写入 ZEUS PRD/STG 两套目标及凭证，初始激活
`项目 DEV -> ZEUS PRD`，并将生命周期和回调保持在 `fake`；第二个迁移 `YHSYS` PRD Workflow
profile。切换到项目 STG 时，受治理命令激活 ZEUS STG；它不会改变模型或 Workflow target。输出只包含
profile 元数据、源/key hash 和凭证存在标记，始终为
`secret_in_output=false`。旧源码本身不进入 source bundle，写好的 env/key 只进入受保护 private overlay。

还需确认：

- 首轮不使用代理、自定义 CA 或客户端证书；如真实 TLS/连接错误证明需要，再按实际错误补配置，不预先增加复杂度。
- DEV 是否要求来源 IP 白名单。
- `companyCode: all` 是否仍允许。
- 请求超时、限流和典型 HTTP/业务错误码。
- 旧源码把三条 workflow 的 `message.by` 固定为 `WANGWENBIN520`，本次兼容实现保持一致；若未来平台 owner 要求调用人透传，应作为新的已评审协议版本实现，而不是临时环境覆盖。
- 旧源码没有 `YHSYS` STG credential，因此不再要求或猜测 STG secret；当前 workflow profile 明确指向 reviewed PRD endpoint。
- PRD profile 同时锁定 environment/base URL/allowlist/secret，并需要显式 confirmation；真正发请求还必须由 live runner 的 `--confirm-live` 二次确认。
- 旧资产归属修正规则是否仍有效；如存在新的 BU/company code override，提供脱敏规则表。

### 3.5 D12-B test matrix / D12-B 测试矩阵

测试值不需要带出内网。请在 Mac 上准备一个 gitignored JSON/YAML，至少包含：

| Case | Required input | Expected boundary |
|---|---|---|
| ZEUS direct hit | 已知存在的 IP/host/domain | `searchAssetInfo` 命中，不调用 workflow |
| Asset-to-BU fallback | ZEUS 查无、资产 workflow 可命中的值 | 进入 terminal/datacenter workflow |
| UM fallback | 前两步查无、UM workflow 可命中的账号 | 进入 user workflow |
| Definite not found | 确认不存在的值 | `found=false`，不是 provider failure |
| Ambiguous ownership | 可返回多个有效归属的值；没有则注明 unavailable | `ambiguous=true`，不擅自选第一条 |
| Authentication failure | 使用无效/缺失测试凭证或 approved fault injection | fail closed，不回退 fake |
| Timeout/unavailable | approved fault injection 或不可达 DEV 地址 | provider failure，与正常查无分开 |

每个 case 只需记录输入引用、预期阶段和预期结果；不要把 secret 放进去。

## 4. Priority B - Next PI-01 Read-Only Providers / 后续只读能力

### 4.1 ZEUS threat intelligence / 威胁情报

旧代码已提供请求和响应解析基础。内网需要验证并记录：

- DEV `/public/indicatorSearch` 是否返回真实/测试情报，还是始终空结果。
- 一条 approved 命中 IP、一条查无 IP；实际值只留内网。
- 脱敏后的成功、查无、业务错误响应各一份，主要用于验证 schema 是否漂移。
- `ipAnalyseReport`、`ipReputationReport` 中哪些字段稳定，哪些可能缺失。
- 数据时间、过期时间、情报来源和多来源结果的语义。
- 限流、超时和最大批量；当前实现默认单 IP 查询，不自动照搬旧风险评分公式。

Provider 只返回类型化情报事实，不把旧代码中的 hardcoded score/geo/封禁规则迁入通用 Runtime。

### 4.2 ZEUS security tags / 安全标签

内网需要验证并记录：

- DEV `/public/searchTagContent` 可查询的对象类型：IP、host、UM、域名、工具或其他实体。
- `label`、`tagCode`、`tagType` 的枚举或样例解释。
- `isValid`、`expireTime` 的准确语义、时区和永久有效表示。
- 一条有效标签、一条过期标签、一条查无结果；实际值只留内网。
- 脱敏后的成功、查无、业务错误响应各一份。
- 确认顶层 `code` 的成功值及缺失语义，并确认正常查无是否始终返回明确的 `data: []`；`data: null` 不按查无处理。
- 哪些标签属于授权扫描、红蓝队/护网、白名单、维护窗口或内部安全工具。

标签只形成 ordinary `InvestigationEvidence`；不能直接把告警判安全、关闭工单、写 confirmed memory
或创建 `GovernedContextFact`。

安全标签查询与权威授权事实同步是两个独立 gate。还需确认 change、scanner、maintenance、exercise
roster 或其他系统能否提供带 source/version/scope/validity 的事实；若当前 DEV 没有入口，记录
`PI-01B2 data-gated`，不得用本地 fixture 或标签查询结果冒充完整授权事实来源。

### 4.3 ZEUS external disposition feedback / 老系统状态与理由回流

如果 DEV 已有可用入口，请收集：

- 传输方式：Webhook、HTTP polling、数据库视图或其他方式。Kafka 当前不考虑。
- 脱敏 payload：告警/工单 ID、事件唯一 ID、状态、理由、操作人、更新时间、版本/序号、标签。
- 状态枚举及其业务含义：待处理、转交、关闭、误报、真实攻击、抑制等。
- 哪些状态/理由是人工确认，哪些是自动流程生成。
- 告警 ID、run ID、ReviewQueue ID 与外部 case ID 的关联方式。
- 重复事件、乱序更新、状态回退和更正事件的规则。
- 调用方鉴权、签名和重放保护；真实凭证只留内网。

真实 source adapter 最终仍写入现有 `POST /api/soc/external-dispositions` canonical boundary，不直接改 repository。

## 5. Runtime and Local Storage / Runtime 与本地存储

### 5.1 LLM DEV configuration

已确认并提供 `backend/samples/pingan_dev/config.example.yaml`：DeerFlow profile 和本地 gateway 公共 alias 均为 `deepseek-v4-flash`。Gateway 再根据 `.env.soc-dev.local` 中的 EAGW scene/upstream 配置调用内网模型；仍需在内网确认并发/RPM 限制及 `SOC_LLM_SENSITIVE_EVIDENCE_MODE=full` 的使用范围。

验证分两层：Gateway `/health` 只证明进程和本地鉴权边界可达；
`backend/scripts/soc_pingan_model_gateway_smoke.py --confirm-live --report-path ...` 才真实调用一次
`POST /v1/chat/completions`。该脚本只接受 loopback endpoint，使用固定无业务提示词，并保存不含响应正文的
`soc.pingan_model_gateway_smoke.v1` 报告。

### 5.2 SQLite-only DEV/STG rehearsal databases

本阶段不收集 PostgreSQL 信息。内网 DEV/STG 演练使用相互隔离的本地 SQLite：

```text
backend/.deer-flow/data/soc_agent_dev.db
backend/.deer-flow/data/soc_agent_stg.db
```

要求：

- DeerFlow `config.yaml` 保持 `database.backend: sqlite`；SOC 根据 `SOC_PINGAN_ENV=dev|stg` 解析为对应独立文件，不需要收集或配置数据库凭证。
- `--database-url` 和 `SOC_DATABASE_URL` 只作为测试隔离或显式覆盖，并继续保持最高优先级。
- 通过 `soc db upgrade` 建表，不与 DeerFlow 通用数据库混用。
- smoke 可使用独立临时 DB，正式 DEV 验收使用上面的固定文件。
- DB、WAL、验收报告和真实 payload 全部 gitignored。
- SQLite 只能证明 DEV 功能链路，不算 PostgreSQL/准生产验收。

## 6. Explicitly Not Collected Now / 当前明确不收集

- Kafka broker、topic、ACL、consumer group、DLQ。
- K8s namespace、镜像仓库、ServiceAccount、资源限制、Ingress。
- PostgreSQL DEV URL、账号、SSL 和 migration 权限。
- EDR 外部进程树查询、HIDS 外部主机上下文查询。
- 封禁 IP、隔离主机、禁用账号等真实写操作接口。
- Prometheus/SLO/生产容量参数。

这些项目保留在长期 PI 路线中，但不阻塞 D12-B 和本轮内网 DEV 验证。

## 7. Collection Package / 建议整理结果

| Artifact | Can leave intranet? | Content |
|---|---|---|
| `pingan-legacy-source-audit.md` | Yes | 已审阅的环境、状态、签名和 safe-path 边界 |
| `pingan-dev-contract.yaml` | Yes | 非敏感能力开关、endpoint path、timeout、错误码、字段语义 |
| `zeus-*-response.redacted.json` | Yes after review | TI/tag/feedback 的脱敏成功、查无和错误响应 |
| `.env.soc-dev.local` / `config.pingan-dev.local` | Out-of-band only | 可包含真实 URL、App ID/App Key、model key 和必要网络配置；必须 Git ignored，可随完整工作目录或受控方式复制到内网；workflow operator 由 Adapter 固定，不在 env 中 |
| `d12b-test-cases.local.yaml` | No | 真实 IP/host/UM、expected outcome/attempt 和 fault-injection 环境变量引用；文件权限 `0600` |
| `direct-provider-cases.json` | No by default | `soc.pingan_asset_case_matrix_report.v1`；只含 query hash、latency、attempt/error 分类，不含 raw query/UM/Provider body/override value |
| `evidence-readback.json` | No by default | `soc.pingan_d12b_evidence_acceptance.v1`；只含 ID/hash/check/error type，证明 MCP/Dispatcher/evidence/shared context 和 Run/Review 不变式，不含 raw lookup/result |
| `model-gateway-smoke.json` | Yes after review | 固定无业务 prompt 的连通性报告；只含模型、状态、latency、token、文本长度/hash，不含 key 或模型原文 |
| `legacy-compat/task-request.local.json` | No | 请求准备器根据一个获批 pending `alert_id` 从 Workbench payload store 自动生成完整旧 task 请求和唯一 session；权限 `0600`，不手工编辑，不进源码包 |
| `legacy-compat/lifecycle-smoke.json` | Yes after review | `soc.pingan_zeus_lifecycle_smoke.v1`；模型调用前只读验证真实签名、业务码和 pending 状态，不含告警 ID/正文或凭证 |
| `legacy-compat/lifecycle-response.local.json` | No | 未知生命周期业务码的显式诊断产物；包含完整 Provider JSON，仅限内网本机、权限 `0600`，不得进 Git/邮件/支持包，也不能替代 bounded smoke 门禁 |
| `legacy-compat/live-acceptance.json` | Yes after review | `soc.pingan_legacy_live_acceptance.v3`；只含请求/结果 hash、任务状态、Runtime/precheck/callback 证明、bounded provider code 和耗时，不含正文或凭证 |

## 8. Implementation Order After Collection / 收集后的实现顺序

```text
DEV profile + no-network preflight (implemented)
    -> project model gateway + durable legacy fake E2E (implemented externally)
    -> loopback gateway -> real EAGW chat.completions smoke (internal execution pending)
    -> read-only ZEUS lifecycle/signature smoke
    -> local 8090 submit/status + real ZEUS precheck + Runtime + callback acceptance
    -> ZEUS-originated submit + old-page readback
    -> old ZEUS page result/status readback
    -> PI-01A threat_intel.ip_reputation.lookup Provider/MCP (implemented externally)
    -> PI-01A DEV Host -> ZEUS PRD hit/not-found/error/timeout + actual field coverage + evidence readback
    -> PI-01B1 security_tag.lookup Provider/MCP (implemented externally)
    -> PI-01B1 DEV Host -> ZEUS PRD entity/validity/scope/error coverage + evidence readback
    -> PI-01B2 authoritative-fact source availability (currently data-gated)
    -> external disposition source contract, if DEV transport/event schema exists (currently data-gated)
    -> PI-01D1 governed planner/service (implemented externally)
    -> PI-01D2 strict config/composition (implemented externally)
    -> PI-01D3 persistent Kafka/batch investigation workflow (implemented externally)
    -> PI-01D4 shadow report/telemetry/addendum boundary (implemented externally)
    -> PI-01E APT/NDR and EDR/HIDS shadow Runtime + Provider + ReviewQueue/Lead Agent review (current)

Parked but still required before Pilot readiness:
    D12-B seven-case direct/MCP/evidence/Web-TUI acceptance (runners implemented)
```

每一步都必须区分 `found`、`not_found`、`failed`，记录 `mocked=false`、环境、延迟、payload/result size 和裁剪状态；任何失败都不得静默回退到 fake provider。
