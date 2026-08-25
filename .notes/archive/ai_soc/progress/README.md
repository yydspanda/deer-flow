# SOC Agent Progress Archive / 进度归档

这里保存从活动进度台账移出的完成记录。`progress.md` 是当前执行状态的唯一来源；本目录仅用于审计和追溯。

## Monthly Archives / 月度归档

| Month | Records | Notes |
|---|---:|---|
| [2026-08](2026-08.md) | 92 | Legacy records migrated by `PI-06` |
| [2026-07](2026-07.md) | 172 | Legacy records migrated by `PI-06` |
| [2026-06](2026-06.md) | 25 | Legacy records migrated by `PI-06` |
| [Legacy registers](legacy-registers.md) | n/a | Superseded capability table, stage duplicate and early slice plan |

迁移前的记录早于 `soc.progress.v1`，因此可能没有结构化 Task 行或完整实验 manifest。它们保留原始口径，但不能反向覆盖当前 Roadmap、实现事实或 Gate 状态。从 `PI-06` 启用治理后，新增归档记录的结构化 Task 必须继续存在于 Roadmap。

## Rotation Rule / 轮转规则

1. `progress.md` 最多保留 10 条近期完成记录。
2. 超出上限时，将最旧记录追加到完成日期对应的 `YYYY-MM.md`，同月按日期倒序排列。
3. 归档记录不可继续标记 `In Progress`；任务重新开启时，在活动文件新增记录并保留原 task ID。
4. 归档后运行 `python scripts/check_soc_progress.py`，确认月份、任务引用和活动文件预算。

## Experiment Manifest / 实验清单

凡是运行模型、评测语料、性能压测、对比 Prompt/config 或产生可引用指标，都在对应近期记录下增加一个 `#### Experiment` 小节和以下 JSON。`model` 在纯确定性实验中可写 `none`，但其他字段不得省略。

````markdown
#### Experiment — concise name

```json soc-experiment
{
  "experiment_id": "EXP-20260826-example",
  "task_id": "PI-03",
  "upstream_commit": "0123456789abcdef0123456789abcdef01234567",
  "model": "provider/model-name-or-none",
  "config_hash": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "data_hash": "sha256:abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
  "hardware": "Apple M5 / 32 GB / macOS 26; or CPU/GPU instance description",
  "command": "exact reproducible command with secrets replaced by environment variable names",
  "metrics": {
    "processed": 20,
    "failed": 0,
    "p95_latency_ms": 1234
  }
}
```
````

`config_hash` 应绑定去密后的有效配置或版本化运行 manifest；`data_hash` 应绑定实际输入 corpus/manifest，而不是文件名。命令不得包含密钥、Token 或内部密码。
