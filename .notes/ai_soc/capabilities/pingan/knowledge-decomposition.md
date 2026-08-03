# PingAn Knowledge Decomposition

> Updated: 2026-07-07
>
> 目的：把 `.notes/ai_soc/capabilities/pingan/source-docs/` 中沉淀的平安 APT / EDR / HIDS 研判经验，拆成可维护、可审计、可复用的 SOC Agent artifact。核心原则是：不要把历史 prompt 原文直接当 prompt 使用；它们里面混合了通用研判方法、平安环境知识、字段映射、工具调用、处置策略和回归样例。

## 1. 结论

PingAn 文档里的内容要拆成六类：

```text
PingAn docs
  -> generic skill knowledge
  -> tenant/environment memory
  -> adapter/normalizer rules
  -> MCP/action adapter capability
  -> policy/config
  -> eval fixtures
```

只有 **跨客户、跨厂商仍成立的研判方法** 才进入 `skills/public/soc-*`。平安内部环境、组织、账号、白名单、业务规则、处置模板 ID、字段名和历史误报经验，不能进入通用 skill。

## 2. 分类准则

| 问题 | 如果答案是 | 落点 |
|---|---|---|
| 换一家公司是否仍成立？ | 是 | generic SOC skill / domain handler |
| 是否只对平安环境、组织、账号、域名、路径、工具有效？ | 是 | tenant memory / profile / config |
| 是否依赖平安字段名或供应商字段名？ | 是 | PingAn adapter / mapping / normalizer test |
| 是否需要查询外部系统才能确认？ | 是 | read-only MCP/action adapter |
| 是否会改变生产状态？ | 是 | high-risk action adapter + approval |
| 是否只是历史误报/命中模式？ | 是 | memory candidate / eval fixture |
| 是否是阈值、状态映射、模板映射？ | 是 | policy/config，必要时加版本和审批 |

## 3. 不再叫 Prompt 的内容

平安文档中很多“Prompt”实际是 operational knowledge：

| 原文类型 | 例子 | 正确落点 |
|---|---|---|
| 字段说明 | `str_cmd` 是命令行，`detail_process_tree` 是进程树 | PingAn adapter / canonical schema 文档 |
| 环境事实 | 内部域名、内部安全工具、安全组、业务 BU、PA code | `environment_fact` memory / tenant profile |
| 误报模式 | 某组使用某工具、某脚本路径常见误报 | `detection_lesson` 或 `benign_pattern` memory |
| 规则分流 | 某些 `rule_code` 走某流程 | adapter/domain router config，`rule_code` 只是 vendor alias |
| 外部查询 | 资产归属、威胁情报、黑白名单、进程树 | read-only MCP/action adapter |
| 处置动作 | IP 封堵、主机隔离、UM 封禁、转 BU | approval-gated action / external disposition |
| 输出 JSON | `{action, summary, rationale}` | runtime contract / parser / domain schema |

## 4. 现有 SOC Skills 的边界

| Skill | 应保留的通用内容 | 不应写入的内容 |
|---|---|---|
| `soc-alert-triage` | 通用研判循环：事实、冲突、不确定性、verdict、safe next step | 平安状态名、Zeus 流程、特定规则码 |
| `soc-asset-direction` | attacker/victim/target/suppression target 角色判定方法；字段冲突降级原则 | 平安天眼字段名、固定 `sip/dip` 判断、具体内网段 |
| `soc-asset-extraction` | 从 bounded context 抽取 IP/DOMAIN/URL/HOST/USER/UM 等资产；生成 lookup proposal | 平安 BU/PA code 映射表、Zeus 查询方式 |
| `soc-endpoint-triage` | EDR/XDR/endpoint 进程树、命令行、路径、用户、权限、横向移动研判方法 | 平安安全软件路径、特定账号、特定部门 |
| `soc-network-apt-triage` | APT/NDR/NIDS/C2/IOC/HTTP 攻击方向和攻击成功证据 | 天眼具体字段模板、平安内部域名例外 |
| `soc-web-application-triage` | HTTP、WAF/F5、XFF、URI、web attack、代理链和抑制目标判断 | 平安 F5 策略名、内部业务域名白名单 |
| `soc-email-phishing-triage` | 可疑邮件、发件人、投递、正文意图、URL、附件、QR 和用户影响判断 | 平安 VIP/可信发件人、内部邮箱 ID、特定活动例外 |

