# Ten-Alert End-to-End Validation

这是当前 SOC MVP 的统一端到端审阅入口。它固定选择 10 条完整告警，调用生产
`SocAnalysisService`、真实模型、SQLite 持久化和只读模拟 Provider，然后把每条告警从
原始输入到最终结论按时间顺序放在一个目录中。

它不会用两个已知上游输入缺口样本凑数：

- `1965452`：NDR 原始证据缺失；
- `1965795`：EDR 原始证据缺失。

替代样本是：

- `2025642`：NDR 反弹 Shell，验证连接方向、攻击者/受害者角色重建；
- `1980502`：EDR SAM Dumping，验证进程、用户、主机与凭据访问研判。

完整固定清单见 [`ten-alert-cases.json`](ten-alert-cases.json)。

## Plan First

从仓库根目录执行。默认只打印计划，不读模型、不写输出：

```bash
backend/.venv/bin/python \
  validation/compact_zeus/e2e/run_ten_alert_e2e.py
```

计划必须显示 10 个固定 ID、`globalai-deepseek-v4-flash-0731` 主模型、reasoning 默认关闭、
`globalai-deepseek-v4-pro` 条件式角色复核、10 次 Runtime 主模型调用、最多 10 次主模型输出纠错、
最多 10 次 PingAn Policy Skill 调用和 `simulated_read_only` Provider。精确确定性策略命中
的告警不会再调用 Policy Skill；默认每个模型节点只允许一次结构纠错，不能把它变成开放式重试循环。

默认命令使用 GlobalAI Flash 主分析、GlobalAI Pro 条件式窄复核，并关闭 reasoning 以控制中转服务
延迟；两者写进不同的 provider journal。下面是等价的显式命令：

```bash
backend/.venv/bin/python \
  validation/compact_zeus/e2e/run_ten_alert_e2e.py \
  --output-root backend/.deer-flow/soc-validation/e2e-ten-role-verifier \
  --model-name globalai-deepseek-v4-flash-0731 \
  --thinking disabled \
  --output-retry-attempts 1 \
  --role-verifier enabled \
  --role-verifier-model globalai-deepseek-v4-pro \
  --role-verifier-min-confidence 0.35 \
  --execute \
  --confirm-live \
  --confirm-investigation \
  --replace
```

固定 cohort 的 Verifier 默认开启，但仍先执行 v2 确定性 gate；只有 gate 命中才调用 Pro，且只复核
整体网络方向和 attacker/victim。因此“已配置 Pro”不等于“该告警实际调用了 Pro”。
`inferred`、`tentative`、一般 evidence gap、intermediary、response target 或低 confidence 单独出现都
不会调用第二个模型。`--resume` 只用于
同一配置、同一代码版本的中断续跑或失败项重试，不能把跨 Parser/Prompt 版本的结果包装成 fresh 对照。

`run-manifest.json` 和 `summary.json` 记录 `thinking_enabled_requested`；每次 primary/repair/verifier
provider call 还记录 `requested_model_name`、`thinking_enabled_requested`、
`response_reasoning_present` 与 `response_reasoning_chars`。前者证明客户端请求参数，后两者只说明
网关响应是否暴露 reasoning，不能把“未返回 reasoning 文本”误判为“模型未推理”。

## Execute

```bash
backend/.venv/bin/python \
  validation/compact_zeus/e2e/run_ten_alert_e2e.py \
  --execute \
  --confirm-live \
  --confirm-investigation
```

该入口始终验证 `Base -> Memory -> PingAn Policy -> Effective` 四阶段留痕。十条业务验收不再安装
`simulate-reviewed-network-source-block` 合成策略，也不创建 mock 封禁；动作授权/执行必须均为 0。
通用 Automation 引擎由 `backend/tests/test_soc_automation.py` 独立验证，真实租户动作只能由另行评审的
Automation Policy 和 adapter 验收。

