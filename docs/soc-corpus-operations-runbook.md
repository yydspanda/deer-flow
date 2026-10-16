## 8. 第一批：运行、补跑失败，再进入审核

本手册后半段按以下顺序操作：**第一批运行并补跑 → 审核第一批经验 → 第二批首次验证并补跑 → 导出首次报告 → 审核增量经验 → 第二批重新验证 → 导出报告和精简统计。**

页面名单保持第一批 **9,364条**、第二批 **5,917条**。两批接续同一学习代次的实际行为积累；本流程不会把页面第一批改成15,281条。
全量参与积累后，再用固定第二批观察经验复用效果。学习与验证样本存在重叠，结果用于说明已见场景的复用效果；全量参与积累不等于所有行为都已形成可用经验。

### 8.1 在页面启动第一批

在部署Mac打开：

```text
http://localhost:2026/workspace/soc/corpus-validation
```

1. 选择第一批，核对9,364条。
2. 设置：**语义核对开启；企业策略、安全软件路径策略、LLM策略建议关闭**。最大并发可设1～16，建议先用16。
3. 尚未开始时点“开始积累”。已排队任务按原设置续跑用“继续”。
4. 如果原来忘开语义核对，先暂停两批、等待运行中均为0，再对第一批点“重新配置并全部重跑”，确认上述设置后提交。
5. 运行结束后继续第8.2～8.4节，处理失败；**不要直接跳到第9节审核**。

“继续”沿用该轮保存的开关。第一批“重新配置并全部重跑”会建立新学习代次；已有第一批完成且设置正确时不要重复执行，第二批后续复测也不需要重新跑第一批。

### 8.2 查询第一批轮次和保存设置

以下命令只查询，不启动任务。输出简短轮次信息，不打印全部经验记录：

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python - <<'PY'
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

    current = get("/quick-validation", batch="learning", scope="all", limit=1)
    print("当前实验：", current["experiment_id"], "第一批总数：", current["total"])
    offset = 0
    while True:
        rows = get(f"/experiments/{current['experiment_id']}/rounds", batch="learning", limit=100, offset=offset)
        for row in rows:
            p = get(f"/rounds/{row['round_id']}")
            r = p["round"]
            print(r["round_id"], "状态=" + r["state"], "范围=" + str(p["selected_count"]),
                  "完成=" + str(p["completed_count"]), "失败=" + str(p["failed_count"]),
                  "运行中=" + str(p.get("active_count", 0)), "任务分布=" + str(p["counts"]))
            print("  保存设置：", r.get("options", {}), "积累代次：", r.get("learning_generation"),
                  "已被替代：", r.get("superseded_by_round_id"))
        if len(rows) < 100:
            break
        offset += len(rows)
PY
```

找到**当前未被替代、覆盖9,364条**的第一批轮次，复制它的 `ROUND-...`。后面的命令每次都会提示填写ID，不依赖上一个终端的变量。不要填单条研判的 `RUN-...`。

### 8.3 第一批执行失败、语义核对失败：依次在同轮次补跑

先在页面暂停其他正在运行的轮次，等待活动任务结束；补跑完成前不要审核经验。
下面一块依次暂停目标轮次、等待收尾、补跑执行失败、补跑语义核对失败。输入第8.2节查到的第一批轮次：

```bash
bash <<'BASH'
set -euo pipefail
cd "$HOME/deer-flow"
read -r -p '第一批完整轮次 ROUND-ID：' soc_retry_round </dev/tty
read -r -p '补跑并发（1～16，回车用16）：' soc_retry_concurrency </dev/tty
soc_retry_concurrency="${soc_retry_concurrency:-16}"
[[ "$soc_retry_round" == ROUND-* ]]
[[ "$soc_retry_concurrency" =~ ^([1-9]|1[0-6])$ ]]

backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py pause --round "$soc_retry_round"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --round "$soc_retry_round" --watch

backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py retry \
  --round "$soc_retry_round" --kind failed --concurrency "$soc_retry_concurrency"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --round "$soc_retry_round" --watch

backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py retry \
  --round "$soc_retry_round" --kind semantic_failed --concurrency "$soc_retry_concurrency"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --round "$soc_retry_round" --watch
BASH
```

- `retried > 0`：已自动启动该类补跑，不再点页面“继续”。没有这类失败时 `retried=0`，继续下一步。
- 执行失败包含模型研判失败。语义核对失败可能已兜底为任务完成，所以即使 `failed_count=0`，仍需执行语义补跑命令。
- 语义补跑要求原轮已开启 `apply`；只处理明确 `failed` 的核对结果，`partial`、`skipped`不算失败。会重新核对并执行后续研判。
- 补跑保留同一个ROUND-ID和旧Run记录。单个任务累计最多3次尝试；`not_retryable`不为0时检查原因，不会无限重试。
- 报 `snapshot changed`、`configuration_changed`、轮次阻断或已被替代时，停止本节；修复原因后用页面重新配置重跑，不能强行覆盖旧快照。

### 8.4 第一批还有排队任务时，恢复整轮剩余范围

补跑只调度选中的失败项。结束后如果 `counts` 中仍有 `queued`，执行下面一块；没有排队项则跳过：

```bash
bash <<'BASH'
set -euo pipefail
cd "$HOME/deer-flow"
read -r -p '第一批同一个 ROUND-ID：' soc_resume_round </dev/tty
read -r -p '运行并发（1～16，回车用16）：' soc_resume_concurrency </dev/tty
soc_resume_concurrency="${soc_resume_concurrency:-16}"
[[ "$soc_resume_round" == ROUND-* ]]
[[ "$soc_resume_concurrency" =~ ^([1-9]|1[0-6])$ ]]
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py pause --round "$soc_resume_round"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --round "$soc_resume_round" --watch
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py resume \
  --round "$soc_resume_round" --restore-full-scope --limit all --concurrency "$soc_resume_concurrency"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --round "$soc_resume_round" --watch
BASH
```

已完成项不重跑，失败项不会由 `resume` 自动重试。新增失败再执行第8.3节。
第一批 **9,364条完成、执行失败0、排队0、运行中0** 后进入第9节；语义失败已按第8.3节补跑，仍不能恢复的项保留失败事实，不当成核对成功。

## 9. 审核第一批经验，再启动第二批

本节使用默认的首次审核模式，只用于**第一批已全部完成、第二批尚未开始**的实验。第二批已经跑过，使用第13.1节的增量审核模式，不能复用本节的首次审核计划。
批量结论统一为“误报／精确复用”，核心行为沿用默认勾选，保留已保存附加条件；复核180天、有效期365天。仅用于运营已决定统一采用该结论的名单，不代表逐条人工核实。

### 9.1 校验工具

```bash
cd "$HOME/READY-TO-TRANSFER/LEARNING-REVIEW-TOOLS"
shasum -a 256 -c SHA256SUMS
```

全部 `OK` 后继续。待审数为0则跳过第9.2～9.3节。

### 9.2 保存本次待审名单

新计划使用新目录；之前生成过计划、现在只是续审时，不重新执行本节：

```bash
bash <<'BASH'
set -euo pipefail
umask 077
read -r -p '本轮页面待审核条数：' soc_pending_count </dev/tty
[[ "$soc_pending_count" =~ ^[1-9][0-9]*$ ]]
soc_review_dir="$HOME/soc-review-learning-$(date +%Y%m%d-%H%M%S)-$$"
python3.12 "$HOME/READY-TO-TRANSFER/LEARNING-REVIEW-TOOLS/soc_bulk_review_learning.py" plan \
  --expected-count "$soc_pending_count" --output-dir "$soc_review_dir"
