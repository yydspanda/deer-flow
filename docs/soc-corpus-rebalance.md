# 按原分组扩大第一批，在页面完成两批验证

本次只调整原来的静态分批名单：从“验证经验复用”移一部分告警到第一批，
“其他测试告警”名单保持不变。不运行全量 `rebalance-facts`，不重建语料索引。
正常研判仍开启语义核对，经验按真实核心行为分别积累，最低支持数和审核条件不变。

## 1. 本次分配结果

对原来已有第一批样本的粗分组，按事件时间排序，取前约80%（向上取整）学习，
至少留1条验证；取消每组最多学习10条的限制。原第一批成员全部保留。
例如8条分为7学1验，20条分为16学4验。原1～5条的小组、单例和时间不明样本继续留在其他测试告警。
这里的比例只描述静态抽样组，不保证每种真实核心行为都积累满5条。

对当前配套的15,281条有效语料，名单为：

| 页面范围 | 原数量 | 新数量 |
| --- | ---: | ---: |
| 第一批：沉淀经验 | 2,997 | **9,364** |
| 第二批：验证经验复用 | 8,520 | **2,153** |
| 第二批：其他测试告警 | 3,764 | **3,764** |
| 第二批：全部 | 12,284 | **5,917** |

只有原主验证中的6,367条移动到第一批；每条告警的原分组不变。
原始15,288条语料保留，7条既有范围排除不变；无需改PKL、索引或载荷SQLite。
分配仅使用固定分组、事件时间和稳定身份，不看历史处置标签、模型判断或经验命中结果。

## 2. 停止旧运行，准备新一轮

如果终端还在显示旧事实准备进度，先按Ctrl+C结束查看；这不会停止后台任务。
在内网Mac停止Host DEV，等待退出，再按部署手册备份：

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python scripts/soc_pingan_macos_host_dev.py stop
```

本次用户倾向重新开始，推荐**保留完整旧库备份，再显式重置SOC DEV**。
新包内按 `PINGAN-INTERNAL-MAC-REINITIALIZE-RUNBOOK.md` 执行停服备份、安装、语料落位、
依赖安装、预检、DEV归档重置、启动和模型Smoke。不要再串行执行保留数据升级教程，
也不要重复已经完成的重置。当前仍在旧包时先停在此处，更新后的源码才能产生新名单。

安装器不会自行重置数据库；归档重置仍须由操作员执行既有确认命令。
旧库及101条已审核经验留在备份中，本轮从空结果、空经验重新学习。
不要只删数据库文件或手工改批次，也不要恢复旧库覆盖本轮运行。

如果选择保留活动数据库，新plan会有独立实验/任务，但历史告警结果仍可显示，
旧候选与经验仍存在，不能把它描述成从零验证。旧运行队列必须先暂停，
否则重启后可能恢复事实任务；仅停服并不等于持久暂停。
本次采用归档重置后的页面流程，避免旧任务恢复和新旧结果混淆。

## 3. 在页面跑第一批

在部署Mac打开 `http://localhost:2026/workspace/soc/corpus-validation`。

1. 核对第一批显示 **9,364条**、第二批全部 **5,917条**；若仍是2,997和12,284，
   说明尚未使用新代码或页面仍旧，不要开始运行。
2. 第一批运行设置开启“语义核对”，经验验证保持企业策略关闭；确认最大并发16。
   重置不会删除单独保存的并发设置，因此要核对页面当前值。
3. 点击“开始积累”，后续用同一页面“暂停／继续”，在页面查看完成数、失败和历史记录。
4. 第一批全部完成、失败处理完后，点击“审核经验”。按实际待审名单审核；
   如使用批量审核工具，重新生成计划，不沿用旧117条计划。

这次不用事实实验ID，也不执行 `rebalance-plan`、`rebalance-apply` 或CLI `learn`。
页面和后台使用同一份固定新名单。语义核对随正常研判执行，不额外先跑完整语料一遍。
并发16是上限，同组顺序和模型容量仍会影响速度；第一批扩大后也会比原2,997条处理更多任务。
上述页面“暂停／继续”用于整批操作；同批存在历史轮次或定向复测时，按第5.2节指定轮次控制，
不要用页面“继续”代替复测轮次的续跑命令。

## 4. 在页面完整验证第二批