已有同一输出需要续跑时加 `--resume`；明确要丢弃旧结果并重新开始时才使用
`--replace`。

默认输出：

```text
backend/.deer-flow/soc-validation/e2e-ten-current/
├── SUMMARY.md
├── summary.json
├── run-manifest.json
├── soc-e2e.sqlite
├── knowledge-review/
│   ├── REVIEW.md
│   └── candidates.json
├── runtime-batch/
└── cases/<alert_id>/
    ├── 00-ingress.json
    ├── 01-normalization.json
    ├── 02-entity-extraction.json
    ├── 03-fact-reconstruction.json
    ├── 04-bounded-analysis-input.json
    ├── 05-runtime-trace.json
    ├── 06-llm-analysis.json
    ├── 06a-role-verification.json
    ├── 07-evidence-grounding.json
    ├── 08-decision.json
    ├── 09-investigation.json
    ├── 10-review-and-agent-context.json
    ├── 11-knowledge-candidates.json
    ├── 12-effective-decision-and-automation.json
    └── final-conclusion.json
```

先看 `SUMMARY.md`，再进入一个 `cases/<alert_id>/` 按编号审阅。每个
`final-conclusion.json` 汇总结构验收、模型结论、Grounding、确定性决策、只读调查证据、
ReviewQueue 和 Lead Agent 有界上下文。

每个 `05-runtime-trace.json` 保存 `steps[].duration_ms` 和 Runtime 总耗时；每个
`final-conclusion.json` 还保存 analysis service、Memory pattern、调查 workflow/reporting 和完整 E2E
总耗时。Token 来源明确分为 `reported`、`estimated`、`mixed`、`unavailable`；内网兼容估算不是账单，
没有经审核价格表时金额成本仍为 `not_measured`。

主模型结果的健壮性看 `06-llm-analysis.json` 和 `final-conclusion.json` 中的
`analysis_output_quality`：

- `accepted`：首轮严格通过；
- `repaired`：经过无语义机械修复或一次有界模型纠错后完整通过；
- `degraded`：core 和部分区块有效，坏区块以 inert default 隔离，结论可持久化但必须 review；
- `deterministic_fallback`：模型 core 在一次纠错后仍无效，保留显式 stub 结论并必须 review。

报告同时汇总 rejected section、repair 和 fallback 计数。它们是输出契约可靠性指标，不是告警研判
准确率；Provider timeout/auth/capacity/transport 仍按 retryable Runtime failure 处理，不会被 fallback 隐藏。

角色复核统计区分 `projected_candidate_claim_count` 与 `atomic_claim_count`：前者是 Gate 为审计稳定投影的
候选声明，未触发时也可能非零；后者只统计真正发送给 verifier 并完成逻辑复核的声明，不能混用。

`12-effective-decision-and-automation.json` 不能与 `08-decision.json` 混为一谈：后者是不可变的
base Runtime decision；前者按 `Base -> Memory -> PingAn Policy -> Effective` 保存阶段快照、
operational disposition 和可选 action lineage。Memory 可以经 reviewed typed directive 影响
effective decision；PingAn 策略通过精确规则或版本化 Policy Skill 形成独立 Decision；二者都不直接
授权动作。

保留旧结果后，可生成同 cohort 的逐条对比：

```bash
backend/.venv/bin/python \
  validation/compact_zeus/e2e/compare_ten_alert_e2e.py \
  backend/.deer-flow/soc-validation/e2e-ten-current/summary.json \
  backend/.deer-flow/soc-validation/e2e-ten-governed-current/summary.json \
  --output-dir backend/.deer-flow/soc-validation/e2e-ten-before-after
```

`COMPARISON.md` 会把 live LLM 的 base 输出漂移与有 append-only transition 支撑的 effective
decision 变化分开，不能把一次模型重采样误称为 Memory 或 automation 的效果。

