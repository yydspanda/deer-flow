# SOC Runtime Validation Runbook / 运行时逐步验证手册

> Purpose: reproduce every current Runtime/evaluation/governance artifact for review.  
> Storage: `backend/.deer-flow/soc-runtime-validation/` (gitignored, contains real-alert-derived data).  
> Latest local index: `backend/.deer-flow/soc-runtime-validation/RUN-INDEX.md`.

## 1. What Is Actually Linear / 真正固定流水线

```mermaid
flowchart LR
    A["📨 Input<br/>原始告警"] --> B["🔌 Normalize<br/>适配与规范化"]
    B --> C["🧩 Entity Extract<br/>实体提取"]
    C --> D["🧭 Fact Reconstruct<br/>事实/角色/冲突重建"]
    D --> E["📦 Bounded Input<br/>有界、脱敏模型输入"]
    E --> F["🧰 Skill Context<br/>技能上下文"]
    F --> G["🧠 Analyze<br/>Stub 或 Live LLM"]
    G --> H["✅ Schema Validate<br/>结构与领域校验"]
    H --> I["🔗 Evidence Grounding<br/>证据落地校验"]
    I --> J["⚖️ Decide<br/>确定性决策策略"]
```

`normalize -> entity_extract -> fact_reconstruct -> build_analysis_input -> skill_context -> analyze -> schema_validate -> evidence_grounding -> decide`

以下编号是审阅轨道，不应被误解为都在上述主流水线中：

- Step 7 是离线 normalization mapping suggestion，永不自动修改 Adapter。
- Step 9 是人工真值/置信度校准门禁。
- Step 10-11 是确定性回放、主编排和 correlation 离线评测。
- Step 11-12 是 governed context 与 authorization shadow 旁路，不改 Runtime verdict。

## 2. One Entry Point / 唯一重跑入口

从仓库根目录执行：

```bash
# 只跑确定性的 Step 01-05
./scripts/soc-runtime-validation.sh core

# 调用真实模型，跑 Step 06-09
SOC_VALIDATION_MODEL=deepseek-v4-flash ./scripts/soc-runtime-validation.sh live

# 跑 Step 10-12，不调用外部模型
./scripts/soc-runtime-validation.sh evaluations

# 根据已有结果重建 manifest 和总索引
./scripts/soc-runtime-validation.sh finalize

# 重跑 212 条真实语料的 Checkpoint D；D0-D6 全部确定性执行，不调用模型
./scripts/soc-runtime-validation.sh checkpoint-d

# 在已确认 D5 上调用真实模型并生成 D7 typed Analyzer 产物
SOC_VALIDATION_MODEL=deepseek-v4-flash ./scripts/soc-runtime-validation.sh checkpoint-d-live

# 对已保存的 D7 做 deterministic D8 Grounding，不再次调用模型
./scripts/soc-runtime-validation.sh checkpoint-d-grounding

# 对已保存的 D5/D7/D8 执行 production D9 Decision Policy，不调用模型或数据库
./scripts/soc-runtime-validation.sh checkpoint-d-decision

# 按 topic 选择代表样本并纳入全部 known input gaps，执行 D10 真实模型完整 Runtime 回放
SOC_VALIDATION_MODEL=deepseek-v4-flash ./scripts/soc-runtime-validation.sh checkpoint-d-cross-source

# 全部 212 条各执行两次无模型 Runtime，验证 payload 兼容性与语义稳定性
./scripts/soc-runtime-validation.sh checkpoint-d-full-corpus

# 全部依次执行
./scripts/soc-runtime-validation.sh all
```

重跑前如需保留当前本地产物：

```bash
./scripts/soc-runtime-validation.sh snapshot
```

Runtime 验证本身不需要 Docker。Boss Web 演示需要 Docker；若 CLI/socket 不可用，先启动
Docker Desktop，等待 Engine Ready，并确认当前发行版的 WSL Integration 已开启。

`checkpoint-d` 与下面的历史 Step 01-12 review package 是两条不同审阅轨道：前者按当前真实
PKL 语料逐边界验证 deterministic D0-D6；`checkpoint-d-live` 只在确认后的 D5 上调用一次真实
Analyzer 生成 D7；`checkpoint-d-grounding` 再对 D7 运行 production D8 Grounding，不调用
模型；`checkpoint-d-decision` 消费保存的 D5/D7/D8 并运行 production D9 Decision Policy，不写
数据库；`checkpoint-d-cross-source` 从 D0 选择各 topic 代表样本和全部 known input gaps，使用
显式配置的真实模型运行 production Runtime，产生模型费用且禁止回退 stub；
`checkpoint-d-full-corpus` 对全部 212 条执行双遍 stub Runtime，只验证结构兼容性、fail-closed 和
语义稳定性，不执行持久化 replay。后者保留五个 legacy demos、live model、correlation 和
governance 证据。D6 只是全语料 Skill 路由覆盖，D7-D10 是 Analyzer/Grounding/Decision/跨来源
审阅边界，D11 是全语料兼容性门禁，都不是固定 Runtime 新节点。

