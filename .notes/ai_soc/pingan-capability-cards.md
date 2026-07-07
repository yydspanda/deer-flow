# PingAn SOC Capability Cards

> Updated: 2026-07-07
>
> 目的：把 `pingan_docs/` 中的平安 APT / EDR / HIDS 经验拆成可实现、可评测、可审计的 capability cards。本文档是 `PA-01` 的产物；原始文档仍保留为 source evidence，不直接复制进 public skill、Lead Agent prompt 或 node prompt。

## 1. 使用方式

后续实现平安能力时，先从这里选 card，再进入工程实现：

```text
card
  -> artifact decision: skill / tenant memory / adapter / MCP action / policy / eval / domain handler
  -> implementation slice
  -> smoke/eval/replay
  -> review/staging/active
```

本文档只记录脱敏后的能力边界和验收要求，不保存生产 endpoint、secret、真实白名单、真实策略 ID 的生产值或敏感样本。

## 2. Source Register

| Source ID | 文件 | 覆盖范围 | 初始拆解状态 |
|---|---|---|---|
| `PA-APT-SRC` | `pingan_docs/apt-alert-assess-flow.md` | APT/天眼流量告警、攻击类型 prompt 分流、IP 情报、黑白名单、IP 封堵、FollowUp、攻击链 | PA-02 expanded |
| `PA-EDR-SRC` | `pingan_docs/edr-alert-assess-flow.md` | EDR 终端告警、进程树/路径/提权、UM 提取、渗透测试名单、资产定位、终端隔离、攻击链 | PA-03 expanded |
| `PA-HIDS-SRC` | `pingan_docs/hids-alert-assess-flow.md` | HIDS 主机事件、event_type 分流、主机上下文、误报模式、服务器隔离、FollowUp、攻击链 | PA-04 expanded |

## 3. Card Register

| Card ID | Priority | Source | Capability | Default Artifact | Risk | Status |
|---|---|---|---|---|---|---|
| `PA-COM-001` | P0 | APT/EDR/HIDS | 历史关联预警漏斗判断 | correlation / policy / eval | read-only | Draft |
| `PA-COM-002` | P0 | APT/EDR/HIDS | 资产提取、角色标注与归属定位 | skill + read-only action + evidence | read-only | Draft |
| `PA-COM-003` | P1 | APT/EDR/HIDS | 攻击详情与攻击链报告 | report schema / domain output | read-only | Draft |
| `PA-APT-001` | P0 | APT | APT 攻击方向与受害资产重建 | skill + domain handler + eval | read-only | Expanded |
| `PA-APT-002` | P0 | APT | APT 攻击类型场景化研判 | skill + domain handler | read-only | Expanded |
| `PA-APT-003` | P0 | APT | 威胁情报 IP 信誉查询 | read-only action adapter | read-only | Expanded |
| `PA-APT-004` | P0 | APT/EDR | 渗透测试/白名单/授权标签查询 | read-only action adapter | read-only | Expanded |
| `PA-APT-005` | P2 | APT | IP 封堵候选 | high-risk action proposal + approval policy | high-risk | Boundary defined |
| `PA-EDR-001` | P0 | EDR | EDR 进程树、路径和命令行研判 | skill + domain handler + existing mock evidence | read-only | Expanded |
| `PA-EDR-002` | P1 | EDR | LoginData/System 文件读取分支 | domain handler + policy/eval | read-only | Expanded |
| `PA-EDR-003` | P1 | EDR | 提权行为研判 | skill + domain handler + identity memory candidate | read-only | Expanded |
| `PA-EDR-004` | P1 | EDR | UM/账号提取与身份模式 | entity extraction + tenant memory candidate | read-only | Expanded |
| `PA-EDR-005` | P2 | EDR | UM 封禁与 EDR IP 隔离候选 | high-risk action proposal + approval policy | high-risk | Boundary defined |
| `PA-HIDS-001` | P0 | HIDS | HIDS 主机事件上下文查询 | read-only action adapter | read-only | Expanded |
| `PA-HIDS-002` | P0 | HIDS | HIDS event_type 场景化研判 | endpoint/host skill + domain handler | read-only | Expanded |
| `PA-HIDS-003` | P1 | HIDS | HIDS 误报/授权运维模式沉淀 | tenant memory candidate + eval | read-only | Expanded |
| `PA-HIDS-004` | P2 | HIDS | 服务器隔离候选 | high-risk action proposal + approval policy | high-risk | Boundary defined |
| `PA-RESP-001` | P1 | APT/EDR/HIDS | FollowUp / BU / PA code 处置归属 | policy/config + external disposition sync | analyst-write | Draft |

## 4. P0 Card Details

### PA-COM-001 — 历史关联预警漏斗判断

**Source**

- `PA-APT-SRC`：关联预警半年内、同三级类型，APT 预警默认忽略阈值较高。
- `PA-EDR-SRC`：关联预警状态、忽略理由、最新原因描述用于快速判断。
- `PA-HIDS-SRC`：HIDS 使用更低的默认忽略阈值。

**Scenario**

分析师处理重复或相似预警时，会先看历史处置结论：历史全转交、历史全忽略、近期连续忽略、忽略理由全是误报或规则准确，都会影响当前判断。

**Input**

- canonical detection identity：source、category、rule/vendor alias、canonical detection name。
- history window：默认半年，但实现上必须配置化。
- historical status counts、reason counts、latest reason descriptions。
- external disposition events：后续由 Zeus/外部工单同步进入。

**Output**

- `CorrelationHint` 或 `PolicyHint`：`suggested_verdict`、`confidence`、`matched_history_count`、`reason_summary`。
- 不能直接变成最终 verdict；必须进入 runtime trace / review context。

**Artifact Decision**

- 通用部分：`SocCorrelationService` / policy config。
- 平安状态名和阈值：tenant policy/config，不进 public skill。
- 历史理由文本：memory candidate 或 eval fixture，默认 `pending_review`。

