# 保留现有数据，扩大第一批后重新验证

这套流程适用于第一批已经积累经验、第二批已经运行部分样本的 DEV 实验。
升级应用代码后，在同一个数据库中建立有来源记录的新实验，保留旧实验、旧结果和审核记录。
不执行 `reset-dev-data`、第二批清理或手工 SQL 改批次；现有语料文件保持原样。
本功能不自动生成部署包。

新版实验通过 CLI 使用明确的实验 ID 操作。网页原来的快捷第一批、第二批仍指向原实验，
不要用旧快捷按钮启动新版实验。新版候选可通过带 `experiment` 参数的审核页面打开。

## 划分规则

先收集每条告警经过语义核对的真实核心行为，再生成固定名单。旧索引的粗分组只用于
原始来源追溯，不作为合并不同核心行为的依据，也不根据历史处置标签、模型判断或经验命中
结果挑选学习样本。

默认每种行为保留较后的两个独立事件用于验证，可以选择只保留一个：

| 同一行为的独立事件总数 | 默认学习 / 验证 | 选择 `--holdout 1` |
| --- | --- | --- |
| 8 | 6 / 2 | 7 / 1 |
| 6 | 5 / 1 | 5 / 1 |
| 5 | 4 / 1，验证归补充场景 | 相同 |
| 2 | 1 / 1，验证归补充场景 | 相同 |
| 1 | 0 / 1，验证归补充场景 | 相同 |

原第一批成员始终留在学习侧。已经用于学习的同一事件重复告警也不能进入验证侧；
重复告警不增加独立事件计数。因此实际告警条数可能与表中的独立事件数不同。
如果原学习成员已经是该行为的最后事件，无法同时保证保留原学习和留下更晚事件，
名单会明确记录 `no_holdout_after_existing_learning`，该行为可能没有验证样本。
时间相同按告警 ID 排序，时间交叠会记录 `learning_holdout_times_overlap`，不据此声称严格未来预测效果。

少量样本也可以先积累，但产生候选的最低支持数仍为 5，质量检查和人工审核保持不变。
单例不能复制成一条学习、一条验证。第一批最终可以比第二批多。

## 1. 升级、暂停旧轮次并备份

先完成保留数据的代码升级。以下命令在内网部署 Mac 的同一终端执行：

```bash
cd "$HOME/deer-flow"
umask 077
soc_parent_experiment="EXP-QUICK-aff29c78b815e4abcb216c0fb9b50a66"
soc_new_experiment="EXP-LEARNING-FIRST-20261010"
soc_rebalance_output="$HOME/soc-rebalance-$soc_new_experiment"
mkdir -p "$soc_rebalance_output"

soc() {
  "$HOME/deer-flow/backend/.venv/bin/python" \
    "$HOME/deer-flow/backend/scripts/soc_corpus_experiment.py" "$@"
}

soc list --experiment "$soc_parent_experiment"
soc candidates --experiment "$soc_parent_experiment" --stage pending
```

旧第一批待审队列应为零。有待审项时先完成审核或按业务判断取消沉淀；事实准备、计划和应用
都会检查这一前提，避免旧待审候选被新积累引用却不在新审核列表中。已确认、已拒绝的记录保留。

把仍运行的旧轮次 ID 代入下面两条命令。若有多个运行轮次，逐个执行。
`pause` 停止派发新任务，已在处理的任务会正常收尾；`status --watch` 等待活动任务归零。

```bash
soc pause --round ROUND_替换为旧运行轮次ID
soc status --round ROUND_替换为旧运行轮次ID --watch
```

确认旧轮次均已停止派发且 `active_count=0`，同时暂停审核操作。后续旧实验保持暂停，
不要再对旧实验提炼或重跑；新工作全部使用新实验 ID。保留整库备份。
默认 Host DEV 数据库可用下面的 SQLite 在线备份命令生成一致副本；该命令只读原库，
不会删除记录，也不会改写原库的日志文件。若部署使用自定义数据库路径，先修改 `source`。

