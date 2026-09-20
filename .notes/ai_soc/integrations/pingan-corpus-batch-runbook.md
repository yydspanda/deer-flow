# 内网 DEV：先沉淀经验，再验证效果

本手册用于本项目已经启动的 Mac DEV。不是 ZEUS 上游回写验收，不要提供生产 alert ID。
历史语料任务只写本地 SOC 数据库，不访问 ZEUS 生命周期/回调，不执行外部动作。
告警模型调用仅在你执行 `learn`、`validate`、`resume` 或网页启动后发生。
已明确选择的 `draft-candidates` 和 `draft-retry` 也会启动经验起草模型；`draft-plan`、查询和保存草稿不调用模型。
外网已用隔离数据库和模拟模型验证流程；内网模型质量、速度和容量仍由本轮验证。

## 1. 前置检查

本次使用随包 `PINGAN-INTERNAL-MAC-UPGRADE-RUNBOOK.md`，完成备份、保留数据安装、语料落位、依赖安装、预检、启动和模型 Smoke。
内网第一批已完成、正在审核经验，本次升级保留已有结果和审核进度；跳过本手册第1.1节。
另一本 `PINGAN-INTERNAL-MAC-REINITIALIZE-RUNBOOK.md` 只供明确从零验证时选择，不与升级手册依次执行。
确认第一批完成数量、待审核经验和已审核记录仍在，再从第2节的“审核经验”继续；不重跑成功第一批。
本版原始语料仍为15,288条；两批验证排除7条 `RPAADM_002192` SIEM 邮件告警，数据保留。
第一批2,997条，用于沉淀待审核经验。第二批分为：

- **验证经验复用：8,520条**。第一批中有同类样本，用来验证审核后的经验对后续告警是否有用，不保证每条都命中经验。
- **其他测试告警：3,764条**。同类只有1～5条，或事件时间无法确认；单独看研判结果，不混入经验复用效果统计。

本次按用户要求重建源码与配套私有配置包。数据继续单独交付，内网 Downloads 中hash相同的四个文件无需重新跨网传输；
完整安装器替换仓库后仍需重新落位语料、安装项目依赖。Mac 基础工具可复用。

### 网页运行设置

部署 Mac 本机通过 `http://localhost:2026` 修改运行设置。局域网同事看到只读配置，
仍可开始、暂停和重跑；使用当前批次最后保存的配置，无运行记录时使用部署默认值。
本机修改在提交运行时保存；已排队任务不被改写。无需增加启动参数，Host DEV 自动应用限制。

新版 Host DEV 默认并发上限为8，运行配置和模型网关的并发值同步为8；上线需更新配套私有配置并完整重启Host。
已有批次点击“继续”时采用当前服务端并发上限，保留原任务、运行开关和经验快照。
同类组仍按顺序执行，聊天和经验起草也共享模型容量，因此实际运行数不保证一直达到8。

告警演练顶部保留 **运行设置** 四个开关：语义核对、企业策略、安全软件路径策略、LLM策略建议。
开关值随新任务保存，不修改服务器全局配置；已排队任务沿用原设置。配置失效时停止并说明原因，显式重新运行才采用新设置。
企业策略关闭时，两个子策略也关闭；未配置的能力不可打开。网页默认使用经验验证配置，不偷偷开启企业策略。
开启企业策略后按完整流程统计，并标记“含企业策略”，避免将企业规则直接处理误当成经验复用效果。
命令行仍通过 `--purpose memory|full_flow` 和相应选项独立指定轮次配置，不读取浏览器开关。
新版 PKL、载荷 SQLite、索引必须配套；旧4,343条数据不能与新版索引混用。
同一份原始数据不拆成两个 PKL，不删除小组或单例。
独立语料包的生成/校验工具为 `scripts/build_pingan_corpus_transfer.py`。数据包只含
`source/`和`corpus/`下四个语料文件及hash清单，不包含运行/经验数据库、密钥或源代码。
新数据需一次性更新到Downloads目录，再按主Runbook落位；以后源码升级复用hash一致的数据。

```bash
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
python3.12 scripts/soc_pingan_macos_host_dev.py status
```

应看到 Runtime 为 `dev`、数据库 `ready`、schema `0031_memory_working_drafts`，三个 Core 和
三个 Sidecar 就绪。页面入口是 `http://localhost:2026/workspace/soc/corpus-validation`。
可信演示可以沿用 `--demo-no-auth`；有身份验证时，CLI 需在 `SOC_DEV_API_TOKEN` 中提供有效访问令牌。
这不是修改权限的命令，未授权时返回403，不降级成匿名管理员。