**Failure Modes**

- 外部状态定义不同或客户没有“已忽略/已关闭”。
- 忽略理由混杂，不能直接套阈值。
- 历史样本本身可能错，不能污染 confirmed memory。

**Acceptance**

- 没有 vendor rule code 时仍可用 source/category/entities 做弱关联。
- 输出必须解释匹配了哪些历史维度。
- 不调用 LLM，不调用 MCP，不执行 action。

### PA-COM-002 — 资产提取、角色标注与归属定位

**Source**

- APT/EDR/HIDS 都依赖资产定位、角色分配和 BU/PA code 兜底分单。
- 旧流程使用 AssetExtractor、资产查询、asset_to_bu 和 UM 兜底。

**Scenario**

分析师需要知道谁是 attacker、target/victim、suppression target，以及最终应该转给哪个 BU 或 owner。

**Input**

- normalized alert entities：IP、DOMAIN、URL、HOST、USER、UM-like account、process、file。
- role hints：source fields、direction、field trust、conflict report。
- optional tenant mappings：BU/PA code、asset owner、environment、criticality。

**Output**

- `role_assignments`：attacker / target / victim / related。
- `disposal_target`：attacker / target / unknown。
- `asset.locate` read-only evidence：company/owner/business group/environment/criticality。

**Artifact Decision**

- 通用提取方法：`soc-asset-extraction`、`soc-asset-direction`。
- 资产归属查询：read-only action adapter，结果写 `InvestigationEvidence`。
- 平安 BU/PA code 映射：tenant config/memory candidate，不进 public skill。

**Failure Modes**

- 上游方向字段反了。
- 只有 host 或账号，没有 IP。
- 资产查询多结果或查不到。
- `disposal_target="-"` 时不得 fallback 执行高风险处置。

**Acceptance**

- 能生成 read-only `asset.locate` proposal。
- 查询结果只作为 evidence，不直接改 verdict。
- 查不到时 review context 明确显示 unknown，而不是编造归属。

### PA-APT-001 — APT 攻击方向与受害资产重建

**Source**

- `PA-APT-SRC` 中 APT 事件、代理工具、挖矿病毒、弱口令、Web 攻击等场景都依赖方向判断。
- 同事会议反馈指出天眼/Zeus 字段中攻击方/受害方可能反向或冲突。

**Scenario**

APT 流量告警必须先判断是外到内攻击、内到外反连、内到内异常，还是我方资产访问互联网非平台服务。方向错会导致资产查询对象、抑制目标和处置建议全部错。

**Input**

- raw message 优先重建五元组、HTTP request/response、host、uri、agent、packet_data。
- field trust/conflict report。
- internal network/environment profile，必须 tenant-scoped。
- attack_type / detection category 作为 vendor alias，而不是必需字段。

**Output**

- `direction_finding`：direction、attacker candidate、victim candidate、confidence、conflict notes。
- `evidence_refs`：使用了哪些 raw message/entity/field trust。
- `recommended_next_actions`：是否需要 asset.locate、threat_intel、security_tag 查询。

**Artifact Decision**

- 通用方法进入 `soc-network-apt-triage` 和 domain handler。
- 平安网段、内部域名、字段别名进入 tenant profile/adapter/eval。
- 方向冲突样例进入 eval fixture。

**Failure Modes**

- 缺 raw message，只能 fallback 加工字段。
- SIP/DIP 字段语义不可靠。
- NAT、代理、XFF、反向连接导致表面方向反转。

**Acceptance**

- 明确输出“不确定”而不是强行定方向。
- 方向依据必须能在 evidence/trace 中回看。
- 不因缺少 rule_code 失败。

### PA-APT-002 — APT 攻击类型场景化研判

**Source**

- `PA-APT-SRC` 有弱口令、命令执行、文件读取、目录遍历、SQL 注入、未授权访问、webshell、代理工具、黑市工具、挖矿病毒等场景。

**Scenario**

APT 不是一个单一判断逻辑。不同攻击类型关注的证据不同：响应状态、返回内容、payload、敏感路径、工具特征、攻击是否成功。

**Input**

- attack_type / rule name / canonical detection。
- HTTP fields：uri、method、req/rsp headers/body/status、agent、host。
- packet_data / DNS repeat count / response body snippets。
- historical/correlation hints。

**Output**

- `domain_finding`：attack_type、success_likelihood、impact_asset、evidence_summary、uncertainty。
- `recommended_queries`：threat_intel、security_tag、asset.locate。

**Artifact Decision**

- 跨客户攻击成功证据进入 `soc-network-apt-triage` / `soc-waf-f5-triage`。
- 平安 URI 例外、内部 host、特定忽略路径进入 tenant memory/eval。
- 23 种原 prompt 不整体迁移；只抽通用步骤。

**Failure Modes**

- 业务健康检查/GPT/iobs 等平安例外不能进 public skill。
- HTTP body 过长或被截断。
- 攻击 payload 存在但未成功。

**Acceptance**

- 每个 finding 必须区分“攻击尝试”和“攻击成功证据”。
- 特定平安例外只能以 tenant-scoped evidence/memory candidate 出现。

### PA-APT-003 — 威胁情报 IP 信誉查询

**Source**

- `PA-APT-SRC` 的 ZEUS IP 情报查询、IPScorer、威胁标签和时效衰减。

**Scenario**

APT/外联/扫描类告警需要查询 IP 情报，但情报只能作为 evidence，不能单独决定最终 verdict 或自动封堵。

**Input**

- IP entity，通常来自 attacker candidate 或 IOC。
- alert time / tenant scope。
- optional source context：APT、EDR、NIDS、WAF。

**Output**

- `threat_intel.ip_reputation.lookup` evidence：labels、confidence、last_seen、geo、source、score、expiry。
- `risk_interpretation`：why risky / why stale / why whitelisted。

**Artifact Decision**