```bash
backend/.venv/bin/python - <<'PY'
import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

source = Path.home() / "deer-flow/backend/.deer-flow/data/soc_agent_dev.db"
directory = Path.home() / "soc-rebalance-backups"
directory.mkdir(mode=0o700, parents=True, exist_ok=True)
destination = directory / ("soc_agent_dev-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".db")
if not source.is_file():
    raise SystemExit("DEV 数据库不存在，请核实路径")
with destination.open("xb"):
    pass
destination.chmod(0o600)
print("正在备份：", destination, flush=True)
with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src:
    with sqlite3.connect(destination) as dst:
        src.backup(dst, pages=4096)
        if dst.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise SystemExit("备份校验失败，请保留原库并处理问题")
with destination.open("rb") as stream:
    digest = hashlib.file_digest(stream, "sha256").hexdigest()
print("备份完成：", destination)
print("SHA256：", digest)
PY
```

服务保持运行，后续命令通过本机 Gateway 执行。不要为了切分样本重装数据库。

## 2. 补齐分组事实

```bash
soc rebalance-facts --experiment "$soc_parent_experiment" \
  --start --wait --concurrency 16 \
  --request-key "$soc_new_experiment-facts"
```

命令输出 `experiment_id`，这是独立的事实准备实验 ID，后续使用这个值。
已有兼容语义事实可复用；需要补齐的样本通过后台事实任务处理。
事实任务不生成安全判定，不自动积累经验，也不提交处置。尚未处理过的样本可能仍需调用
语义核对模型，因此该步骤不是瞬间改名单。

退出终端不会取消服务器已经受理的任务。保留输出中的事实实验和轮次 ID，可用
`soc list --experiment 事实实验ID`、`soc status --round 轮次ID --watch` 查看。
需要暂停或继续时使用原轮次的 `pause` / `resume`，失败项用 `retry-failed` 后再检查状态。
响应丢失后沿用相同 `--request-key`，回读原事实准备结果；配置更新后明确重新预处理时使用新 key。
重复准备相同来源不应被当作主动重跑整个事实实验的方式。

## 3. 固定新名单

将下面第一行替换为步骤 2 输出的事实实验 ID。默认留两个验证事件；如果希望每种行为
尽量多学习，可将 `--holdout 2` 改成 `--holdout 1`。

```bash
soc_facts_experiment="EXP_替换为事实实验ID"
soc rebalance-plan --experiment "$soc_parent_experiment" \
  --facts-experiment "$soc_facts_experiment" \
  --holdout 2 \
  --output "$soc_rebalance_output/plan.json"
```

这一步不调用模型。输出列出新第一批、第二批主验证、补充验证、迁移条数、事实缺失数、
可继承经验数量及 `blockers`。文件同时固定原实验和数据身份、每条事实的 Run ID、划分规则、
继承经验版本与完整成员名单。不要手工修改文件；应用时会再次核对数据库中的来源。

`blockers=[]` 才能应用。若存在缺失事实，优先处理事实任务；如果已确认部分原始证据无法补齐，
可以明确保留为补充验证，并用新文件名重新生成：

```bash
soc rebalance-plan --experiment "$soc_parent_experiment" \
  --facts-experiment "$soc_facts_experiment" \
  --holdout 2 --missing-facts supplementary \
  --output "$soc_rebalance_output/plan-with-supplementary.json"
```

该选项不会虚构核心行为或回到粗分组，缺失事实清单仍保留。其他来源冲突仍须解决。
若采用第二份名单，后续 `--plan` 必须使用第二份文件。

## 4. 建立新版实验并完成第一批

```bash
soc rebalance-apply --plan "$soc_rebalance_output/plan.json" \
  --experiment "$soc_new_experiment" --name "扩大第一批后的完整阶段验证"
```