`acceptance_status=passed` 只表示结构、持久化和安全门禁通过，不是模型准确率声明。
`quality_status=review_required` 和 `quality_findings` 会单独暴露 Grounding 全拒绝、证据描述
夹带相邻事实等模型输出质量问题；它们不能通过放宽 Grounding 被静默消除。

当前 analyzer 契约把事实和推理解耦：

- `04-bounded-analysis-input.json` 的 `evidence_catalog` 是 Runtime 生成的 `E-*` 当前告警原子
  事实目录；`context_catalog` 是 `S/A/M/C/T-*` 受治理上下文目录。
- `06-llm-analysis.json` 的 `evidence[]` 必须逐字选择 `E-*`，`reasoning[]` 用 `R-*` 表达
  LLM 的安全推理，scenario 再引用两者。
- `07-evidence-grounding.json` 分别报告原子事实是否精确匹配、推理是否引用了完整且允许的
  上下文。`grounded` 证明引用链成立，不等于模型推理已经成为确定性事实。
- `11-knowledge-candidates.json` 和根目录 `knowledge-review/REVIEW.md` 汇总 `K-*` 建议。它们
  全部保持 `pending_review`，不会自动写 Memory、修改 Skill/Adapter/Policy 或影响当前结论。

Parser 只允许无安全语义的机械修复并记录 repair log：唯一 path/value 恢复 `E-*`、补出已被
`R-*` 引用的精确目录事实、精确重复引用去重、严格 JSON boolean 字符串转换、删除明确空引用、
根据已显式引用的 `S/A/M/C/T-*` 补齐对应的冗余 basis，以及在 E/R 引用都存在时用明确占位说明
标记模型漏写的 scenario rationale。方向/角色可以直接引用本次 context catalog 中的精确
`S/A/M/C/T-*`，不必在 `R-*` 中机械重复；不存在的引用仍拒绝。可选 response target 的实体类型和值
必须精确对应一个已裁决实体；动作场景角色可以不同于该实体的全局语义角色。例如同一台主机可以在
`roles[]` 中是 `victim`，在 `isolate_host` 提议中是 `impacted_asset`。没有已裁决实体的 target 会被
删除并留下包含角色、类型和值的 repair log；该放宽不改 verdict，也不授予动作权限。歧义引用、冲突
事实和安全语义缺失仍然拒绝。

当模型输出不合约时，主分析或角色复核节点最多调用一次受控纠错 Prompt。主分析先分别校验 required
core 与 scenario/direction/role/knowledge 区块；core 有效时只重做坏区块，journal purpose 为
`primary_analysis_section_repair`。core 无法解析时才使用完整 `primary_analysis_retry`；角色复核使用
`role_verification_retry`。纠错节点只接收无效候选/区块、校验错误、允许的引用目录和响应 Schema，不
重新读取原始厂商 payload，也不能引入新安全事实。主分析可通过
`SOC_LLM_OUTPUT_FALLBACK_MODEL` 为这一次纠错指定更强的已注册模型。

## 2026-08-12 Role Verifier Ten-Alert Result

Gate v2 的新 live 结果位于：

- 当前结果：`backend/.deer-flow/soc-validation/e2e-ten-role-verifier-v2-20260812/`；
- v1/v2 对照：`backend/.deer-flow/soc-validation/e2e-ten-role-verifier-v1-v2-comparison-20260812/`。

同一输入 hash 的 10 条告警结构/安全验收为 10/10 passed，Gate 从 v1 的 10/10 触发降为 5/10。
触发项为 `1965794`、`1965802`、`1965935`、`1966442`、`1980502`；五次逻辑复核实际发送 14 个
Claim，得到 6 supported / 5 challenged / 3 unresolved。其余五条没有 verifier Provider 调用，反弹
Shell `2025642` 的 victim-initiated wire flow 与 attacker/victim 分离语义得到保留。

