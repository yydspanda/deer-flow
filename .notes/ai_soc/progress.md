# SOC Agent Execution Progress / 执行进度
> 本文件只保存当前执行指针和近期完成记录。完整历史按月份归档；聊天记录、方案正文和能力总表不再进入本文件。
## Current Pointer / 当前指针
- **Current Stage:** `PI`
- **In Progress Task:** `PI-01`
- **Current Objective:** `PI-01H / D12-B` 正在真实内网验收。项目 DEV 已证明 EAGW completion、ZEUS PRD 待审 lifecycle `code=200/status=1/mocked=false`，并由兼容 Worker 完成 SOC Runtime、持久化结果和 Callback Outbox。当前本机自提交回调因 ZEUS 未登记对应 `taskId` 返回业务码 `40020`，因此不能替代 ZEUS 上游真实发起。新交付同时把 PingAn DeerFlow chat 固定为 buffered non-streaming，并将模型网关与 Runtime 并发统一为 `3`；SQLite compatibility Worker 仍独立保持单 Worker。受治理部署映射仍为项目 `DEV -> ZEUS PRD + Agent Platform PRD`、项目 `STG -> ZEUS STG + Agent Platform STG`。
- **Next Gate:** 部署 buffered chat / `3 + 3` 并发配置，验证普通聊天不再发送 `stream=true`、三条不同告警可同时研判；随后由 ZEUS 上游真实调用 `/workflow/task` 并完成 callback/旧页面回读。再使用已审阅的 IP/Host/UM 发现种子运行 D12-B 资产 direct smoke，核对 ZEUS 命中与 Agent Platform 降级 attempts，完成 MCP、`InvestigationEvidence` 回读；最后验证 TI/标签 PRD `mocked=false` 证据并进入 shadow/容量验收。
- **Roadmap:** [`delivery-roadmap.md`](delivery-roadmap.md)
- **Last Updated:** `2026-09-18`
## Current Constraints / 当前约束
| Boundary | Current fact |
|---|---|
| Fork strategy | SOC 继续作为 DeerFlow 增量层；除非需要小型通用扩展点，不修改上游核心 |
| Persistence | 生产/准生产目标为 PostgreSQL；当前 DEV/仿真使用独立 SOC SQLite |
| LLM control | Runtime 掌握固定控制流；LLM 处理 bounded analysis；Policy/approval/service 掌握动作权限 |
| Real integration | 外网 mock/fixture 只证明流程可达；真实门禁必须以 PingAn DEV 的 `mocked=false` 证据关闭 |
| Upstream baseline | `upstream/main@452d09b96b0dfdf00f53b8655e41c64232612dc3`；2026-09-11 同步新增 `105` 个提交，保留 SOC 增量边界；记录见月度归档 |