若页面提示“两批实验暂不可用：需要先升级数据库”，说明当前库未完成新版表结构升级，
不是样本不存在或四个开关失效。按主部署手册停止旧服务并用新版Host启动器启动一次，
启动器会先执行数据库升级；完成后刷新页面。不要为此重置或删除已有经验库。
接口只检查结构，不会自动迁移、创建实验或启动告警任务。

本次继续使用内网已有 SOC DEV 数据库，不搬入外网经验库，也不为升级或重试删除库。
第二批使用已经审核并开放的经验；新版过滤仅影响新学习时超过512字符的候选实体特征，
已有审核条件及第二批查询匹配规则保持兼容，不需要重新审核原有经验。

### 1.1 可选重置：仅在另行明确要求从零验证时使用

本次保留第一批及审核成果，必须跳过本节。以下仅供用户另外明确要求重新开始整套验证时使用。
若未来已在完全初始化重新部署手册中完成重置，也跳过本节，避免清空刚产生的新结果。

这不是每次部署、失败重试或第二批开始时都要执行的步骤。它会移走 SOC DEV 中的
告警结果、任务、候选、草稿、经验、反馈与实验记录，保留带hash清单的本地备份。
已有任务先暂停并等在途任务结束；本次尚未开始批跑时直接执行：

```bash
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
python3.12 scripts/soc_pingan_macos_host_dev.py stop
python3.12 scripts/soc_pingan_macos_host_dev.py reset-dev-data
```

预览应为 `status=preview`、`ready=true`，目标只为当前项目
`backend/.deer-flow/data/soc_agent_dev.db`。`files` 是将备份的SQLite文件组。
如提示端口、Worker或文件仍打开，先处理旧服务；不自动kill，也不继续清理。
确认要从零开始后执行一次：

```bash
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
mkdir -p backend/.deer-flow/internal-host-dev
umask 077
python3.12 scripts/soc_pingan_macos_host_dev.py reset-dev-data \
  --confirm RESET-SOC-DEV \
  > backend/.deer-flow/internal-host-dev/dev-reset-receipt.json
python3.12 -m json.tool backend/.deer-flow/internal-host-dev/dev-reset-receipt.json
```

成功为 `status=reset`，回执的 `backup_directory` 指向
`backend/.deer-flow/data/soc-dev-reset-backups/` 下本次备份；原本无库则为 `already_empty`。
不执行SQL删表，不改账号库、STG库、语料和密钥。重置失败不要继续启动，先看错误；
常规移动异常会恢复原文件，异常中断时保留 `state=prepared` 的清单供恢复。

然后启动一次，由启动器负责迁移空库，不需要手动执行 `db upgrade`：

```bash
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
python3.12 scripts/soc_pingan_macos_host_dev.py start --daemon --demo-no-auth
python3.12 scripts/soc_pingan_macos_host_dev.py status
```

确认schema为 `0031_memory_working_drafts`、服务就绪后进入第2节。旧备份不会被运行或检索。
不要在浏览器中恢复先前未发送的候选编辑草稿；新实验只审核新的候选。

需要撤销本次重置且尚未启动/创建新库时，可以恢复刚才的回执：

```bash
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
python3.12 scripts/soc_pingan_macos_host_dev.py stop
BACKUP_NAME="$(python3.12 - <<'PY'
import json
from pathlib import Path
receipt = json.loads(Path("backend/.deer-flow/internal-host-dev/dev-reset-receipt.json").read_text(encoding="utf-8"))
print(Path(receipt["backup_directory"]).name)
PY
)"
python3.12 scripts/soc_pingan_macos_host_dev.py restore-dev-data \
  --backup-name "$BACKUP_NAME" --confirm RESTORE-SOC-DEV
```

恢复会验证文件hash；目标已有新库时拒绝覆盖。先保留当前新库自己的重置备份，再选定旧备份恢复。
恢复旧库用于撤销/排错，不适合作为本次“从零实验”的起点。

## 2. 网页快速验证（推荐）

第一批已完成的本次升级从第4步“审核经验”继续。第1–3步供尚未开始积累或需要处理失败项时使用。

