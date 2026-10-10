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
            print(row["round_id"], "状态=" + p["state"], "范围=" + str(p["selected_count"]),
                  "完成=" + str(p["completed_count"]), "失败=" + str(p["failed_count"]))
        if len(rows) < 100:
            break
        offset += len(rows)
PYREPORT
```

找到这轮**范围5917、完成5917、失败0**的完整第二批ID，填入下面命令。
不要选择后续单条重跑轮次；如果只有部分范围或失败尚未处理，先回页面完成整批。
若做过整批重新运行，选择本次要汇报的完整轮次，旧轮次不覆盖。

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

## 旧命令的边界

代码仍保留可选的语义事实后继实验接口和历史结果读取能力，便于审计与兼容；
本次不使用这条高级命令流程。无需Git整体回退，也不回退已经修复的核心行为提取、
经验匹配和180天默认值。内网部署、数据库归档重置和真实效果验证由操作员执行。