printf '\n本次审核目录，请复制保存：%s\n' "$soc_review_dir"
BASH
```

看到 `Plan saved`，保存打印出的目录路径。数量不符或第二批已开始时脚本停止。

### 9.3 审核；失败后用原计划重试

首次执行，或普通中断后续审：

```bash
bash <<'BASH'
set -euo pipefail
read -r -p '第9.2节审核目录完整路径：' soc_review_dir </dev/tty
read -r -p '审核并发（1～8，回车用8）：' soc_review_concurrency </dev/tty
soc_review_concurrency="${soc_review_concurrency:-8}"
[[ "$soc_review_concurrency" =~ ^[1-8]$ ]]
test -f "$soc_review_dir/plan.json"
python3.12 "$HOME/READY-TO-TRANSFER/LEARNING-REVIEW-TOOLS/soc_bulk_review_learning.py" apply \
  --plan "$soc_review_dir/plan.json" --concurrency "$soc_review_concurrency" \
  --confirm APPROVE-FALSE-POSITIVE
BASH
```

显示 `draft_failed` 等失败、处理原因后，用**同一个目录**重试：

```bash
bash <<'BASH'
set -euo pipefail
read -r -p '原审核目录完整路径：' soc_review_dir </dev/tty
read -r -p '审核并发（1～8，回车用8）：' soc_review_concurrency </dev/tty
soc_review_concurrency="${soc_review_concurrency:-8}"
[[ "$soc_review_concurrency" =~ ^[1-8]$ ]]
test -f "$soc_review_dir/plan.json"
python3.12 "$HOME/READY-TO-TRANSFER/LEARNING-REVIEW-TOOLS/soc_bulk_review_learning.py" apply \
  --plan "$soc_review_dir/plan.json" --concurrency "$soc_review_concurrency" \
  --retry-failed --confirm APPROVE-FALSE-POSITIVE
BASH
```

看最终 `Review counts`；已完成项自动跳过。保留 `plan.json`、`state.json`。`blocked`不强行通过，按原因在页面选择不沉淀或其他合适处理；`confirmed_unverified`先去页面核对。当前待审核项处理完再启动第二批。
当前 `--demo-no-auth`无需额外认证；普通认证模式需配置 `SOC_REVIEW_COOKIE` 和 `SOC_REVIEW_CSRF_TOKEN`。

## 10. 第二批首次验证：使用第一批经验，并继续积累

在部署Mac打开：

```text
http://localhost:2026/workspace/soc/corpus-validation
```

1. 选择第二批，范围选**全部**，核对5,917条。
2. 设置：**语义核对开启；企业策略、安全软件路径策略、LLM策略建议关闭**。不勾选“重新核对事实”，可复用此前有效语义结果；最大并发单独保存为16或所需值。
3. 第二批尚未开始时点“开始验证”。已有同设置任务只需接着跑时点“继续”。已有旧设置结果且需要重新测时，先暂停等待运行中0，再点“重新配置并全部重跑”，确认上述设置并提交。
4. 本轮使用启动时冻结的已审核经验。**本轮期间不审核新经验、不修改已有经验。**
5. 整批结束后到第11节补跑失败并导出首次报告。新版正常轮次直接进入第13节审核增量经验；仅旧结果补记或接续失败恢复时查看第12节。

第二批新版正常轮次会自动接续第一批的行为积累；**不需要先执行 `accumulate` 才能运行第二批**。
第二批新增候选暂不影响正在运行的经验快照。粗分组仍是2,153条“预估有适用历史经验”和3,764条“预估无历史经验”，实际使用经验以Runtime结果为准。

## 11. 第二批：同轮补跑、并发和首次报告

本节同时适用于第10节首次验证和第13节复测。每次填**当前这次第二批的完整ROUND-ID**；不要把首次轮次、复测轮次或单条RUN-ID混用。

### 11.1 查询第二批轮次，核对范围和经验快照

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python - <<'PY'
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
            r = p["round"]
            print(r["round_id"], "状态=" + r["state"], "范围=" + str(p["selected_count"]),
                  "完成=" + str(p["completed_count"]), "失败=" + str(p["failed_count"]),
                  "运行中=" + str(p.get("active_count", 0)), "任务分布=" + str(p["counts"]))
            print("  保存设置：", r.get("options", {}), "经验模式：", r.get("memory_mode"),
                  "冻结经验数：", len(r.get("memory_snapshot", [])),
                  "已被替代：", r.get("superseded_by_round_id"))
        if len(rows) < 100:
            break
        offset += len(rows)
PY
```