### 2.1 D12 Asset Provider Handoff / 资产能力源交接

D12 不是固定 Runtime 新节点，而是 PI-01 的外部只读 provider 接入验证。D12-A 已在外网通过
fake transport 验证代码、协议和 MCP/action 链路；它必须保留 `mocked=true`，不能作为真实接入证据。
完整命令、配置变量和内网交接说明见
`backend/samples/mcp/pingan_asset/README.md`。

D12-A 产物默认保存到：

```text
backend/.deer-flow/soc-runtime-validation/checkpoint-d/
  step-d12-pingan-asset-provider/d12-a-fake-smoke.json
```

D12-B 必须在内网直接使用 tracked `extensions.internal.example.json` 并注入真实环境变量运行同一条
`soc mcp smoke` 路径，并分别保留成功、查无、鉴权失败、超时和 evidence persistence 报告。
只有结果明确 `mocked=false` 后，才能把 D12-B、PA-12 或第一项 PI-01 provider 标记为完成。

## 3. Step Contract / 每步看什么

| Seq | Directory / 目录 | Input / 输入 | Output / 重点产物 | Pass meaning / 通过含义 |
|---:|---|---|---|---|
| 1 | `step-01-input-adapter` | 5 条 `datas/legacy_demos/*.json` | Adapter、source type、raw-message inventory | 输入来源和供应商 Adapter 被明确识别 |
| 2 | `step-02-message-parsing` | Step 1 + 原始 payload | canonical alert、完整 message parse、decode/repair、coverage | 原始 payload 保留；可解析字段进入高信任 message projection |
| 3 | `step-03-fact-reconstruction` | canonical + role/scenario claims | role resolutions、scenario hypotheses、conflicts、provenance | 不默认 attacker=source；角色不确定性和冲突可见 |
| 4 | `step-04-build-analysis-input` | Step 3 | `LLMAnalysisRequest` | 模型只看到有界、脱敏、限长证据，不看到无限 raw payload |
| 5 | `step-05-normalization-maintenance` | schema/coverage observations | baseline + maintenance issues | 格式漂移/截断产生维护 issue，但不改变 verdict |
| 6 | `step-06-live-llm` | APT 代表样本 | 完整 live `AnalysisRun` | DeerFlow model path、JSON/schema validation、grounding、decision 全部执行 |
| 7 | `step-07-live-normalization-suggestion` | normalization report | candidate mappings | 仅供工程师审阅，`auto_apply_allowed=false` |
| 8 | `step-08-runtime-hardening` | Step 6 live run | 安全断言摘要 | 不合格证据必须触发 degraded + review + automation blocked |
| 9 | `step-09-confidence-labeling` | 5 条 live runs | 新 pending label set + validation | 未经人工真值不得校准；不覆盖历史人工标注文件 |
| 10 | `step-10-five-sample-repair` | 5 条样本 | stub deterministic runs | 解析/修复/决策可离线重复 |
| 10 | `step-10-correlation-bridge` | 3 条 capability fixtures | unified investigation reports | 主编排能消费 typed correlation/evidence/finding |
| 11 | `step-11-correlation-eval` | 8 条受控 pair | baseline + replay diff | lineage 无泄漏、重跑无变化；不开放生产 suppress |
| 11 | `step-11-governed-context` | 授权业务真值 | proposed/active/history/query + SQLite | append-only、乐观版本、生命周期查询生效 |
| 12 | `step-12-authorization-shadow` | HIDS/EDR + active facts | exact match records | 只读 shadow；不改检测真值、不关单、不授权动作 |

Checkpoint D 当前增加：

