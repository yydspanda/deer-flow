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

计划必须显示 10 个固定 ID、`deepseek-v4-flash`、10 次模型调用和
`simulated_read_only` Provider。

## Execute

```bash
backend/.venv/bin/python \
  validation/compact_zeus/e2e/run_ten_alert_e2e.py \
  --execute \
  --confirm-live \
  --confirm-investigation
```

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
    ├── 07-evidence-grounding.json
    ├── 08-decision.json
    ├── 09-investigation.json
    ├── 10-review-and-agent-context.json
    ├── 11-knowledge-candidates.json
    └── final-conclusion.json
```

先看 `SUMMARY.md`，再进入一个 `cases/<alert_id>/` 按编号审阅。每个
`final-conclusion.json` 汇总结构验收、模型结论、Grounding、确定性决策、只读调查证据、
ReviewQueue 和 Lead Agent 有界上下文。

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
标记模型漏写的 scenario rationale。歧义引用、冲突事实和安全语义缺失仍然拒绝。

## Latest Local Result

2026-08-10 最终同版代码 fresh cohort：

- `10/10` structural/safety acceptance passed；`6 suspicious / 4 needs_review`；
- 10 个 ReviewQueue、10 个 tenant-policy shadow decision、33 条 `mocked=true` 只读调查证据；
- 3 个模型质量 finding：`1965794` 把 boolean 复制成字符串，`1965802` 丢失正文尾部 CRLF，
  `1965449` 引用了不存在的 `A-*`；合计 3 条 `E-*`、5 条关联 `R-*` 未落地；
- 20 个 `K-*` review item，其中 17 个 support grounded、3 个 unresolved，全部 pending review；
- 0 automation、0 high-risk action、0 auto-close、0 confirmed-memory write。

这些 finding 是严格契约的真实输出，不通过重复采样或放宽 Grounding 隐藏。当前权威数字始终以
本地 `SUMMARY.md` 和 `summary.json` 为准。

## Authority Boundary

- 权威链路是 `原始输入 -> 固定 Runtime -> 持久化决策 -> ReviewQueue`。
- `asset.locate` / `security_tag.lookup` 当前使用现有只读模拟 Provider，结果必须保持
  `mocked=true`，不能关闭真实内网接入债务。
- Lead Agent 只生成可供分析师继续对话的有界上下文；其聊天文本不替代 Runtime 结论。
- 所有样本都必须保持 `automation_allowed=false`，本验证不会封禁、隔离或自动关单。
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
  validation/compact_zeus/internal_batch/test_run_pingan_runtime_batch.py
```