复制当前范围5,917、未被替代且设置正确的ROUND-ID。旧轮次保留历史，旧失败不会因为新轮次成功而被删除；判断本次结果看本次轮次。

### 11.2 顺序补跑执行失败和语义核对失败

其他正在运行的轮次先在页面暂停、等待活动任务结束。以下命令会暂停目标轮次并等待收尾，再依次自动启动两类补跑：

```bash
bash <<'BASH'
set -euo pipefail
cd "$HOME/deer-flow"
read -r -p '当前第二批完整 ROUND-ID：' soc_retry_round </dev/tty
read -r -p '补跑并发（1～16，回车用16）：' soc_retry_concurrency </dev/tty
soc_retry_concurrency="${soc_retry_concurrency:-16}"
[[ "$soc_retry_round" == ROUND-* ]]
[[ "$soc_retry_concurrency" =~ ^([1-9]|1[0-6])$ ]]
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py pause --round "$soc_retry_round"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --round "$soc_retry_round" --watch
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py retry \
  --round "$soc_retry_round" --kind failed --concurrency "$soc_retry_concurrency"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --round "$soc_retry_round" --watch
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py retry \
  --round "$soc_retry_round" --kind semantic_failed --concurrency "$soc_retry_concurrency"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --round "$soc_retry_round" --watch
BASH
```

两类补跑沿用该轮开关和冻结经验，ROUND-ID不变，报告无需合并。只重试个别告警时，在对应 `retry` 命令末尾加 `--alert 告警ID`，多条重复添加。
语义失败可藏在“任务完成”中，所以即使执行失败0也运行语义重试。无匹配项会返回 `retried=0`；`partial/skipped`不会被当作失败重试。原轮必须开启语义核对。
单个任务累计最多3次尝试；`not_retryable`、快照变化、轮次阻断或被替代的处理与第8.3节相同。

### 11.3 随时查看状态；中途暂停、继续或调整并发

另开终端也可执行，填写正在关注的ROUND-ID：

```bash
bash <<'BASH'
set -euo pipefail
cd "$HOME/deer-flow"
read -r -p '要查看的 ROUND-ID：' soc_status_round </dev/tty
[[ "$soc_status_round" == ROUND-* ]]
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --round "$soc_status_round" --watch
BASH
```

看 `state`、`active_count`、`completed_count`、`failed_count`、`counts`、`concurrency`。去掉 `--watch`只查一次；没有变化时不重复输出。**Ctrl+C只退出查看，不停止后台任务。**

只想暂停：

```bash
bash <<'BASH'
set -euo pipefail
cd "$HOME/deer-flow"
read -r -p '要暂停的 ROUND-ID：' soc_pause_round </dev/tty
[[ "$soc_pause_round" == ROUND-* ]]
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py pause --round "$soc_pause_round"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --round "$soc_pause_round" --watch
BASH
```

继续当前调度范围、或调整并发后继续，用下面一块。先暂停等待运行中0，再以新并发恢复；不会改变业务开关：

```bash
bash <<'BASH'
set -euo pipefail
cd "$HOME/deer-flow"
read -r -p '要继续的同一个 ROUND-ID：' soc_resume_round </dev/tty
read -r -p '运行并发（1～16，回车用16）：' soc_resume_concurrency </dev/tty
soc_resume_concurrency="${soc_resume_concurrency:-16}"
[[ "$soc_resume_round" == ROUND-* ]]
[[ "$soc_resume_concurrency" =~ ^([1-9]|1[0-6])$ ]]
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py pause --round "$soc_resume_round"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --round "$soc_resume_round" --watch
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py resume \
  --round "$soc_resume_round" --concurrency "$soc_resume_concurrency"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --round "$soc_resume_round" --watch
BASH
```