### HIDS 的处理

短期：HIDS 先复用 `soc-endpoint-triage`，因为 HIDS 仍属于 host / endpoint evidence。

中期：如果 HIDS 经验继续膨胀，新增 `soc-host-hids-triage`，只放通用主机事件研判方法，例如：

- 主机反弹 shell、web command、后门、webshell、暴破、病毒、提权、蜜罐事件的通用判断。
- 进程链、登录用户、命令行、文件路径、主机角色、资产暴露面的通用分析。

不要把“青藤 HIDS 某字段名”“平安某部门例外”“某账号/脚本路径”写进该 skill。

## 5. PingAn 文档到 Artifact 的映射

### APT

| 内容 | 落点 |
|---|---|
| APT 攻击方向、内到外/外到内/内到内/我方攻击互联网的判断方法 | `soc-network-apt-triage` + domain handler |
| 天眼/Zeus 字段方向不可靠，raw message 和五元组优先 | `soc-asset-direction` + PingAn field trust rule |
| `attack_type` 到场景的映射 | adapter/domain router config，不写死通用 skill |
| SQLi/XSS/webshell/文件读取/弱口令等通用攻击成功证据 | `soc-network-apt-triage` / `soc-web-application-triage` |
| URI 含内部业务路径、内部域名、特殊主机例外 | PingAn `environment_fact` / `benign_pattern` memory |
| 威胁情报 IP 评分、黑白名单、渗透测试名单 | read-only MCP/action adapter |
| IP 封堵策略、strategyId、封堵时长 | high-risk action adapter + policy/config |
| APT 仅部分类型允许转生产预警 | tenant policy/config |

### EDR

| 内容 | 落点 |
|---|---|
| 进程树、父子进程、命令行、路径可信度、提权判断 | `soc-endpoint-triage` + domain handler |
| LoginData/System 文件读取分支 | domain handler / deterministic rule，规则码作为 vendor alias |
| 安全路径、不安全路径、用户可写路径概念 | 通用 skill 可以保留“用户可写路径风险更高”；具体路径进 tenant memory/config |
| 普通域用户、外包用户格式、VIP_PCadmin、特定管理员组 | PingAn `environment_fact` / `identity_pattern` memory |
| UM 提取正则和账号封禁 | UM 提取可进 adapter/helper；封禁是 high-risk action |
| 资产归属、BU、PA code | read-only `asset.locate` adapter + tenant memory/config |
| 渗透测试名单检查 | read-only `security_tag.lookup` adapter |
| IP 隔离、UM 封禁 | high-risk action adapter + approval |
| 攻击链报告结构 | report builder / domain report schema |

### HIDS

| 内容 | 落点 |
|---|---|
| 主机可疑操作、后门、反弹 shell、web command、暴破、病毒、提权、蜜罐、webshell 判断方法 | `soc-endpoint-triage`，后续可拆 `soc-host-hids-triage` |
| event_type 到 process part 的映射 | domain router config |
| 内部安全组、特定账号、特定工具、特定脚本路径误报 | PingAn `benign_pattern` / `environment_fact` memory |
| HIDS 主机上下文、进程链、登录用户 | PingAn normalizer + bounded alert-native evidence + endpoint/HIDS skill |
| 主机隔离 | high-risk `endpoint.isolate_host` / `host.isolate_server` action |
| HIDS 0415 版本的新增过程和规则 | versioned memory candidate / eval cases，不直接塞 skill |

## 6. Memory Types For PingAn

PingAn 经验进入 DB memory 时，建议使用这些类型：

| Memory type | 用途 | 示例 |
|---|---|---|
| `procedure` | 通用或半通用 SOP | “攻击方向冲突时先重建角色，不直接相信加工字段” |
| `detection_lesson` | 某类检测/场景的经验 | “HIDS web_command 命中后，应先检查进程链是否为运维脚本” |
| `benign_pattern` | 常见误报模式 | “某内部安全组运行某类扫描工具，历史多为授权行为” |
| `environment_fact` | 环境事实 | “某域名/路径/工具属于公司内部基础设施” |
| `identity_pattern` | 租户账号/身份模式 | “外包账号通常具有 EX- 前缀” |
| `response_policy_hint` | 处置策略候选 | “某场景优先转 BU，而不是封 IP” |
| `negative_memory` | 禁止重复犯错 | “不要把某加工字段直接当攻击方向事实” |

