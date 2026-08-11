# Compact Zeus Validation

该目录用于本地、敏感的 Zeus 告警语料整理与 `zeusRawLogs` 上下文压缩验证。
它不是生产 ingestion 路径，生成的 PKL、HTML、Excel 和 notebook 输出不得提交或
对无告警权限的人员分享。

目录边界：

```text
datas/
├── source/          # 权威原始 PKL；只读输入
└── legacy_demos/    # 历史 JSON demo；仅用于 lineage/回归

validation/compact_zeus/
├── checkpoint_d/    # Runtime D0-D3 逐步验证与契约测试
├── corpus/          # 统一语料和压缩报告构建
├── audits/          # Topic/Adapter 全量字段流向审计
├── reviews/         # 人工审阅样本构建
├── shared/          # 受限 PKL loader 与编码压缩复用工具
├── internal_batch/  # 内网 5 -> 50 -> all、可续跑的生产 Runtime 批跑入口
├── e2e/             # 固定 10 条完整告警的统一端到端审阅入口
├── policy/          # post-Runtime Tenant Policy 与 PingAn EDR 快速策略验收
├── docs/            # 长期设计与审阅说明
├── data/            # gitignored 可再生产物
│   ├── corpus/      # 统一 212 条语料及 manifest
│   ├── audits/      # 全量字段审计 JSON
│   ├── reviews/     # 人工审阅样本
│   ├── compaction/  # HTML/Excel 压缩报告
│   └── exploration/ # 本地 notebook
└── README.md        # 总入口与可复跑命令
```

每个源码子目录都有自己的 `README.md`。脚本按职责归档，但所有生成物路径保持不变，
因此旧的本地审阅 JSON 无需迁移。`checkpoint_d/` 是后续逐步复盘 Runtime 的首选入口；
`audits/` 与 `reviews/` 用于 Adapter 批量覆盖验证，不代表 Runtime 固定流水线步骤。

`datas/` 与 `data/` 都包含内部告警数据并被 Git 忽略。前者是输入，不得由验证脚本
改写；后者的 `corpus/audits/reviews/compaction` 可按本 README 重建，`exploration`
只是可丢弃的本地研究记录，不作为验收证据。

## 1. 统一告警语料

源数据：

- `datas/source/full_alert_2026_month_forth_sample_200.pkl`：文件名写 200，实际为
  210 行、210 个唯一 `alert_id`。
- `datas/legacy_demos/*.json`：5 个历史 demo；其中 3 个 ID 已在 PKL，2 个缺失。

生成统一语料：

```bash
backend/.venv/bin/python \
  validation/compact_zeus/corpus/build_alert_validation_corpus.py
```

输出：

```text
validation/compact_zeus/data/corpus/
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
  validation/compact_zeus/shared/compact_encoded_llm_context.py --self-check

backend/.venv/bin/python \
  validation/compact_zeus/corpus/build_zeus_compaction_artifacts.py
```

输出按职责分开：HTML/Excel 写入
`validation/compact_zeus/data/compaction/`，可长期引用的 Markdown 说明写入
`validation/compact_zeus/docs/zeus_raw_logs_compaction_technical_intro.md`。探索 notebook
只保存在 gitignored `data/exploration/`。

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

## 3. 内网 Runtime 批跑

`internal_batch/run_pingan_runtime_batch.py` 使用同一个生产
`SocAnalysisService` 重放 PKL，不复制 Runtime 组装逻辑。它通过受限 unpickler 加载
`alert_full_data.alert_data`，默认单 worker、非持久化；真实模型必须显式
`--confirm-live`。输出逐条原子写入 Git-ignored、权限受限的
`backend/.deer-flow/soc-internal-validation/runtime-batches/`，支持指纹校验后的
`--resume`。

内网运行按 `5 -> 50 -> all` 扩大，完整命令、SQLite 持久化边界和产物解释见
[`internal_batch/README.md`](internal_batch/README.md)。该入口只跑固定 Runtime，不自动
调用 `asset.locate` 或 `endpoint.software_path.lookup`；这些 enrichment 继续由
Lead Agent/Action Dispatcher 治理。

## 4. 测试

### 4.1 统一 10 告警端到端验证

不再跨 `soc-runtime-validation`、`soc-internal-validation` 和
`soc-lead-agent-validation` 拼接一次完整告警。先查看计划，再显式运行 10 次真实模型调用：

```bash
backend/.venv/bin/python \
  validation/compact_zeus/e2e/run_ten_alert_e2e.py

backend/.venv/bin/python \
  validation/compact_zeus/e2e/run_ten_alert_e2e.py \
  --execute --confirm-live --confirm-investigation

backend/.venv/bin/python \
  validation/compact_zeus/e2e/run_ten_alert_e2e.py \
  --output-root backend/.deer-flow/soc-validation/e2e-ten-governed-current \
  --execute --confirm-live --confirm-investigation \
  --governed-automation-simulation --confirm-automation-simulation
```