响应记录实际承接的学习运行、继承经验和新成员数量。原实验与原记录保留，新实验有明确的
继承来源；不会重复制造独立样本或把旧第二批结果计作新第二批结果。
批量继承并不意味着所有旧结果都兼容：不满足当前事实、版本或独立学习条件的成员需要重新学习。

```bash
soc learn --experiment "$soc_new_experiment" --purpose memory \
  --normalization apply --limit all --concurrency 16 \
  --request-key "$soc_new_experiment-learning"
```

记下输出的第一批 `round_id`，使用 `soc status --round 轮次ID --watch` 等待完整第一批结束。
可复用的已导入学习结果会按保存来源承接，其余成员正常学习。旧第二批曾经复用经验得到的结论，
不能当作独立学习结论直接重复累加。

若响应丢失，用同一条 `learn` 命令及同一个 `--request-key` 重交；已有轮次的继续和重试
使用 `resume` / `retry-failed`，不要随意更换 key 创建重复轮次。

## 5. 审核新版第一批的新增经验

```bash
soc candidates --experiment "$soc_new_experiment" --stage pending
```

只审核新实验待审名单；已继承的有效审核权限不需要再点一次。新实验独立积累仍可能生成与
已有经验范围相同的候选，因此“新候选数量”不等于“新增经验种类”。重复候选按现有审核流程
处理，不能为了批量通过而自动替换已审核经验。可打开：

```text
http://127.0.0.1:2026/workspace/soc/review/memory-candidates?experiment=新实验ID
```

如果运营决定将这份新名单全部采用“误报 / 精确复用”，使用本次代码配套的审核脚本，
明确指定新实验和实际待审数量。不要沿用旧 117 条的审核计划：

```bash
python3.12 scripts/soc_bulk_review_learning.py plan \
  --experiment "$soc_new_experiment" \
  --expected-count 替换为新版实际待审数量 \
  --output-dir "$soc_rebalance_output/review"

python3.12 scripts/soc_bulk_review_learning.py apply \
  --plan "$soc_rebalance_output/review/plan.json" \
  --concurrency 8 --confirm APPROVE-FALSE-POSITIVE
```

本操作记录批量运营判断，不声称逐条人工核查。仍不允许精确复用的候选保留失败原因，
不能通过降低核心行为要求绕过。没有新增待审候选时跳过这一步。

## 6. 固定经验后完整验证第二批

审核结束、确认经验状态后启动新版第二批，之后不要同时修改这批经验：

```bash
soc validate --experiment "$soc_new_experiment" --scope all --purpose memory \
  --normalization apply --limit all --concurrency 16 \
  --request-key "$soc_new_experiment-validation"
```

记下新第二批轮次 ID，等待全部成员完成。`--scope all --limit all` 包含主验证和补充场景；
只完成一部分或只跑经验命中场景，都不作为本阶段的完整结论。
确认 `active_count=0`、失败为零、完成数等于完整选择数后导出固定记录：

```bash
soc status --round ROUND_替换为新第二批轮次ID --watch
soc export --round ROUND_替换为新第二批轮次ID \
  --output-dir "$soc_rebalance_output/validation-report"
```

最终阶段结论说明新旧名单变化、第一批与第二批实际样本数、继承及新增经验数量，再报告：

- 第二批实际适用经验的告警数量、占比，以及其中建议忽略的数量。
- 直接复用与只作为参考的使用情况。
- 无历史经验告警数量及建议转交情况。
- 整轮完成情况、耗时及剩余证据缺口。

划入第一批的样本从新第二批分母中排除；旧第二批已完成记录只保留为历史。
不把原始处置标签作为本轮效果的对错标准，不把这次学习优先样本比例的提升说成模型准确率提升。
当新版名单与旧版不同，两轮覆盖率不是同一测试集的配对对比；阶段结论以新版完整第二批为范围。