1. 打开“告警研判演练”，第一批点击 **开始积累**，覆盖整批；无需准备实验或轮次。
2. 点击 **暂停** 后不再领取批量任务，在途任务继续保存结果；点击 **继续** 跳过已完成告警。
3. 任意告警点击 **直接运行**，即使批量暂停也只运行该条。运行中看行内进度；完成后查看结果或重新运行，失败可重试。单条与批量共用任务和统计。
4. 点击 **审核经验**，审核列表自动限定当前积累范围。继续使用原有人工审核规则，确认并开放经验。完成后点击返回告警演练回到原批次。
5. 切到第二批，默认 **全部**，也可选择 **验证经验复用** 或 **其他测试告警**，再点击 **开始验证**。复用验证需要已审核开放的经验；其他测试告警允许无经验执行。

搜索、同类组等列表筛选只改变浏览内容，不缩小批量执行范围。页面展示完成、运行中、剩余、失败和待审核经验数；同一告警重跑不会增加批次总数，旧结果保留在详情和固定运行记录中。刷新页面或重启 Gateway 后恢复持久任务，包括暂停批量中的手动任务。

安装器先保留运行数据，本次再通过独立的 `reset-dev-data` 明确重置 SOC DEV；普通源码更新不自动清库。
新包须配套将 Runtime 与模型网关并发都设为8的私有配置，完整重启Host时重新构建前端。
本轮无新增数据库迁移，未变化的语料不用重新跨网传输。完整安装仍须源码包与匹配的私有配置包一起交付，
内网实际安装、重置与验收按随包主 Runbook 执行。

以下章节仅用于已有 CLI / 固定记录导出工作流；不是网页操作的前置步骤。

## 2. 初始化命令与本轮路径

在同一终端执行以下整段；重新打开终端时只需重新执行本段，不会启动或重跑告警。
它定义两个小函数，后面的命令不需要手填轮次 ID。

```bash
export TARGET_REPO="$HOME/deer-flow"
cd "$TARGET_REPO"
export SOC_EXPERIMENT_ID="EXP-CORPUS-MAC-01"
export SOC_EXPERIMENT_OUTPUT="$TARGET_REPO/backend/.deer-flow/soc-validation/memory-batch/$SOC_EXPERIMENT_ID"
mkdir -p "$SOC_EXPERIMENT_OUTPUT"
chmod 700 "$SOC_EXPERIMENT_OUTPUT"
umask 077

soc() {
  "$TARGET_REPO/backend/.venv/bin/python" \
    "$TARGET_REPO/backend/scripts/soc_corpus_experiment.py" "$@"
}
round_id() {
  "$TARGET_REPO/backend/.venv/bin/python" - "$SOC_EXPERIMENT_OUTPUT/$1-receipt.json" <<'PY'
import json
import sys
from pathlib import Path
receipt = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(receipt["round_id"])
PY
}
```

准备并固定名单，不调用模型：

```bash
soc prepare --experiment "$SOC_EXPERIMENT_ID" --name "内网两批经验验证"
soc learn --experiment "$SOC_EXPERIMENT_ID" --dry-run
```

重复 `prepare` 回读相同名单；源文件/Profile/划分规则不同则拒绝复用同一实验 ID。
`--dry-run` 只查看选择范围。页面切换第一批/第二批只取当前批次的分页数据，同类组浏览保留。

## 3. CLI 可选：分段运行第一批

第一批不读取任何历史 Memory，保留语义核对及正常研判，目的是产生可审核的经验候选。
`--purpose memory` 只对本轮关闭企业策略，不修改服务的默认开关；否则企业策略直接转交
可能跳过主研判及观察，无法形成你要审核的候选。
本轮按实际行为模式统一积累，保留真实事件时间，不受普通30天窗口分隔。
不同真实行为仍分开计数，静态同类组有10条不保证同一个实际模式有5条。

```bash
soc learn --experiment "$SOC_EXPERIMENT_ID" --purpose memory \
  --limit 5 --concurrency 8 --request-key "$SOC_EXPERIMENT_ID-learning" \
  > "$SOC_EXPERIMENT_OUTPUT/learning-receipt.json"
soc status --round "$(round_id learning)" --watch
```

正常完成应显示 `completed_count=5`、`active_count=0`、`state=completed`。
这里 completed 表示本轮当前额度完成，不是全部2,997条都完成。确认无失败后继续同轮：

`status` 的 `timing` 与页面计时同源：运行计时指允许调度期间的墙钟时间，包含等待或服务离线；
暂停累计单列。至少5条任务结束后才估算当前额度的剩余时间，含失败任务，不保证内网模型吞吐。
暂停/未启动/变更阻断时不估算；已完成轮次计时停止。任务的排队、首次领取后耗时及总耗时在导出 CSV 中分列，
不把暂停、准备额度和重试全部算成模型耗时。