输出统一写入 `backend/.deer-flow/soc-validation/e2e-ten-current/`；每条告警都包含
`00-ingress.json` 到 `12-effective-decision-and-automation.json` 及
`final-conclusion.json`，根目录另有待人工审核的 `knowledge-review/REVIEW.md`。固定样本、
`E-*` 原子事实 / `R-*` 推理引用、两个被排除的输入缺口、替代样本和审阅边界见
[`e2e/README.md`](e2e/README.md)。automation simulation 只调用 validation-only mock adapter，
不会访问外部封禁系统；同 cohort 新旧结果使用 `compare_ten_alert_e2e.py` 对比。

### 4.2 PingAn EDR 安全路径 10 条专项验收

```bash
backend/.venv/bin/python \
  validation/compact_zeus/policy/validate_pingan_edr_safe_path_fast_policy.py
```

该命令只使用 stub 验证真实 EDR 输入、路径目录、PingAn Tenant Policy 和 Effective
Decision 的确定性集成，不进行模型质量评估，也不授权/执行外部动作。主报告位于命令输出的
`acceptance.json`；固定样本、正反例断言和真实路径族覆盖缺口见
[`policy/README.md`](policy/README.md)。

### 4.3 验证脚本测试

```bash
backend/.venv/bin/python -m pytest -q \
  validation/compact_zeus/e2e \
  validation/compact_zeus/corpus \
  validation/compact_zeus/audits \
  validation/compact_zeus/checkpoint_d
```

Notebook 只作为探索记录；可复跑规则以 Python 构建器、测试和 manifest 为准。

## 5. Checkpoint D-0：原始输入盘点

进入全量 Runtime 回放前，先独立运行 D-0：

```bash
backend/.venv/bin/python \
  validation/compact_zeus/checkpoint_d/build_checkpoint_d_corpus_inventory.py
```

输出写入：

```text
backend/.deer-flow/soc-runtime-validation/checkpoint-d/
└── step-d0-corpus-inventory/corpus-inventory.json
```

D-0 只读取统一 PKL，验证文件 hash、212 个唯一告警、wrapper/alert ID、topic、
`hitLog`、`zeusRawLogs` 和 `message` 可用性。逐行结果不复制原始 message 值，只记录
计数、输入形态和 issue code。它明确不执行 message parsing、Normalizer、实体/事实重建、
LLM 投影、Analyzer、Decision Policy 或持久化。`evidence_unavailable` 是上游输入缺口，
与 Adapter/Runtime 失败分开报告。

D-0 经人工确认后，D-1 只重放一条 message-first 样本的生产 canonical normalization：

```bash
backend/.venv/bin/python \
  validation/compact_zeus/checkpoint_d/build_checkpoint_d_normalization_review.py \
  --alert-id 1965449
```

输出写入同一 Checkpoint D 树的 `step-d1-canonical-normalization/`。D-1 完整保留本地
`normalized_alert` 供人工对照，同时检查 canonical corpus lineage、Adapter/source type、
message parser、`raw_message_first`、canonical provenance 和 raw payload hash；它不运行 generic
entity extraction、fact reconstruction、analysis input、LLM、decision 或 persistence。Canonical
契约全部通过但存在嵌套 parser warning 时，状态为 `passed_with_parser_warnings`；accepted/rejected
repair 分开计数，且 repaired value 不能冒充 strict decoded source fact。

D-1 经人工确认后，D-2 使用 Runtime 的公开 `inspect_alert_normalization()` 边界重放同一
canonical row，并将 normalized semantics 与 D-1 对比后运行 generic deterministic entity
extraction。完整 hash 仍写入产物；当上游没有接收时间时，只允许 Runtime 生成的
`event.received_at` 不同，其他差异均失败：

```bash
backend/.venv/bin/python \
  validation/compact_zeus/checkpoint_d/build_checkpoint_d_entity_extraction_review.py \
  --alert-id 1965449
```

输出位于 `step-d2-generic-entity-extraction/`，包含完整 `ExtractedEntities`、mention 的
kind/role/evidence path 和生产 `ExtractionReport`。D-2 不运行 fact reconstruction、analysis
input、skill、LLM、decision 或 persistence；网络告警缺少 process/user/host 等实体属于显式
extraction gap，不自动判为 Adapter 或告警失败。

D-2 经人工确认后，D-3 重放 D1/D2 并调用生产 `reconstruct_facts()`：

```bash
backend/.venv/bin/python \
  validation/compact_zeus/checkpoint_d/build_checkpoint_d_fact_reconstruction_review.py \
  --alert-id 1965449
```