- read-only action adapter，短期 mock，未来 MCP/API。
- IP scoring 策略作为 policy/config，不能硬编码进 skill。

**Failure Modes**

- 情报过期。
- CDN、云厂商、移动网络、白名单导致误判。
- 多 IP 只查第一个会丢信息；adapter 必须支持 batch 或明确裁剪。

**Acceptance**

- adapter 通过 `SocActionAdapterRegistry` 进入 runtime。
- 结果写 `InvestigationEvidence`。
- 不直接触发 `response.block_ip`。

### PA-APT-004 — 渗透测试/白名单/授权标签查询

**Source**

- `PA-APT-SRC` 和 `PA-EDR-SRC` 都使用黑白名单/渗透测试名单判断是否关闭或降级。

**Scenario**

命中攻击特征不等于真实恶意。安全团队演练、授权扫描、白名单工具、内部测试可能产生高噪声告警。

**Input**

- entities：IP、HOST、DOMAIN、USER/UM、tool name、process name。
- label query type：pentest、allowlist、authorized_scan、security_team、maintenance。
- alert time，用于判断标签有效期。

**Output**

- `security_tag.lookup` evidence：found、is_valid、has_active、labels、matched_entities、valid_until、source。

**Artifact Decision**

- read-only action adapter，短期 mock。
- 平安特定名单和值进入 tenant config/memory candidate。
- 授权标签命中后只生成 `benign_pattern` 或 review hint，不自动 confirmed。

**Failure Modes**

- 名单过期。
- 匹配到历史记录但不在有效期。
- 同一实体同时有 allowlist 和 malicious intel。

**Acceptance**

- evidence 必须保留有效期和来源。
- 命中授权标签时，不自动关闭；只作为 review/context hint，除非后续 policy 显式允许。

### PA-EDR-001 — EDR 进程树、路径和命令行研判

**Source**

- `PA-EDR-SRC` 的通用 LLM 分支、路径安全分流、进程链、命令行、父进程/祖先进程。

**Scenario**

EDR 告警主要靠 process tree、command line、path trust、user privilege 判断是否为恶意执行、提权、凭证访问、横向移动或误报。

**Input**

- process tree：process、parent、ancestor、path、cmd、user、hash。
- path classification：system path、program files、user writable、unknown。
- account context：user、UM-like account、privilege。
- existing evidence：`endpoint.process_tree.lookup`。

**Output**

- `endpoint_finding`：suspicious_process、suspicious_cmd、path_risk、user_risk、confidence。
- `recommended_queries`：asset.locate、security_tag、host/event context。

**Artifact Decision**

- 通用研判方法进入 `soc-endpoint-triage`。
- 平安安全软件路径、部门、账号例外进入 tenant memory/config/eval。
- 进程树查询复用 existing `endpoint.process_tree.lookup` mock，未来真实 EDR MCP/API。

**Failure Modes**

- 路径看似安全但命令行恶意。
- 文档后缀与可执行脚本后缀的概率规则不能跨客户硬编码。
- LLM 提取路径可能错，必须保留原字段 evidence。

**Acceptance**

- 输出必须列出具体进程链证据。
- path safe 只能降低风险，不能单独判忽略。
- 平安特定安全路径不得进入 public skill。

### PA-HIDS-001 — HIDS 主机事件上下文查询

**Source**

- `PA-HIDS-SRC` 的 host_name、internal_ip、event_type、detail_process_tree、detail_login_user、detail_cmd、detail_src_ip 等字段。

**Scenario**

HIDS 告警误报和真实入侵都高度依赖主机上下文：谁登录、从哪里登录、执行了什么、进程链是否完整、是否属于运维/安全测试。

**Input**

- host entity：hostname/internal_ip。
- event time window。
- event_type / event_level。
- optional user/process/entity hints。

**Output**

- `host.event_context.lookup` evidence：recent logins、process context、related commands、source IPs、host criticality、event summary。

**Artifact Decision**

- read-only action adapter，短期 mock。
- 通用 HIDS 方法先复用 `soc-endpoint-triage`；若 HIDS 卡片继续膨胀，再新增 `soc-host-hids-triage`。
- 平安机房、域名、组名、具体误报例外进 tenant memory/eval。

**Failure Modes**

- 主机事件缺失或时间不同步。
- HIDS event_type 粒度不一致。
- 内部运维行为和攻击工具行为相似。

**Acceptance**

- 查不到主机上下文时返回 failed/empty evidence，不编造。
- evidence 不直接关闭告警。
- 能被 Lead Agent review context 复用。

### PA-HIDS-002 — HIDS event_type 场景化研判

**Source**

- `PA-HIDS-SRC` 的 malic_opera、backdoor_diagnose、bounce_shell、web_command、bruteforce、anti_virus_detect、privilege_escalation、honeypot、webshell 等流程。

**Scenario**

不同 HIDS event_type 关注字段和误报模式不同，必须先场景化，再综合判断。

**Input**

- event_type、event_level、event_content。
- detail fields：src_ip、login_user、uname、cmd、process_tree、rule names、backdoor type。
- host.event_context evidence。

**Output**

- `host_finding`：event_family、malicious_indicators、benign_indicators、uncertainty、recommended_next_actions。

**Artifact Decision**

- 通用判断方法进入 endpoint/host skill 和 domain handler。
- 具体平安组名、账号、路径、工具、域名例外进入 tenant memory/eval。
- 0415 版本规则作为 versioned knowledge candidates，不整体复制 prompt。

**Failure Modes**

- `event_type=bruteforce_inter` 在平安常为运维，但其他客户不一定。
- 命令中有测试关键字不代表安全。
- 安全团队工具与攻击工具可能重叠。

**Acceptance**

- finding 必须分开列 malicious indicators 和 benign indicators。
- 平安特定忽略规则默认是 candidate，不是 confirmed global truth。

## 5. PA-02 APT Source Decomposition

