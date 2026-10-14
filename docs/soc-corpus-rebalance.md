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
验证期间冻结所用审核经验，不同时改写经验，不自动把第二批积累进第一批。

等完整5,917条结束并处理失败后，再导出阶段报告。报告应说明：

- 新旧名单变化，第一批9,364条、第二批5,917条及两个验证范围的分别统计。
- 实际适用经验的数量、占比，以及其中建议忽略的数量。
- 直接复用、参考经验、无历史经验的分流情况。
- 完成情况、耗时和剩余证据缺口。

不比较原始处置标签，不用部分结果代替阶段结论。
移入学习的6,367条不再计入第二批分母；名单不同，不把两轮覆盖率差值说成模型准确率提升。
扩大第一批增加了积累机会，是否增加有效经验与复用覆盖，仍由本轮完整验证确认。

## 5. 完整结束后导出报告

本轮使用项目内 `soc_corpus_experiment.py export`，不要用旧的独立历史标签报告工具作为正式汇报入口。
以下命令仅查询当前网页实验与第二批轮次的简表，不打印经验快照，不启动任务或模型：

```bash
cd "$HOME/deer-flow"
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

    current = get("/quick-validation", batch="validation", scope="all", limit=1)
    print("当前实验：", current["experiment_id"], "第二批总数：", current["total"])
    offset = 0
    while True:
        rows = get(f"/experiments/{current['experiment_id']}/rounds", batch="validation", limit=100, offset=offset)
        for row in rows:
            p = get(f"/rounds/{row['round_id']}")
            print(row["round_id"], "状态=" + p["round"]["state"], "范围=" + str(p["selected_count"]),
                  "完成=" + str(p["completed_count"]), "失败=" + str(p["failed_count"]))
        if len(rows) < 100:
            break
        offset += len(rows)
PYREPORT
```

找到这轮**范围5917、完成5917、失败0**的完整第二批ID，填入下面命令。
不要选择后续单条重跑或定向复测轮次；如果只有部分范围或失败尚未处理，先核对并完成该固定完整轮次。
若做过整批重新运行，选择本次要汇报的完整轮次，旧轮次不覆盖。
页面完成数按各告警的最新任务汇总，可能来自多个轮次；它不能替代该固定轮次的完成状态。

如果原完整轮次存在失败，应重试该轮的失败任务再继续；仅在页面单条重跑会产生另一轮，
不会改写原轮的固定结果。配置或经验快照已变化时按页面提示新开完整轮次，不混拼历史结果。

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py retry-failed \
  --round "替换为需要重试的完整第二批ROUND-ID"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py resume \
  --round "替换为同一个完整第二批ROUND-ID"
```

原轮没有失败则跳过这两条；确认完整结束后导出：

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py export \
  --round "替换为上面确认的完整第二批ROUND-ID" \
  --output-dir "$HOME/soc-stage-report-$(date +%Y%m%d-%H%M%S)"
```

导出目录包含 `REPORT.md`、`report.json` 和明细CSV；只读取固定运行结果。
新版报告按经验适用与分流汇总，不使用历史处置标签评分。未完成的导出仅标为诊断，
不产生阶段结论；正式汇报须核对 `report_scope=stage`、`stage_conclusion_allowed=true`。

### 5.1 统计语义核对失败与兜底

先在下面设置**要检查的报告目录**，5.1和5.2节均使用同一个变量；换一轮时只改这一处。
脚本只读取指定报告，不自动选择最新轮次。统计可以在新轮次运行期间执行，无需停服或调用模型。
重新打开终端后需要再次设置该变量。

```bash
cd "$HOME/deer-flow"
export SOC_SEMANTIC_REPORT_DIR="$HOME/替换为要检查的报告目录"
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
print("语义核对阶段失败：", len(failed))
if rows:
    print(f"占全部告警：{len(failed) / len(rows):.2%}")
print("其中任务仍完成：", sum(r["status"] == "completed" for r in failed))
print("\n失败记录（告警 ID / Run ID）：")
for row in failed:
    print(row["alert_id"], row["run_id"])
PYSEMANTIC
```

这里按每条告警最终保存的 Run 统计 `normalization_assist` 阶段失败；
“其中任务仍完成”表示该阶段失败后，任务仍通过兜底继续完成。`partial` 和 `skipped` 不算失败。
这不是严格的LLM请求失败率：阶段失败还可能来自输出解析或合并异常，
也不包含更早失败后重跑成功的 Run、SDK内部重试后恢复的请求。
若要进一步区分超时、服务异常和输出异常，按打印的 Run ID 查看运行审计中的语义核对失败原因。

### 5.2 定向复测语义核对失败的告警

统计结束后，如需补跑这批语义核对失败的告警，使用下面的定向复测命令。
`retry-failed` 和 `retest-plan --failed` 只处理整个任务失败，不能选中已经兜底完成的告警。