## Recent Completion Records / 近期完成记录
09-18 `PI-03E` 两批验证实施中：15,288条固定为第一批3,002、验证同类经验8,522、探索少样本3,764。持久轮次、共享队列、隔离积累、冻结经验、CLI和固定Run报告已接入。聚焦后端90项、打包/数据42项、浏览器5项及前端检查通过；此前前端组件44项通过。15,288模拟任务创建从66.94秒降至7.58秒，进度分页P95 37.64ms；非内网吞吐承诺。候选按本实验分页/回跳，Mac实操手册与0031清单已补；独立4文件数据包约1.04GB已校验。后续计时/API/CLI/报告22项、打包35项通过；共享版本化草稿与后台批量起草已接入，任务/API/CLI/迁移26项、前端55项及桌面/手机浏览器3项通过。再补受控DEV备份重置/恢复与独立起草成本导出：Host/打包102项、报告/API/CLI19项、集中后端70项通过；真实临时空库0031迁移通过。最新补齐固定报告定向复测、人工单条优先、模型超时占位：集中后端101项、随后定向39项及打包35项通过（重叠不累加）。网页固定基线对照已补齐，后端30项、前端51项、浏览器2项、打包清单35项及完整前端check通过；发布版本与校验以生成清单为准。最新upstream检查落后138提交，本专项未合并，另需同步回归。无真实模型调用或外网清库；模拟审核仅临时DB。见[方案](architecture/corpus-memory-batch-validation-design.md)与月度记录，PI-01指针不变。
09-17 `PI-03E` 完成本机导航提速、列表轻量索引和 Mac Host 预构建接入：保留同类组浏览，移除固定推荐演练与归一化运营导航。迁移0028只新增可重建查询表；SQL分页、增量更新及10,005条合成结果容量回归通过，完整页详情即时读治理状态。最终只读首开经验中心566ms、演练1466ms，20次切换P95 166ms，列表约29KB；保留热重载引起的首开失败记录。Mac启动/Runbook模板已改并测试，真实Mac性能仍待验。未调用模型、改经验、清库或打包；两批后台验证尚未实施。见[方案3.5](architecture/corpus-memory-batch-validation-design.md)和月度 `EXP-20260917-soc-navigation`，PI-01指针不变。
本日 `PI-03E` 收尾补验：修复1984510当前进程/目标文件归属及六条HIDS的detail字段消费，旧210条指纹覆盖恢复到197条；271项聚焦后端回归通过。v47两次主研判、v10一次经验起草真实调用完成，共86236Token、无额外调用；但2488604解释和草稿仍弱化未覆盖检测，质量门禁未关闭。未修改审核经验、清库、重跑语义模型或打包。完整记录见月度 `EXP-20260917-closure`，下文当日先前验证保留其时间口径。
09-17 `PI-03E` 完成 [Memory 范围整改](architecture/memory-reuse-scope-remediation.md)：Profile 9/v7、审核覆盖 v4、宽窄优先、暂停边界、分页实体和细分候选已接入。用户闭环后修复身份任选与草稿刷新：Prompt v9 保留 rule/sig 命名空间，相同输入复用事实但重评当前经验/策略，显式重新核对可比对变化。2480991 真实补充正确绑定两条检测，同输入复跑特征一致、核对调用为0；旧经验未覆盖新增 Nmap，仍参考使用，不自动扩张授权。详见月度 `EXP-20260917-normalization-stability`。同项主研判 Prompt v46 区分匹配差异与业务影响，190 项回归通过；固定事实两次新版调用，一次正确解释未覆盖检测、一次连接失败，非两次稳定通过，见 `EXP-20260917-memory-difference-explanation`。后续将系统匹配说明与模型依据分开，来源事实进入新候选和经验起草；132 项后端回归、10 项前端组件与两告警各两尺寸只读检查通过，v47/v10 未新增真实模型调用，详见 `EXP-20260917-memory-matching-facts`。批量质量、PostgreSQL 并发和内网验收仍开放，PI-01 指针不变。
09-16 `PI-03E` 修复带开关请求头与 GlobalAI 非流式文本兼容，模型显式流式聚合、内网默认不变。2025642 已恢复真实模型研判，见月度 `EXP-20260916-soc-buffered-stream`。Prompt v6 提炼业务地址后解释仍未采用，见 `EXP-20260916-semantic-business-clues`；后续去除 Skill/审核知识静默截断，以类型化线索接通通用知识选择器，PingAn 配置隔离。恢复轮已引用 AskBob 知识，真实攻击/80% 变为可疑/62%，企业策略仍转交；26 项补充采用、方向不变，282+1 项回归通过。首次 DEV 热重载中断/恢复有留痕，不作为误报率验收；见 `EXP-20260916-knowledge-bridge`。后续修复已放弃的旧窗口候选永久阻挡新窗口提炼及跨窗口候选展示遗漏，2484162 同组7条已保存观察补聚合为待审候选，无模型重跑或自动审核；见 `EXP-20260916-pattern-candidate-recovery`，内网指针不变。 同项完成自动/人工经验入口协调、已审核范围覆盖和待审修订导航；无清库、自动启用或模型调用，详见月度 `EXP-20260916-memory-learning-coordination`。
本次补齐 `PI-03E` 告警演练按次运行开关：语义核对、企业策略及安全路径/LLM 子项；配置随运行保存，不改进程环境、历史结果或外部动作权限。64 项后端与 2 项浏览器回归通过，类型/lint 通过；无付费模型调用、打包或清库。详见[方案](architecture/direct-resolution-design.md)和月度 `EXP-20260915-corpus-run-controls`，内网指针不变。
09-15 `PI-03E` SOC模型JSON紧凑发送：研判、语义核对、角色复核、经验生成、策略与修复入口共用无损序列化，SOC工具文本同步；不改DeerFlow核心、签名、审计排版或Memory匹配。冻结请求对照去排版后输入33680→24764Token并正常回复；新版2651342完整Web回放21.118秒、2次调用、24项补充进入模型、14条引用通过，已保存观察。三组133/71/5项回归通过（部分重叠）；批量稳定性与远端精确限制仍未验证。见[方案](architecture/normalization-assistance-design.md)与月度归档，PI-01指针不变。
09-15 `PI-03E` 企业规则前置、精确 Memory 直接复用：确定性 enforced 忽略/转交可跳过全部模型；否则保留语义核对并复查，精确审核 override 可跳过主研判/角色复核。待定模型依赖规则保留优先级，完整指令集合冲突贯穿后置治理；v3 行为勾选保持有效。直接结果不伪造 Base/置信度，共用存储、回传、反馈、动作出口，使用不算新独立 Pattern 样本。133 项后端回归、后续 77 项及最终 17 项专项（有重叠）、10 项前端组件、桌面/手机浏览器通过；既有192→191快照问题保留。后续2546323暴露直接策略分支遗漏请求环境：原运行676ms、零模型调用已完成，工作台却无法识别；现前置绑定环境，旧结果仅按冻结策略范围恢复只读展示，轨迹不再等待不需要的Pattern步骤。详见[完整方案](architecture/direct-resolution-design.md)、[流程图](governance/decision-to-policy-flow.md)与月度归档。无真实模型/内网调用、清库或经验迁移，PI-01 指针不变。
09-15 `PI-03E` Memory 适用条件改造：固定范围只读、核心行为全展开默认全选；v3 按审核选择逐项匹配，不再强制来源完整 hash 相同，去重排序不受顺序影响。额外限制只收窄直接复用，源/目标 AND，参考经验仍可影响模型判断；旧 Memory/索引不迁移。167 项后端、7 项组件、3 项浏览器测试及两个真实候选四次只读检查通过；六个范围变体是模拟，无真实模型/业务写入，不代表准确率提升。见[方案与剩余工作](architecture/memory-applicability-design.md)及月度归档，内网指针不变。
09-15 `PI-03E` 外网默认切 GlobalAI V4.1 Flash、关闭 thinking、24576输出预算，内网不改。先前六例121项离线投影通过；随后用户确认网页切 apply，修正按保存模式读取 Profile、按实际签名读取观察及重跑去重。2448412真实全流程16.073秒完成，17项补充进入模型输入和新经验条件；2651342两次24项合入成功，但主研判被中转空SSE响应阻断，两次检测对象绑定不同，指纹稳定性待审。122项后端、1项新组件、3项浏览器测试通过；未创建/审核新Memory或重建索引。1984510对象归属隔离、旧快照192→191、32组全面验收、maintenance降噪、邮件尾项保留。不宣称全面准确率提升。详见[月度归档](../archive/ai_soc/progress/2026-09.md)与[方案](architecture/normalization-assistance-design.md)，内网指针不变。
### 2026-09-08 — Explain decision lineage and concrete handling progress