本节是 `PA-02` 的产物：只拆 `PA-APT-SRC`，不把原 APT prompt 整体迁移到 skill，也不提前实现 MCP/mock adapter。

### 5.1 APT Artifact Split

| APT source content | Target artifact | 处理原则 |
|---|---|---|
| 攻击方向、受害资产、抑制目标判断方法 | `soc-network-apt-triage` + domain handler + eval | 只保留通用角色重建方法；平安网段、字段别名、内部域名进入 tenant artifact |
| 弱口令、命令执行、文件读取、SQL 注入、webshell、代理工具等攻击成功证据 | `soc-network-apt-triage` / `soc-waf-f5-triage` + APT domain findings | 抽取“攻击尝试 vs 攻击成功”通用证据，不复制 23 个 prompt |
| `attack_type` / 三级类型 / rule-like identifier 分流 | adapter/domain router config | 作为 vendor alias 和路由提示；不能成为必需主键 |
| raw message、HTTP 五元组、请求/响应、packet_data 优先级 | PingAn adapter + field trust eval | 原始 message 优先；缺失才 fallback 加工字段，并记录低可信 |
| URI、host、内部业务路径、内部系统例外 | tenant memory/config + eval | 只作为 PingAn tenant candidate，不进入 public skill |
| IP 情报、威胁标签、地理、时效 | read-only `threat_intel.ip_reputation.lookup` | 查询结果进入 `InvestigationEvidence`，不直接封堵或改判 |
| 渗透测试/白名单/授权扫描 | read-only `security_tag.lookup` | 命中只作为 review hint / evidence；confirmed policy 后才能自动降级 |
| IP 封堵时长、策略映射、风险阈值 | high-risk action proposal + approval policy | 只生成候选，不执行；真实策略 ID 不写入 public code |
| APT FollowUp / 转 BU 条件 | policy/config + external disposition sync | 不写进 skill；后续由外部处置同步和 tenant policy 管理 |
| 攻击链/攻击详情 Markdown | report schema / domain output | 后续统一到 `UnifiedInvestigationReport`，不让自由 Markdown 成为协议 |

### 5.2 What Goes Into Public Skills

APT 相关 public skill 只能增加这些通用方法：

- 先重建事实和方向，再判断受害资产和处置目标。
- 区分攻击尝试、攻击命中、攻击成功、影响已发生。
- HTTP 类攻击要同时看 request payload、response status、response body、host/uri、user-agent 和上下文。
- 文件读取、目录遍历、敏感信息泄露要寻找“敏感文件内容实际返回”的证据。
- 命令执行、代码执行要寻找命令参数、执行输出、系统信息回显或副作用证据。
- webshell 上传/利用要区分上传请求、执行请求、响应回显和后续连接。
- 扫描/弱口令/暴力破解要区分单次探测、自动化扫描、认证成功迹象和业务正常登录。
- 如果字段冲突或证据不足，输出 uncertainty 和 recommended read-only queries，而不是强判。

APT public skill 不能包含：

- 平安内部 host、URI、业务系统名、内部域名、网段、部门、工具名。
- 平安 `attack_type` 到处置模板的固定映射。
- 平安封堵策略 ID、operateType、模板 ID、BU/PA code。
- “某路径/某 host/某业务一定忽略”这类环境事实。

### 5.3 APT Domain Handler Contract Draft

后续 APT domain handler 的输入输出先按这个边界设计：

```text
AptTriageRequest
  alert_ref
  normalized_entities
  field_trust_report
  conflict_report
  raw_message_excerpt
  http_context
  detection_aliases
  existing_evidence_refs
  confirmed_memory_refs

AptTriageResult
  direction_finding
  attack_findings[]
  success_likelihood
  impacted_assets[]
  recommended_readonly_actions[]
  recommended_high_risk_actions[]
  uncertainty
  evidence_refs[]
```

约束：

- domain handler 不直接调用 MCP，不直接写 DB，不直接改 verdict。
- `recommended_readonly_actions` 只能输出 action proposal，例如 `threat_intel.ip_reputation.lookup`、`security_tag.lookup`、`asset.locate`。
- `recommended_high_risk_actions` 只能生成 approval-gated proposal，例如 `response.block_ip`。
- result 后续由 Main SOC Agent / Runtime 合并，不由 APT handler 单独决定关闭或转交。

### 5.4 APT Read-Only Action Candidates

| Action | Input | Output evidence | Trigger condition | Failure behavior |
|---|---|---|---|---|
| `threat_intel.ip_reputation.lookup` | IP list, alert time, source context | labels, confidence, last_seen, geo, source, score, expiry | attacker candidate / IOC / external IP 需要确认信誉 | failed/empty evidence；不得编造标签 |
| `security_tag.lookup` | entity list, tag types, alert time | found, is_valid, has_active, matched_entities, labels, valid_until | 需要排除授权扫描、渗透测试、白名单、内部安全团队活动 | 只返回 evidence；不得自动关闭 |
| `asset.locate` | IP/DOMAIN/WEB/HOST candidates, role hints | owner, business group, environment, criticality, source | 方向/受害资产明确后定位归属和 review context | 多结果要标记 ambiguity；查不到不能 fallback 高风险处置 |

这些 action 后续可以由 MCP-backed adapter、HTTP adapter、DB adapter 或 mock adapter 实现。SOC Runtime 只认 action contract，不认底层连接方式。

### 5.5 APT High-Risk Action Boundary

`PA-APT-005` 暂不实现真实执行，只定义边界：

```text
response.block_ip proposal
  route: response.block_ip
  risk_level: high-risk
  source: APT domain handler / Lead Agent proposal
  required_context:
    - attacker IP evidence
    - direction finding
    - threat intel evidence
    - security tag evidence or explicit skip reason
    - approval actor
    - idempotency key
```

禁止事项：

