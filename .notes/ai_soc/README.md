# SOC Agent 文档

## 当前文档

| 文档 | 状态 |
|---|---|
| `soc-agent-solution.md` | 当前权威方案，包含产品定位、CLI/API/Daemon/Web 边界、PostgreSQL 记忆、队列策略、长期 Security Agent Platform 演进 |
| `progress.md` | 长期开发进度台账，记录当前 Phase、切片状态、完成记录和下一步 |

## 已清理内容

- 三份旧 HTML 架构/选型图已删除：它们基于早期 `memory.json` 和旧技术选型，已被当前 PostgreSQL + 主/子 Agent 方案取代。
- 旧汇报稿和 v3 平台草案已归档到 `../archive/ai_soc/`，仅用于追溯早期思路。

后续设计只维护 `soc-agent-solution.md`，开发进度只维护 `progress.md`。除非出现独立研究主题，否则不要在本目录新增新的平行方案文档。
