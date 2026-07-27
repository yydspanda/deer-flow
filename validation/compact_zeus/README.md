# Compact Zeus Validation

该目录用于本地、敏感的 Zeus 告警语料整理与 `zeusRawLogs` 上下文压缩验证。
它不是生产 ingestion 路径，生成的 PKL、HTML、Excel 和 notebook 输出不得提交或
对无告警权限的人员分享。

## 1. 统一告警语料

源数据：

- `datas/full_alert_2026_month_forth_sample_200.pkl`：文件名写 200，实际为
  210 行、210 个唯一 `alert_id`。
- `datas/*.json`：5 个历史 demo；其中 3 个 ID 已在 PKL，2 个缺失。

生成统一语料：

```bash
backend/.venv/bin/python \
  validation/compact_zeus/build_alert_validation_corpus.py
```

输出：

```text
validation/compact_zeus/data/
├── full_alert_validation_corpus.pkl
└── full_alert_validation_corpus.manifest.json
```

合并规则：

- 保持一条 `alert_id` 一个 canonical row。
- PKL 已存在的 ID 以 PKL 为权威版本。
- 完全相同的历史 JSON 只增加 `source_refs`。
- 内容冲突的历史 JSON 完整保存在 `legacy_demo_variants`，但不成为 canonical
  输入，也不增加重复行。
- 缺失的历史 JSON 包装为标准
  `app_code/flow_id/alert_id/alert_data` 的 `alert_full_data` 行。
- 新增 demo 没有历史 `agent_response`，明确保存为 null/missing；既有
  `agent_response` 是历史模型输出，不是人工 ground truth。

构建器会验证：

- wrapper、payload、metadata 和三处 alert ID 一致；
- 原 PKL 的全部原始列/行保持不变；
- 输出 ID 唯一、hash 与 lineage 完整；
- 历史 `agent_response` 是 JSON object 且 alert ID 一致；
- 每条 canonical `alert_data` 都可通过当前
  `normalize_alert_payload()` 和 message-schema observation。
- 每条存在第一条 structured raw event 的 `structured_fallback` 都会产生
  `BoundedAnalysisEvidence`；manifest 分开统计可投影 fallback、空
  `zeusRawLogs` 数据缺口、字段投影数和预算截断数。
- 每条 canonical 告警都会构建一次生产 `LLMAnalysisRequest`，不按 topic
  选择性启用；manifest 按 topic/source type 统计已检查告警、实际压缩告警和片段数。
- 构建前后比较 canonical payload SHA-256；任何 Runtime 投影导致的输入变更都会使
  policy contract 失败。

Manifest 不保存原始字段值，只保存计数、hash、冲突路径和 adapter 覆盖缺口。

## 2. ZeusRawLogs 压缩验证

统一 corpus 生成后运行：

```bash
backend/.venv/bin/python \
  validation/compact_zeus/compact_encoded_llm_context.py --self-check

backend/.venv/bin/python \
  validation/compact_zeus/build_zeus_compaction_artifacts.py
```

压缩只作用于 `alert_full_data` 下的 `zeusRawLogs` LLM projection，不修改
canonical PKL，不处理 `agent_response`。HTML 左右对比含完整原始日志，只能在
授权范围内使用。

生产 Runtime 独立拥有
`backend/soc_agent/pipeline/encoded_context.py`。本目录的验证脚本只能导入该生产
模块，生产代码禁止反向导入 `validation.*`。算法在统一 LLM bounded-evidence
边界处理所有 PingAn topic，而不是仅处理 NIDS；只按内容形态压缩长
Base64/JWT/hex/escape/PEM 等片段，不做解码。占位符记录 kind、原字符数和 12 位
SHA-256 前缀，侧车记录 path 与完整 SHA-256。二者不能通过 evidence grounding
变成研判事实，完整原值仍只存在于不可变 raw/parsed evidence。

当前 212 条 corpus 的生产投影实测结果：

- 212/212 条、8/8 个 topic 均完成投影检查；
- 112 条实际压缩 210 段：NIDS 180、APT 8、APT Detail 3、HIDS 19；
- EDR、SIEM、Threat Intel 已检查但当前样本无满足阈值的长编码片段；
- raw payload hash 变化数为 0。

## 3. 测试

```bash
backend/.venv/bin/python -m pytest -q \
  validation/compact_zeus/test_build_alert_validation_corpus.py \
  validation/compact_zeus/test_build_pingan_nids_field_audit.py
```

Notebook 只作为探索记录；可复跑规则以 Python 构建器、测试和 manifest 为准。

## 4. PingAn Adapter 覆盖审阅

在修改 PingAn Adapter 前，先审阅
[`pingan_adapter_rebuild_review.md`](pingan_adapter_rebuild_review.md)。它记录
212 条语料的 Topic/source type/message parser 基线、保持不变的 message-first
与 structured-fallback 契约、代表样本以及需要业务确认的分类。

重建 Checkpoint B 本地审阅产物：

```bash
backend/.venv/bin/python \
  validation/compact_zeus/build_pingan_adapter_review_artifacts.py
```

输出位于 `validation/compact_zeus/data/pingan-adapter-checkpoint-b/`，包含完整解析
字段、source-field semantics 和 bounded evidence。构建器显式使用获批的 `full`
evidence mode，字段值保持原始且产物属于敏感本地数据，不得提交。

### Checkpoint C: NIDS 字段使用

重跑 95 条 NIDS、128 个 message 的字段流向审计：

```bash
backend/.venv/bin/python \
  validation/compact_zeus/build_pingan_nids_field_audit.py
```

生成四组敏感本地 before/after 产物：

```bash
backend/.venv/bin/python \
  validation/compact_zeus/build_pingan_nids_review_artifacts.py \
  --phase before_adapter_mapping

backend/.venv/bin/python \
  validation/compact_zeus/build_pingan_nids_review_artifacts.py \
  --phase after_adapter_mapping
```

输出：

```text
validation/compact_zeus/data/
├── pingan-nids-field-audit.json
└── pingan-nids-checkpoint-c/
    ├── before_adapter_mapping/
    └── after_adapter_mapping/
```

审计把每个 parsed leaf 分成 `canonical_provenance`、`fact`、`scenario`、
`llm` 四条使用通道，并单独统计五元组、网络/HTTP observation、high-value gap 和
LLM encoded compaction。未进入 canonical 的字段仍保留在 parsed/bounded evidence；
不能把“未 canonical 化”解释成“原始字段已丢失”。