```bash
soc resume --round "$(round_id learning)" --limit 50
soc status --round "$(round_id learning)" --watch
```

确认50条的耗时、失败和候选情况，再执行：

```bash
soc resume --round "$(round_id learning)" --limit all
soc status --round "$(round_id learning)" --watch
```

退出终端或关闭网页不取消后台任务。已经完成的告警不会再次调用模型。
网络中断导致第一条提交没有收到回执时，可重复原 `learn` 命令及相同 request-key；
相同参数回读原轮次，不新建一批。收到有效回执后用 `resume`，不要反复新建 `learn`。

## 4. 只审核本实验经验

页面点击“审核本实验经验”，或直接打开：

```text
http://localhost:2026/workspace/soc/review/memory-candidates?experiment=EXP-CORPUS-MAC-01
```

CLI 可分页查看待审名单：

```bash
soc candidates --experiment "$SOC_EXPERIMENT_ID" --stage pending
soc candidates --experiment "$SOC_EXPERIMENT_ID" --stage pending --offset 20
```

运营选择最终判断，补充已知业务事实，点击 AI 生成经验或人工编辑，确认适用条件及未来用途，
再确认启用。AI 起草需要额外模型调用；它不替人作最终判断，也不自动批准经验。
第一批继续运行不会使用刚确认的经验，不以自己的复用结果增加独立样本。
可选择网页逐候选生成/编辑，也可按下面步骤后台批量起草；不支持批量代填最终判断或自动审核。
同类候选按实际模式汇总，经验数不会等于第一批告警数。少于阈值时可以人工提炼，但仍需审核。

### 4.1 后台起草、网页编辑

1. 在本实验的候选审核页选择最终判断，业务事实可留空，点击“保存草稿”。这不是确认经验。
2. 只为已保存运营判断、尚无经验文字的候选生成一份固定名单，默认最多5条，不调用模型：

```bash
soc draft-plan --experiment "$SOC_EXPERIMENT_ID" --limit 5 \
  --output "$SOC_EXPERIMENT_OUTPUT/draft-plan-01.json"
```

3. 查看命令返回的 `selected_count` 和排除原因，确认后提交后台起草：

```bash
soc draft-candidates --plan "$SOC_EXPERIMENT_OUTPUT/draft-plan-01.json"
soc draft-status --experiment "$SOC_EXPERIMENT_ID"
```

重复提交同一文件会回读相同任务，不再次调用。CLI退出后任务继续；服务重启会恢复未完成任务。
已保存模型结果不会重新调用；若进程中断导致无法确认远端调用结果，任务标明
`generation_uncertain`，需明确重试，不会自动反复扣费。失败任务的 `version` 与 `job_id`
可用于 `draft-retry --experiment ... --job ... --version ...`，最多3次领取尝试。
`draft-status --experiment ... --job ...` 可查看保留的生成结果及用量；未知用量不填0。
待后台起草任务结束后单独导出，避免把经验生成成本算成告警研判成本：

```bash
soc draft-export --experiment "$SOC_EXPERIMENT_ID" \
  --output-dir "$SOC_EXPERIMENT_OUTPUT/drafting-report-01"
```

输出 `REPORT.md`、`report.json`、`draft-jobs.csv` 和hash清单。已有目录不会覆盖。
仅统计本实验后台起草，不含网页同步起草；失败或恢复前未保存的调用可能无法取得用量，
报告显式标记，不声称这是完整账单。任务尚未结束或导出中发生变化时拒绝输出，稍后重新执行。

4. 回到对应候选，读取共享草稿，七项经验内容仍可编辑；确认业务结论、适用条件和未来用途后，
再走原来的“确认并沉淀经验”。后台起草绝不自动审核或启用。

生成期间人工编辑、候选来源更新都会阻止覆盖；旧草稿与已生成结果保留，运营核对后再保存。
确实需要重生成已有文字时，生成新名单文件并加 `draft-plan --regenerate`，不要改写旧回执。
确认5条生成质量后，可用新文件、`--limit 50` 或 `--limit all` 分批扩大；并发仍与批跑共享额度。

## 5. 第二批：冻结已审核经验后验证

建议第一批和审核结束后开始第二批。创建时固定本实验第一批已确认且可使用的经验及版本。
第二批只记使用和结果，不自动积累 Pattern 或产生新经验，避免边测边改变答案。
只测同类经验效果：