只改终端变量不会改变后台并发；实际同时运行数可能低于所设上限。网络超时未收到提交结果时，先查状态，不立即重复提交。

### 11.4 补跑结束后仍有queued：恢复整轮剩余队列

`retry`只调度失败名单，普通 `resume`继续该名单。如果还有原来的排队任务，用以下完整范围恢复命令：

```bash
bash <<'BASH'
set -euo pipefail
cd "$HOME/deer-flow"
read -r -p '当前第二批同一个 ROUND-ID：' soc_resume_round </dev/tty
read -r -p '运行并发（1～16，回车用16）：' soc_resume_concurrency </dev/tty
soc_resume_concurrency="${soc_resume_concurrency:-16}"
[[ "$soc_resume_round" == ROUND-* ]]
[[ "$soc_resume_concurrency" =~ ^([1-9]|1[0-6])$ ]]
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py pause --round "$soc_resume_round"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --round "$soc_resume_round" --watch
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py resume \
  --round "$soc_resume_round" --restore-full-scope --limit all --concurrency "$soc_resume_concurrency"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --round "$soc_resume_round" --watch
BASH
```

已完成项不重跑；新增失败再执行第11.2节。完整验证应达到5,917条完成、执行失败0、排队0、运行中0。

### 11.5 增量审核前，先保存第二批首次报告和统计

**必须在第13节增量审核之前导出。** 审核会改变经验快照；先固定首次结果，再开始新增经验审核。
以下命令提示输入首次第二批的ROUND-ID，创建新目录，导出报告并复制精简统计到剪贴板：

```bash
bash <<'BASH'
set -euo pipefail
umask 077
cd "$HOME/deer-flow"
read -r -p '首次第二批完整 ROUND-ID：' soc_report_round </dev/tty
[[ "$soc_report_round" == ROUND-* ]]
soc_report_dir="$HOME/soc-stage-before-incremental-$(date +%Y%m%d-%H%M%S)-$$"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --round "$soc_report_round" --watch
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py export \
  --round "$soc_report_round" --output-dir "$soc_report_dir"
python3.12 "$HOME/READY-TO-TRANSFER/soc_stage_report_summary.py" \
  "$soc_report_dir" --output "$soc_report_dir/summary.json"
pbcopy < "$soc_report_dir/summary.json"
printf '\n首次报告：%s\n精简统计已复制到剪贴板，可发外网总结。\n' "$soc_report_dir"
BASH
```

服务保持运行。导出要求目标轮次非运行中且 `active_count=0`；暂停后仍有正在收尾的任务，继续等待，不是立刻导出。
报告 `REPORT.md`及`summary.json`都留在打印的目录内。完整阶段汇报核对 `stage_conclusion_ready=true`；如果按已完成部分统计，必须明确排除失败、排队和快照变化项，不描述为全部完成。

## 12. 已有结果接续积累：仅旧结果或补记需要时执行

**本节的 `accumulate` 不会启动第二批、不重新调用模型。** 它只把已保存的成功Run接续到当前学习代次的行为积累。
第8、10节创建的新版正常轮次已自动积累，通常直接进入第13节；只有旧轮次尚未接续、或者修复了接续报错需要重试时，才执行本节。

### 12.1 选来源，暂停所有任务

第一批来源用第8.2节查询，第二批来源用第11.1节查询。选当前学习代次、保存最新成功结果的轮次。
页面暂停第一批、第二批及单条任务，等待所有运行中为0；接续期间不要开始新任务或审核经验。命令暂停方式见第11.3节。

### 12.2 先预检，再接续同一个来源

```bash
bash <<'BASH'
set -euo pipefail
cd "$HOME/deer-flow"
read -r -p '要接续已有结果的 ROUND-ID：' soc_accumulate_round </dev/tty
[[ "$soc_accumulate_round" == ROUND-* ]]
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py accumulate \
  --round "$soc_accumulate_round" --dry-run
read -r -p '预检无错误且来源正确，输入 APPLY 执行：' soc_accumulate_confirm </dev/tty
[[ "$soc_accumulate_confirm" == APPLY ]]
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py accumulate \
  --round "$soc_accumulate_round"
BASH
```