- **Task:** `PI-04C`
- **Status:** `Done`
- **Outcome:** Effective policy v3 将 Tenant Policy 的 `unknown` 作为未选择处置，而非新处置；保留前一阶段 Base/Memory 的处理建议和完整原始策略审计。企业单独要求排查不再显示为关键事实失败，独立的 materiality/conflict 仍受限。Web、语料历史对比、ZEUS 回传共用处置分类；建议与实际执行进度分离。旧记录只读重投影，不重写数据库或重新调用模型。
- **Verification:** 96 项后端结果、策略、Memory/自动化、语料与兼容回归、12 项架构检查，以及 5 项前端组件测试、lint/type-check 通过。只读核对现存 `2471269` 和 `2502512`，并在桌面/移动尺寸点击验证：前者保留可疑/75%及原始主机排查建议，后者保留误报/82%及企业强制转交要求；没有真实模型或外部处置调用。
  后续同一项优化将 Prompt v39 / public triage Skill 中的已审核经验与普通历史标签分开，使用可选
  `conclusion_support` 在同次研判中说明已解决问题、可选补充和未来重判条件。Parser v25 校验冻结
  M-* 引用，无效说明独立丢弃；真实缺口/反证不清除。页面将非阻断补充与未用动作能力放入展开
  记录，并区分本次实际读取/引用的 Memory 与同类组沉淀记录，修复版本化 source_id 被当作链接 ID。
  当前接口只读核对 `2448168`：确实引用 `MEM-52B94F38659F@v4` 的 context-only 经验，保留误报
  结论及企业转交要求，不再因未使用的目标能力显示结论受限。旧 Run 不伪造新说明；本轮不跑真实模型。
  本次新增路径通过 140 项后端 Prompt/parser/analyzer/outcome/架构回归、8 项前端组件测试及前端
  lint/type-check；只读浏览器在 1440/390 宽度核对真实保存结果、经验链接和折叠记录，无业务写请求。
  09-08 继续细分处理进度：结构/引用、关键输入、事实/角色/决策分歧分别说明；后续处理区分
  方案已确定、转交待确认、动作待执行/失败/跳过/成功待最终反馈。策略忽略或单个动作成功不再
  直接显示告警结案；使用当前 Run/alert 已映射终态反馈。页面展示服务端进度依据，技术审计表
  补齐处置方案、复核要求和建议。全部分支与 `1966558` 实例见 `governance/decision-to-policy-flow.md`。
  90 项后端结果/策略/兼容/架构回归和 10 项前端组件测试通过；只读投影，不修改 verdict、策略、
  数据库或增加模型调用。8 张 Mermaid 图可解析渲染；1440/390 页面核对进度依据与阶段字段，
  未发起 SOC 业务写请求，前端 lint/type-check 通过。
  根据运营反馈继续简化为一份主处理结论：忽略/转交、采用依据和必要下一步。技术风险判断、
  置信度、普通进度与非阻断疑点默认折叠；实质分歧、运行/动作失败仍可见。告警列表和过滤
  读取服务端有效处置分类，不再显示 Base verdict 为最终结果；与详情、历史对比共用投影。
  本次结果/兼容核心 64 项回归通过，语料/策略/架构另 57 项通过；前端 10 项组件测试及
  lint/type-check 通过。只读浏览器在 1440/390 核对 `2502512`、`1966558` 单一主结论、
  折叠后可查原始风险与决策阶段，无页面溢出、无 SOC 写请求；9 张流程图可渲染。
  未重跑模型、清空数据库或改变 Memory/策略/动作权限，内部详细状态只保留作复盘。
  09-09 补齐标记到处置的实际对应：普通缺口与可选能力限制不单独触发转交；未解决的结论级
  问题即使保留误报 verdict 也显示转交确认、具体原因和下一步。Effective policy v4 保留这些
  独立阻断，不采用忽略方案、不生成处置应用记录或动作授权；通用复核的明确策略授权仍可用。
  旧受阻计划只留阶段审计，已发生的外部反馈不删。列表、详情、历史对比与 ZEUS 共用分类。
  流程图明确 context-only Memory 在主模型前进入上下文、Base 已可使用它，之后是指令复用。
  聚焦后端结果/自动化/回传 92 项及策略/materiality/架构/语料 64 项通过；50 张 Mermaid 图可解析，
  其中 9 张处置图实际渲染通过。无真实模型调用、内网请求、数据库迁移、清空或重打包。
  09-09 补齐新旧经验治理：候选审核展示已有业务结论及范围差异；同范围挑战合并待审任务，
  不让模型可疑/企业转交自动推翻旧经验。启用端拦截同范围重复答案和相反答案、重叠范围相反指令，
  审核人可在原页面选择旧经验并以一次确认原子修订，保留新旧 lineage、版本与审核审计。
  AI 起草 Prompt v6 接收有界的新旧对照，没有新增每告警模型调用或必填理由。94 项聚焦后端回归、
  3 项组件测试及 1440/390 两项浏览器演练通过；前端类型/lint 与后端格式检查通过。
  PostgreSQL advisory-lock 路径尚未真实并发验收；现有数据不清空、不迁移或自动废止，Profile 不变。