复测会新建轮次，重新核对事实，再继续经验匹配和研判，可能再次调用研判模型；
它不是只补一次LLM调用后修改旧结果。第一批不重跑，原轮次及已导出的报告保留。
如果正在进行“四项全开”的整批验证，先完成并导出该轮报告，再执行本节，避免交叉运行影响页面进度和耗时统计。
**定向复测只使用明确 `--round` 的查询、暂停和续跑命令，不点击页面“继续”。**
页面“继续”是批次命令，旧部署可能同时恢复其他历史轮次；页面开关不会改写它们已经保存的设置。

**第一步：从指定报告生成名单，不调用模型。** 沿用上面设置的报告目录；数量由报告自动计算，
实验ID、原轮次ID和告警ID均从报告读取，不需要手填。

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python - <<'PYSEMANTICPLAN'
import json
import os
import subprocess
import sys
from pathlib import Path

report_dir = Path(os.environ["SOC_SEMANTIC_REPORT_DIR"]).expanduser()
report_path = report_dir / "report.json"
report = json.loads(report_path.read_text(encoding="utf-8"))
alert_ids = [
    row["alert_id"] for row in report["rows"]
    if any(
        step.get("phase") == "normalization_assist"
        and step.get("status") == "failed"
        for step in row.get("summary", {}).get("phase_timings", [])
    )
]
if not alert_ids:
    raise SystemExit("没有语义核对阶段失败的告警，无需复测。")

command = [
    sys.executable, "backend/scripts/soc_corpus_experiment.py",
    "retest-plan", "--report", str(report_path),
    "--output", str(report_dir / "semantic-retest-plan.json"),
]
for alert_id in alert_ids:
    command.extend(["--alert", alert_id])
subprocess.run(command, check=True)
PYSEMANTICPLAN
```

名单写入报告目录中的 `semantic-retest-plan.json`。已经生成过时保留文件，直接下一步，不删除重建。

**第二步：启动定向复测。** 实验、原轮次、运行开关、并发和幂等键自动从报告与名单取得。
脚本先检查该实验所有轮次；任何轮次仍在运行或存在进行中的任务时，会在提交前停止。
**你正在跑新配置时，不执行复测；等该轮结束并导出后，改为选它的报告即可复测它的失败项。**

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python - <<'PYSEMANTICSTART'
import hashlib
import json
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path("backend").resolve()))
from soc_agent.demo.corpus_retest_selection import read_retest_plan

report_dir = Path(os.environ["SOC_SEMANTIC_REPORT_DIR"]).expanduser()
raw = (report_dir / "report.json").read_bytes()
report = json.loads(raw)
plan = read_retest_plan(report_dir / "semantic-retest-plan.json")
old = report["round"]
if hashlib.sha256(raw).hexdigest() != plan.provenance.report_sha256:
    raise SystemExit("报告与复测名单不一致，请使用生成该名单的原报告。")
if old["experiment_id"] != plan.experiment_id or old["round_id"] != plan.provenance.parent_round_id:
    raise SystemExit("报告与名单的实验或轮次不一致。")
if plan.selection.batch != "validation" or old["purpose"] == "facts":
    raise SystemExit("本节仅用于第二批验证报告。")
options = dict(old["options"])
if options.get("normalization_review_mode") != "apply":
    raise SystemExit("来源报告未开启语义核对 apply，不属于本节的失败恢复范围。")
options["refresh_normalization"] = True
body = {
    "experiment_id": plan.experiment_id,
    "selection": plan.selection.model_dump(mode="json"),
    "purpose": old["purpose"],
    "options": options,
    "memory_mode": old["memory_mode"],
    "parent_round_id": plan.provenance.parent_round_id,
    "retest_provenance": plan.provenance.model_dump(mode="json"),
    "execution_limit": 2147483647,
    "concurrency": old["concurrency"],
}
key = "semantic-retest-" + plan.plan_hash
headers = {}
if token := os.environ.get("SOC_DEV_API_TOKEN"):
    headers["Authorization"] = f"Bearer {token}"
prefix = "/api/soc/dev/corpus-workbench"
with httpx.Client(base_url="http://127.0.0.1:2026", headers=headers, timeout=120, trust_env=False) as client:
    def request(method, path, **kwargs):
        response = client.request(method, prefix + path, **kwargs)
        response.raise_for_status()
        return response.json()

    offset = 0
    while True:
        rounds = request("GET", f"/experiments/{plan.experiment_id}/rounds", params={"limit": 100, "offset": offset})
        for item in rounds:
            progress = request("GET", f"/rounds/{item['round_id']}")
            if progress["round"]["state"] == "running" or progress["active_count"] > 0:
                raise SystemExit(f"未提交复测：{item['round_id']} 仍在运行或收尾。请先完成并导出该轮。")
        if len(rounds) < 100:
            break
        offset += len(rounds)

    print("来源实验：", plan.experiment_id)
    print("来源轮次：", plan.provenance.parent_round_id)
    print("复测告警数：", len(plan.selection.alert_ids))
    print("复测设置：", json.dumps(options, ensure_ascii=False), flush=True)
    new = request("POST", "/rounds", json=body, headers={"Idempotency-Key": key})
    print("新复测轮次：", new["round_id"], flush=True)
    if new["state"] == "prepared":
        new = request("POST", f"/rounds/{new['round_id']}/start", json={})
    print("状态：", new["state"])
PYSEMANTICSTART
```

