# Human Review Artifact Builders

该目录从 canonical corpus 生成可供人工逐条对照的敏感 JSON。它服务于 Adapter
Checkpoint B/C 审阅，和 `checkpoint_d/` 的逐步 Runtime 重放是两条不同验证轨道。

- `build_pingan_adapter_review_artifacts.py`：Adapter 基线代表样本。
- `build_pingan_*_review_artifacts.py`：NIDS、EDR、NDR/HIDS、Threat Intel/SIEM 专项样本。

产物写入 `validation/compact_zeus/data/reviews/`。构建器可能显式使用 `full` evidence mode；
输出含真实字段值、不得提交或向无权限人员分享。业务逻辑必须留在
`backend/soc_agent/`，本目录只负责投影和展示。

具体命令与样本说明见上级 [`README.md`](../README.md)。
