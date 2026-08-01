# PingAn SOC Capability Onboarding

> Updated: 2026-07-07
>
> 目的：把用户掌握的平安 SOC 工具、MCP、skill、研判经验和处置经验，持续、可审计地嵌入 DeerFlow SOC Agent，而不是零散写进 prompt。
>
> 本文档是“经验输入 -> 产品能力 -> 工程落地”的工作台账模板。它不要求一次性收集完整信息；每次只要拿到一个可验证能力，就转成一个小切片实现和评测。平安 APT/EDR/HIDS 历史 prompt/经验文档的拆解规则见 `capabilities/pingan/knowledge-decomposition.md`。

## 1. 定位

通用 SOC Agent 框架只能提供边界、流程和安全性；真正像生产 AI SOC system 跑起来，需要接入真实运营经验：

- 平安内部告警来源、字段习惯、脏数据模式和可信度规则。
- Zeus / 天眼 / EDR / HIDS / NIDS / F5 / 资产系统 / SOAR 等工具能力。
- 分析师平时怎么判断攻击方向、资产归属、误报、抑制目标和处置动作。
- 哪些信息只能作为候选，哪些可以进入固定 skill，哪些需要 MCP/tool 查询，哪些必须人工审批。

这一步不是把经验直接塞进 LLM prompt，而是把经验拆成稳定 artifact：

```text
field experience
  -> capability card
  -> classification: skill / tenant memory / MCP adapter / normalizer / policy config / domain handler / eval case
  -> implementation slice
  -> smoke/eval/replay
  -> staging/active governance
```

## 2. 经验分类

| 经验类型 | 应落到哪里 | 例子 | 生产边界 |
|---|---|---|---|
| 通用研判原则 / SOP | `skills/public/soc-*` 或 domain skill | “进程树研判要看父子进程、命令行、用户权限和路径可信度” | skill 只提供指导，不直接改判 |
| 平安环境知识 / 误报模式 | tenant-scoped memory | “某内部安全组运行某工具常见为授权行为” | 必须 pending review 起步，带来源和有效期 |
| 字段可信度 / 归一化规则 | `normalizers/` + `FactReconstructionResult` | “message 优先；缺失时 fallback 到 zeusRawLogs 全字段，但标记低可信” | 必须在 trace 中体现降级和冲突 |
| 只读查询工具 | `SocActionAdapter` / MCP-backed adapter | 资产归属、EDR 进程树、F5 访问日志、HIDS 主机事件 | read-only，结果写 `InvestigationEvidence` |
| 高风险处置工具 | approval + adapter execute | 封禁 IP、隔离终端、下发 F5 策略、关闭生产工单 | 必须人工审批、dry-run、idempotency、audit |
| 阈值 / 状态映射 / 模板映射 | policy/config | 忽略次数阈值、外部状态映射、处置模板选择 | 必须版本化；生产启用需要评测和审批 |
| 领域子研判能力 | domain handler / later domain agent | APT、EDR、HIDS、F5/WAF 各自的 finding schema | 子研判不能直接写 DB 或执行工具 |
| 经验记忆 / lesson | `soc_facts` / `lessons_learned` candidate | 某类规则在特定资产段总是误报 | 默认 `pending_review`，人工确认后才可注入 |
| 回归样本 | `samples` / eval fixtures | 脱敏真实 APT/EDR/HIDS/F5 告警 | 不提交敏感字段；必要时只提交 schema skeleton |

### 2.1 Skill 与 Memory 的分界

只有换一家公司仍成立的研判方法可以进通用 skill。以下内容不能进入 `skills/public/soc-*`：

- 平安内部域名、内部安全工具、部门、团队、账号、BU/PA code。
- 天眼/Zeus/EDR/HIDS 的原始字段名和字段别名。
- 具体 `rule_code` 分流、模板 ID、策略 ID、operateType。
- “某组/某路径/某账号通常可忽略”这类环境事实或误报模式。

这些内容应进入 tenant-scoped memory、adapter mapping、policy/config 或 eval fixture。通用 skill 只描述怎么判断，不保存平安环境事实。

## 3. Capability Card 模板

每次用户提供一个经验点时，先整理成下面这个卡片。信息不完整也可以先建 draft。

