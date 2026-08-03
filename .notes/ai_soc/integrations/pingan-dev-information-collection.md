# PingAn SOC DEV Information Collection / 平安内网 DEV 信息收集说明

> Scope: `PI-01 / D12-B` and the next read-only PingAn integration slices
> Environment: internal Mac + DEV services
> Database: isolated local SQLite
> Explicitly out of scope: Kafka, K8s, DEV PostgreSQL, endpoint process-tree lookup, host-event-context lookup, and real response actions

## 1. Purpose / 目的

这份文档用于在内网一次性收集 SOC Agent 接入 DEV 所需的信息。外网先完成可移植代码、类型化配置、预检和 smoke 脚本；项目复制到内网 Mac 后，只填本地配置并执行验证，不再来回复制修改业务代码。

收集时分成两类：

- **可提供给外网开发**：模块结构、配置键名、接口路径、请求/响应结构、状态枚举、脱敏样例和失败语义。
- **只能留在内网 Mac**：真实 URL、App Key、Token、账号密码、企业 CA、未脱敏 IP/UM/告警和完整内部响应。

任何 secret 都不要写入本文件、聊天、Git、sample JSON 或 smoke report。

### Quick collection checklist / 本次最小收集包

你下一次只需先带回下面这些非敏感信息，便可开始 D12-B 代码准备：

- [ ] 脱敏的 `root_config`，以及它直接调用的 DEV/STG/PRD 环境选择片段。
- [ ] 选择 DEV 所需的环境变量/配置键名和合法值，不含真实 secret。
- [ ] 内网 Mac 能否 import `util.util_tools`、`util.root_config`、`model.agent_platform.util_tools`；不能时只说明需要加入哪一层父目录，真实绝对路径留在内网。
- [ ] `run_workflow` 的同步/异步返回形态，以及内部包需要的 Python 版本、公司 PyPI 或企业 CA 情况。
- [ ] 确认 DEV ZEUS 需要哪些网络前置条件：代理、CA、客户端证书、IP 白名单；不给真实凭证。
- [ ] 确认是否能在内网准备资产命中、查无、UM fallback、ambiguous、鉴权失败和 timeout 测试 case；真实测试值留在内网。

D12-B 之后再收集：TI 和安全标签的脱敏成功/查无/错误响应，以及可用时的 Zeus 状态理由回流协议。

当前**不需要**收集 Kafka、K8s、PostgreSQL DEV 或真实响应动作信息。DEV 数据库不需要账号密码，项目会从 DeerFlow `database.backend: sqlite` 自动使用独立的 `soc_agent_dev.db`。

## 2. Already Known / 不需要重复收集

以下内容已经从旧 ZEUS 代码确认，除非内网 smoke 证明已变更，否则不再要求手工说明：

| Item | Known contract |
|---|---|
| ZEUS signer | `util.util_tools:isec_sign`，调用形态 `isec_sign(data=..., app_id=..., app_key=...)` |
| Workflow runner | `model.agent_platform.util_tools:run_workflow` |
| Shared ZEUS config | 旧代码通过 `util.root_config` 读取 `ZEUS_SYSTEM_URL`、`ZEUS_APP_ID`、`ZEUS_APP_KEY` |
| Asset API | `POST /public/searchAssetInfo`，签名鉴权，`companyCode` header |
| Asset workflow chain | `searchAssetInfo -> asset_to_bu -> UM`，查无才进入下一步 |
| Workflow IDs | terminal `1087710`、datacenter `1087787`、user `1092332` |
| Workflow app ID | `YHSYS` |
| Threat intelligence | `POST /public/indicatorSearch`，与资产接口共用 ZEUS App ID/App Key |
| Security tags | `POST /public/searchTagContent`，与资产接口共用 ZEUS App ID/App Key |
| Success parsing | `searchAssetInfo` 和 `run_workflow` 的成功响应结构按旧代码实现并做兼容解析 |

SOC Runtime 不实现 `endpoint.process_tree.lookup` 或 `host.event_context.lookup`。进程树、命令行、登录账号和主机事件只使用告警自身携带的 bounded native evidence。

## 3. Priority A - Required Before D12-B / D12-B 前必须收集

### 3.1 DEV environment selection / DEV 环境选择

请提供**脱敏后的** `root_config` 及其直接依赖的环境加载片段，重点回答：

- `util.root_config` 如何选择 `dev/stg/prd`：环境变量、配置文件、配置中心还是不同模块。
- 选择 DEV 所需的键名和合法值，例如 `APP_ENV=dev`；只给键名和示例，不给 secret。
- `isec_sign` 和 `run_workflow` 是否自动继承同一个环境，还是分别配置。
- DEV 配置缺失时抛出的异常类型或表现。
- 是否存在“默认连 PRD”的行为；如有，必须在 SOC preflight 中显式拦截。

期望提供：

```text
root_config.redacted.py
environment-loader.redacted.py        # 仅在 root_config 依赖它时
```

### 3.2 Internal Python availability / 内部 Python 依赖可用性

导入路径已经确定，只需在内网确认：

- 项目虚拟环境能否直接 import `util.util_tools`、`util.root_config` 和 `model.agent_platform.util_tools`。
- 若不能，需加入哪个**父目录**到 `PYTHONPATH`；真实绝对路径只留在内网 `.env`。
- `run_workflow` 是同步函数还是会返回 awaitable。
- 内部包是否需要公司 PyPI、特定 Python 版本或企业 CA。

### 3.3 Local-only ZEUS settings / 只留内网的 ZEUS 配置

下列值只需确认“DEV 可用”，实际值写入内网 `.env.soc-dev.local`：

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

确认内网 DEV 能使用的模型配置：

- DeerFlow `config.yaml` 中的模型名称，默认目标为 `deepseek-v4-flash`。
- Base URL/API key 的本地配置键名；真实值不带出内网。
- 是否需要代理、企业 CA、并发/RPM 限制。
- DEV 是否允许 `SOC_LLM_SENSITIVE_EVIDENCE_MODE=full`；未批准时保持默认脱敏模式。

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
| `root_config.redacted.py` | Yes | DEV/STG/PRD 选择逻辑、键名和模块关系；secret 替换为 `<redacted>` |
| `pingan-dev-contract.yaml` | Yes | 非敏感能力开关、endpoint path、timeout、错误码、字段语义 |
| `zeus-*-response.redacted.json` | Yes after review | TI/tag/feedback 的脱敏成功、查无和错误响应 |
| `.env.soc-dev.local` | No | 真实 URL、App ID/App Key、operator、CA/PYTHONPATH |
| `d12b-test-cases.local.yaml` | No | 真实 IP/host/UM 和 expected result |
| `d12b-smoke-report.local.json` | No by default | 调用结果、latency、大小、attempt/error 分类；先审查再决定是否脱敏带出 |

## 8. Implementation Order After Collection / 收集后的实现顺序

```text
Root config/profile adapter + DEV-only preflight
    -> D12-B direct ZEUS/workflow smoke
    -> asset.locate MCP/action/evidence persistence smoke
    -> real threat_intel.ip_reputation.lookup provider
    -> real security_tag.lookup provider
    -> external disposition source adapter, if DEV transport exists
    -> one real alert end-to-end Runtime + Lead Agent + ReviewQueue review
```

每一步都必须区分 `found`、`not_found`、`failed`，记录 `mocked=false`、环境、延迟、payload/result size 和裁剪状态；任何失败都不得静默回退到 fake provider。