| Seq | Directory / 目录 | Review / 审阅重点 |
|---:|---|---|
| D5 | `checkpoint-d/step-d5-skill-context` | 选择原因、实际 Skill package、bounded guidance、package/projection hash、token budget；不调用 LLM |
| D6 | `checkpoint-d/step-d6-skill-route-coverage` | 212 条 typed HTTP/email 路由覆盖、host/asset 误路由、package 投影完整性；离线评测 |
| D7 | `checkpoint-d/step-d7-analyzer-output` | 真实模型、Prompt/Parser 版本、`AnalysisResult.v2`、开放场景、行为阶段、证据索引、竞争解释、缺口和核查项；不运行 Grounding/Decision |
| D8 | `checkpoint-d/step-d8-evidence-grounding` | production source/value Grounding、description sibling-fact leakage、scenario 引用接受/拒绝状态；不运行 Decision |
| D9 | `checkpoint-d/step-d9-decision-policy` | production Decision Policy、evidence state、review reasons、automation guard；不运行模型、租户处置或持久化 |
| D10 | `checkpoint-d/step-d10-cross-source-runtime` | 8 topic / 6 source family 真实模型 representative matrix、完整 9-step Runtime、模型/token provenance、Grounding 质量和 known input gap fail-closed；无人工标签时不评估模型准确率 |
| D11 | `checkpoint-d/step-d11-full-corpus-runtime` | 212 条 × 2 次 stub Runtime、D0/corpus lineage、九步兼容性、语义哈希稳定性、known gap fail-closed，以及 parser warning/compaction/omission/truncation/high-value gap/conflict/Grounding/Decision 分层统计与验收；仅失败行保存完整 diagnostic |
| D12-A | `checkpoint-d/step-d12-pingan-asset-provider` | production-shaped PingAn asset provider + fake MCP smoke；必须为 `mocked=true`，只证明代码/协议/fallback |
| D12-B | 内网指定报告目录 | 真实 ZEUS/workflow provider smoke；必须为 `mocked=false`，覆盖成功/查无/鉴权失败/超时和 `InvestigationEvidence` |

## 4. 2026-07-17 Latest Run / 本次实跑结果

总结果：`13 passed + 1 expected_pending + 0 failed/missing`。

| Sample | Live verdict | Confidence | Grounded | Rejected | Runtime result |
|---|---|---:|---:|---:|---|
| `apt-1965449` | `needs_review` | 0.52 | 3 | 8 | 安全降级并阻止自动化 |
| `apt-2025642` | `suspicious` | 0.78 | 13 | 2 | 安全降级并阻止自动化 |
| `apt-2026494` | `suspicious` | 0.70 | 10 | 0 | 全部证据落地，仍因未校准而复核 |
| `edr-1965810` | `needs_review` | 0.55 | 7 | 0 | 全部证据落地，仍因未校准而复核 |
| `hids-1965448` | `suspicious` | 0.65 | 5 | 1 | 安全降级并阻止自动化 |

本次关键发现：

1. 5 条真实模型调用均完成，没有 silent fallback。
2. 49 条模型证据中 38 条落地、11 条被拒绝；3 条样本触发
   `ungrounded_analysis_evidence`。Runtime 正确将其降级为人工复核并保持
   `automation_allowed=false`，但模型引用质量仍是后续优化项。
3. 5 条 confidence label 全部保持 pending；这是治理门禁的预期结果，不是失败。
4. Step 5 对 APT 样本保留 nested decode warning/evidence truncation maintenance signal；外层 parser
   成功时 schema 仍为 recognized，routine truncation 不再直接降级 Decision。
5. Step 7 生成 14 条候选 mapping，全部禁止 auto-apply。
6. Correlation 受控语料 retrieval precision `0.667`、recall `1.0`，replay diff `changed=false`；
   该数字不代表生产分布，也不能用于自动抑制。
7. HIDS/EDR 已知授权事实均得到 `exact` shadow match，但不会修改检测真值或自动关单。

## 5. Review Order / 明天逐步审阅顺序

1. 先看 `RUN-INDEX.md`，确认本次产物完整性和已知发现。
2. 逐样本查看 Step 1-4，重点审查字段是否遗漏、message 是否完整解析、角色/场景是否正确。
3. 查看 Step 5，区分真实格式漂移、证据截断和已接受 baseline。
4. 查看 Step 6 的完整 live run，再看 Step 8 如何拦截未落地证据。
5. 查看 Step 9 五样本 manifest；人工真值确认前不要运行 confidence calibration。
6. 最后查看 Step 10-12，确认关联、治理事实和授权旁路均没有越过决策/审批边界。

## 6. Boundaries / 注意事项

- 产物含真实告警衍生字段，只能留在 gitignored 本地目录。
- `label-set.pending.json` 是已有人工审阅数据；脚本只写
  `label-set.rerun.pending.json`，绝不覆盖前者。
- LLM 输出能被成功解析不等于证据可信；必须继续经过 grounding 和 decision policy。
- Step 7 suggestion、Step 11 correlation threshold、Step 12 exact match 都不能自动修改生产逻辑。
- 任何 live 模型失败都会留下 `.failed` 临时产物并停止，不用旧结果冒充新重跑。