第一批审核完成后，切到第二批，重新确认语义核对开启及运行设置。
选择“全部”并开始验证，覆盖2,153条验证经验复用和3,764条其他测试告警。
验证期间冻结所用审核经验，不同时审核或改写经验。新版第二批也将实际行为观察接续到同一实验、同一积累代次；新产生的候选留待本轮结束后审核，不参与本轮判断。

等完整5,917条结束并处理失败后，再导出阶段报告。报告应说明：

- 新旧名单变化，第一批9,364条、第二批5,917条及两个验证范围的分别统计。
- 实际适用经验的数量、占比，以及其中建议忽略的数量。
- 直接复用、参考经验、无历史经验的分流情况。
- 完成情况、耗时和剩余证据缺口。

不比较原始处置标签，不用部分结果代替阶段结论。
移入学习的6,367条不再计入第二批分母；名单不同，不把两轮覆盖率差值说成模型准确率提升。
扩大第一批增加了积累机会，是否增加有效经验与复用覆盖，仍由本轮完整验证确认。

## 5. 在原轮次补跑失败项并导出完整报告

第一批、第二批的模型研判失败和语义核对失败，都支持原轮次补跑。`round_id` 表示这次固定样本与设置，
每次实际运行另有 `run_id`；补跑保留旧 Run，更新该轮告警的当前结果。完成后导出同一个轮次，
每条告警只计一次，无需合并报告。

页面中失败告警的“重试”沿用其所属轮次及保存设置；成功告警的“重新运行”仍是另一次运行，
不能用它替代原轮次的失败恢复。批量恢复和语义核对恢复可使用本节命令。
这些命令要求安装包含 `retry` 子命令的新版本；旧包不支持时需要更新代码，不能只复制教程执行。

### 5.1 选定这次要恢复、汇报的完整轮次

以下只读查询列出当前网页实验的指定批次轮次。`SOC_RETRY_BATCH` 选 `learning`（第一批）或 `validation`（第二批）；不启动任务、不打印经验快照：

```bash
cd "$HOME/deer-flow"
export SOC_RETRY_BATCH=validation
backend/.venv/bin/python - <<'PYREPORT'
import os
import httpx

headers = {}
if token := os.environ.get("SOC_DEV_API_TOKEN"):
    headers["Authorization"] = f"Bearer {token}"
base = "/api/soc/dev/corpus-workbench"
with httpx.Client(base_url="http://127.0.0.1:2026", headers=headers, timeout=120, trust_env=False) as client:
    def get(path, **params):
        response = client.get(base + path, params=params)
        response.raise_for_status()
        return response.json()

    batch = os.environ["SOC_RETRY_BATCH"]
    if batch not in {"learning", "validation"}:
        raise ValueError("SOC_RETRY_BATCH 必须为 learning 或 validation")
    current = get("/quick-validation", batch=batch, scope="all", limit=1)
    print("当前实验：", current["experiment_id"], "批次：", batch, "总数：", current["total"])
    offset = 0
    while True:
        rows = get(f"/experiments/{current['experiment_id']}/rounds", batch=batch, limit=100, offset=offset)
        for row in rows:
            p = get(f"/rounds/{row['round_id']}")
            print(row["round_id"], "状态=" + p["round"]["state"], "范围=" + str(p["selected_count"]),
                  "完成=" + str(p["completed_count"]), "失败=" + str(p["failed_count"]))
        if len(rows) < 100:
            break
        offset += len(rows)
PYREPORT
```

选择这次要恢复的完整轮次，核对范围和保存设置。不要选择只有一条或少量告警的历史复测轮次。
页面按各告警最新任务汇总；页面全部完成，不一定表示某个固定轮次也全部完成。
仅在这里填写一次轮次 ID，后续命令都使用同一个值；重新打开终端后需重新设置。

```bash
cd "$HOME/deer-flow"
export SOC_RETRY_ROUND="替换为本次完整ROUND-ID"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status \
  --round "${SOC_RETRY_ROUND:?先设置要恢复的轮次ID}"
```

配置、模型、代码或已审核经验快照变化时，旧轮次仍会拒绝重试。此时按提示重新配置并新建完整轮次，
保留旧记录；升级新功能不代表升级前的旧轮次能直接补跑。不要手工改数据库或绕过快照检查。