不修改旧报告、运行结论或已审核经验。以前单条重跑落在独立轮次的，对保存最新结果的轮次分别执行；旧结果已被替代时会跳过。

### 12.3 看输出，结束后进入增量审核

| 输出 | 含义或下一步 |
| --- | --- |
| `eligible` | 来源预检通过，不保证形成候选 |
| `processed` | 累计检查的成功来源数，包含跳过项 |
| `counts`、`skipped_reasons` | 当前页统计，每页最多100条 |
| `model_calls=0` | 正常，接续没有调用模型 |
| `newer_batch_result_exists` | 旧结果已被替代，改用保存最新结果的轮次 |
| `semantic_review_failed_learning_deferred` | 语义核对失败暂缓积累，按第8.3或11.2节补跑 |
| `pattern_facet_value_out_of_bounds:...` | 匹配特征为空或超过512字符，整条暂不积累；原研判保留，其余继续 |
| 错误退出 | 处理原因后重新执行，已成功接续的来源不会重复计数 |

`accumulated`是当前页已接续项数，不是新增经验条数。`--dry-run`检查来源身份，实际准入以执行结果为准。
命令正常退出即本轮接续结束；有新增候选再到第13节审核。

两批共享行为积累，4条独立有效样本加1条符合条件的独立样本可以达到5条门槛，仍须通过质量检查。直接复用或参考已审核经验的结果可以贡献行为发生记录，但不作为新的独立结论支持；不能依靠反复复用同一经验来证明该经验正确。
因此本流程是“全量参与接续积累后的复测”。若另需所有样本都无经验独立研判，应单独配置无经验学习轮，不能通过 `accumulate`实现。

## 13. 审核第二批新增经验，再重新验证固定第二批

### 13.1 审核增量经验

先确认第一批已完成、第11.5节首次报告已导出。暂停两批及单条任务，等待所有运行中为0；审核期间不启动任务、不同时在页面审核同一名单。
本节批量指定“误报／精确复用”，核心行为沿用默认勾选、已有附加条件保持，复核180天、有效期365天。仅用于运营已决定统一采用该结论的名单，不代表脚本逐条核实。

**第一步：校验工具，保存增量待审名单。** 输入第11.5节打印的首次报告目录，以及当前实验页面待审核数；待审为0则跳到第13.2节。

```bash
bash <<'BASH'
set -euo pipefail
umask 077
cd "$HOME/READY-TO-TRANSFER/LEARNING-REVIEW-TOOLS"
shasum -a 256 -c SHA256SUMS
read -r -p '第11.5节首次报告目录完整路径：' soc_report_dir </dev/tty
read -r -p '当前实验本次待审核条数：' soc_pending_count </dev/tty
test -f "$soc_report_dir/report.json"
test -f "$soc_report_dir/manifest.json"
[[ "$soc_pending_count" =~ ^[1-9][0-9]*$ ]]
soc_review_dir="$HOME/soc-review-incremental-$(date +%Y%m%d-%H%M%S)-$$"
python3.12 "$HOME/READY-TO-TRANSFER/LEARNING-REVIEW-TOOLS/soc_bulk_review_learning.py" plan \
  --phase incremental --report-dir "$soc_report_dir" \
  --expected-count "$soc_pending_count" --output-dir "$soc_review_dir"
printf '\n增量审核目录，请复制保存：%s\n' "$soc_review_dir"
BASH
```

工具从固定报告读取实验和轮次，核对报告校验值、第一批完成、来源第二批非运行且未被替代、保存结果仍与报告一致，再冻结待审名单。不会自动选择最新轮次或修改旧报告；不满足条件时按报错处理，不删除旧验证记录绕过检查。
本次必须使用新目录，不能拿第9节旧计划冒充增量计划。看到 `Plan saved` 后保存打印出的路径。