- 不根据 threat intel score 自动封堵。
- 不把策略 ID、模板 ID、operateType 写进 public skill 或 Lead Agent prompt。
- 不允许 Lead Agent 直接调用 MCP 或外部 SOAR 执行封堵。
- 未命中 security tag 不等于允许封堵；仍必须有人类审批。

### 5.6 APT Tenant Memory Candidates

这些内容只进入 PingAn tenant memory/config/eval，初始状态必须是 `pending_review`：

| Candidate type | 来源内容 | 用途 |
|---|---|---|
| `environment_fact` | 内部域名、内部业务 host、内部网段、业务路径、内部安全工具 | 降低误报，但必须带有效期和来源 |
| `benign_pattern` | 健康检查、授权扫描、内部测试、固定业务路径触发的常见误报 | 作为 review hint，不自动 confirmed |
| `detection_lesson` | 某类 APT 攻击类型在特定场景下如何判断成功/失败 | 后续帮助 domain handler/eval，不直接改判 |
| `response_policy_hint` | 某类 APT 告警是否适合转 BU、是否只生成建议、是否考虑封堵 | 只作为 policy candidate，需要审批 |
| `negative_memory` | 不要相信加工后的方向字段、不要只看单个 IP 情报标签 | 防止重复犯错 |

### 5.7 APT Eval Fixture Candidates

PA-02 后续至少需要这些脱敏 fixture：

| Fixture ID | 覆盖 card | 输入特征 | 期望 |
|---|---|---|---|
| `apt_direction_conflict_001` | `PA-APT-001` | raw message 与加工方向字段冲突 | 输出 direction uncertainty + conflict report，不强行封堵 |
| `apt_web_attack_success_001` | `PA-APT-002` | HTTP payload + response body 有成功证据 | 输出 attack success finding，并建议 asset/threat intel 查询 |
| `apt_probe_failed_001` | `PA-APT-002` | payload 存在但 response status/body 不支持成功 | 输出 attempt/failed，不转成高风险动作 |
| `apt_threat_intel_stale_001` | `PA-APT-003` | IP 有旧情报但已过期 | evidence 标记 stale，不触发封堵 |
| `apt_security_tag_active_001` | `PA-APT-004` | attacker candidate 命中有效授权标签 | 输出 benign/authorized hint，仍需 review/policy 决定 |

### 5.8 PA-02 Done Definition

`PA-02` 完成标准：

- `PA-APT-001..004` 已展开到 source、输入、输出、artifact decision、failure、acceptance。
- `PA-APT-005` 已定义 high-risk 边界，但不实现执行。
- 已明确哪些内容进 public skill，哪些进 tenant memory/config/eval，哪些进 read-only action。
- 下一步可以安全进入 `PA-03` EDR source decomposition，或开始实现 APT eval/mock 的最小切片。

## 6. PA-03 EDR Source Decomposition

本节是 `PA-03` 的产物：只拆 `PA-EDR-SRC`，不把原 EDR prompt 整体迁移到 skill，也不提前实现真实 EDR MCP/API。

### 6.1 EDR Artifact Split

| EDR source content | Target artifact | 处理原则 |
|---|---|---|
| 进程树、父子进程、祖先进程、命令行、路径可信度 | `soc-endpoint-triage` + EDR domain handler + eval | 只保留通用 endpoint 方法；平安安全路径、特定组/账号例外进入 tenant artifact |
| LoginData/System 文件读取类规则 | EDR domain handler + deterministic policy/eval | rule_code 只是 vendor alias；规则分流必须可配置，不能作为 core 必需字段 |
| 提权类规则和管理员组判断 | `soc-endpoint-triage` + identity memory candidate + eval | 通用提权方法进 skill；平安 VIP/admin group 例外进 tenant memory/config |
| 路径提取与安全路径判断 | entity extraction + field trust + eval | LLM/规则提取结果必须保留原字段 evidence；安全路径只能降低风险，不能单独关闭 |
| UM/账号格式、普通域用户、外包账号 | entity extraction + `identity_pattern` memory candidate | 通用抽取“enterprise account / UM-like account”；平安格式只进 tenant memory |
| 渗透测试名单检查 | read-only `security_tag.lookup` | 查询结果进入 evidence；不自动关闭，除非后续 tenant policy 显式允许 |
| 资产归属、BU、PA code、owner | read-only `asset.locate` + tenant config/memory | 归属结果写 `InvestigationEvidence`，不直接改 verdict |
| UM 封禁、EDR IP 隔离 | high-risk action proposal + approval policy | 只定义候选和审批边界；不执行真实封禁/隔离 |
| 攻击链报告 | report schema / domain output | 后续统一到 `UnifiedInvestigationReport`，不让 free-form Markdown 成为协议 |

### 6.2 What Goes Into Public Skills

EDR 相关 public skill 只能增加这些通用方法：

- 从 process tree 重建执行链：process、parent、ancestor、user、path、cmd、hash。
- 区分“路径可信”“命令行可信”“父进程可信”“用户权限可信”，不能只看单一维度。
- 用户可写目录、临时目录、下载目录、脚本解释器、Office 子进程、浏览器下载执行等通常需要更高关注。
- 提权研判要看操作者、目标权限、组变更、执行上下文、是否符合授权运维。
- 凭证/浏览器数据/系统敏感文件读取要区分正常软件访问、可疑批量读取、恶意工具访问。
- 如果证据不足，输出 uncertainty 和 recommended read-only queries，例如 `endpoint.process_tree.lookup`、`asset.locate`、`security_tag.lookup`。

EDR public skill 不能包含：

- 平安安全软件路径清单、内部部门、具体管理员组、特定 UM/外包账号格式。
- 平安 rule_code 到分支流程的硬编码映射。
- 平安 BU/PA code、Zeus 处置模板、operateType、封禁账号类型。
- “某组/某工具/某路径一定忽略”这类环境事实。

### 6.3 EDR Domain Handler Contract Draft

后续 EDR domain handler 的输入输出先按这个边界设计：

