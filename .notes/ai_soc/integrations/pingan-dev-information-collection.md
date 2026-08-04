# PingAn SOC DEV Information Collection / 平安内网 DEV 信息收集说明

> Scope: `PI-01 / D12-B` and the next read-only PingAn integration slices
> Environment: internal Mac + DEV services
> Database: isolated local SQLite
> Explicitly out of scope: Kafka, K8s, DEV PostgreSQL, endpoint process-tree lookup, host-event-context lookup, and real response actions

## 1. Purpose / 目的

这份文档用于在内网一次性收集 SOC Agent 接入 DEV 所需的信息。外网先完成可移植代码、类型化配置、预检和 smoke 脚本；项目复制到内网 Mac 后，只填本地配置并执行验证，不再来回复制修改业务代码。

配置分成两类：

- **Tracked / 可提交**：模块结构、配置键名、接口路径、请求/响应结构、状态枚举、脱敏样例和失败语义。
- **Local / 不提交**：真实 URL、App Key、Token、账号密码、企业 CA、IP/UM、测试 case 和完整内部响应。它们可以直接写入 `.env.soc-dev.local`、`config.pingan-dev.local` 及 `.deer-flow/` 验收文件，只要先确认 Git 忽略规则生效。

不要把 secret 写进 tracked 文档、sample 或 commit；本地 runnable config 不必用占位符。

### Quick collection checklist / 本次最小收集包

旧项目源码审计和外网代码准备已经完成。复制到内网后只需补完以下运行输入：

- [x] `root_config`、LOCAL/DEV 环境选择和本地 OpenAI-compatible model endpoint 已从源代码确认。
- [x] ZEUS signer 已提取为本项目内的无旧依赖实现，不再要求 import 整个 `util.util_tools`。
- [ ] 内网 Mac 能否 import `model.agent_platform.util_tools:run_workflow`；不能时把旧 Agent Platform 包父目录写入本地 `SOC_PINGAN_PROVIDER_IMPORT_PATHS`。
- [x] 旧调用方以同步函数使用 `run_workflow(app_id, workflow_id, query_data)`；返回 `dict`、JSON string 或 `None`。
- [ ] 内部包需要的 Python 版本、公司 PyPI 或企业 CA 情况。
- [ ] 确认并配置 DEV ZEUS 网络前置条件：代理、CA、客户端证书、IP 白名单。
- [ ] 确认是否能在内网准备资产命中、查无、UM fallback、ambiguous、鉴权失败和 timeout 测试 case；真实测试值留在内网。

D12-B 之后再收集：TI 和安全标签的脱敏成功/查无/错误响应，以及可用时的 Zeus 状态理由回流协议。

当前**不需要**收集 Kafka、K8s、PostgreSQL DEV 或真实响应动作信息。DEV 数据库不需要账号密码，项目会从 DeerFlow `database.backend: sqlite` 自动使用独立的 `soc_agent_dev.db`。

## 2. Already Known / 不需要重复收集

以下内容已经从旧 ZEUS 代码确认，除非内网 smoke 证明已变更，否则不再要求手工说明：

| Item | Known contract |
|---|---|
| ZEUS signer | 旧协议来自 `util.util_tools:isec_sign`；可移植实现为 `soc_agent.integrations.pingan.zeus_signing:isec_sign` |
| Workflow runner | `model.agent_platform.util_tools:run_workflow` |
| Shared ZEUS config | 旧代码通过 `util.root_config` 读取 `ZEUS_SYSTEM_URL`、`ZEUS_APP_ID`、`ZEUS_APP_KEY` |
| Asset API | `POST /public/searchAssetInfo`，签名鉴权，`companyCode` header |
| Asset workflow chain | `searchAssetInfo -> asset_to_bu -> UM`，查无才进入下一步 |
| Workflow IDs | terminal `1087710`、datacenter `1087787`、user `1092332` |
| Workflow app ID | `YHSYS` |
| Threat intelligence | `POST /public/indicatorSearch`，与资产接口共用 ZEUS App ID/App Key |
| Security tags | `POST /public/searchTagContent`，与资产接口共用 ZEUS App ID/App Key |
| Success parsing | `searchAssetInfo` 和 `run_workflow` 的成功响应结构按旧代码实现并做兼容解析 |
| Local model endpoint | LOCAL profile exposes OpenAI-compatible `http://localhost:4001/v1/`; provider alias is `DeepSeek_V4_Flash` |
| ZEUS status map | `0..10` 对应已忽略、待审阅、退回中、待确认、处理中、待复核、待关闭、子单处理中、子单已关闭、已关闭、编辑 |

SOC Runtime 不实现 `endpoint.process_tree.lookup` 或 `host.event_context.lookup`。进程树、命令行、登录账号和主机事件只使用告警自身携带的 bounded native evidence。

完整源码结论和 safe-path 数据边界见 `pingan-legacy-source-audit.md`。