**第二步：执行审核；普通中断后仍用这一块续审。**

```bash
bash <<'BASH'
set -euo pipefail
read -r -p '本次增量审核目录完整路径：' soc_review_dir </dev/tty
read -r -p '审核并发（1～8，回车用8）：' soc_review_concurrency </dev/tty
soc_review_concurrency="${soc_review_concurrency:-8}"
[[ "$soc_review_concurrency" =~ ^[1-8]$ ]]
test -f "$soc_review_dir/plan.json"
python3.12 "$HOME/READY-TO-TRANSFER/LEARNING-REVIEW-TOOLS/soc_bulk_review_learning.py" apply \
  --plan "$soc_review_dir/plan.json" --concurrency "$soc_review_concurrency" \
  --confirm APPROVE-FALSE-POSITIVE
BASH
```

**第三步：有 `draft_failed` 等失败时，处理原因后显式重试。** 已完成项自动跳过，`blocked`保持阻断，不强行通过。

```bash
bash <<'BASH'
set -euo pipefail
read -r -p '同一个增量审核目录完整路径：' soc_review_dir </dev/tty
read -r -p '审核并发（1～8，回车用8）：' soc_review_concurrency </dev/tty
soc_review_concurrency="${soc_review_concurrency:-8}"
[[ "$soc_review_concurrency" =~ ^[1-8]$ ]]
test -f "$soc_review_dir/plan.json"
python3.12 "$HOME/READY-TO-TRANSFER/LEARNING-REVIEW-TOOLS/soc_bulk_review_learning.py" apply \
  --plan "$soc_review_dir/plan.json" --concurrency "$soc_review_concurrency" \
  --retry-failed --confirm APPROVE-FALSE-POSITIVE
BASH
```

**随时查看状态：** 可另开终端执行，仅读取本地计划和进度，不调用模型、不提交审核。

```bash
bash <<'BASH'
set -euo pipefail
read -r -p '本次增量审核目录完整路径：' soc_review_dir </dev/tty
test -f "$soc_review_dir/plan.json"
python3.12 "$HOME/READY-TO-TRANSFER/LEARNING-REVIEW-TOOLS/soc_bulk_review_learning.py" status \
  --plan "$soc_review_dir/plan.json"
BASH
```

保留整个审核目录，包括 `plan.json`、`state.json` 和回执。`confirmed_unverified`先在页面核对，不能当作已验证成功。新出现的候选不会加入已冻结计划；需要另审时重新核对范围、建立新计划。

**不采用统一结论，或需要单独处理阻断项时，在页面审核：**

```text
http://localhost:2026/workspace/soc/review/memory-candidates
```

需要直接限定当前实验时，第11.1节查询到EXP-ID后，替换地址中的占位文字：

```text
http://localhost:2026/workspace/soc/review/memory-candidates?experiment=替换为当前EXP-ID&return_batch=validation
```

核对当前实验，只处理本次待审核经验；已有审核不重做。按实际业务选择结论、复用方式及条件。缺少核心行为或证据不足的候选不强行精确复用，可按页面流程不沉淀。
处理完毕进入第13.2节，**新建第二批复测轮次**，不要继续旧经验快照下的轮次。

### 13.2 用新经验快照重新跑第二批

审核完成后，在页面操作：

```text
http://localhost:2026/workspace/soc/corpus-validation
```

1. 选择第二批，核对仍为5,917条，两类粗分组不变。
2. 点击**重新配置并全部重跑**，不要点击旧轮次“继续”。
3. 确认：**语义核对开启；企业策略、安全软件路径策略、LLM策略建议关闭**，与首次验证一致；不勾选“重新核对事实”。最大并发按需设1～16。
4. 点击**全部重新运行**。本次创建新ROUND-ID，冻结包含新增已审核经验的快照；第一批和首次第二批报告保留。
5. 用第11.1节查询，复制**这次新建的复测ROUND-ID**，核对范围5,917和冻结经验数。
6. 复测结束后执行第11.2节，输入**新复测ROUND-ID**补跑执行失败和语义失败；还有排队项执行第11.4节，同样填写新ID。
7. 完成后到第14节导出最终复测结果。此时不要再次审核新候选，先固定报告。

