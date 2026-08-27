# SOC Agent Execution Progress / 执行进度

> 本文件只保存当前执行指针和近期完成记录。完整历史按月份归档；聊天记录、方案正文和能力总表不再进入本文件。

## Current Pointer / 当前指针

- **Current Stage:** `PI`
- **In Progress Task:** `PI-01`
- **Current Objective:** 在 PingAn DEV 完成真实只读 Provider 与调查链的 `mocked=false` 验收，不改变通用 SOC Runtime 契约。
- **Next Gate:** `D12-B` asset Provider direct/MCP/persistence smoke，然后依次关闭 `PI-01A` 与 `PI-01B1` 的真实证据门禁。
- **Roadmap:** [`delivery-roadmap.md`](delivery-roadmap.md)
- **Last Updated:** `2026-08-27`

## Current Constraints / 当前约束

| Boundary | Current fact |
|---|---|
| Fork strategy | SOC 继续作为 DeerFlow 增量层；除非需要小型通用扩展点，不修改上游核心 |
| Persistence | 生产/准生产目标为 PostgreSQL；当前 DEV/仿真使用独立 SOC SQLite |
| LLM control | Runtime 掌握固定控制流；LLM 处理 bounded analysis；Policy/approval/service 掌握动作权限 |
| Real integration | 外网 mock/fixture 只证明流程可达；真实门禁必须以 PingAn DEV 的 `mocked=false` 证据关闭 |
| Upstream baseline | `upstream/main@788a890bd022689ef293e6bbfa2c12988173db6c`；2026-08-26 测量为 ahead `261` / behind `0` |

## Recent Completion Records / 近期完成记录

### 2026-08-27 — Alert result and operator-workflow separation

- **Task:** `BD-02`
- **Status:** `Done`
- **Outcome:** 以 `run_id` 建立所有告警可见的研判结果与调查上下文；ReviewQueue 收窄为仅处理未解决关键事实冲突，并将告警修正、经验审核、高风险动作审批和技术审计拆成独立操作路径。
- **Verification:** Alert-result/attention policy、Gateway API、后端服务回归、前端 lint/type-check、API 单测及 Playwright 告警研判/人工介入/动作审批流程。

### 2026-08-26 — Alert rehearsal UX simplification

- **Task:** `PI-03`
- **Status:** `Done`
- **Outcome:** 将用户入口统一为“告警研判演练”，推荐区收敛为同一 `rule_code` 下的 context-only 与精确复用两组真实样本；运行保持在原列表并由用户显式打开结果，不再强制跳转到下方轨迹。
- **Verification:** 真实语料分组清单、后端 workbench 回归、前端 lint/type-check、桌面/移动端 Playwright 交互与截图检查。

### 2026-08-26 — DEV tenant-policy acceptance and Memory authority clarity

- **Task:** `PI-03`
- **Status:** `Done`
- **Outcome:** PingAn DEV 工作台显式开启确定性处置规则、安全软件路径和 bounded Policy Advisor，同时继续禁止真实外部动作；Memory Center 强制重取生命周期投影，详情/修订页区分 exact Directive 与 context-only 使用语义。
- **Verification:** DEV safety contract、Docker/macOS 启动配置、tenant policy/software-path 回归、前端 lint/type-check 和浏览器验收。

### 2026-08-26 — Progress ledger governance and monthly archive

- **Task:** `PI-06`
- **Status:** `Done`
- **Outcome:** 将 8,008 行混合台账收敛为单一当前指针；289 条历史完成记录按 `2026-06/07/08` 归档，重复的能力表和早期计划转为只读 legacy register。
- **Verification:** `scripts/check_soc_progress.py`、聚焦单元测试和 GitHub Actions 同时约束唯一 Stage/task、Roadmap 引用、实验 manifest、活动文件预算及归档月份。

### 2026-08-26 — Upstream synchronization

- **Task:** `UP-SYNC`
- **Status:** `Done`
- **Outcome:** 合并 `upstream/main@788a890bd022689ef293e6bbfa2c12988173db6c`，保留上游 Subagent/MCP/Gateway/Frontend 能力与 SOC 增量边界。
- **Verification:** 后端重点回归 959 项、前端 1,089 项单测及 lint/type-check 通过；宿主 DNS 导致的 5 个浏览器 SSRF 环境失败未通过放宽安全策略规避。

### 2026-08-25 — Memory governance and evidence-gap correction

- **Task:** `PI-03`
- **Status:** `Done`
- **Outcome:** Confirmed Memory 台账、修订、Match Test 和 evidence coverage 等价投影完成；调查缺口保留为未来 selected-case Agent 输入，不直接驱动 MCP。
- **Archive:** [`2026-08`](../archive/ai_soc/progress/2026-08.md)

### 2026-08-25 — Capability walkthrough and reporting package

- **Task:** `PI-04`
- **Status:** `Done`
- **Outcome:** 完成可复跑能力演示路线、项目摘要、技术方案和 FAQ，并明确真实、mock、shadow 与未验收边界。
- **Archive:** [`2026-08`](../archive/ai_soc/progress/2026-08.md)

### 2026-08-24 — Memory inventory and operator-direct revision

- **Task:** `PI-03`
- **Status:** `Done`
- **Outcome:** 增加正式 Memory inventory、版本化修订、使用历史和 read-only Match Test，Pattern 与 Memory record 不再混用。
- **Archive:** [`2026-08`](../archive/ai_soc/progress/2026-08.md)

### 2026-08-24 — Corpus interactive rerun and immutable lineage

- **Task:** `PI-03`
- **Status:** `Done`
- **Outcome:** DEV 语料工作台允许任意顺序重跑；每次 replay 新建 Run，保留 lineage，且同一原始告警不会重复增加 Pattern 支持数。
- **Archive:** [`2026-08`](../archive/ai_soc/progress/2026-08.md)

### 2026-08-21 — PingAn Memory pattern precision

- **Task:** `PI-03`
- **Status:** `Done`
- **Outcome:** 行为指纹加入 network service、CVE 和攻击行为族，减少同 rule 不同行为被错误聚合，并改善审核范围的中文可读性。
- **Archive:** [`2026-08`](../archive/ai_soc/progress/2026-08.md)

## Update Contract / 更新约定

1. 只能有一个 `Current Stage` 和一个 `In Progress Task`；两者必须引用权威 Roadmap。
2. 每个近期记录必须包含一个 Roadmap task ID 和终态，不在这里复制方案、能力矩阵或长测试日志。
3. 近期记录最多保留 10 条；超出后按完成日期移动到[月度归档](../archive/ai_soc/progress/README.md)。
4. 每次实验必须附 `soc-experiment` JSON manifest，记录 upstream commit、模型、config/data SHA-256、硬件、精确命令和指标；格式见归档 README。
5. 修改本文件或 Roadmap 后运行：

   ```bash
   python scripts/check_soc_progress.py
   ```

6. upstream ahead/behind 由 `.github/workflows/soc-project-governance.yml` 每周检查；behind 超过阈值必须先同步或形成显式兼容决策。