报告中的 29 个 `projected_candidate_claim_count` 是全部十条的稳定 Gate 候选投影；只有 14 个
`atomic_claim_count` 真正进入 verifier。Verifier 消耗为 5 次 Provider invocation、120,929 个
provider-reported tokens、93,266 ms，且没有 verifier output retry。历史首轮 `1966442` 的主模型输出两个
null role values，在一次受控主分析纠错后仍失败；该冻结产物早于当前 `unresolved -> value=null` 契约，
随后 `--resume` 只重试该项并成功。

当前仍没有独立人工方向/角色真值，因此 10/10 passed 只证明结构、持久化和安全门禁；5/10 trigger、
challenge 和 unresolved 统计都不是 accuracy/precision/recall。邮件 `1965802` 与纯端点样本是否应该
进入该 verifier，需要在人工标签后再决定，不能为追求更低触发率直接写场景硬编码。

### Historical Gate v1 Baseline

同一固定 cohort 的最终代码实跑结果位于：

- Primary-only：`backend/.deer-flow/soc-validation/e2e-ten-role-primary-final-20260812/`；
- Primary + verifier：`backend/.deer-flow/soc-validation/e2e-ten-role-verifier-20260812/`；
- 对照：`backend/.deer-flow/soc-validation/e2e-ten-role-comparison-final-20260812/`。

Verifier run 的结构验收为 10/10，通过六类来源（EDR/HIDS/NDR/NIDS/SIEM/TI）。10 条当前全部命中
gate：8 `challenged`、1 `unresolved`、1 `unavailable`；64 个原子 claim 为 31 supported、20
challenged、13 unresolved。`unavailable` case 正确 fail closed：保留第一轮，证据降级并强制 review，
不让整条 Runtime 失败，也不产生动作权限。

10 个 verifier case 实际产生 16 次 Provider invocation，其中 6 个 case 触发一次结构纠错；1 个
最终纠错失败 case 没有返回 usage。因此报告中的 verifier token 汇总是已计量下界，不能当作完整账单。

报告区分一次告警级逻辑复核、其中的原子 claim 数，以及真实 Provider invocation 数。Token 统计覆盖
主分析、角色复核和平安策略顾问三条模型调用链；任何已调用但没有 usage 的链路都会令汇总标记为
`partial` / `is_lower_bound=true`。仓库没有配置经审核的模型单价表，因此只统计 Token 和耗时，金额成本
明确为 `not_measured`，不会拿不完整用量估算伪精确费用。

这套 E2E 对 Memory 是 `read_existing_only`：`11-knowledge-candidates.json` 与 `knowledge-review/` 只是
人工审核包，不会自动写 `soc_memory_candidates` 或 `soc_memory_records`。正式 Memory 必须经过 admission、
人工确认和独立 retrieval activation；`--replace` 会删除整个输出目录及其中的 `soc-e2e.sqlite`，所以
fresh run 默认从空 Memory 库开始。

这不是准确率证明：目前没有为这 10 条录入独立人工方向/角色真值。两个 live run 也会分别重采样主
模型，所以 base verdict 差异不能归因给 verifier。当前最明确的产品结论是 gate 触发率 100% 过宽：
Verifier 增加 349,938 tokens 和约 657 秒，必须在人工真值评测后收紧触发条件或 claim 预算，不能按
当前参数直接全量上线。该目录是 v1 历史证据，不会被回写。v2 Gate 在这些冻结输出上的离线重算为
3/10，分别是 `1965810`、`1965935`、`1966442`；它只说明触发范围收窄，尚不是新的 live 模型质量结果。

## Previous PingAn Policy Result

2026-08-11 v2.1 fresh + failure-only resume 历史输出位于
`backend/.deer-flow/soc-validation/e2e-ten-pingan-policy-20260811/`：

- 10/10 structural/safety acceptance passed；10 个 ReviewQueue、10 个四阶段 decision transition、
  33 条 `mocked=true` 只读调查证据；