新旧经验治理、统一经验中心、AI 草稿编辑和适用范围 `EXP-20260910-memory-optional-values` 已归档至 [2026-09](../archive/ai_soc/progress/2026-09.md)；范围明细由后端核验，可选条件逐值选择，未改已有经验、指纹或审核权限。

09-09 同项补齐检索后、主模型前的精确经验优先边界。`soc.memory_context_precedence.v1`
将同检测/场景内与明确精确经验相反的 partial 经验放入运行级差异审计，不进入可引用 M-*、
MemoryUse 或模型使用统计；没有精确答案仍可泛化，相互矛盾的精确答案不按分数选优。
Prompt v40、75 项聚焦测试和真实保存请求 `2448168` 的三组隔离筛选对比通过。
没有修改现存 Memory、增加模型调用或重写历史 Run；B 的相反审核结论为仿真，不是人工真值。

#### Experiment — Exact Memory context precedence

```json soc-experiment
{
  "experiment_id": "EXP-20260909-memory-context-precedence",
  "task_id": "PI-04C",
  "upstream_commit": "9146bfa03da1d8b463a54494d8e050771640008c",
  "model": "none (saved real request; simulated opposite reviewed Memory; no inference)",
  "config_hash": "sha256:7cdf592955f75adf3f27af9a7b95c583156681ff4bc65618b8b44209266a6b2d",
  "config_basis": "backend/soc_agent/memory/retrieval.py; prompt soc-analysis-v40",
  "data_hash": "sha256:9afd1669967c83eef4326a5e016d1cc19c5616ccd769dd887aaf245934056913",
  "data_basis": "exported saved audit 2448168; Memory inventory hash and three simulated variants retained in comparison.json",
  "hardware": "local Linux x86_64 CPU; Python 3.12.7; no GPU/model calls",
  "command": "backend/.venv/bin/python validation/compact_zeus/memory/validate_memory_context_precedence.py --audit-file /tmp/soc-2448168-audit-current.json --memory-records /tmp/soc-memory-records-current.json --memory-id MEM-52B94F38659F --output-dir backend/.deer-flow/soc-validation/memory-context-precedence-20260909/final",
  "metrics": {
    "focused_tests_passed": 75,
    "replay_cases_passed": 3,
    "excluded_opposite_lessons_visible_in_prompt": 0,
    "real_model_calls": 0,
    "production_database_mutations": 0
  },
  "artifacts": "backend/.deer-flow/soc-validation/memory-context-precedence-20260909/final/comparison.json"
}
```