### 5.2 同轮次批量补跑

先暂停选中的轮次，等待 `active_count=0`。已完成的轮次无需暂停，直接检查状态。
若同批其他轮次仍在运行，先让它结束或暂停收尾，避免两个验证配置交叉运行。

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py pause \
  --round "${SOC_RETRY_ROUND:?先设置要恢复的轮次ID}"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status \
  --round "${SOC_RETRY_ROUND:?先设置要恢复的轮次ID}" --watch
```

**任务失败：** 补跑该轮允许重试的失败任务，包括模型研判失败。示例并发8，可手工改为1～16，
仍受服务器保存的最大并发限制；省略 `--concurrency` 则沿用该轮原并发。

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py retry \
  --round "${SOC_RETRY_ROUND:?先设置要恢复的轮次ID}" --kind failed --concurrency 8
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status \
  --round "${SOC_RETRY_ROUND:?先设置要恢复的轮次ID}" --watch
```

**语义核对失败：** 等前一类补跑结束后，补跑该轮语义核对明确失败、但已兜底完成的告警（两批均支持）。
只选取 `normalization_assist=failed`；`partial`、`skipped` 和关闭核对不算失败。
原轮次必须开启语义核对 `apply`，并且不是事实准备轮次。关闭核对的旧轮次不能用此命令补开功能，需重新配置运行。

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py retry \
  --round "${SOC_RETRY_ROUND:?先设置要恢复的轮次ID}" --kind semantic_failed --concurrency 8
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status \
  --round "${SOC_RETRY_ROUND:?先设置要恢复的轮次ID}" --watch
```

两类命令分别按需执行，没有对应失败项就跳过。命令直接查询指定轮次当前失败项，
不依赖旧报告目录或手工失败名单，也不会误用页面上另一轮的开关。
只重试个别告警时，在对应命令后加 `--alert 告警ID`；多条可重复添加 `--alert`。
命令会自动读取该轮当前版本再提交；期间状态变化会拒绝提交，不会自动重复发送。
网络超时或响应丢失时先用 `status` 确认是否已开始；再次执行 `retry` 表示明确发起下一次补跑。

`retried` 是本次重新入队数，`not_retryable` 是因错误性质或重试次数限制未入队的数量。
命令提交后后台自动启动选中项，不用再执行 `resume`；`status --watch` 只看进度，Ctrl+C只退出观察。
需要暂停时用同一个轮次的 `pause`；继续补跑用该轮 `resume`，不加 `--restore-full-scope`。
语义补跑会重新核对事实并重新执行后续研判，可能再次调用研判模型；不是只改核对状态。
原轮的业务开关、模型和经验快照保留，单次事实刷新作为重试信息记录。
新版连续积累中，核对明确失败的样本暂缓积累；成功补跑后才纳入。同一来源只保留一个当前有效观察，
修正行为组会撤销旧组中这条支持，旧观察和原 Run 仍保留审计。不会把重试次数算作多个独立样本。

补跑只调度本次选中项。若这个轮次原本还有尚未运行的排队任务，补跑结束后先确认该轮暂停且
`active_count=0`，再恢复其完整固定范围继续执行：

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py pause \
  --round "${SOC_RETRY_ROUND:?先设置要恢复的轮次ID}"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py resume \
  --round "${SOC_RETRY_ROUND:?先设置要恢复的轮次ID}" --restore-full-scope
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status \
  --round "${SOC_RETRY_ROUND:?先设置要恢复的轮次ID}" --watch
```

这个恢复范围命令不会重跑已完成任务，也不会自动重试失败任务。已经完整结束则无需执行。
再次失败会继续保留失败状态或语义兜底状态，不能只选择成功尝试汇报。

### 5.3 导出同一个轮次的完整报告

确认该轮覆盖完整第二批、全部完成且没有任务失败后导出。语义兜底仍需在报告中说明。
每次使用新的输出目录，保留先前导出；无需新建复测轮次或人工替换报告行。

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py export \
  --round "${SOC_RETRY_ROUND:?先设置要恢复的轮次ID}" \
  --output-dir "$HOME/soc-stage-report-$(date +%Y%m%d-%H%M%S)"
