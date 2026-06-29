# SOC Agent 文档

## 当前文档

| 文档 | 状态 |
|---|---|
| `soc-agent-solution.md` | 当前权威产品/系统方案，决定“做什么、先后顺序、用户入口和阶段边界” |
| `normalization-drift-strategy.md` | 供应商日志归一化、漂移检测、LLM 低频辅助策略 |
| `progress.md` | 长期开发进度台账，记录当前 Phase、切片状态、完成记录和下一步 |

## 执行规则

1. 先看 `soc-agent-solution.md` 决定产品方向和阶段范围。
2. 再看 `.notes/reference-index/soc-agent-engineering-contracts.md` 决定代码怎么分层、接口怎么设计、测试怎么约束。
3. `reference-index` 是工程契约和参考索引，不覆盖 `soc-agent-solution.md` 的产品优先级。
4. 若两份文档措辞冲突，以本目录的 `soc-agent-solution.md` 为方向源头，并同步修正 `reference-index`。
5. 开发进度和 checkpoint 只写入 `progress.md`，不要散落到新文档。

## 已清理内容

- 三份旧 HTML 架构/选型图已删除：它们基于早期 `memory.json` 和旧技术选型，已被当前 PostgreSQL + 主/子 Agent 方案取代。
- 旧汇报稿和 v3 平台草案已归档到 `../archive/ai_soc/`，仅用于追溯早期思路。

后续设计只维护 `soc-agent-solution.md`，开发进度只维护 `progress.md`。除非出现独立研究主题，否则不要在本目录新增新的平行方案文档。
