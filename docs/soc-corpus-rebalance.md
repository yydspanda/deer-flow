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

**第一批、第二批都支持执行失败和语义核对失败重试，轮次ID不变，不用合并报告。需先安装支持 `retry` 的新版。**

### 5.1 查轮次，设置轮次ID和并发

查询第二批用 `validation`；查第一批把下面一行改为 `learning`。

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
            saved = p["round"]
            print(row["round_id"], "状态=" + saved["state"], "范围=" + str(p["selected_count"]),
                  "完成=" + str(p["completed_count"]), "失败=" + str(p["failed_count"]),
                  "运行中=" + str(p.get("active_count", 0)))
            print("  保存设置：", saved.get("options", {}), "用途：", saved.get("purpose"),
                  "积累代次：", saved.get("learning_generation"),
                  "连续积累：", bool(saved.get("pattern_accumulation_scope")))
        if len(rows) < 100:
            break
        offset += len(rows)
PYREPORT
```

选择本次完整轮次，核对数量和保存设置。填写一次，后续保持同一个终端：

```bash
cd "$HOME/deer-flow"
export SOC_RETRY_ROUND="替换为本次完整ROUND-ID"
export SOC_RETRY_CONCURRENCY=8
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status \
  --round "${SOC_RETRY_ROUND:?先设置轮次ID}"
```

并发可设为1～16，不能超过服务器上限。轮次ID用 `ROUND-...`，不要填 `RUN-...`。

### 5.2 暂停、重试、查看状态

**① 暂停并等待收尾。** 已完成且 `active_count=0` 的轮次跳过此块。

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py pause \
  --round "${SOC_RETRY_ROUND:?先设置轮次ID}"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status \
  --round "${SOC_RETRY_ROUND:?先设置轮次ID}" --watch
```

看到 `active_count=0` 且状态不是 `running` 后继续；其他正在运行的轮次也先暂停收尾。

**② 重试执行失败（含模型研判失败）。** 没有执行失败就跳过。

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py retry \
  --round "${SOC_RETRY_ROUND:?先设置轮次ID}" \
  --kind failed --concurrency "${SOC_RETRY_CONCURRENCY:?先设置并发}"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status \
  --round "${SOC_RETRY_ROUND:?先设置轮次ID}" --watch
```

**③ 重试语义核对失败。** 等②结束后执行；原轮次必须已开启语义核对 `apply`。

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py retry \
  --round "${SOC_RETRY_ROUND:?先设置轮次ID}" \
  --kind semantic_failed --concurrency "${SOC_RETRY_CONCURRENCY:?先设置并发}"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status \
  --round "${SOC_RETRY_ROUND:?先设置轮次ID}" --watch
```

只重试核对明确失败、已兜底完成的告警；`partial`、`skipped` 不算失败。会重新核对并执行后续研判。
**②③提交成功会自动运行，不再点击页面“继续”。** 只选个别告警时，在 `retry` 命令末尾加 `--alert 告警ID`，多条可重复添加。

**④ 随时查看进度。** 另开终端时先重新设置同一个轮次ID。

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status \
  --round "${SOC_RETRY_ROUND:?先设置轮次ID}" --watch
```

看 `state`、`active_count`、`completed_count`、`failed_count`、`counts` 和 `concurrency`。
去掉 `--watch` 只查一次；没有变化时不重复输出。**Ctrl+C只退出查看，不停止后台任务。**

**⑤ 暂停后继续补跑。** 暂停用①；等运行中为0，再执行：

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py resume \
  --round "${SOC_RETRY_ROUND:?先设置轮次ID}"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status \
  --round "${SOC_RETRY_ROUND:?先设置轮次ID}" --watch
```

**⑥ 中途修改并发。** 先执行①暂停并等运行中为0，再改数字后执行：

```bash
cd "$HOME/deer-flow"
export SOC_RETRY_CONCURRENCY=16
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py resume \
  --round "${SOC_RETRY_ROUND:?先设置轮次ID}" \
  --concurrency "${SOC_RETRY_CONCURRENCY:?先设置并发}"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status \
  --round "${SOC_RETRY_ROUND:?先设置轮次ID}" --watch
```

核对输出 `concurrency`；只改变量不会改变后台并发。业务开关不变，实际同时运行数可能低于上限。

**⑦ 补跑结束后，继续该轮原来未执行的队列。** 仅还有原排队任务时执行；先用①暂停并等运行中为0。

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py resume \
  --round "${SOC_RETRY_ROUND:?先设置轮次ID}" --restore-full-scope
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status \
  --round "${SOC_RETRY_ROUND:?先设置轮次ID}" --watch
