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
```

修改上游 Adapter 或 D1-D4 任一生产边界后，从最早受影响的步骤开始重放。即使 D2 实体内容
没有变化，只要 D1 semantic hash 变化，也要重建其后的 D2-D4，保持产物链引用一致。

D3 的 `FieldTrust` 分开记录 `source_trust` 与 `reasoning_status/participates`。从 selected raw
evidence 得到的 canonical 副本继承来源可信度，但以 `excluded_duplicate_projection` 排除，避免
同一证据重复参与；unselected fallback 则保持 `unknown/excluded_unselected_fallback`。

D4 是内部、gitignored 的真实告警审阅步骤，经项目负责人明确批准后默认使用 `full` evidence
mode：对已选中的 password、token、cookie、header、body 不做额外脱敏，coverage 必须报告
`llm_sanitized_count=0`。源数据本身已有的掩码不会被还原。超长 JWT/base64 等编码片段仍按统一
bounded-context 策略替换为带长度与 hash 的占位符，并单独计入
`llm_compacted_encoded_paths`；这是模型上下文压缩，不是 sensitive redaction，完整原值仍保留在
immutable raw payload。D4 产物位于 `step-d4-bounded-analysis-input/`。

```bash
backend/.venv/bin/python -m pytest -q validation/compact_zeus/checkpoint_d
```