09-09 同项修复待审修订的导航断点：经验详情与修订页按持久化 predecessor lineage
查询已有候选，提供“继续审核修订”，不再展示重复创建入口。候选查询在数据库分页前
按 `revision_of_memory_id` 过滤；旧记录无需迁移。只读确认实际 `MEM-52B94F38659F`
对应待审 `MC-17407CB73CB8`，未代用户审核、删除、恢复使用或调用模型。

#### Experiment — Pending Memory revision navigation

```json soc-experiment
{
  "experiment_id": "EXP-20260909-memory-revision-navigation",
  "task_id": "PI-04C",
  "upstream_commit": "9146bfa03da1d8b463a54494d8e050771640008c",
  "model": "none (read-only navigation; synthetic browser fixtures)",
  "config_hash": "sha256:233a022714cd9d7e3fe126ef2113c0a330c7b07c9d02074e61e00fa788025002",
  "config_basis": "frontend/src/components/workspace/soc/soc-memory-pending-revision.tsx",
  "data_hash": "sha256:c567fce44794c0baf31748fdb5a95e12b69d6341f3f12524bee0b69649db5588",
  "data_basis": "frontend/tests/e2e/soc-memory-pending-revision.spec.ts synthetic pending revision fixture",
  "hardware": "Linux x86_64; Python 3.12.7; Node 24.14.0; Chromium; 1440/390px viewports",
  "command": "cd frontend && PLAYWRIGHT_BASE_URL=http://localhost:2026 PLAYWRIGHT_SKIP_WEB_SERVER=1 python3 ../scripts/pnpm.py exec playwright test tests/e2e/soc-memory-pending-revision.spec.ts --workers=1 --reporter=line",
  "metrics": {
    "backend_tests_passed": 37,
    "frontend_component_tests_passed": 3,
    "browser_tests_passed": 2,
    "real_model_calls": 0,
    "user_database_mutations": 0
  },
  "artifacts": "backend/.deer-flow/soc-validation/memory-revision-navigation-20260909"
}
```

