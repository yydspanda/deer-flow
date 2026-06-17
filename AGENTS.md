# SOC Agent — DeerFlow + LangGraph

## 项目目标
基于 DeerFlow 框架 + LangGraph 构建 SOC 预警研判 Agent。

## 项目详情
详见 `.notes/project-overview.md`

## 参考项目（只读，通过 CodeGraph 查询，不直接修改）

| 项目 | 绝对路径 | 用途 |
|---|---|---|
| claude-code-sourcemap | `/home/yydspei/projects/claude-code-sourcemap` | Claude Code 源码，参考 Agent 架构设计模式 |

查询参考项目：
```
codegraph_explore --projectPath /home/yydspei/projects/claude-code-sourcemap "query"
codegraph_search --projectPath /home/yydspei/projects/claude-code-sourcemap "symbol"
```

## 边界规则
- 所有代码修改仅限本仓库目录
- 参考项目通过 CodeGraph `--projectPath` 参数查询，不直接 `ls`/`cat` 参考项目文件
- 参考项目的设计模式应理解后在本项目中重新实现，不直接复制代码

## 相关研究文档
- `.notes/research/hermes-vs-deerflow-agent-patterns.md` — Claude Code 可借鉴设计模式（含代码位置）
- `.notes/ai_soc/soc-agent-solution.md` — SOC Agent 设计方案 v4
- `.notes/research/tech-selection-report.md` — 技术选型报告