## 3. Priority A - Required Before D12-B / D12-B 前必须收集

### 3.1 DEV environment selection / DEV 环境选择

该项已完成源码审计：

- 旧模块通过 `env_profile` 选择 profile；本项目 D12-B 强制 `env_profile=LOCAL` 与 `SOC_PINGAN_ENV=dev`。
- DeerFlow 的模型 endpoint、ZEUS endpoint 和 workflow runner 分别显式配置，不依赖旧 `root_config` 的隐式全局读取。
- `soc_pingan_dev_preflight.py` 会在发请求前拒绝未知环境、非 LOCAL profile、非 internal provider、未 allowlist 的 ZEUS host 和非 loopback model endpoint。
- 实际 DEV URL/App ID/App Key 可以直接写入 Git-ignored `.env.soc-dev.local`。

已提供：

```text
backend/samples/pingan_dev/config.example.yaml
backend/samples/pingan_dev/env.example
config.pingan-dev.local               # Git ignored; real values allowed
.env.soc-dev.local                    # Git ignored; real values allowed
```

### 3.2 Internal Python availability / 内部 Python 依赖可用性

签名不再依赖旧项目，只需在内网确认 workflow runner：

- 项目虚拟环境能否 import `model.agent_platform.util_tools:run_workflow`。
- 若不能，需加入哪个**父目录**到 `PYTHONPATH`；真实绝对路径只留在内网 `.env`。
- [x] 旧调用点是同步调用；内网 smoke 再确认安装版本没有发生接口漂移。
- 内部包是否需要公司 PyPI、特定 Python 版本或企业 CA。

### 3.3 Local-only ZEUS settings / 只留内网的 ZEUS 配置

下列实际值直接写入 `.env.soc-dev.local`，该文件必须保持 Git ignored：

```text
SOC_PINGAN_ENV=dev
SOC_PINGAN_ZEUS_BASE_URL=<internal-only>
SOC_PINGAN_ZEUS_APP_ID=<internal-only>
SOC_PINGAN_ZEUS_APP_KEY=<internal-only>
SOC_PINGAN_WORKFLOW_OPERATOR=<internal-only service identity or UM>
```

还需确认：

- DEV 是否要求代理、企业 CA、客户端证书或来源 IP 白名单。
- `companyCode: all` 是否仍允许。
- 请求超时、限流和典型 HTTP/业务错误码。
- Workflow `message.by` 应使用当前用户 UM、固定服务账号还是调用方身份。
- 旧资产归属修正规则是否仍有效；如存在新的 BU/company code override，提供脱敏规则表。

### 3.4 D12-B test matrix / D12-B 测试矩阵

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
- 哪些标签属于授权扫描、红蓝队/护网、白名单、维护窗口或内部安全工具。

标签只形成 governed investigation evidence；不能直接把告警判安全、关闭工单或写 confirmed memory。

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

已确认并提供 `backend/samples/pingan_dev/config.example.yaml`：DeerFlow profile 名为 `deepseek-v4-flash`，向本地 OpenAI-compatible gateway 发送 provider alias `DeepSeek_V4_Flash`。Base URL 和 API key 从 `.env.soc-dev.local` 注入；仍需在内网确认代理/CA、并发/RPM 限制及 `SOC_LLM_SENSITIVE_EVIDENCE_MODE=full` 的使用范围。

### 5.2 SQLite-only DEV database

本阶段不收集 PostgreSQL 信息。内网 DEV 使用独立本地 SQLite：

```text
backend/.deer-flow/data/soc_agent_dev.db
```

要求：

- DeerFlow `config.yaml` 保持 `database.backend: sqlite`；SOC 在没有显式 URL 时自动解析为上述独立文件，不需要收集或配置数据库凭证。
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
| `.env.soc-dev.local` / `config.pingan-dev.local` | Out-of-band only | 可包含真实 URL、App ID/App Key、model key、operator、CA/PYTHONPATH；必须 Git ignored，可随完整工作目录或受控方式复制到内网 |
| `d12b-test-cases.local.yaml` | No | 真实 IP/host/UM 和 expected result |
| `d12b-smoke-report.local.json` | No by default | 调用结果、latency、大小、attempt/error 分类；先审查再决定是否脱敏带出 |

## 8. Implementation Order After Collection / 收集后的实现顺序

```text
DEV profile + no-network preflight (implemented)
    -> D12-B direct ZEUS/workflow smoke (internal DEV)
    -> asset.locate MCP/action/evidence persistence smoke
    -> real threat_intel.ip_reputation.lookup provider
    -> real security_tag.lookup provider
    -> external disposition source adapter, if DEV transport exists
    -> one real alert end-to-end Runtime + Lead Agent + ReviewQueue review
```

每一步都必须区分 `found`、`not_found`、`failed`，记录 `mocked=false`、环境、延迟、payload/result size 和裁剪状态；任何失败都不得静默回退到 fake provider。