输出位于 `step-d3-fact-reconstruction/`，保留完整 `FactReconstructionResult`：evidence policy、
`FieldTrust`、canonical provenance、`RoleClaim`、`ScenarioHypothesis`、`RoleResolution`、
`ConflictReport` 和 warnings。D-3 同时验证 D1 normalized semantics 与 D2 entities 未漂移；
`raw_message_first` 成功时，未选中的 structured fallback 必须是 `unknown` trust、不可参与事实
重建的审计记录；canonical duplicate projection 从 provenance 继承 `source_trust`，但使用
`reasoning_status=excluded_duplicate_projection`、`participates=false` 防止同一证据重复投票。
D-3 不构建 analysis input，不运行 skill、LLM、grounding、decision 或 persistence。

D-3 经人工确认后，D-4 重放 D1-D3，并调用生产 `build_llm_analysis_request()` 生成真正会交给
后续 Skill/Prompt 节点的 bounded contract 与 `EvidenceCoverageReport`：

```bash
backend/.venv/bin/python \
  validation/compact_zeus/checkpoint_d/build_checkpoint_d_bounded_analysis_input_review.py \
  --alert-id 1965449
```

输出位于 `step-d4-bounded-analysis-input/`。当前 PingAn 内部验证经项目负责人明确批准，D-4
默认使用 `full` mode，不额外脱敏已选中的 password/token/cookie/header/body；通用 Runtime
部署默认仍为 `redact`。超长编码片段继续由 bounded-context compaction 替换为带 hash 的占位符，
完整原文只保留在 immutable raw payload；coverage 将其计入 `llm_compacted_encoded_paths`，不得
误记为 sanitized。D-4 不运行 skill resolution、Prompt、LLM、grounding、decision 或 persistence。

## 6. PingAn Adapter 覆盖审阅

在修改 PingAn Adapter 前，先审阅
[`docs/pingan_adapter_rebuild_review.md`](docs/pingan_adapter_rebuild_review.md)。它记录
212 条语料的 Topic/source type/message parser 基线、保持不变的 message-first
与 structured-fallback 契约、代表样本以及需要业务确认的分类。

重建 Checkpoint B 本地审阅产物：

```bash
backend/.venv/bin/python \
  validation/compact_zeus/reviews/build_pingan_adapter_review_artifacts.py
```

输出位于 `validation/compact_zeus/data/reviews/pingan-adapter-checkpoint-b/`，包含完整解析
字段、source-field semantics 和 bounded evidence。构建器显式使用获批的 `full`
evidence mode，字段值保持原始且产物属于敏感本地数据，不得提交。

### Checkpoint C: NIDS 字段使用

重跑 95 条 NIDS、128 个 message 的字段流向审计：

```bash
backend/.venv/bin/python \
  validation/compact_zeus/audits/build_pingan_nids_field_audit.py
```

生成四组敏感本地 before/after 产物：

```bash
backend/.venv/bin/python \
  validation/compact_zeus/reviews/build_pingan_nids_review_artifacts.py \
  --phase before_adapter_mapping

backend/.venv/bin/python \
  validation/compact_zeus/reviews/build_pingan_nids_review_artifacts.py \
  --phase after_adapter_mapping
```

输出：

```text
validation/compact_zeus/data/
├── audits/pingan-nids-field-audit.json
└── reviews/pingan-nids-checkpoint-c/
    ├── before_adapter_mapping/
    └── after_adapter_mapping/
```

审计把每个 parsed leaf 分成 `canonical_provenance`、`fact`、`scenario`、
`llm` 四条使用通道，并单独统计五元组、网络/HTTP observation、high-value gap 和
LLM encoded compaction。未进入 canonical 的字段仍保留在 parsed/bounded evidence；
不能把“未 canonical 化”解释成“原始字段已丢失”。

### Checkpoint C: EDR 字段使用

重跑 37 条 EDR、60 个 message 的字段流向审计：

```bash
backend/.venv/bin/python \
  validation/compact_zeus/audits/build_pingan_edr_field_audit.py \
  --output validation/compact_zeus/data/audits/pingan-edr-field-audit.after.json
```

生成五组敏感本地 before/after 审阅产物：

```bash
backend/.venv/bin/python \
  validation/compact_zeus/reviews/build_pingan_edr_review_artifacts.py \
  --phase before_adapter_mapping

backend/.venv/bin/python \
  validation/compact_zeus/reviews/build_pingan_edr_review_artifacts.py \
  --phase after_adapter_mapping
```

输出位于：

```text
validation/compact_zeus/data/
├── audits/pingan-edr-field-audit.before.json
├── audits/pingan-edr-field-audit.after.json
└── reviews/pingan-edr-checkpoint-c/
    ├── before_adapter_mapping/
    └── after_adapter_mapping/
```