- Base verdict 为 5 `suspicious`、4 `needs_review`、1 `unknown`；没有 Memory directive contributor；
- 2 条命中确定性 `http-200-success-signal-escalation`；6 条得到已校验 Policy Skill advice；2 条
  Policy Skill 输出校验失败后 `failed_closed + no_match`；
- disposition 为 3 `escalated`、2 `unknown`，其余不设置 disposition；8 条要求 review、2 条 preserve；
  没有样本同时满足非 `200`、非生产、内部范围、无效果和无关键缺口的完整忽略条件，因此 0 ignored；
- 0 action authorization、0 action execution、0 real external call。旧的三条合成 Mock 封禁已消失；
- 2 个模型质量 case：`1971013` 拒绝 1 条 evidence 和 1 条 reasoning，`1965919` 拒绝 1 条 reasoning；
  safety gate 保持生效；
- 与 `e2e-ten-current` 同输入比较为 10/10 input hash 一致、3 条 base verdict 因 live-model 重采样变化、
  effective verdict 相对各自 base 变化为 0。比较文件位于
  `e2e-ten-pingan-policy-comparison-20260811/COMPARISON.md`。

该报告早于 v2.2 的 canonical HTTP 全非 `200`、明确 provider 成功/失败和强制转交优先级修复。
其中 `http-200-success-signal-escalation` 已删除，统计不得沿用为当前验收结论。

## Historical PingAn Policy v2.2 Result

2026-08-11 v2.2 fresh + failure-only resume 输出位于
`backend/.deer-flow/soc-validation/e2e-ten-pingan-policy-v2.2-20260811/`：

- 10/10 structural/safety acceptance passed；7 `needs_review`、3 `suspicious`，10 个 ReviewQueue、
  10 个四阶段 decision transition、33 条 `mocked=true` 只读调查证据；
- 1 条命中确定性 `provider-confirmed-success-escalation`；其余 9 条进入 Policy Skill，3 条形成
  `llm-policy-skill-advice`，6 条 no-match，其中 2 次 advisor 校验失败后 fail closed；
- disposition 为 2 `escalated`、1 `unknown`；0 confirmed Memory contributor、0 action
  authorization/execution、0 real external call；
- 本 cohort 没有 canonical HTTP 全非 `200`、明确失败或强制 rule code 样本；这些确定性规则的正反例
  由 `backend/tests/test_soc_tenant_policy.py` 覆盖；
- 与旧 v2.1 同输入比较位于
  `backend/.deer-flow/soc-validation/e2e-ten-pingan-policy-v2.2-comparison-20260811/`：10/10 input hash
  相同，5 条 base verdict 差异是 live-model 重采样；每条自身 base 到 effective 的 verdict/review
  变化为 0。

第一次在受限网络环境运行产生连接失败，随后 `--resume` 仅重试失败 case；两个模型格式/空输出失败也
只重试对应 case。没有为完成批次而放宽 Parser、引用或 Grounding 契约。

当前代码已经升级到 `pingan-disposition-v2.3.0`：删除确定性
`provider-confirmed-success-escalation`，让成功/失陷标签阻止非 `200` 直接忽略后进入 Policy Skill。
因此本节只能作为 v2.2 历史基线；组件测试已经覆盖 v2.3 交接，完整 v2.3 live 十条尚未重跑。

首轮 Runtime 有 3 条严格输出失败，连续 `--resume` 只重试失败项，最终 10/10 完成；没有通过放宽
Schema/Grounding 隐藏非法 `E-?`、重复引用、schema 回显或 unsupported field。Policy advisor 的
fail-closed 记录后续会输出不含敏感内容的阶段码（prompt/model/output/reference），便于定位。

## Previous Baseline

2026-08-10 automation redesign 之前的 fresh cohort：