```bash
soc validate --experiment "$SOC_EXPERIMENT_ID" --purpose memory --scope reuse \
  --limit 5 --concurrency 8 --request-key "$SOC_EXPERIMENT_ID-validation" \
  > "$SOC_EXPERIMENT_OUTPUT/validation-receipt.json"
soc status --round "$(round_id validation)" --watch
```

没有可用第一批经验会直接拒绝创建，不会偷偷转为无经验实验。
核对5条的参考使用/直接复用/新行为和结果，确认后依次续跑：

```bash
soc resume --round "$(round_id validation)" --limit 50
soc status --round "$(round_id validation)" --watch
```

50条通过后：

```bash
soc resume --round "$(round_id validation)" --limit all
soc status --round "$(round_id validation)" --watch
```

“其他告警测试”另建轮次，命令对应 `explore`，不混入经验复用效果的分母：

```bash
soc validate --experiment "$SOC_EXPERIMENT_ID" --purpose memory --scope explore \
  --limit 5 --request-key "$SOC_EXPERIMENT_ID-exploration" \
  > "$SOC_EXPERIMENT_OUTPUT/exploration-receipt.json"
soc status --round "$(round_id exploration)" --watch
```

后续是否扩到全部由你决定。“验证经验复用”并不保证每条都有精确 Memory；行为不同、经验未批准、
只允许参考等都会进入正常研判。“其他告警测试”包括2～5条的小组和单例，不是无价值数据。

## 6. 暂停、失败与恢复

以第二批为例，第一批只需把函数参数换成 learning：

```bash
soc pause --round "$(round_id validation)"
soc status --round "$(round_id validation)" --watch
```

暂停只停止新领取；在途任务完成后 active_count 归零。恢复用：

```bash
soc resume --round "$(round_id validation)"
```

可恢复的失败需要显式重试，不重复成功任务：

```bash
soc retry-failed --round "$(round_id validation)"
soc resume --round "$(round_id validation)"
```

配置、代码、数据或已冻结经验发生变化时，旧轮次会 blocked，不能通过继续点击 resume 忽略。
已保存结果保留。先检查错误，确需使用新配置/经验时建立新轮次，不覆盖旧回执或旧报告。
服务进程异常停止后，持久租约恢复原任务；远端完成但本地未保存的崩溃无法保证零重复计费，
报告保留尝试次数与已记录的模型用量，未知 Token 不计作0。

## 7. 导出结果与比较

轮次完成，或 pause 且在途数归零后导出：

```bash
soc export --round "$(round_id learning)" --output-dir "$SOC_EXPERIMENT_OUTPUT/learning-report"
soc export --round "$(round_id validation)" --output-dir "$SOC_EXPERIMENT_OUTPUT/validation-report"
```

输出包括 `REPORT.md`、`report.json`、逐条结果 CSV、Memory使用和失败 CSV。
报告与网页使用相同名称：第一批“沉淀经验”，第二批“验证经验复用／其他告警测试”。
报告按该轮固定 Run 读取；后来重跑同一个 alert 不会改写本轮报告。
按实际 Rule Code 和 Memory 使用关系统计，不让一条 Memory 强制只能属于一个规则。
名单/配置/经验版本、真实或Mock、每阶段耗时、总时间、模型调用和 Token 均可追踪。
处置标签只能比较忽略/转交，不当作攻击真假的准确率；未反馈不算人工确认正确。

第一批和第二批不是相同告警，不能直接用两批比例差声称经验提升。
需要无经验对照时，另选少量同一第二批告警，显式 `--memory none` 新建对照轮；
这会额外调用模型，先确认范围。两轮报告可用 `soc compare --help` 查看比较参数。
比较工具只接受同一实验、同一数据身份、同一批次的报告；缺少批次字段的旧文件需重新导出，不能猜测归属。
经验停用/修订会阻止旧快照继续派发，不会被冻结副本重新启用。

## 8. 修订后只复测相关告警

先导出第7节固定报告。以下只生成失败项名单，不调用模型、不创建任务；没有失败项会明确提示，
这时无需继续本节。不要覆盖旧报告或回执。

```bash
soc retest-plan --report "$SOC_EXPERIMENT_OUTPUT/validation-report" \
  --failed --output "$SOC_EXPERIMENT_OUTPUT/retest-plan.json"
soc validate --experiment "$SOC_EXPERIMENT_ID" \
  --selection-file "$SOC_EXPERIMENT_OUTPUT/retest-plan.json" --dry-run
```

