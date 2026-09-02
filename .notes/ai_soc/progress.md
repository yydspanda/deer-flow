# SOC Agent Execution Progress / 执行进度

> 本文件只保存当前执行指针和近期完成记录。完整历史按月份归档；聊天记录、方案正文和能力总表不再进入本文件。

## Current Pointer / 当前指针

- **Current Stage:** `PI`
- **In Progress Task:** `PI-01`
- **Current Objective:** `PI-01H` 外网实现与内网交付契约已冻结；下一阶段只注入 PingAn private overlay，关闭真实 EAGW、ZEUS 生命周期/回调、旧页面回读和容量门禁，不改通用 Runtime 契约。
- **Next Gate:** 在内网 Apple Silicon DEV 留存已通过的 EAGW completion 报告；随后只输入一个获批 pending `alert_id`，由请求准备器自动装配旧 `zeus/alert_agent` 完整请求并运行 live acceptance，关闭 ZEUS task/status/precheck/callback 门禁；再完成旧页面回读、告警 `1 -> 5 -> 50 -> 200/5000+` 递进 shadow，以及模型并发 `1 -> 2 -> 4 -> 6` 容量验收。
- **Roadmap:** [`delivery-roadmap.md`](delivery-roadmap.md)
- **Last Updated:** `2026-09-02`

## Current Constraints / 当前约束

| Boundary | Current fact |
|---|---|
| Fork strategy | SOC 继续作为 DeerFlow 增量层；除非需要小型通用扩展点，不修改上游核心 |
| Persistence | 生产/准生产目标为 PostgreSQL；当前 DEV/仿真使用独立 SOC SQLite |
| LLM control | Runtime 掌握固定控制流；LLM 处理 bounded analysis；Policy/approval/service 掌握动作权限 |
| Real integration | 外网 mock/fixture 只证明流程可达；真实门禁必须以 PingAn DEV 的 `mocked=false` 证据关闭 |
| Upstream baseline | `upstream/main@788a890bd022689ef293e6bbfa2c12988173db6c`；2026-08-26 测量为 ahead `261` / behind `0` |

## Recent Completion Records / 近期完成记录

### 2026-09-01 — PingAn private-profile migration and transfer freeze

- **Task:** `PI-01`
- **Status:** `Done`
- **Outcome:** 新增只读 AST profile preparer，从已审阅旧源码生成项目自有 EAGW 网关、旧 ZEUS ingress 与 `YHSYS` Workflow 的 Git-ignored 私有配置；RSA key 独立保存为 mode-`0600` private-overlay 文件。Transfer builder 拒绝旧 LiteLLM 环境变量/本地 model profile、占位值、宽权限 key、非 loopback 模型网关、非安全初始 lifecycle/callback 模式及不一致 app-key，并随包生成逐步内网安装、Fake、model smoke、live compatibility 与递进 shadow 手册。三个大 PKL 与 Workbench payload SQLite 改为内网既有数据，私有包只冻结 manifest/index；项目 staging 脚本在 Host DEV 前按 SHA-256/大小校验并以 `0600` 原子落位，数据库 migration 显式使用解析后的独立 SQLite 路径。旧源码复核进一步确认 `8090` 对 Bearer/`app-key` 使用全局 allowed-key-set 语义，`app_code=zeus` 只属于业务请求。新增 live-request preparer：操作员只输入获批 pending `alert_id`，脚本从 hash-bound payload store 保留完整 `alert_data`、生成 fresh session 和 `0600` 请求；lifecycle/callback mode 也由独立命令成对切换，不再手工编辑 JSON/env。安装收敛为自包含 `INSTALL-PINGAN-MAC.sh` 子脚本，不再把裸 `exit` 和长篇目录替换逻辑交给操作员终端；已有部署默认保留 DeerFlow/SOC SQLite、JWT、用户 Memory、Agent/线程工作区与受管集成，release-owned private overlay 保持更新，常规重部署不再清空业务状态。
- **Verification:** 真实旧源码 dry-run 不执行旧代码且 `secret_in_output=false`；staging dry-run/apply/missing/hash-mismatch 回归、兼容执行面/Host DEV/transfer 回归与自带无敏感合成夹具的 hermetic Fake E2E 通过，交付不再依赖 `datas/legacy_demos`。Runbook 中每个加载本地配置的命令块会自行定位 checkout，不依赖前序终端状态。内网手工无业务请求已证明 EAGW 非推理 completion 与 usage 可用，并暴露原 Smoke 的 8 Token/误报参数问题；基线已改为与 Runtime 一致的 `thinking=false`、128 Token。允许集合鉴权、鉴权先于任务存在性、旧 `zeus/alert_agent` 请求形态及歧义密钥 fail-closed 均有聚焦回归。请求准备器已用真实 4343 条 Workbench store 中的告警验证完整 payload、ID/hash/status 与权限，Provider mode updater 验证成对原子切换；安装器回归覆盖无前序 shell 状态的完整替换、坏 Hash 保留旧 checkout、持久化 allowlist 跨版本恢复以及旧 PID/新 `pingan-context` 隔离，重部署固定先停止旧 checkout 并确认 `3000/8001/2026/4001/8090` 全部释放，再事务式替换目录。Runbook 同时固定以 `soc_alembic_version` 验证 SOC migration。兼容/live/架构/transfer 回归通过；正式内网 live 报告、ZEUS 生命周期/回调和旧页面回读仍待关闭。

