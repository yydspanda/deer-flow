# Checkpoint D Runtime Validation

该目录把一条 canonical PingAn 告警按 SOC Runtime 的真实生产边界逐步重放，供人工逐步
审阅。它是验证与解释工具，不是另一套 Runtime，也不允许复制生产逻辑。

## 顺序

| 步骤 | 入口 | 验证边界 | 明确不执行 |
| --- | --- | --- | --- |
| D0 | `build_checkpoint_d_corpus_inventory.py` | 语料 hash、ID、wrapper、topic、raw log/message 可用性 | Normalizer 及后续全部步骤 |
| D1 | `build_checkpoint_d_normalization_review.py` | 生产 canonical normalization、message-first、provenance、raw hash | 实体、事实、LLM、决策、持久化 |
| D2 | `build_checkpoint_d_entity_extraction_review.py` | 重放 D1 后调用生产 generic entity extraction | 事实、LLM、决策、持久化 |
| D3 | `build_checkpoint_d_fact_reconstruction_review.py` | 重放 D1/D2 后调用生产 fact reconstruction | Analysis input、LLM、决策、持久化 |
| D4 | `build_checkpoint_d_bounded_analysis_input_review.py` | 重放 D1-D3 后调用生产 bounded analysis input 与 `EvidenceCoverageReport` | Skill resolution、Prompt、LLM、grounding、决策、持久化 |
| D5 | `build_checkpoint_d_skill_context_review.py` | 消费确认后的 D4，通过生产入口选择 Skill 并投影包内 bounded Runtime guidance | Prompt、LLM、grounding、决策、持久化 |
| D6 | `build_checkpoint_d_skill_route_coverage.py` | 212 条语料离线重放 D1-D5，审计 typed HTTP/email、host/asset 与 package 路由覆盖 | Prompt、LLM、决策、持久化；不是 Runtime 节点 |
| D7 | `build_checkpoint_d_analyzer_output_review.py` | 消费确认后的 D5，渲染生产 Prompt，调用显式配置的真实 LLM，并校验 `AnalysisResult.v2` 和 typed scenario contract | grounding、决策、correlation/memory、MCP/tool、持久化、ReviewQueue/action |
| D8 | `build_checkpoint_d_evidence_grounding_review.py` | 消费 D5/D7，调用 production Grounding 校验 source/value，并拒绝 description sibling facts | LLM、决策、correlation/memory、MCP/tool、持久化、ReviewQueue/action |
| D9 | `build_checkpoint_d_decision_policy_review.py` | 消费 D5/D7/D8，调用 production Decision Policy 验证 fail-closed | LLM、重新 Grounding、租户处置、持久化、ReviewQueue/action |
| D10 | `build_checkpoint_d_cross_source_runtime_review.py` | 每 topic 代表样本 + 全部 known input gaps，经显式配置真实 LLM 执行完整 production Runtime | DB、租户处置、模型准确率评测；不是 Runtime 节点 |

默认审阅样本为 `alert_id=1965449`。产物写入
`backend/.deer-flow/soc-runtime-validation/checkpoint-d/step-d*/`，包含真实告警派生数据，
已 Git 忽略，不得提交。

## 重放

从仓库根目录执行：

```bash
backend/.venv/bin/python validation/compact_zeus/checkpoint_d/build_checkpoint_d_corpus_inventory.py
backend/.venv/bin/python validation/compact_zeus/checkpoint_d/build_checkpoint_d_normalization_review.py --alert-id 1965449
backend/.venv/bin/python validation/compact_zeus/checkpoint_d/build_checkpoint_d_entity_extraction_review.py --alert-id 1965449
backend/.venv/bin/python validation/compact_zeus/checkpoint_d/build_checkpoint_d_fact_reconstruction_review.py --alert-id 1965449
backend/.venv/bin/python validation/compact_zeus/checkpoint_d/build_checkpoint_d_bounded_analysis_input_review.py --alert-id 1965449
backend/.venv/bin/python validation/compact_zeus/checkpoint_d/build_checkpoint_d_skill_context_review.py --alert-id 1965449
backend/.venv/bin/python validation/compact_zeus/checkpoint_d/build_checkpoint_d_skill_route_coverage.py
backend/.venv/bin/python validation/compact_zeus/checkpoint_d/build_checkpoint_d_analyzer_output_review.py --alert-id 1965449 --model-name deepseek-v4-pro
backend/.venv/bin/python validation/compact_zeus/checkpoint_d/build_checkpoint_d_evidence_grounding_review.py --alert-id 1965449
```

也可使用唯一编排入口一次重跑 D0-D6；它不调用 LLM：

```bash
./scripts/soc-runtime-validation.sh checkpoint-d
# 可选：SOC_CHECKPOINT_D_ALERT_ID=OTHER_ID ./scripts/soc-runtime-validation.sh checkpoint-d
```

修改上游 Adapter 或 D1-D5 任一生产边界后，从最早受影响的步骤开始重放。即使 D2 实体内容
没有变化，只要 D1 semantic hash 变化，也要重建其后的步骤，保持产物链引用一致。Skill 包、
Resolver 或 `SocSkillContext` 变更至少重跑 D5-D6。

D7 会调用真实模型并产生费用，因此与 deterministic D0-D6 分开：

```bash
./scripts/soc-runtime-validation.sh checkpoint-d-live
# 可选：SOC_VALIDATION_MODEL=OTHER_MODEL ./scripts/soc-runtime-validation.sh checkpoint-d-live
```

D8 不调用模型，但依赖已保存的 D5/D7：

```bash
./scripts/soc-runtime-validation.sh checkpoint-d-grounding
```