确认数量与范围后，才执行新轮次，初始仍只跑5条：

```bash
soc validate --experiment "$SOC_EXPERIMENT_ID" --purpose memory \
  --selection-file "$SOC_EXPERIMENT_OUTPUT/retest-plan.json" \
  --limit 5 --request-key "$SOC_EXPERIMENT_ID-retest-01" \
  > "$SOC_EXPERIMENT_OUTPUT/retest-receipt.json"
soc status --round "$(round_id retest)" --watch
```

如需复测某条经验实际用过的告警，改用下面这段生成另一份名单。输入经验详情或报告里的Memory技术编号，
不需要编辑JSON。这里包括精确复用和参考使用，不包括“仅存在于经验库存、从未被使用”。

```bash
printf '输入要复测的 Memory 编号：'
IFS= read -r SOC_RETEST_MEMORY_ID
if [ -n "$SOC_RETEST_MEMORY_ID" ]; then
  soc retest-plan --report "$SOC_EXPERIMENT_OUTPUT/validation-report" \
    --used-memory "$SOC_RETEST_MEMORY_ID" \
    --output "$SOC_EXPERIMENT_OUTPUT/memory-retest-plan.json"
fi
```

然后将上面 `--selection-file` 指向 `memory-retest-plan.json`，使用新的request-key和回执名。
`retest-plan`也支持`--rule`、`--group`、`--alert`，同类参数可重复；不同参数条件同时满足，
或单用`--all`选旧报告全部成员。名单保留“其他告警测试”标签，不能用`--scope`覆盖原范围。
服务端仍检查实验/数据/批次/父轮次；此轮冻结当前已审核经验，不静默沿用已被修订的旧快照。

修订扩大/缩小了适用范围时，不能只跑旧经验用过的告警；另选范围内外的组检查，避免漏掉新覆盖或被排除的告警。
第一批的失败项同理，但使用learning报告和`learn --selection-file`，不把第二批倒灌为积累数据。

结束或暂停且在途任务归零后，再导出和比较：

```bash
soc export --round "$(round_id retest)" --output-dir "$SOC_EXPERIMENT_OUTPUT/retest-report"
soc compare --before "$SOC_EXPERIMENT_OUTPUT/validation-report" \
  --after "$SOC_EXPERIMENT_OUTPUT/retest-report" \
  --output "$SOC_EXPERIMENT_OUTPUT/retest-comparison.json"
```

比较保留旧/新Run ID和任务状态。失败变成功有单独计数；只有两边都有效的结论才参与结论变化比较。
复测是调优后的检查，不再称为首次未见样本验证。

### 网页查看前后对照

在告警演练中切到对应批次，选择本实验的复测轮次，再点「前后对照」。CLI创建的复测轮次也在此处。
每页最多20条，展示旧/新处置、实际使用的经验及版本、耗时和Token；点击「查看旧结果」或「查看复测结果」
打开对应Run，不会再次研判。配置不同会明确标注，不能把全部差异都归功于Memory。

网页手动复测时，先选已完成或已暂停且没有在途任务的旧轮次，再点「准备本批轮次」，勾选
「作为当前轮次的复测，保留旧结果对照」。核对本次范围并准备后，还要显式点击「开始运行」。
只能比较同实验、同批次；新选告警不在旧轮次时显示「旧轮次无此告警」，不伪造基线。
旧结果在准备复测时冻结，之后重试旧任务不会改写这个对照。旧版轮次没有保存基线时显示「旧结果未留存」。
CLI固定报告生成的复测名单还会校验所选旧结果摘要；旧任务在导出后变化，需要重新导出和生成名单。

## 9. 批跑时人工操作

在页面点击“直接运行”，或CLI用单个`--alert`，会优先使用下一个空闲任务位置。
网页批量暂停时也能执行指定单条，不会恢复整批。单纯打开页面不会运行。已在运行的模型调用不打断，同一告警不能并发执行两次。
经验页同步AI起草也优先等待模型空位；后台批量起草和批跑共用原来的容量，不各自再开3个。
持续人工操作会推迟后台进度，属于预期；此优先级不代表对普通聊天或其他独立系统的全局限流。
本地调用超时但底层尚未退出时，会保留占用的模型名额，后续请求等待或按预算超时；
不是把运行失败当成算力已经释放。不要因此反复重启服务或提高并发，先看模型网关与调用日志。
