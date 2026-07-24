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

Manifest 不保存原始字段值，只保存计数、hash、冲突路径和 adapter 覆盖缺口。

## 2. ZeusRawLogs 压缩验证

统一 corpus 生成后运行：

```bash
backend/.venv/bin/python \
  validation/compact_zeus/build_zeus_compaction_artifacts.py
```

压缩只作用于 `alert_full_data` 下的 `zeusRawLogs` LLM projection，不修改
canonical PKL，不处理 `agent_response`。HTML 左右对比含完整原始日志，只能在
授权范围内使用。

## 3. 测试

```bash
backend/.venv/bin/python -m pytest -q \
  validation/compact_zeus/test_build_alert_validation_corpus.py
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
字段和 bounded evidence。构建器显式使用获批的 `full` evidence mode，字段值保持
原始且产物属于敏感本地数据，不得提交。