同轮失败重试不换ROUND-ID；新增经验后的完整复测必须新建ROUND-ID，因为经验快照变了。本次结果衡量已见场景下经验积累、审核后的复用效果，不作为未见告警效果。

## 14. 导出复测报告，把精简统计发到外网总结

### 14.1 导出指定复测轮次，并生成summary.json

先按第11.1节确认新复测ROUND-ID。下面每次新建报告目录，**不会自动选择页面最新轮次，不会覆盖首次报告**：

```bash
bash <<'BASH'
set -euo pipefail
umask 077
cd "$HOME/deer-flow"
read -r -p '新增经验后复测的完整 ROUND-ID：' soc_report_round </dev/tty
[[ "$soc_report_round" == ROUND-* ]]
soc_report_dir="$HOME/soc-stage-after-incremental-$(date +%Y%m%d-%H%M%S)-$$"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --round "$soc_report_round" --watch
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py export \
  --round "$soc_report_round" --output-dir "$soc_report_dir"
python3.12 "$HOME/READY-TO-TRANSFER/soc_stage_report_summary.py" \
  "$soc_report_dir" --output "$soc_report_dir/summary.json"
pbcopy < "$soc_report_dir/summary.json"
printf '\n复测报告：%s\n精简统计：%s/summary.json\n已复制到剪贴板。\n' "$soc_report_dir" "$soc_report_dir"
BASH
```

`status --watch`只观察运行，不会替你恢复暂停的排队任务。完整结果应为5,917完成、失败0、排队0、运行中0；按完成子集汇报时必须保留排除数量。导出报“先pause并等待任务完成”时，按第11.3节暂停并等收尾后重试。

### 14.2 已有报告时，只生成精简统计

不需要重新导出大报告。填写已有且包含 `report.json` 的目录，生成新的精简文件并复制：

```bash
bash <<'BASH'
set -euo pipefail
umask 077
read -r -p '已有报告目录完整路径：' soc_report_dir </dev/tty
test -f "$soc_report_dir/report.json"
soc_summary_file="$soc_report_dir/summary-$(date +%Y%m%d-%H%M%S)-$$.json"
python3.12 "$HOME/READY-TO-TRANSFER/soc_stage_report_summary.py" \
  "$soc_report_dir" --output "$soc_summary_file"
pbcopy < "$soc_summary_file"
printf '\n精简统计：%s\n已复制到剪贴板。\n' "$soc_summary_file"
BASH
```

这里调用的是交付目录根部独立脚本；源码中维护的同一工具为 `scripts/soc_stage_report_summary.py`。它只读指定固定报告，不查询数据库、不调用模型、不影响正在运行的新轮次。

### 14.3 发哪些内容，如何核对

向外网粘贴第11.5节**首次报告的summary.json**和第14.1节**复测报告的summary.json**，标明“增量审核前／增量审核后”即可。无需传输完整 `report.json`、数据库或原始告警。

核对精简统计中的：

- `source_rounds`：指定ROUND-ID、实际保存开关、冻结经验数。
- `scope`：计划数、完成/失败/排队、`stage_conclusion_ready`及问题列表。第一批数量是计划数，不单凭此字段声称已全部完成审核。
- `metrics.main`、`metrics.supplementary`：保留两类粗分组的忽略/转交、实际经验使用、直接复用和平均耗时。
- `final_step_counts`：最终语义核对阶段状态；`unknown`不等于失败，`partial/skipped`也不算失败。

百分比分母采用完成且快照稳定的样本；平均耗时采用最终Run，不把被替换尝试、排队耗时混入。忽略和转交均为系统建议，不与原始处置标签比较。首次与复测样本固定，但经验已包含第二批贡献，汇报注明学习与验证重叠，不能将结果解释为新告警的泛化效果。