```

只继续补跑名单用⑤；恢复整轮剩余队列才用⑦。已完成项不重跑，失败项不自动重试。

| 输出或报错 | 怎么处理 |
| --- | --- |
| `retried` 大于0 | 已启动本次重试，用④看状态 |
| `retried=0` | 没有选中可重试项；查看 `not_retryable` |
| `not_retryable` | 检查失败原因/尝试数；单个任务累计最多3次尝试 |
| `snapshot changed` / `configuration_changed` / 轮次阻断或已被替代 | 不能强行同轮重试；处理原因后，在页面重新配置并全部重跑 |
| 网络超时，未收到提交结果 | 先用④查状态，不立即重复提交 |

### 5.3 导出同一个轮次的完整报告

第二批完整结束、`failed_count=0` 后执行。目录每次新建，旧报告保留。

```bash
cd "$HOME/deer-flow"
export SOC_STAGE_REPORT_DIR="$HOME/soc-stage-report-$(date +%Y%m%d-%H%M%S)"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py export \
  --round "${SOC_RETRY_ROUND:?先设置轮次ID}" \
  --output-dir "$SOC_STAGE_REPORT_DIR"
```

打开该目录 `REPORT.md`；核对 `report_scope=stage`、`stage_conclusion_allowed=true`。
报告每条告警只统计当前结果，保留历史尝试成本；不需要另建复测轮次或合并报告。

### 5.4 统计报告中剩余的语义核对失败

紧接第5.3节执行；换终端时，把目录变量改为已导出的报告路径。

```bash
cd "$HOME/deer-flow"
export SOC_SEMANTIC_REPORT_DIR="${SOC_STAGE_REPORT_DIR:?先填写或设置报告目录}"
backend/.venv/bin/python - <<'PYSEMANTIC'
import json
import os
from pathlib import Path

path = Path(os.environ["SOC_SEMANTIC_REPORT_DIR"]).expanduser() / "report.json"
report = json.loads(path.read_text(encoding="utf-8"))
rows = report["rows"]

def semantic_failed(row):
    summary = row.get("summary") or {}
    if "normalization_assist_status" in summary:
        return summary["normalization_assist_status"] == "failed"
    return any(
        step.get("phase") == "normalization_assist"
        and step.get("status") == "failed"
        for step in summary.get("phase_timings", [])
    )

failed = [row for row in rows if semantic_failed(row)]
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

看“最终语义核对阶段失败”；`partial`、`skipped` 不计入。这里统计最终结果的阶段失败，不是LLM请求失败率。

## 6. 两批连续积累与已有结果接续

**旧版第一批已完成时执行本节，再开始新版第二批。新版轮次已自动连续积累，无需重复接续。**

### 6.1 查来源轮次，暂停两批

按第5.1节查询轮次：`learning` 查第一批，`validation` 查第二批。找到当前代次的第一批来源轮次。
对每个仍在运行的轮次，填入对应ID后执行；已完成且运行中为0的跳过：

```bash
cd "$HOME/deer-flow"
export SOC_PAUSE_ROUND="替换为仍在运行的ROUND-ID"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py pause \
  --round "${SOC_PAUSE_ROUND:?先设置要暂停的轮次}"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status \
  --round "${SOC_PAUSE_ROUND:?先设置要暂停的轮次}" --watch
```

两批全部停止运行、`active_count=0`、单条任务结束后继续；接续期间不要开始任务或审核经验。

### 6.2 预检，再接续保存结果

填写已有第一批的来源轮次，先预检：

```bash
cd "$HOME/deer-flow"
export SOC_ACCUMULATE_ROUND="替换为已有第一批ROUND-ID"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py accumulate \
  --round "${SOC_ACCUMULATE_ROUND:?先选择来源轮次}" --dry-run
```

没有错误且来源正确，再执行：

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py accumulate \
  --round "${SOC_ACCUMULATE_ROUND:?先选择来源轮次}"
```

不调用模型，不改旧报告和已审核经验。旧版单条重跑有独立轮次的，再对保存最新成功结果的轮次重复本节。

### 6.3 看输出，再启动第二批

| 输出 | 含义或操作 |
| --- | --- |
| `eligible` | 来源预检通过，不保证生成候选 |
| `processed` | 累计检查的成功来源数 |
| `counts` / `skipped_reasons` | 当前页统计；每页最多100条 |
| `model_calls=0` | 未调用模型，正常 |
| `newer_batch_result_exists` | 旧结果已被替代，改用保存最新结果的轮次接续 |
| `semantic_review_failed_learning_deferred` | 语义核对失败，暂缓积累；按第5节检查能否补跑 |
| `pattern_facet_value_out_of_bounds:...` | 匹配特征为空或超过512字符，整条暂不计入积累；原始研判保留，其余样本继续 |
| 错误退出 | 先处理错误，再执行；已经成功接续的来源不会重复计数 |

`processed` 是累计处理的来源数，包含跳过项；`accumulated` 是当前页已接续项，不是新增经验条数。
容量跳过不表示缺少核心行为，也不会截断核心行为后合并分组。`--dry-run` 只检查来源身份，实际准入以执行结果为准。

完成后在页面启动新版第二批。两批共用本代次的实际行为积累；4条独立有效样本加1条可达5条门槛，仍须通过质量检查。
直接复用或使用已审核经验的结果不作为独立结论支持。第二批新候选等本轮结束后在页面审核，不能用要求“第二批尚未开始”的第一批批量审核脚本。
本轮使用的经验快照不变；观察修正保留历史，不重复计数。旧代次不能接续到新代次；不需要重跑第一批或清库。