```markdown
## Capability: <短名称>

### 1. 场景

- 告警来源：
- 告警类型：
- 分析师当前怎么做：
- 这个能力解决什么痛点：

### 2. 输入

- 必需字段：
- 可选字段：
- 常见别名：
- 字段可信度：
- 示例 payload：脱敏或伪造

### 3. 输出

- 期望输出结构：
- 分析师如何使用：
- Web/TUI 应展示什么：
- 是否能进入 Lead Agent bounded context：

### 4. 能力类型

- skill / tenant memory / MCP adapter / normalizer / policy config / domain handler / eval case：
- read-only / analyst-write / high-risk：
- 是否需要人工审批：

### 5. 失败模式

- 工具查不到：
- 字段冲突：
- 上游脏数据：
- 超时/限流：
- 不能自动采纳的情况：

### 6. 验收样例

- 输入样例：
- 期望 finding：
- 期望 evidence：
- 不应该发生的行为：
```

## 4. 平安 SOC 能力初始 Backlog

这些能力来自当前讨论和已有 demo，优先做能让 Alpha 可见的 read-only / skill / domain handler。

| 优先级 | 能力 | 类型 | 当前状态 | 下一步 |
|---|---|---|---|---|
| P0 | ZEUS/天眼 raw message first | normalizer / field trust | 已有方案和代码基础 | 后续继续补真实样本 drift case |
| P0 | 资产提取与归属定位 | skill + read-only MCP adapter | `soc-asset-extraction` + `asset.locate` mock 已落地 | 用真实字段/样例补 capability card |
| P0 | APT 攻击方向重建 | skill + domain handler | skill 有基础，domain handler 未落地 | 收集方向判断规则、raw message 示例、反例 |
| P0 | EDR 进程树研判 | read-only adapter + domain handler | `endpoint.process_tree.lookup` mock 已落地 | 收集真实 EDR process tree 字段和 finding 模板 |
| P1 | HIDS 主机事件研判 | skill + domain handler | 未落地 | 收集 HIDS 事件类型、关键字段、误报规则 |
| P1 | F5/WAF 攻击方向和抑制目标 | skill + domain handler | `soc-web-application-triage` skill 有基础 | 收集 URI、method、source/target、抑制目标规则 |
| P1 | 历史相似告警复用 | correlation service | 下一刀 | 基于 summary/evidence 先做 deterministic |
| P2 | 处置动作：封禁/隔离/策略下发 | high-risk adapter | approval boundary 已有，真实执行未开 | 等 staging 工具和审批策略成熟 |
| P2 | 经验记忆和 lesson | memory candidate | 方案已有，代码未收口 | 等 domain/correlation 输出稳定后接入 |

## 5. PingAn 专项执行 TODO

这个 TODO 是 PingAn 经验落地的执行清单。它优先于零散讨论：后续只要做“平安能力”，先看这张表，按顺序推进。目标不是一次性把平安所有知识做完，而是先把 APT / EDR / HIDS 三份文档拆清楚，形成可审计、可验证、可复用的能力资产。

### 5.1 执行原则

- `.notes/ai_soc/capabilities/pingan/source-docs/` 原文只作为 source evidence，不直接复制进 Lead Agent prompt、node prompt 或 public skill。
- 每条经验必须先落成 capability card，再决定进入 skill、tenant memory、adapter/normalizer、MCP/action、policy/config 或 eval fixture。
- MCP/mock adapter 只能在 capability card 明确之后实现；mock 是验证链路，不是替代知识拆解。
- Public skill 只能保留跨客户通用方法；平安字段名、内部环境、规则码、模板 ID、处置策略、误报白名单进入 PingAn tenant artifact。
- read-only 查询结果写 `InvestigationEvidence`；高风险动作只生成 approval request，不直接执行。

### 5.2 Source Inventory

| Source ID | 文件 | 主要内容 | 处理方式 |
|---|---|---|---|
| `PA-APT-SRC` | `.notes/ai_soc/capabilities/pingan/source-docs/apt-alert-assess-flow.md` | 天眼/APT 告警、攻击方向、威胁情报、封禁策略、字段可信度 | 拆成 APT direction、threat intel、security tag、response policy、eval cases |
| `PA-EDR-SRC` | `.notes/ai_soc/capabilities/pingan/source-docs/edr-alert-assess-flow.md` | EDR 进程树、命令行、账号/UM、资产归属、终端处置 | 拆成 endpoint triage、process tree evidence、asset locate、identity pattern、high-risk action |
| `PA-HIDS-SRC` | `.notes/ai_soc/capabilities/pingan/source-docs/hids-alert-assess-flow.md` | HIDS 主机事件、登录用户、进程链、后门/反弹 shell/web command、误报经验 | 拆成 host triage、host event context lookup、benign pattern、eval cases |