```text
EdrTriageRequest
  alert_ref
  normalized_entities
  field_trust_report
  conflict_report
  process_context
  detection_aliases
  existing_evidence_refs
  confirmed_memory_refs

EdrTriageResult
  endpoint_findings[]
  path_risk
  command_line_risk
  account_risk
  privilege_risk
  recommended_readonly_actions[]
  recommended_high_risk_actions[]
  uncertainty
  evidence_refs[]
```

约束：

- domain handler 不直接调用 EDR MCP，不直接写 DB，不直接改 verdict。
- `recommended_readonly_actions` 只能输出 action proposal，例如 `endpoint.process_tree.lookup`、`asset.locate`、`security_tag.lookup`。
- `recommended_high_risk_actions` 只能生成 approval-gated proposal，例如 `account.disable_um`、`endpoint.isolate_host`、`endpoint.isolate_ip`。
- result 后续由 Main SOC Agent / Runtime 合并，不由 EDR handler 单独决定关闭或转交。

### 6.4 EDR Read-Only Action Candidates

| Action | Input | Output evidence | Trigger condition | Failure behavior |
|---|---|---|---|---|
| `endpoint.process_tree.lookup` | host/agent/process identifiers, event time | process tree, parent/ancestor, cmd, user, hash, network hints | 原始告警缺进程链或进程链不完整 | failed/empty evidence；不得编造进程链 |
| `asset.locate` | IP/HOST/UM/account candidates, role hints | owner, business group, environment, criticality, source | 需要定位受害资产、分单 BU 或处置归属 | 多结果标记 ambiguity；查不到不阻塞 review |
| `security_tag.lookup` | IP/HOST/USER/tool/process, tag types, alert time | authorized_scan, pentest, allowlist, security_team, valid_until | 需要排除授权测试、安全团队工具、白名单行为 | 只返回 evidence；不得自动关闭 |
| `host.event_context.lookup` | host, event time, user/process hints | login context, related commands, recent events | EDR 告警需要补充主机上下文时 | 查不到返回 empty evidence；不升级风险 |

### 6.5 EDR High-Risk Action Boundary

`PA-EDR-005` 暂不实现真实执行，只定义边界：

```text
account.disable_um proposal
  route: account.disable_um
  risk_level: high-risk
  required_context:
    - account extraction evidence
    - endpoint finding
    - impacted user/host evidence
    - approval actor
    - idempotency key

endpoint.isolate_host / endpoint.isolate_ip proposal
  route: endpoint.isolate_host or endpoint.isolate_ip
  risk_level: high-risk
  required_context:
    - disposal_target finding
    - asset locate evidence
    - process tree evidence
    - security tag evidence or explicit skip reason
    - approval actor
    - idempotency key
```

禁止事项：

- 不根据单条 EDR finding 自动封禁账号或隔离终端。
- 不把 AD/UM/快乐平安等平安账号系统细节写进 public skill。
- 不允许 Lead Agent 直接调用 EDR MCP 或外部 SOAR 执行隔离。
- `disposal_target="-"` 或角色不清时不得 fallback 执行高风险动作。

### 6.6 EDR Tenant Memory Candidates

这些内容只进入 PingAn tenant memory/config/eval，初始状态必须是 `pending_review`：

| Candidate type | 来源内容 | 用途 |
|---|---|---|
| `environment_fact` | 平安安全软件路径、内部安全工具、内部管理员组、工作时间习惯 | 降低误报，但必须带有效期和来源 |
| `identity_pattern` | 普通域用户、外包账号、UM-like account 格式 | 辅助账号抽取和身份解释，不作为全局规则 |
| `benign_pattern` | 内部运维、安全测试、授权工具触发的 EDR 常见误报 | 作为 review hint，不自动 confirmed |
| `detection_lesson` | LoginData/System、提权、可疑脚本、路径风险等场景经验 | 帮助 domain handler/eval，不直接改判 |
| `response_policy_hint` | 账号封禁、IP/主机隔离、转 BU 的倾向 | 只作为 policy candidate，需要审批 |
| `negative_memory` | 不要只看路径安全、不因一个账号格式就判定正常 | 防止重复犯错 |

### 6.7 EDR Eval Fixture Candidates

PA-03 后续至少需要这些脱敏 fixture：

| Fixture ID | 覆盖 card | 输入特征 | 期望 |
|---|---|---|---|
| `edr_process_tree_suspicious_001` | `PA-EDR-001` | 用户可写路径 + 可疑父进程 + 可疑命令行 | 输出 endpoint finding，并建议 process tree / asset 查询 |
| `edr_path_safe_cmd_risky_001` | `PA-EDR-001` | 路径看似安全但命令行有高危行为 | 不因路径安全直接忽略，输出 uncertainty/risk |
| `edr_logindata_branch_001` | `PA-EDR-002` | LoginData/System 类检测，路径和上下文可解释 | 输出 branch finding，不依赖硬编码 rule_code |
| `edr_privilege_escalation_001` | `PA-EDR-003` | 添加管理员组或提权操作 | 区分授权运维 candidate 与高风险提权 evidence |
| `edr_um_extract_001` | `PA-EDR-004` | 多处字段出现 UM-like account | 输出 account candidates + confidence，不自动封禁 |
| `edr_security_tag_active_001` | `PA-APT-004` / `PA-EDR-001` | 实体命中有效授权标签 | 输出 authorized hint，仍需 review/policy 决定 |

### 6.8 PA-03 Done Definition

`PA-03` 完成标准：

- `PA-EDR-001..004` 已展开到 source、输入、输出、artifact decision、failure、acceptance。
- `PA-EDR-005` 已定义 high-risk 边界，但不实现执行。
- 已明确哪些内容进 public skill，哪些进 tenant memory/config/eval，哪些进 read-only action。
- 下一步可以安全进入 `PA-04` HIDS source decomposition，或开始实现 EDR eval/mock 的最小切片。

