# Notes 文档索引

本目录只放项目内的当前主线设计、参考索引和必要入口。低频学习材料、历史研究和已被吸收的背景文档统一放入 `archive/`。

## 当前主线

| 文档 | 用途 |
|---|---|
| `project-overview.md` | DeerFlow 2.0 项目全貌、分层、核心数据流 |
| `ai_soc/soc-agent-solution.md` | SOC Agent 当前权威方案；后续产品和工程讨论优先更新这里 |
| `ai_soc/progress.md` | SOC Agent 长期开发进度台账；每个可验证切片完成后更新 |
| `reference-index/soc-agent-engineering-contracts.md` | SOC Agent 代码风格、框架设计、API、通信协议和测试门禁 |
| `reference/cross-project-workflow.md` | 跨项目参考工作流：先定义问题，再用 Understand Anything / CodeGraph 查参考项目 |
| `reference-index/` | 已沉淀的跨项目研究索引和工具选型记录 |

## 目录分工

| 目录 | 保留标准 |
|---|---|
| `ai_soc/` | 只保留 SOC Agent 当前方案和当前相关说明 |
| `reference/` | 只保留跨项目查阅工作流；其他 DeerFlow 学习笔记已归档 |
| `reference-index/` | 已验证的主题索引、工具安装和选型结论 |
| `research/` | 只保留仍被当前方案直接引用的研究材料 |
| `archive/` | 已被当前方案取代、但仍有追溯价值的历史文档 |

## 维护规则

- SOC Agent 的新决策优先写入 `ai_soc/soc-agent-solution.md`，不要再新增平行版本方案。
- 过时但可能有追溯价值的文档移入 `archive/`；无引用、可再生成的 HTML/临时产物直接删除。
- 研究结论如果会影响开发路线，必须在 `reference-index/` 增加一页索引；长研究报告默认归档。
- 通用教程、个人学习笔记、泛产品想法、模块学习笔记默认归档，不放在主线目录。
- `AGENTS.md` 引用的文档不要移动；如确需移动，必须同步更新 `AGENTS.md`。