### 5.3 Execution Backlog

| ID | 状态 | 任务 | 产物 | 验收标准 |
|---|---|---|---|---|
| `PA-00` | Done | 固定知识边界 | `capabilities/pingan/knowledge-decomposition.md` + 六个 `soc-*` skill 的 `Knowledge Boundary` | 通用 skill 不含平安内部字段、账号、部门、规则码、模板 ID、策略 ID |
| `PA-01` | Done | 建立 PingAn capability card register | 已新增 `capabilities/pingan/capability-cards.md`，先列 APT / EDR / HIDS P0/P1/P2 cards | 每张 P0 card 有 source、场景、输入、输出、artifact 分类、风险等级、失败模式、验收要求 |
| `PA-02` | Done | 拆 `PA-APT-SRC` | 已在 `capabilities/pingan/capability-cards.md` 展开 APT direction、APT scenario triage、threat intel、security tag、block IP boundary | APT 方向判断方法进入通用 skill/domain handler；平安字段和策略进入 adapter/policy/eval，不进 public skill |
| `PA-03` | Done | 拆 `PA-EDR-SRC` | 已在 `capabilities/pingan/capability-cards.md` 展开 EDR process tree、path/cmd、LoginData/System、privilege、UM/account、endpoint response boundary | 通用进程树研判进入 endpoint skill；平安路径/账号/部门/BU 进入 tenant memory/config |
| `PA-04` | Done | 拆 `PA-HIDS-SRC` | 已在 `capabilities/pingan/capability-cards.md` 展开 HIDS host event context、event_type triage、benign/authorized ops、host isolation boundary | HIDS 先复用 endpoint/host skill；必要时再新增 `soc-host-hids-triage` |
| `PA-05` | Done | 建立 `PingAnKnowledgeCandidate` 清单 | 已新增 `capabilities/pingan/knowledge-candidates.md`，每条候选标注 `target_artifact`、`tenant_scope`、`source_doc`、`source_section`、`status=pending_review` | 任意经验都能回答“放哪里、为什么、是否过期、由谁确认”；默认不能影响 runtime decision |
| `PA-06` | Done | 对 public skills 做最小增量修订 | 已更新 `skills/public/soc-*`，只补通用 APT/EDR/HIDS/WAF/asset 研判方法，不补平安事实 | `rg` 检查 public skills 不出现平安内部字段/规则/模板/账号等敏感或专属内容 |
| `PA-07` | Done | 实现 P0 read-only mock action adapters | 已实现 `host.event_context.lookup`、`threat_intel.ip_reputation.lookup`、`security_tag.lookup` mock adapter | 通过 `SocActionAdapterRegistry` 调用；成功结果写 `InvestigationEvidence`；不改 verdict/memory |
| `PA-08` | Done | 建 eval fixtures | 已新增 `backend/samples/eval/pingan/` 三条 fixture、`backend/samples/alerts/pingan_legacy_hids.json` 和 `soc eval pingan` | APT / EDR / HIDS 各 1 条脱敏 fixture；覆盖字段冲突、查不到外部事实、误报/授权标签；成功结果写 `InvestigationEvidence` |
| `PA-09` | Done | 接 memory candidate 入口 | 已新增 `SocMemoryCandidate` contract、`MemoryCandidateRepository`、in-memory repository 和 `SocMemoryService.propose_candidate()` | 只写 `pending_review`；含 source/evidence/validity/idempotency/facets/review 信息；不自动 confirmed |
| `PA-10` | Done | 接 domain triage MVP | 已新增 APT / EDR / HIDS domain handlers，读取 capability card refs、skill context、evidence refs 并输出 domain findings | 子研判只输出 finding/evidence/recommendation，不直接写 DB 或执行动作 |
| `PA-11` | Done | 接 Main Orchestrator demo | 已新增 `SocMainOrchestratorService`、`UnifiedInvestigationReport`、`soc eval pingan-main` | APT/EDR/HIDS demo 能看到 analyze -> skill -> read-only evidence -> domain finding -> review context；仍不写 DB、不执行高风险动作 |
| `PA-12` | Waiting | 真实 PingAn MCP/API 替换 mock | 等 dev/staging endpoint/凭证后替换 mock adapter provider | 保存 smoke report；评估 latency、失败率、敏感字段裁剪、payload size；不能用本地 mock 假装完成 |