## 7. PA-04 HIDS Source Decomposition

本节是 `PA-04` 的产物：只拆 `PA-HIDS-SRC`，不把青藤/HIDS 原 prompt 整体迁移到 skill，也不提前实现真实 HIDS MCP/API。

### 7.1 HIDS Artifact Split

| HIDS source content | Target artifact | 处理原则 |
|---|---|---|
| host_name、internal_ip、event_type、event_level、event_content、detail_* 字段 | PingAn adapter + canonical host/process/event context | 字段名只进 adapter/mapping/eval；core 和 public skill 消费 canonical entities/evidence |
| HIDS event_type 分流 | domain router config + HIDS domain handler | event_type 作为 vendor alias / scenario facet；不能成为 core 必需字段 |
| 可疑操作、后门、反弹 shell、web command、暴破、病毒、提权、蜜罐、webshell 通用研判 | `soc-endpoint-triage` / future `soc-host-hids-triage` + domain handler | 抽取通用主机行为方法；不复制 0415 prompt |
| 进程链、登录用户、来源 IP、执行命令、主机事件上下文 | read-only `host.event_context.lookup` + evidence | 查询结果写 `InvestigationEvidence`，不直接关闭或转交 |
| 内部组名、账号、脚本路径、机房、内部域名、内部安全工具 | tenant memory/config + eval | 只作为 PingAn tenant candidate，必须 pending review / validity |
| `bruteforce_inter` 等“平安场景常忽略”规则 | tenant `benign_pattern` / policy candidate | 不能全局化；其他客户内网暴破可能高危 |
| 服务器隔离 operateType/templateId/请求体 | high-risk action proposal + approval policy | 只定义候选边界；真实模板和 ID 不写入 public skill |
| FollowUp / BU / PA code / 兜底分单 | policy/config + external disposition sync | 不进 skill；由 tenant policy 和 external disposition 管理 |
| 攻击链/攻击详情 Markdown | report schema / domain output | 后续统一到 `UnifiedInvestigationReport` |

### 7.2 What Goes Into Public Skills

HIDS 相关 public skill 只能增加这些通用方法：

- 从主机事件重建“谁在什么时间、从哪里、用什么账号、通过什么进程链、执行了什么命令”。
- 反弹 shell / web command / webshell / 后门 / 提权等事件要优先看进程链、命令行、父进程、用户权限、来源 IP、目标主机角色。
- 区分 malicious indicators 和 benign indicators，不能因为某个内部标签或测试关键字直接忽略。
- 内网暴破、运维工具、健康检查、安全测试都只能作为候选解释，需要 tenant evidence 或 security tag 支撑。
- 如果 evidence 不足，输出 uncertainty 和 recommended read-only queries，例如 `host.event_context.lookup`、`asset.locate`、`security_tag.lookup`。

HIDS public skill 不能包含：

- 平安机房、内部域名、内部网段、组名、账号、脚本路径、工具名。
- 青藤 event_type 到忽略/转交的硬编码结论。
- 平安服务器隔离 operateType、templateId、Zeus 请求体。
- “某部门/某账号/某脚本一定忽略”这类环境事实。

### 7.3 HIDS Domain Handler Contract Draft

后续 HIDS domain handler 的输入输出先按这个边界设计：

```text
HidsTriageRequest
  alert_ref
  normalized_entities
  field_trust_report
  conflict_report
  host_event_context
  detection_aliases
  existing_evidence_refs
  confirmed_memory_refs

HidsTriageResult
  host_findings[]
  event_family
  malicious_indicators[]
  benign_indicators[]
  host_impact
  recommended_readonly_actions[]
  recommended_high_risk_actions[]
  uncertainty
  evidence_refs[]
```

约束：

- domain handler 不直接调用 HIDS MCP，不直接写 DB，不直接改 verdict。
- `recommended_readonly_actions` 只能输出 action proposal，例如 `host.event_context.lookup`、`asset.locate`、`security_tag.lookup`。
- `recommended_high_risk_actions` 只能生成 approval-gated proposal，例如 `host.isolate_server` 或 `endpoint.isolate_host`。
- result 后续由 Main SOC Agent / Runtime 合并，不由 HIDS handler 单独决定关闭或转交。

### 7.4 HIDS Read-Only Action Candidates

| Action | Input | Output evidence | Trigger condition | Failure behavior |
|---|---|---|---|---|
| `host.event_context.lookup` | host/internal_ip, event time window, user/process hints | recent logins, process tree/context, commands, source IPs, related host events | 原始 HIDS 告警缺上下文或需要确认登录/进程链 | failed/empty evidence；不得编造主机上下文 |
| `asset.locate` | HOST/IP candidates, role hints | owner, business group, environment, criticality, source | 需要定位主机归属、环境或隔离影响面 | 多结果标记 ambiguity；查不到不执行隔离 |
| `security_tag.lookup` | HOST/IP/USER/tool/process, tag types, alert time | authorized_ops, pentest, security_team, maintenance, valid_until | 需要排除授权运维、安全测试、白名单行为 | 只返回 evidence；不得自动关闭 |
| `endpoint.process_tree.lookup` | host/process identifiers, event time | expanded process tree, parent/ancestor, cmd, user, hash | HIDS 只给出片段进程链，需要 endpoint/host 补充 | failed/empty evidence；不升级风险 |

### 7.5 HIDS High-Risk Action Boundary

`PA-HIDS-004` 暂不实现真实执行，只定义边界：

```text
host.isolate_server proposal
  route: host.isolate_server
  risk_level: high-risk
  required_context:
    - host finding
    - disposal_target finding
    - asset locate evidence
    - host event context evidence
    - security tag evidence or explicit skip reason
    - approval actor
    - idempotency key
```

禁止事项：