09-09 同项补齐“取消修订并恢复旧经验”：支持待审及最新已放弃修订。复用 review/activation Service，一次事务检查版本/有效期/发布冲突并恢复原使用方式，保留两项审计。失败整体回滚，重试不能撤销后来暂停；普通放弃仍保持暂停。
审核页提供旧经验直达与确认恢复弹窗，成功后进入旧记录；context-only 不变为 Directive。18 项恢复测试、37 项修订/API 回归、3 项浏览器测试及类型/lint/格式检查通过。
真实 `MC-17407CB73CB8` 只读确认已 rejected，未替用户恢复；运行 Gateway 已加载新增契约。

#### Experiment — Cancel revision and restore old Memory

```json soc-experiment
{
  "experiment_id": "EXP-20260909-memory-revision-restore",
  "task_id": "PI-04C",
  "upstream_commit": "9146bfa03da1d8b463a54494d8e050771640008c",
  "model": "none (transactional synthetic fixtures and intercepted browser APIs)",
  "config_hash": "sha256:77f96c93c5e7e5114c94f8b4546c1a064122da4c94915bccd456357ece3d6e9c",
  "config_basis": "backend/soc_agent/core/service.py; working tree after d1dccd6598728b81856f25805bd042f9feba0cc3",
  "data_hash": "sha256:be2fe7d0274f9bc3c7aacd9cbc125b95e04399cb328e9a4078f000a268b7d20d",
  "data_basis": "backend/tests/test_soc_memory_revision_restore.py synthetic SQLite revisions",
  "hardware": "Linux x86_64; Python 3.12.7; Node 24; Chromium at 1440/390px; no GPU",
  "command": "backend/.venv/bin/pytest backend/tests/test_soc_memory_revision_restore.py -q --tb=short; cd frontend && PLAYWRIGHT_BASE_URL=http://localhost:2026 PLAYWRIGHT_SKIP_WEB_SERVER=1 python3 ../scripts/pnpm.py exec playwright test tests/e2e/soc-memory-revision-restore.spec.ts --workers=1 --reporter=line",
  "metrics": {
    "restore_tests_passed": 18,
    "existing_revision_router_tests_passed": 37,
    "browser_tests_passed": 3,
    "restore_test_duration_seconds": 61.21,
    "browser_test_duration_seconds": 14.5,
    "real_model_calls": 0,
    "user_database_mutations": 0
  },
  "artifacts": "backend/.deer-flow/soc-validation/memory-revision-restore-20260909"
}
```

### 2026-09-03 — Governed Agent Platform target mapping