### 5.4 P0 Capability Cards

第一批只做 P0，不扩张到所有平安经验。

| Card ID | 来源 | 名称 | 默认落点 | 为什么先做 |
|---|---|---|---|---|
| `PA-APT-001` | `PA-APT-SRC` | APT 攻击方向重建 | `soc-network-apt-triage` + domain handler + field trust eval | 上游方向字段可能反，直接影响受害资产和抑制目标 |
| `PA-APT-002` | `PA-APT-SRC` | APT 攻击类型场景化研判 | `soc-network-apt-triage` / `soc-web-application-triage` + domain handler | 不同攻击类型证据不同，必须区分攻击尝试和攻击成功 |
| `PA-APT-003` | `PA-APT-SRC` | 威胁情报 IP 查询 | read-only `threat_intel.ip_reputation.lookup` | APT 判断经常需要外部情报，但只能作为 evidence |
| `PA-APT-004` | `PA-APT-SRC` | 授权/白名单/演练标签查询 | read-only `security_tag.lookup` | 避免把授权扫描、演练、白名单误判为攻击 |
| `PA-EDR-001` | `PA-EDR-SRC` | EDR 进程树研判 | `soc-endpoint-triage` + existing `endpoint.process_tree.lookup` | EDR 是否真实入侵主要靠进程链和命令行证据 |
| `PA-EDR-002` | `PA-EDR-SRC` | 账号/UM/身份模式 | tenant `identity_pattern` memory candidate + entity extraction eval | 账号经常决定处置对象，但格式和组织语义有租户差异 |
| `PA-HIDS-001` | `PA-HIDS-SRC` | HIDS 主机事件上下文 | read-only `host.event_context.lookup` + host/endpoint skill | HIDS 告警需要主机上下文，否则误报率高 |
| `PA-RESP-001` | APT/EDR/HIDS | 封禁/隔离/禁用账号候选 | high-risk action proposal + approval policy | 必须先固定审批边界，不能让 Agent 自主执行 |

### 5.5 Done Definition

PingAn 方案算“做好”不是指所有 mock 都写完，而是满足这些条件：

- 三份 `source-docs` 都已拆成 capability cards，并保留 source trace。
- 每张 card 都有唯一 ID、目标 artifact、风险等级、验收样例和不该发生的行为。
- Public skills 只保存通用方法；PingAn 专属知识进入 tenant memory/config/adapter/eval。
- P0 read-only action 都能通过统一 adapter registry 返回 mock evidence。
- ReviewQueue / Lead Agent context 能展示这些 evidence，但不会自动改 verdict 或 confirmed memory。
- 至少 APT、EDR、HIDS 各有一条脱敏 demo/eval 能跑完整链路。

本地回归命令：

```bash
PYTHONPATH=backend backend/.venv/bin/python -m soc_agent.cli eval pingan --pretty
# 或在 backend 环境已完整同步后：
cd backend && uv run soc eval pingan --pretty
```

## 6. 用户提供经验的推荐格式

为了提高落地效率，用户可以每次给一个“小包”，不用一次性写完整文档。

### 最小输入

```text
场景：APT 恶意外联方向判断
当前人工判断：...
关键字段：...
字段坑点：...
希望 Agent 输出：...
脱敏样例：...
```

### 更完整输入

```text
工具/系统：Zeus / 天眼 / EDR / HIDS / F5 / CMDB / SOAR
能力：只读查询 / 研判 SOP / 处置动作 / 字段归一化
输入字段：...
输出字段：...
现有 API 或 MCP：有/没有；如果有，只给接口语义和字段，不给 secret
风险等级：read-only / analyst-write / high-risk
失败时分析师怎么处理：...
真实案例：脱敏后 1-3 条
```

### 禁止直接提供