### 2026-09-01 — Legacy ZEUS compatibility execution plane

- **Task:** `PI-01`
- **Status:** `Done`
- **Outcome:** 保留旧 task/status/precheck/callback 协议，内部替换为持久 Processing Job、租约 Worker、统一 SOC Runtime、legacy result projection 与 Callback Outbox；项目自有 `4001` OpenAI-compatible 模型网关取代旧 LiteLLM，macOS Host DEV 统一启停模型网关、`8090` 兼容 API 和 Worker。仅 `executeType=1/3` 使用 30 分钟排队时限，过期不调用模型但仍持久化兼容结果并回调；脱敏 live runner 可在内网一次验证 fresh submit、幂等 replay、真实 precheck、Runtime 与真实 callback attempt。
- **Verification:** 核心兼容/网关回归、Host sidecar/transfer 回归、SOC 架构边界、空库 `0027` migration、PostgreSQL `FOR UPDATE SKIP LOCKED` 编译回归与真实 Fake E2E 均通过；Fake 报告明确 `simulated=true`、`proves_real_internal_connectivity=false`。

### 2026-08-31 — SOC workspace navigation and corpus projection performance

- **Task:** `PI-04C`
- **Status:** `Done`
- **Outcome:** 告警演练改为服务端筛选与分页，初始响应仅包含当前 20 条告警、重复行为组和两组演练样本；单条运行不再返回完整语料快照。页面不自动恢复/展开首条告警，轨迹与审计按需读取；经验中心复用导航缓存，活动轮询仅在运行中保持高频。SOC 导航增加即时加载反馈，DEV 启动脚本同时预热页面代码和语料索引。
- **Verification:** 后端语料工作台 `16 passed`，前端 lint/type-check 通过，SOC 语料/Memory 浏览器流程 `8 passed`；暖态语料 API 从约 `6.7 MB` 收敛到约 `0.3 MB`，真实浏览器告警演练可用时间由约 `3.2s` 降至约 `2.2s`，导航等待期间持续显示加载状态。

### 2026-08-28 — Effectiveness, rule optimization and Memory feedback UX

- **Task:** `PI-04C`
- **Status:** `Done`
- **Outcome:** 新增最终真值驱动的准确率、漏报、转交、自动忽略、规则质量和算力只读模型，并形成 `Rule Code -> 同类行为 -> Memory 版本` 下钻。共享后分析 Observer 统一记录 Pattern，入口重投按业务事件幂等；Memory 只按实际效果归因，`context-only` 不记改判功劳，历史版本不继承当前状态，错误自动忽略必须同时存在最终攻击真值与真实忽略动作。运营总览将八个公式组织为研判质量、自动化安全、转交质量和减负效果，只显示处理量，不向运营暴露 coverage 等统计术语；无数据项统一显示 `--`。
- **Verification:** 80 项后端效果/Pattern/Memory/API/迁移聚焦回归、35 项前端 SOC API 单测、前端 lint/type-check，以及桌面/移动端 Playwright 下钻与布局验收；新增结论维持口径、快照缓存和四组指标布局继续通过聚焦聚合/API/浏览器回归。真实 Zeus 最终状态和生产 telemetry 仍归 `PI-04D`。

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