D3 的 `FieldTrust` 分开记录 `source_trust` 与 `reasoning_status/participates`。从 selected raw
evidence 得到的 canonical 副本继承来源可信度，但以 `excluded_duplicate_projection` 排除，避免
同一证据重复参与；unselected fallback 则保持 `unknown/excluded_unselected_fallback`。

D4 是内部、gitignored 的真实告警审阅步骤，经项目负责人明确批准后默认使用 `full` evidence
mode：对已选中的 password、token、cookie、header、body 不做额外脱敏，coverage 必须报告
`llm_sanitized_count=0`。源数据本身已有的掩码不会被还原。超长 JWT/base64 等编码片段仍按统一
bounded-context 策略替换为带长度与 hash 的占位符，并单独计入
`llm_compacted_encoded_paths`；这是模型上下文压缩，不是 sensitive redaction，完整原值仍保留在
immutable raw payload。D4 产物位于 `step-d4-bounded-analysis-input/`。

D5 不把完整 `SKILL.md` 或旧 Zeus 长提示词常驻模型上下文。`SocSkillResolver` 只输出白名单
Skill 名称、原因和命中特征；`build_soc_skill_context()` 使用 DeerFlow 的 Skill parser 校验实际
public package，再读取包内 `references/runtime-guidance.md`，记录 package/guidance hash 和估算
token 数。Lead Agent 仍可按 DeerFlow 机制动态读取完整 Skill 和 references，固定 Runtime 只接收
受预算约束的审阅投影。

D6 是离线路由覆盖，不是第六个生产 Runtime 节点。它必须检查每条 typed HTTP/email 证据都有
对应专业 Skill、业务 asset/group 不会单独被当成 endpoint host、typed `host.ip_addresses` 仍作为
endpoint identity、所有选中 Skill 都能从真实 package 投影，并保留每条样本的选择原因供审阅。

D7 只证明真实 Analyzer 能输出合法的开放场景、行为阶段、evidence 引用、竞争解释、证据缺口
和人工核查清单。`status=passed` 是结构验收，不是事实质量验收。

D8 使用 `soc.analysis_evidence_grounding.v2`。如果 source/value 能落地、但 description 引用了
quoted value 之外的有界字段，该项状态为 `description_context_leakage`，同时保留
`matched_context_paths` 和 `foreign_description_context_paths`；它仍计入 ungrounded，后续
Decision 必须降级。模型拼出的 `key=value`、整段对象伪装成 scalar、私有 omission sidecar 值和
未证实 outcome 不能绕过该边界。精确可见的 encoded-omission marker 只能证明值存在、编码形态和
模型边界省略，不能证明隐藏内容、token 有效性或安全结果。

当前 authoritative artifact 使用 `deepseek-v4-pro` / `soc-analysis-v8`，共 10 条 evidence：
8 grounded、2 条 `description_context_leakage`。D8 execution contract 通过，但质量门正确保持
`blocked`；两条拒绝分别把目标 IP 混入 source-IP evidence，以及把弱口令分类混入请求体 evidence。
模型重跑具有随机性，不能通过反复付费采样替代 Grounding。

D9 直接消费已保存的 D5/D7/D8 artifact，并调用生产 `SocDecisionPolicy`。它不重跑模型、不重新
Grounding、不写数据库，也不执行租户处置策略。审阅重点是确认 D8 的 rejected evidence 会形成
degraded/conflicted evidence、结构化 human-review reason 和 `automation_allowed=false`，同时保留 D7 的
detection verdict。产物位于
`step-d9-decision-policy/<alert-id>.decision.json`。

```bash
./scripts/soc-runtime-validation.sh checkpoint-d-decision
```

D10 从 D0 inventory 中按 topic 自动选择代表样本，不硬编码告警 ID：候选必须没有 D0 issue，
然后选择最接近该 topic 的 hitLog/raw-event/non-empty-message 中位数的一条；所有
`evidence_unavailable` known gaps 另行全部纳入。每条样本调用同一个无持久化
`SocAnalysisService`，使用 `SOC_VALIDATION_MODEL` 指定的真实 Analyzer 执行完整 Runtime，生成一份
matrix 和相邻完整 `AnalysisRun` JSON。D10 禁止回退 `StubLLMAnalyzer`，并验证跨来源控制流、source
mapping、模型/Prompt/Parser provenance、bounded evidence、Grounding、Decision fail-closed 和
automation guard。它会产生模型费用；没有人工标签时仍不能据此声称模型准确率，也不是新的
Runtime 节点。

当前 authoritative D10 覆盖 8 个 topic、6 类 source family、8 条代表样本和 2 条 known input
gap。2026-08-01 使用 `deepseek-v4-pro` 的 10/10 次真实调用共消耗 167,042 tokens，得到
8 `suspicious`、1 `needs_review`、1 `unknown`；0 Runtime failure、0 failed check，报告状态为
`passed_with_quality_findings`。87 条模型 evidence 中 67 条 grounded、20 条 ungrounded，其中
14 条为 `description_context_leakage`；Decision 因而全部保持 review-only 和
`automation_allowed=false`。两条空 raw-event 告警仍无 bounded input evidence，并由通用 critical
`analysis_evidence.unavailable` gap 显式触发 degraded evidence，而不是由模型猜测补齐输入。

```bash
./scripts/soc-runtime-validation.sh checkpoint-d-cross-source
# 可选：SOC_VALIDATION_MODEL=OTHER_MODEL ./scripts/soc-runtime-validation.sh checkpoint-d-cross-source
```

```bash
backend/.venv/bin/python -m pytest -q validation/compact_zeus/checkpoint_d
```