所有 PingAn memory 必须带：

- `tenant_id=pingan` 或明确 tenant scope。
- `source_doc`：APT / EDR / HIDS 文档来源。
- `source_section`：原文节标题或行号范围。
- `status=pending_review` 起步。
- `evidence_refs`：后续需要绑定 run/review/eval case。
- `validity`：环境事实和误报模式必须允许过期。

## 7. MCP / Action Adapter 候选

| Route | 风险等级 | 来源 | 说明 |
|---|---|---|---|
| `asset.locate` | read-only | APT/EDR/HIDS | 查资产归属、BU、owner、环境、重要性 |
| `threat_intel.ip_reputation.lookup` | read-only | APT | 查 IP 情报、威胁标签、时效、地理信息 |
| `security_tag.lookup` | read-only | APT/EDR | 查渗透测试名单、黑白名单、内部授权标签 |
| `disposal.template.lookup` | read-only | APT/EDR/HIDS | 查处置模板候选，不执行 |
| `response.block_ip` | high-risk | APT/EDR | 封堵 IP，必须 approval |
| `endpoint.isolate_host` | high-risk | EDR/HIDS | 隔离终端/主机，必须 approval |
| `account.disable_um` | high-risk | EDR | 禁用 UM/AD/账号，必须 approval |
| `external_case.update` | analyst-write / high-risk by field | Zeus | 回写外部工单状态，必须按字段分级 |

短期 mock：

- `asset.locate` 已有本地 mock。
- `threat_intel.ip_reputation.lookup`、`security_tag.lookup` 已有本地 mock，下一步替换为共用 ZEUS 鉴权的真实 DEV Provider。
- 不建立进程树或主机上下文查询 mock；相应信息使用告警原生证据。

## 8. 主 Agent 提示词只放路由原则

SOC Lead Agent prompt 只应该包含这些稳定规则：

- 使用 normalized alert、field trust、conflict report、correlation result 和 confirmed memory。
- 按 source/domain/entity/conflict 选择 skill。
- EDR/HIDS/host/process/user 走 endpoint/host skill。
- APT/NIDS/NDR/IOC/HTTP 走 network/APT skill。
- WAF/F5/HTTP proxy/XFF 走 web application skill；可疑邮件 typed evidence 走 email phishing skill。
- 资产方向不清楚时走 asset-direction + asset-extraction。
- 需要外部事实时发 read-only action proposal。
- 涉及封禁、隔离、禁用账号、改工单、下策略时发 high-risk/analyst-write proposal。
- 不把 PingAn 文档原文、字段表、内部白名单、模板 ID 常驻 prompt。

## 9. 第一批拆解任务

| 顺序 | 任务 | 输出 |
|---|---|---|
| 1 | 给现有六个 `soc-*` skill 补 Knowledge Boundary | 防止 PingAn 知识继续污染通用 skill |
| 2 | 从 PingAn docs 抽 P0 capability cards | Done：已新增 `capabilities/pingan/capability-cards.md`；`PA-02` APT、`PA-03` EDR、`PA-04` HIDS 已展开 |
| 3 | 设计 `PingAnKnowledgeCandidate` 清单 | Done：已新增 `capabilities/pingan/knowledge-candidates.md`；每条标注 target artifact、tenant scope、source、validity、review owner，默认 `pending_review` |
| 4 | mock read-only adapters | `threat_intel.ip_reputation.lookup`、`security_tag.lookup`；进程/主机上下文仅使用告警原生证据 |
| 5 | 建 eval fixtures | 每类至少 1 条脱敏样例，验证 skill/memory/router 不走偏 |
| 6 | 接 memory candidate | 只写 `pending_review`，不自动 confirmed |

## 10. 验收标准

- 任意一条 PingAn 经验都能说明它属于 skill、tenant memory、adapter、policy、eval 还是 normalizer。
- 通用 skill 中没有 PingAn 特定字段名、内部域名、账号、部门、模板 ID、策略 ID。
- PingAn 专属知识都有 tenant scope、source doc、status、validity 和 evidence refs。
- MCP/action adapter 只通过 `SocActionAdapterRegistry` 进入 runtime。
- Lead Agent 只做路由和 proposal，不直接执行、改判或写 memory。