- 不根据单条 HIDS finding 自动隔离服务器。
- 不把 operateType、templateId、Zeus 请求体写进 public skill 或 Lead Agent prompt。
- 不允许 Lead Agent 直接调用 HIDS/Zeus/SOAR MCP 执行隔离。
- `disposal_target="-"`、未定位 HOST、资产归属不明、security tag 冲突时不得 fallback 执行高风险动作。
- 隔离候选必须明确影响面和回滚/补偿约束，后续再进入真实 adapter 设计。

### 7.6 HIDS Tenant Memory Candidates

这些内容只进入 PingAn tenant memory/config/eval，初始状态必须是 `pending_review`：

| Candidate type | 来源内容 | 用途 |
|---|---|---|
| `environment_fact` | 平安机房、内部域名、内部网段、主机环境、内部安全工具 | 辅助解释环境，不作为全局事实 |
| `benign_pattern` | 内部运维脚本、安全组测试、健康检查、固定路径/工具触发的常见误报 | 作为 review hint，不自动 confirmed |
| `detection_lesson` | HIDS event_type 对应的主机行为研判经验 | 帮助 domain handler/eval，不直接改判 |
| `identity_pattern` | 特定账号、登录用户、运维账号格式 | 辅助身份解释，不作为全局规则 |
| `response_policy_hint` | 某类 HIDS 是否建议隔离、转 BU、仅观察 | 只作为 policy candidate，需要审批 |
| `negative_memory` | 不要把“内网暴破常为运维”全局化；不要因测试关键字忽略攻击工具 | 防止重复犯错 |

### 7.7 HIDS Eval Fixture Candidates

PA-04 后续至少需要这些脱敏 fixture：

| Fixture ID | 覆盖 card | 输入特征 | 期望 |
|---|---|---|---|
| `hids_bounce_shell_suspicious_001` | `PA-HIDS-002` | 反弹 shell event + 可疑进程链 + 外联迹象 | 输出 malicious indicators，并建议 host context / asset 查询 |
| `hids_web_command_ops_candidate_001` | `PA-HIDS-002` / `PA-HIDS-003` | web command 命中但疑似运维脚本 | 输出 benign candidate + uncertainty，不自动忽略 |
| `hids_bruteforce_internal_001` | `PA-HIDS-002` / `PA-HIDS-003` | 内网暴破类事件 | 不能全局忽略；需要 tenant memory/security tag 支撑 |
| `hids_host_context_empty_001` | `PA-HIDS-001` | host.event_context 查不到 | 返回 empty evidence，不编造上下文，不执行隔离 |
| `hids_isolate_missing_target_001` | `PA-HIDS-004` | alert_action 高风险但 disposal_target/host 不明 | 只生成 needs_review，不生成 executable isolation proposal |

### 7.8 PA-04 Done Definition

`PA-04` 完成标准：

- `PA-HIDS-001..003` 已展开到 source、输入、输出、artifact decision、failure、acceptance。
- `PA-HIDS-004` 已定义 high-risk 边界，但不实现执行。
- 已明确哪些内容进 public skill，哪些进 tenant memory/config/eval，哪些进 read-only action。
- 已可安全进入 `PA-05` PingAnKnowledgeCandidate 清单；PA-05 已在 `pingan-knowledge-candidates.md` 落地。

## 8. P1/P2 Cards To Expand

这些卡片先保留为 register，不在 PA-01 展开实现：

- `PA-COM-003`：攻击详情与攻击链报告。后续用于 Unified Investigation Report，不应先做成 free-form Markdown。
- `PA-APT-005`：IP 封堵候选。PA-02 已定义边界；真实执行仍 deferred。
- `PA-EDR-002`：LoginData/System 文件读取分支。PA-03 已定义边界；实现仍 deferred。
- `PA-EDR-003`：提权行为研判。PA-03 已定义边界；实现仍 deferred。
- `PA-EDR-004`：UM/账号提取。PA-03 已定义边界；实现仍 deferred。
- `PA-EDR-005`：UM 封禁与 EDR IP 隔离候选。PA-03 已定义边界；真实执行仍 deferred。
- `PA-HIDS-003`：HIDS 误报/授权运维模式。PA-04 已定义边界；实现仍 deferred。
- `PA-HIDS-004`：服务器隔离候选。PA-04 已定义边界；真实执行仍 deferred。
- `PA-RESP-001`：FollowUp / BU / PA code。先进入 external disposition 和 policy/config，不把平安 BU 映射写进 public skill。

## 9. Immediate Implementation Order

下一步不是直接写所有能力，而是按这个顺序小步实现。`PA-06`、`PA-07`、`PA-08`、`PA-09`、`PA-10` 已完成；当前继续 PingAn 专项，下一刀进入 `PA-11` Main Orchestrator demo。

1. Done：`PA-06` 对 `skills/public/soc-*` 做最小增量修订，只补通用研判方法，不补平安事实。
2. Done：`PA-07` 实现 `host.event_context.lookup`、`threat_intel.ip_reputation.lookup`、`security_tag.lookup` mock read-only action adapters。
3. Done：`PA-08` 为 APT/EDR/HIDS 每类建立至少 1 条脱敏 eval fixture，并可通过 `soc eval pingan` 回归。
4. Done：`PA-09` 接 PingAn memory candidate 入口，保持 `pending_review`，不直接确认记忆。
5. Done：`PA-10` 接 APT / EDR / HIDS domain triage MVP，只输出 finding/evidence/recommendation。
6. Next：`PA-11` 接 Main Orchestrator demo，把 analyze、read-only evidence、domain findings 和 review context 串成可见链路。

## 10. Guardrails

- 不把 `pingan_docs` 原文整体迁入 prompt。
- 不把平安字段名和内部环境事实写进 `skills/public/soc-*`。
- 不用 rule_code 作为必需主键；它只是 vendor alias。
- 不把 read-only evidence 自动提升为 confirmed memory。
- 不把 high-risk action 放进 Lead Agent 自由 tool call。
- 所有真实外部调用后续必须经 `SocActionAdapterRegistry` 或 external disposition adapter。