命令提交后即返回，后台继续执行；记录打印的新 `round_id`。以下三种操作分别按需执行，
全部填写这个复测轮次ID，不填写来源报告的轮次ID。查询只观察指定轮次，Ctrl+C只退出观察：

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status \
  --round "替换为打印的新复测ROUND-ID" --watch
```

需要暂停时执行，已有活动任务收尾后才完全停止：

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py pause \
  --round "替换为同一个新复测ROUND-ID"
```

需要继续已暂停的复测时，先按第5节查询轮次列表，确认其他轮次没有运行或收尾，再执行：

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py resume \
  --round "替换为同一个新复测ROUND-ID"
```

已完成的复测不再续跑；`blocked` 按实际停止原因处理，续跑不会解除阻断。
相同名单重复提交沿用相同幂等键；若提示已有轮次运行，查看该轮，不另建名单绕过检查。
检查是提交前预检，执行期间不要同时在页面或其他终端启动任务；它不是服务端全局互斥锁。

**作用范围：所选报告决定告警名单及对比来源；新建的复测轮次保存新结果。**
不会把任务追加到来源轮次或正在跑的新轮次，也不会自动修改已导出的报告。
页面按告警展示最新任务，因此复测会改变这些告警在页面上的最新结果；原轮次的固定结果仍保留。
页面运行开关用于新提交的任务，刷新时可能显示最近保存轮次的设置；它不是所有活动轮次的配置汇总。
每个已有轮次续跑时沿用各自保存的设置。因此页面只显示语义核对开启，也不代表后台旧“四项全开”轮次已关闭企业策略。
后续新建整批任务前仍应核对设置；最大并发是另行保存、两批共享的容量设置。

脚本沿用报告中的语义核对和企业策略等运行开关，仅将“重新核对事实”设为开启；
不会误用当前页面新轮次的开关。模型、代码和已审核经验仍按创建时的部署状态冻结，
不自动恢复旧版本或旧经验快照；若这些已变化，不能把结果差异全部归因于语义核对恢复。

**第三步：单独导出和统计。** 结束后按第5节的 `export` 命令导出新轮次，使用新的输出目录，
再按5.1节统计剩余语义核对失败数。整个任务完成不代表语义核对已成功。
该报告只覆盖选出的复测告警，是定向诊断；不要与来源报告直接拼成一份完整阶段结论。

**如果已经误点页面“继续”。** 先用第5节的只读查询找出复测轮次和旧全量轮次，分别用
`status --round` 查看。125条复测已经完成而旧全量轮次仍在运行，两者可以同时成立；
不能用页面总进度或当前开关认定复测还没结束、旧轮次已换配置。要只保留定向复测，暂停旧全量轮次：

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py pause \
  --round "替换为误恢复的旧全量ROUND-ID"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status \
  --round "替换为同一个旧全量ROUND-ID" --watch
```

确认该旧轮次 `active_count=0` 后，只对尚未完成、可继续的复测轮次执行上面的指定轮次续跑。
若复测已完成，直接按固定复测ID单独导出；旧全量轮次的剩余任务另行决定何时继续。

旧部署的页面“继续”还可能缩窄历史轮次的可调度名单。若旧全量轮次仍有未完成项、
持续没有活动任务且无法推进，先保持暂停，不手工改数据库或反复点页面“继续”。
单看 `active_count=0` 和有剩余任务不能认定名单被裁；需核对轮次详情中 `round.dispatch_alert_ids`
与完整 `selected_count`，并确认调度范围外仍有 `queued` 任务。
新增的 `resume --restore-full-scope` 仅在部署已支持该选项，且该轮冻结的代码、配置与经验快照
仍然匹配时可用；执行前还须保持该轮暂停并确认 `active_count=0`。该选项只恢复原始固定名单的
调度范围，不会改写已保存开关、重跑已完成任务，也不会自动重试已失败的任务。
失败项仍需按该轮的重试条件另行处理；快照不兼容时仍会拒绝续跑，不绕过停止门禁。
目前 `20261013T020624Z` 交付源码归档不含该选项，本次手册修订也没有重打源码包。
**升级代码本身可能使旧轮次的代码快照不再匹配，所以不能承诺升级后就能恢复该旧全量轮次。**
旧部署不能执行新选项，重装同一包也无效；保留已完成结果和旧轮次，具体恢复路径需另行核对。
上面的指定轮次命令仍适用于当前部署中快照匹配、允许续跑的复测轮次。

## 旧命令的边界

代码仍保留可选的语义事实后继实验接口和历史结果读取能力，便于审计与兼容；
本次不使用这条高级命令流程。无需Git整体回退，也不回退已经修复的核心行为提取、
经验匹配和180天默认值。内网部署、数据库归档重置和真实效果验证由操作员执行。