当前结果：5 条 `edr-core-xc` 告警包含 14 个 message、21 个 `detailsN` 记录；
适配后整个 EDR 子集生成 30 个 process observations、39 个 process nodes 和 7 个
file observations。19 个合法 MD5 与 19 个合法 SHA-256 可进入标准实体；2+2 个短值
保持在 parsed/LLM evidence，并由 `invalid_process_hash` 语义明确禁止进入实体。
`iplist`、`str_source_ip`、`device__ip` 只形成 endpoint host IP 与 provisional
victim/impacted-asset claims；合法且不同于 endpoint 的 `str_attack_ip` 只形成 typed IOC
与 tentative attacker candidate。端点排除会在同一 raw-event scope 内同时比较 message
解析字段和 structured fallback，避免字段拆在两层时制造假远端。`str_threat_value`/`str_activity_id` 不按字符串形状映射为
destination/hash。当前语料没有可靠 EDR directional connection contract，因此 37 条告警的
canonical source/destination 和 network observations 均为 0；这属于安全的“未虚构方向”，
不是字段丢失。所有 37 条输入的 raw payload hash 均保持不变。

### Checkpoint C: Threat Intel / SIEM 字段使用

重跑 Threat Intel 与 SIEM 全量子集审计：

```bash
backend/.venv/bin/python \
  validation/compact_zeus/audits/build_pingan_ti_siem_field_audit.py
```

生成四组敏感本地代表样本：

```bash
backend/.venv/bin/python \
  validation/compact_zeus/reviews/build_pingan_ti_siem_review_artifacts.py
```

输出位于：

```text
validation/compact_zeus/data/
├── audits/pingan-ti-siem-field-audit.json
└── reviews/pingan-ti-siem-checkpoint-c/
    ├── threat-intel-single-message-1965919.json
    ├── threat-intel-multiple-messages-1973156.json
    ├── siem-suspicious-email-1966022.json
    └── siem-standard-machine-copy-1965891.json
```

当前结果：3 条 Threat Intel 告警包含 4 个 message，形成 4 个独立网络 observation；
`net.*` 是 wire session，`attacker/victim` 是独立 provider assertions，3/3 条均提取
host、external IOC、malware 和 `T1496`，asset CIDR/range 不进入 host IP。10 条 SIEM
告警包含 15 个 structured events，其中 6 条可疑邮件（7 events）形成 6 个 email
observations，4 条标机克隆（8 events）形成 host/IP candidates；没有 SIEM 告警被虚构
network direction，也没有把 `User=system` 当作 actor。合并审计有 159 条 canonical
provenance、0 high-value gap、0 raw mutation。生成目录包含 `full` 模式真实告警内容，
已 gitignore，不得提交。

### Checkpoint C: NDR/APT / HIDS 字段使用

重跑 NDR/APT 与 HIDS 全量子集审计：

```bash
backend/.venv/bin/python \
  validation/compact_zeus/audits/build_pingan_ndr_hids_field_audit.py

backend/.venv/bin/python \
  validation/compact_zeus/reviews/build_pingan_ndr_hids_review_artifacts.py
```

输出位于：

```text
validation/compact_zeus/data/
├── audits/pingan-ndr-hids-field-audit.json
└── reviews/pingan-ndr-hids-checkpoint-c/
    ├── ndr-*.json
    └── hids-*.json
```

当前结果：44 条 NDR/APT 告警包含 105 个 message，形成 105 个独立网络
observations、63 个 HTTP observations 和 20 个网络内容文件 observations；43 条有完整
canonical source/destination。`ioc` 在该来源语料中实际承载厂商检测描述，不进入 typed
IOC。23 条 HIDS 告警包含 46 个 message，形成 44 个 process observations、122 个 process
nodes、21 个 file observations，以及 5 个仅由明确事件契约产生的 network observations。
HIDS canonical source/destination 保持为空，`external_ip=1.1.1.1` 不进入 host/IOC/网络推理。
两类输入 raw hash 零变化。实例级审计按 nested leaf 追踪 `_origin.*`、`payload.*` 和超过
四条 full supplementary 预算的 message；v3 审计确认 8,436 个非空 parsed leaf 实例均有
typed consumer 或 exact `SourceFieldSemantic`，未分类实例、known high-value instance gap、
structured-fallback violation 均为零。只要存在可解析 message，Zeus 外层加工字段只保留在
raw，不能进入分析通道。高价值
overflow 通过受限 `BoundedEvidenceHighlight` 保留；重复值仅向 Prompt 暴露 occurrence count
和最多 5 个代表路径，完整覆盖路径留在 coverage 审计。11 份代表样本产物使用 `full` 模式，
包含敏感真实告警并已 gitignore，不得提交。