- `10/10` structural/safety acceptance passed；`6 suspicious / 4 needs_review`；
- 10 个 ReviewQueue、10 个 tenant-policy shadow decision、33 条 `mocked=true` 只读调查证据；
- 3 个模型质量 finding：`1965794` 把 boolean 复制成字符串，`1965802` 丢失正文尾部 CRLF，
  `1965449` 引用了不存在的 `A-*`；合计 3 条 `E-*`、5 条关联 `R-*` 未落地；
- 20 个 `K-*` review item，其中 17 个 support grounded、3 个 unresolved，全部 pending review；
- 0 automation、0 high-risk action、0 auto-close、0 confirmed-memory write。

这些 finding 是严格契约的真实输出，不通过重复采样或放宽 Grounding 隐藏。当前权威数字始终以
本地 `SUMMARY.md` 和 `summary.json` 为准。

## Historical Governed Simulation Result

以下是 2026-08-11 旧版合成 Automation 验证的历史结果，现已从十条业务入口移除；通用引擎测试仍保留。
当时 fresh 输出写入
`backend/.deer-flow/soc-validation/e2e-ten-governed-20260811/`：

- `10/10` structural/safety acceptance passed；`9 suspicious / 1 needs_review`；
- 10 个 ReviewQueue、10 个 tenant-policy shadow decision、33 条 `mocked=true` 只读调查证据；
- 10 条 decision transition 均为 `unchanged`，说明本批没有 confirmed Memory directive 改写
  effective decision；`memory_contributor_count=0`；
- validation-only policy 在 3 条 NDR case 上产生 3 条无 Memory automatic authorization 和 3 次
  `mocked=true` 幂等执行；真实外部调用为 0，base `automation_allowed=false` 未被覆盖；
- 仅 `1965449` 保留 1 条 evidence/1 条 reasoning Grounding finding，其余 9 条无质量 finding；
- 首轮有 1 次 analyzer timeout 和 1 次 empty output，`--resume` 复用 8 条完成项并只重试 2 条，
  最终 `10/10` 完成。

旧/新比较位于
`backend/.deer-flow/soc-validation/e2e-ten-comparison-20260811/COMPARISON.md`：10/10 input hash
一致，3 条 base verdict 变化被明确标记为 live-model resampling/runtime difference，而不是
Memory/automation 效果；Grounding 总数 `+30`、rejected `-2`。这些数字仍不是人工标签准确率。

## Authority Boundary

- 权威链路是 `原始输入 -> 固定 Runtime -> 持久化决策 -> ReviewQueue`。
- `asset.locate` / `security_tag.lookup` 当前使用现有只读模拟 Provider，结果必须保持
  `mocked=true`，不能关闭真实内网接入债务。
- Lead Agent 只生成可供分析师继续对话的有界上下文；其聊天文本不替代 Runtime 结论。
- 所有样本的 base Runtime `automation_allowed` 都必须保持 `false`。当前十条业务入口不安装
  Automation Policy，不产生 action authorization/execution，也不会封禁、隔离、关单或调用真实外部系统。
- 候选知识只供分析师审核；确认、有效期、适用范围和检索激活继续走现有 Memory/Governed
  Context 服务，不能由这个验证脚本越权完成。
- 输出含完整内部告警衍生数据，目录权限为 `0700`、文件为 `0600`，且全部 Git 忽略。

`backend/.deer-flow/soc-runtime-validation/`、`soc-internal-validation/` 和
`soc-lead-agent-validation/` 继续保留专项与历史证据；本目录对应的统一输出是日常完整链路
审阅入口，不需要跨三个旧目录拼接一次告警。

## Tests

```bash
backend/.venv/bin/python -m pytest -q \
  validation/compact_zeus/e2e/test_run_ten_alert_e2e.py \
  validation/compact_zeus/e2e/test_compare_ten_alert_e2e.py \
  validation/compact_zeus/internal_batch/test_run_pingan_runtime_batch.py
```