- 生产账号、密码、token、cookie。
- 未脱敏的个人敏感信息。
- 未授权的内部系统地址或真实业务秘密。
- 可以直接用于攻击或绕过生产防护的细节。

如果某个能力必须接真实系统，先只记录接口语义、字段和 mock 返回；真实 endpoint/凭证后续通过本地配置或 secret 注入，不写入仓库。

## 7. 落地规则

每张 capability card 进入实现前，必须先分类：

```text
Can be encoded as deterministic logic?
  -> normalizer / field trust / domain handler

Is it human operational know-how?
  -> skill / domain skill / eval case

Does it need external data?
  -> read-only MCP/action adapter -> InvestigationEvidence

Can it change production state?
  -> high-risk action -> approval request -> grant -> dry-run -> execute boundary

Is it a repeated lesson?
  -> memory candidate -> pending_review -> confirmed after human review
```

工程约束：

- skill 不执行工具。
- MCP/tool 不绕过 `SocActionAdapterRegistry`。
- read-only result 必须写 `InvestigationEvidence`，不能直接改 verdict。
- high-risk action 必须走 approval。
- LLM 输出只能是结构化候选，不能直接变成 production fact。
- 每个能力至少有一个脱敏样例或 fake fixture，用于 smoke/eval/replay。

## 8. 与接下来路线的关系

PingAn SOC capability onboarding 是 Slice 0，不替代后续路线，而是为后续路线提供真实内容：

```text
Slice 0: PingAn SOC capability onboarding
  -> 给 Slice 1 Correlation 提供真实相似判断维度
  -> 给 Slice 2 Memory Tracking 提供真实 typed memory、facets 和检索信号
  -> 给 Slice 3 Domain Contract 提供真实字段和 finding 类型
  -> 给 Slice 4 EDR/APT/HIDS/F5 handlers 提供 SOP 和样例
  -> 给 Slice 5 Main Orchestrator 提供真实路由和合并策略
  -> 给 Slice 6 Web/TUI 展示提供分析师真正关心的信息
  -> 给 Slice 7 Demo/Eval 提供脱敏回归样本
```

当前建议执行顺序：

1. 已完成 `SocCorrelationService` MVP、`PA-01..PA-11` PingAn 可见链路。
2. `PA-12` 只在真实 dev/staging PingAn MCP/API 参数可用时推进：替换 provider、跑 smoke/eval、保存报告，并评估延迟、失败率、字段裁剪和敏感信息风险。
3. 在真实接口未就绪前，下一刀应转向外部处置反馈、typed memory tracking 或 Web/TUI 可见化，而不是继续堆更多 mock。
4. 做 Memory Tracking Contract 时，用这些 card 固定 memory type、topics、canonical detection、vendor aliases、scenario facets 和 evidence refs。
5. 做 Web/TUI 可见化时，用 `UnifiedInvestigationReport` 展示 route、skill、evidence、domain finding 和 review context。

## 9. 第一批建议让用户补充的信息

优先收集这些内容，能最快让 Alpha 看起来像真实生产 SOC：

1. **APT 方向判断**
   - 天眼/Zeus 常见方向错判模式。
   - raw message 里哪些字段更可信。
   - “内到外反连 / 外到内攻击 / 内到内异常 / 我方攻击互联网”各自怎么区分。

2. **EDR 研判**
   - 进程树里哪些字段最关键。
   - 常见高危父子进程组合。
   - UM/user/account 在 EDR 里怎么出现。
   - 什么情况下要查资产、查登录、查网络连接。

3. **资产归属和处置目标**
   - 资产归属系统实际会返回哪些字段。
   - 公司码、业务组、owner、环境、重要性等字段如何影响研判。
   - 抑制目标到底应该是 IP、host、UM、URI、rule 还是组合。

4. **F5/WAF**
   - 如何判断源/目的。
   - URI/method/header/body 哪些字段最关键。
   - 什么样的告警适合抑制，什么样必须升级。

5. **HIDS**
   - 常见事件类型和字段。
   - 哪些规则误报多。
   - 哪些主机/账号/命令组合需要升级。

这些信息可以是自然语言、表格、伪代码、脱敏 JSON、截图文字摘录。后续我会把它们逐条转成 capability card，再决定落到 skill、MCP adapter、domain handler、eval case 或 memory candidate。