```

目录包含 `REPORT.md`、`report.json` 和明细CSV。结论采用该轮每条告警最新的补跑结果，
历史 Run 及已保存调用用量保留；缺失用量不会假设为零。报告不使用历史处置标签评分。
正式汇报须核对 `report_scope=stage`、`stage_conclusion_allowed=true`；未完整结束的导出仅为诊断。

### 5.4 统计最终剩余的语义核对失败

下面只读取指定报告，不会启动模型；换报告只改目录变量。

```bash
cd "$HOME/deer-flow"
export SOC_SEMANTIC_REPORT_DIR="$HOME/替换为刚导出的报告目录"
backend/.venv/bin/python - <<'PYSEMANTIC'
import json
import os
from pathlib import Path

path = Path(os.environ["SOC_SEMANTIC_REPORT_DIR"]).expanduser() / "report.json"
report = json.loads(path.read_text(encoding="utf-8"))
rows = report["rows"]
failed = [
    row for row in rows
    if any(
        step.get("phase") == "normalization_assist"
        and step.get("status") == "failed"
        for step in row.get("summary", {}).get("phase_timings", [])
    )
]
print("统计轮次：", report["round"]["round_id"])
print("告警总数：", len(rows))
print("最终语义核对阶段失败：", len(failed))
if rows:
    print(f"占全部告警：{len(failed) / len(rows):.2%}")
print("其中任务仍完成：", sum(r["status"] == "completed" for r in failed))
print("\n失败记录（告警 ID / Run ID）：")
for row in failed:
    print(row["alert_id"], row["run_id"])
PYSEMANTIC
```

这是最终采用 Run 的阶段失败数，包含超时、输出解析或合并异常等情况；不是严格的LLM请求失败率，
也不包含先前失败但已恢复的 Run 或 SDK 内部重试。`partial` 和 `skipped` 不算失败。
要区分具体原因，按 Run ID 查看运行审计；历史失败尝试不因补跑成功而删除。

## 旧命令的边界

代码仍保留可选的语义事实后继实验接口和历史结果读取能力，便于审计与兼容；
本次不使用这条高级命令流程。无需Git整体回退，也不回退已经修复的核心行为提取、
经验匹配和180天默认值。内网部署、数据库归档重置和真实效果验证由操作员执行。


## 6. 两批连续积累与已有结果接续

新版创建的轮次在同一实验、同一积累代次内共用实际行为积累池。粗分组仅用于浏览和名单划分；
不同核心行为、Profile 或独立事件仍按原有规则区分。第一批同一行为已有4条独立有效样本，
第二批再出现1条即可达到5条支持；还需满足结论质量等门槛，才生成待审核候选。
直接复用经验或研判输入含已审核经验的样本可以计入出现次数，但不作为独立结论支持。

第二批仍使用启动时冻结的已审核经验。新增候选不会自动通过审核，也不会改变本轮后续告警的判断；
等本轮完成再审核，下一轮才能使用。两批全部完成后，在经验中心查看合并后的实际行为组，
不要将两批静态分组数相加当作行为指纹总数。显式“第一批全部重跑”仍开启新的积累代次。

升级前已经完成的第一批不必为了接续积累重跑模型。新版部署后，先暂停该实验的运行并等待活动任务归零，
使用保存的成功 Run 接续到连续积累池。先选择来源第一批轮次；如旧版单条重跑产生了独立轮次，
再对那些来源轮次执行同样步骤。已被更新任务替代的旧结果会跳过，重复执行不会重复计数。

```bash
cd "$HOME/deer-flow"
export SOC_ACCUMULATE_ROUND="替换为已有第一批ROUND-ID"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py accumulate \
  --round "${SOC_ACCUMULATE_ROUND:?先选择来源轮次}" --dry-run
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py accumulate \
  --round "${SOC_ACCUMULATE_ROUND:?先选择来源轮次}"
```

此命令只根据保存的事实、Profile 和结果更新观察与候选，不调用语义核对或研判模型，不改旧 Run、旧任务、
轮次报告或已审核 Memory。只有当前积累代次的匹配来源可接续；不能借此恢复旧轮次的代码快照。
旧待审核候选若引用被替代的观察，会保留为失效历史并重新满足门槛后生成新候选；已审核经验保留原内容。
接续完成后，在页面按所需设置创建新版第二批轮次，正常开始验证即可。
