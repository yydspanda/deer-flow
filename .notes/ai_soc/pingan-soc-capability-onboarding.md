# PingAn SOC Capability Onboarding

> Updated: 2026-07-05
>
> 目的：把用户掌握的平安 SOC 工具、MCP、skill、研判经验和处置经验，持续、可审计地嵌入 DeerFlow SOC Agent，而不是零散写进 prompt。
>
> 本文档是“经验输入 -> 产品能力 -> 工程落地”的工作台账模板。它不要求一次性收集完整信息；每次只要拿到一个可验证能力，就转成一个小切片实现和评测。

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
  -> classification: skill / MCP adapter / normalizer / domain handler / eval case / memory candidate
  -> implementation slice
  -> smoke/eval/replay
  -> staging/active governance
```

## 2. 经验分类

| 经验类型 | 应落到哪里 | 例子 | 生产边界 |
|---|---|---|---|
| 研判原则 / SOP | `skills/public/soc-*` 或 domain skill | “天眼 APT 方向字段不可信，优先 raw message 和五元组重建方向” | skill 只提供指导，不直接改判 |
| 字段可信度 / 归一化规则 | `normalizers/` + `FactReconstructionResult` | “message 优先；缺失时 fallback 到 zeusRawLogs 全字段，但标记低可信” | 必须在 trace 中体现降级和冲突 |
| 只读查询工具 | `SocActionAdapter` / MCP-backed adapter | 资产归属、EDR 进程树、F5 访问日志、HIDS 主机事件 | read-only，结果写 `InvestigationEvidence` |
| 高风险处置工具 | approval + adapter execute | 封禁 IP、隔离终端、下发 F5 策略、关闭生产工单 | 必须人工审批、dry-run、idempotency、audit |
| 领域子研判能力 | domain handler / later domain agent | APT、EDR、HIDS、F5/WAF 各自的 finding schema | 子研判不能直接写 DB 或执行工具 |
| 经验记忆 / lesson | `soc_facts` / `lessons_learned` candidate | 某类规则在特定资产段总是误报 | 默认 `pending_review`，人工确认后才可注入 |
| 回归样本 | `samples` / eval fixtures | 脱敏真实 APT/EDR/HIDS/F5 告警 | 不提交敏感字段；必要时只提交 schema skeleton |

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

- skill / MCP adapter / normalizer / domain handler / memory candidate / eval case：
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
| P1 | F5/WAF 攻击方向和抑制目标 | skill + domain handler | `soc-waf-f5-triage` skill 有基础 | 收集 URI、method、source/target、抑制目标规则 |
| P1 | 历史相似告警复用 | correlation service | 下一刀 | 基于 summary/evidence 先做 deterministic |
| P2 | 处置动作：封禁/隔离/策略下发 | high-risk adapter | approval boundary 已有，真实执行未开 | 等 staging 工具和审批策略成熟 |
| P2 | 经验记忆和 lesson | memory candidate | 方案已有，代码未收口 | 等 domain/correlation 输出稳定后接入 |

## 5. 用户提供经验的推荐格式

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

## 6. 落地规则

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

## 7. 与接下来路线的关系

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

1. 先实现 `SocCorrelationService` MVP，不等完整平安经验包。
2. 同时开始收集 3-5 张 P0 capability card：APT 方向、EDR 进程树、资产归属、F5 抑制目标、HIDS 主机事件。
3. 做 Memory Tracking Contract 时，用这些 card 固定 memory type、topics、canonical detection、vendor aliases、scenario facets 和 evidence refs。
4. 做 Domain Sub-Agent Contract 时，用这些 card 校验 schema 是否够用。
5. 做 MVP handlers 时，把 card 转成 skill/context、deterministic rule、mock adapter 或 eval case。

## 8. 第一批建议让用户补充的信息

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