- **Task:** `PI-01`
- **Status:** `Done`
- **Outcome:** PingAn Integration 新增独立 Agent Platform target contract，环境切换现在原子应用 `DEV -> ZEUS PRD + Agent Platform PRD` / `STG -> ZEUS STG + Agent Platform STG`，Host status、preflight、资产降级链和 transfer profile 共同拒绝错配。旧 `agent_config.py` 只读 AST 迁移确认 PRD `YHSYS` 与三个归属 workflow、提取 STG endpoint，并明确拒绝用其他应用身份替代缺失的 STG `YHSYS` 配置；五项 STG 私有值不完整时在 env 写入前 fail closed。通用 Runtime 继续只认识 `asset.locate`，未引入 PingAn 环境或凭证概念。
- **Verification:** DEV -> PRD 与完整 STG -> STG 配置回归、错配/缺确认/部分 STG profile 的 fail-closed 回归、Host/transfer 私有 overlay 检查通过；Runbook 增加只读 IP/Host/UM 发现 smoke。外网结果只证明配置和无网络边界，真实 Agent Platform/ZEUS `mocked=false` 调用仍属于 D12-B 内网 gate。
  内网随后已证明真实待审告警 lifecycle `code=200/status=1/mocked=false`；完整任务因 Worker 缺少共享 Policy 配置停留 `PENDING`。修复新增 PID-bound Worker readiness，并通过 Host/compat/Processing Job/架构聚焦回归；需随下一交付在内网用 fresh Job 复验。
  后续 fresh Job 已完成 Runtime 并写入结果，证明 Worker 修复有效；本机自提交 callback 因 ZEUS 未登记该 `taskId` 返回 `40020`，最终 callback 门禁必须由 ZEUS 上游真实发起关闭。PingAn chat 另发现 EAGW 不支持流式请求，现由专用 model profile 使用 LangChain buffered fallback，并把 gateway/Runtime 并发统一为 `3`。

### 2026-09-02 — Legacy live-acceptance recovery and signed-wire hardening

- **Task:** `PI-01`
- **Status:** `Done`
- **Outcome:** Host 路径解析器统一导出 checkout-owned 绝对 `SOC_DATABASE_URL`；启动器在任何 Sidecar/Web 进程前集中完成 SOC migration，并关闭 API/Worker 的重复自动迁移。新建 SQLite 首次发生瞬时 `disk I/O error` 时，只清理本次失败产生的半库并重试一次；调用前已存在的数据库绝不自动删除。`status` 增加数据库路径、状态和 revision，生成式 Runbook 删除手工建库分支；无状态重装确认从 `/dev/tty` 读取，避免 heredoc 吞掉后续 Shell。live acceptance 在任何 `8090` 请求前验证 SQLite 文件、`soc_alembic_version` 和 Processing Job/Callback 表，并提供显式恢复模式。内网首轮真实证据进一步定位 `code=40100/签名验证失败`：旧实现签名 `json.dumps`，却让 HTTPX `json=` 重新压缩 wire body。现已让 lifecycle/callback/asset/TI/security-tag 五个 ISEC Provider 一次序列化并以相同 bytes 签名和发送；报告契约升级为 v3，失败 callback 的 HTTP/provider code 与 response hash 可安全持久化。新增模型调用前的只读 lifecycle/signature smoke，并把 local self-submit 与 ZEUS-originated 最终验收分开。内网复验已从 `40100` 推进到业务码 `65505`；新增显式完整响应探针，复用 Worker 的真实 Provider，只在忽略目录以 `0600` 保存完整响应，且不创建 Job、调用模型、触发回调或放宽 bounded smoke 门禁。根据内网联调约束，新增受治理部署 profile：DEV/STG 分别绑定独立 SOC SQLite、Memory/Policy/Automation scope，STG 禁用 DEV Workbench/免登录，两者均关闭真实动作；私有 env 保存 ZEUS PRD/STG 两套 profile，切换原子应用 `项目 DEV -> ZEUS PRD`、`项目 STG -> ZEUS STG`，Host/preflight 对错配 fail closed。模型目标、Provider mode 和权限保持独立；Agent Platform target 后续与 ZEUS 一样纳入 Runtime 环境映射治理，生命周期和回写仍默认 `fake`。
- **Verification:** 外网 350 项 PingAn 集成/Host/签名/Provider/transfer 回归及 12 项 SOC 架构边界回归通过；真实形状私有 env 副本完成 DEV -> STG -> DEV 往返，除 Runtime selector 外其余内容哈希不变，STG 解析到独立数据库并使用 `--prod`。migration/legacy/Processing Job 回归继续覆盖瞬时新库失败、已有库非破坏性失败、数据库先于 Sidecar 启动及只读状态检查。真实内网已人工证明 SQLite 可升级到 `0027_processing_jobs`、六个进程全部启动、模型网关 completion 通过；新签名代码和 lifecycle smoke 仍需随下一交付在内网复验。


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
