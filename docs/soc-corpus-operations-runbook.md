## 8. 第一批：运行、补跑失败，再进入审核

顺序：**第一批运行和补跑 → 第9节审核 → 第10～11节第二批运行、补跑、保存首次报告 → 第13节审核新增经验并重新验证 → 第14节导出总结。** 新版本正常运行不用执行第12节。

以下命令需要本次新版服务和脚本。第一批用 `--batch learning`，第二批用 `--batch validation`；不需要查找或填写 ROUND-ID。后台保留历史来源，成功补跑自动计入当前批次。

页面名单仍是第一批9,364条、第二批5,917条。两批共享行为积累；同一告警反复研判不会重复增加独立样本数。全量积累后复测，衡量已见场景的经验复用效果。

### 8.1 在页面启动第一批

```text
http://localhost:2026/workspace/soc/corpus-validation
```

1. 选择第一批，核对范围。
2. 默认语义核对开启；企业策略、安全软件路径策略、LLM策略建议关闭；“重新核对事实”默认不勾选。
3. 最大并发填1～16，点“保存并发”；通常用16。
4. 首次点“开始积累”；接着未完成任务点“继续”。需要改变已排队任务设置时，点“重新配置并全部重跑”。
5. 结束后做8.2、8.3，再到第9节审核。

第一批全部重跑会重建当前学习代次。仅复测第二批时，不需要重跑第一批。

### 8.2 看第一批状态

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --batch learning --watch
```

`completed_count`已完成，`failed_count`执行失败，`semantic_failed_count`语义核对失败，`active_count`运行中，`remaining_count`未完成队列。`Ctrl+C`只退出查看。

### 8.3 补跑第一批执行失败和语义核对失败

页面也有“重试执行失败”“重试语义核对失败”。要顺序处理两类失败，复制下面整块；并发不要超过页面已保存的最大并发。

```bash
bash <<'BASH'
set -euo pipefail
cd "$HOME/deer-flow"
read -r -p '补跑并发（1～16，回车用16）：' soc_retry_concurrency </dev/tty
soc_retry_concurrency="${soc_retry_concurrency:-16}"
[[ "$soc_retry_concurrency" =~ ^([1-9]|1[0-6])$ ]]
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py pause --batch learning
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --batch learning --watch
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py retry --batch learning --kind failed --concurrency "$soc_retry_concurrency"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --batch learning --watch
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py retry --batch learning --kind semantic_failed --concurrency "$soc_retry_concurrency"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --batch learning --watch
BASH
```

`retried=0`表示没有可补跑的该类失败。`not_retryable`不为0需检查原因，不会无限重试。语义补跑只处理明确失败，`partial`和`skipped`不算失败；它会重新核对事实并执行后续研判。已成功的其他告警不重跑。

### 8.4 还有未完成队列：继续第一批

`remaining_count>0`且没有运行任务时执行；为0则到第9节。

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py resume --batch learning --concurrency 16
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --batch learning --watch
```

把16改成需要的并发（1～16）。此命令同时保存最大并发并继续当前批次；暂停仅停止领取新任务，已开始的任务需要收尾。

## 9. 审核第一批经验，再启动第二批

### 9.1 生成当前待审名单

脚本自动读取当前实验、当前学习代次的待审核经验，不需要报告或任务编号。新候选必须有可复用核心行为；缺少核心行为的只保留积累记录，不进入待审核队列。

```bash
bash <<'BASH'
set -euo pipefail
umask 077
cd "$HOME/deer-flow"
soc_review_dir="$HOME/soc-review-learning-$(date +%Y%m%d-%H%M%S)-$$"
backend/.venv/bin/python scripts/soc_bulk_review_learning.py plan \
  --phase pending --output-dir "$soc_review_dir"
printf '\n审核目录，请保存：%s\n' "$soc_review_dir"
BASH
```

### 9.2 批量审核；失败后用原计划重试

以下操作按你已决定的“误报／精确复用”提交审核，核心行为默认全选、附加条件保留，复核180天、有效期365天；脚本本身不替代人工确认业务结论。仅审核计划中冻结的名单，后续新候选不会被顺带批准。

```bash
bash <<'BASH'
set -euo pipefail
cd "$HOME/deer-flow"
read -r -p '本次审核目录完整路径：' soc_review_dir </dev/tty
read -r -p '审核并发（1～8，回车用8）：' soc_review_concurrency </dev/tty
soc_review_concurrency="${soc_review_concurrency:-8}"
[[ "$soc_review_concurrency" =~ ^[1-8]$ ]]
test -f "$soc_review_dir/plan.json"
backend/.venv/bin/python scripts/soc_bulk_review_learning.py apply \
  --plan "$soc_review_dir/plan.json" --concurrency "$soc_review_concurrency" \
  --confirm APPROVE-FALSE-POSITIVE
BASH
```

普通续审再次执行上面一块，填写原目录。若出现 `draft_failed`、起草中断或超时，需要明确重试失败项：

```bash
bash <<'BASH'
set -euo pipefail
cd "$HOME/deer-flow"
read -r -p '原审核目录完整路径：' soc_review_dir </dev/tty
backend/.venv/bin/python scripts/soc_bulk_review_learning.py apply \
  --plan "$soc_review_dir/plan.json" --concurrency 8 --retry-failed \
  --confirm APPROVE-FALSE-POSITIVE
BASH
```

并发8可改成1～8。已完成的自动跳过；已有旧计划中的 `blocked` 不强行审核。

### 9.3 查看审核进度

```bash
bash <<'BASH'
set -euo pipefail
cd "$HOME/deer-flow"
read -r -p '本次审核目录完整路径：' soc_review_dir </dev/tty
backend/.venv/bin/python scripts/soc_bulk_review_learning.py status --plan "$soc_review_dir/plan.json"
BASH
```

待审为0则跳过审核。页面审核也可用；不要同时在页面与脚本审核同一条候选。

## 10. 第二批首次验证：使用第一批经验，并继续积累

1. 页面选第二批，核对5,917条，范围“全部”。
2. 默认语义核对开启，其他三个开关关闭；保存并发16。
3. 首次点运行。已有旧第二批结果、现在要完整重新验证时，点“重新配置并全部重跑”。
4. 第二批自动接续第一批行为积累：第一批某行为已有4个独立样本，第二批再来1个符合条件的样本，可达到5个支持样本的候选门槛。还需通过核心行为与质量检查。
5. 跑完执行第11节。无需提前获取新任务编号。

直接复用经验形成的判断不作为独立学习证据；语义核对失败的样本暂缓积累。新增候选仍需审核才可使用。

## 11. 第二批：补跑、并发和首次报告

### 11.1 看当前第二批状态

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --batch validation --watch
```

按当前批次统计，包含成功补跑。不会因为最后处理的是1条或33条，就把整批范围显示成1条或33条。

### 11.2 顺序补跑执行失败和语义核对失败

```bash
bash <<'BASH'
set -euo pipefail
cd "$HOME/deer-flow"
read -r -p '补跑并发（1～16，回车用16）：' soc_retry_concurrency </dev/tty
soc_retry_concurrency="${soc_retry_concurrency:-16}"
[[ "$soc_retry_concurrency" =~ ^([1-9]|1[0-6])$ ]]
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py pause --batch validation
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --batch validation --watch
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py retry --batch validation --kind failed --concurrency "$soc_retry_concurrency"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --batch validation --watch
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py retry --batch validation --kind semantic_failed --concurrency "$soc_retry_concurrency"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --batch validation --watch
BASH
```

`retried=0`表示没有可补跑的该类失败。`not_retryable`不为0需检查原因，不会无限重试。语义补跑只处理明确失败，`partial`和`skipped`不算失败；它会重新核对事实并执行后续研判。已成功的其他告警不重跑。

### 11.3 暂停、继续、手工设置并发

暂停并等待收尾：

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py pause --batch validation
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --batch validation --watch
```

继续并设置并发：

```bash
cd "$HOME/deer-flow"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py resume --batch validation --concurrency 16
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --batch validation --watch
```

把16改成需要的并发（1～16）。此命令同时保存最大并发并继续当前批次；暂停仅停止领取新任务，已开始的任务需要收尾。

### 11.4 还有排队项或调整过经验

`remaining_count>0`时，执行11.3的继续命令或点页面“继续”。废止、恢复已审核经验导致的变化由系统自动衔接剩余任务；已完成告警保留当时依据，运行中受影响的告警重新处理。旧版本留下的此类停止也可通过“继续”接续。

连续多次改变经验后，自动接续达到上限会等待你点“继续”。若 `memory_recovery_exhausted_count>0`，这些告警已用完累计重试次数，需在告警处显式重新运行；未开始的其他告警仍可继续。

修改模型配置、语料来源等属于另一类变化，仍需页面“重新配置并全部重跑”，系统不会悄悄替换你的运行设置。

### 11.5 保存第二批首次报告和精简统计

增量审核前保存一次，留作扩大经验库之前的结果。导出自动组合当前第二批所有有效结果，无需手工合并补跑报告；导出期间结果变化会提示重试。

```bash
bash <<'BASH'
set -euo pipefail
umask 077
cd "$HOME/deer-flow"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py pause --batch validation
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --batch validation --watch
soc_report_dir="$HOME/soc-stage-baseline-$(date +%Y%m%d-%H%M%S)-$$"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py export --batch validation --output-dir "$soc_report_dir"
backend/.venv/bin/python scripts/soc_stage_report_summary.py \
  "$soc_report_dir" --output "$soc_report_dir/summary.json"
pbcopy < "$soc_report_dir/summary.json"
printf '\n报告目录：%s\nsummary.json已复制到剪贴板。\n' "$soc_report_dir"
BASH
```

`stage_conclusion_ready=true`表示满足完整阶段条件。失败、排队或不稳定结果未完成时，只能描述已完成部分，不能宣称全部完成。

## 12. 旧结果补记积累：新版本正常跑批直接跳过

### 12.1 什么时候需要

只有旧版本结果未写入共享积累，或明确需要补记时执行本节。新版本第一批、第二批运行成功时已经自动积累；通常直接去第13节。

### 12.2 按批次预检和补记，不调用模型

先暂停两批、等待运行中为0。下面输入 `learning` 或 `validation`，自动收集该批次当前成功结果，包括成功补跑：

```bash
bash <<'BASH'
set -euo pipefail
cd "$HOME/deer-flow"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py pause --batch learning
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py pause --batch validation
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --batch learning --watch
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --batch validation --watch
read -r -p '补记哪批（learning第一批 / validation第二批）：' soc_accumulate_batch </dev/tty
[[ "$soc_accumulate_batch" == learning || "$soc_accumulate_batch" == validation ]]
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py accumulate --batch "$soc_accumulate_batch" --dry-run
read -r -p '预检完成，输入 APPLY 执行：' soc_accumulate_confirm </dev/tty
[[ "$soc_accumulate_confirm" == APPLY ]]
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py accumulate --batch "$soc_accumulate_batch"
BASH
```

### 12.3 如何看输出

`model_calls=0`表示没有重新研判；`processed`是累计处理来源数。`semantic_review_failed_learning_deferred`表示语义核对失败，暂不积累，可先按8.3或11.2补跑。`direct_resolution_not_an_independent_sample`表示直接复用经验，不重复当作独立证据。补记可重复执行，独立样本不会重复计数。

## 13. 审核新增经验，再重新验证固定第二批

### 13.1 批量审核当前新增待审经验

不需要填写首次报告目录或任务编号。名单可能含第一批遗留待审和第二批新增待审，已审核经验自动排除。每次生成新计划，不能拿第9节旧计划代替新增名单；页面审核也可用。

```bash
bash <<'BASH'
set -euo pipefail
umask 077
cd "$HOME/deer-flow"
soc_review_dir="$HOME/soc-review-incremental-$(date +%Y%m%d-%H%M%S)-$$"
backend/.venv/bin/python scripts/soc_bulk_review_learning.py plan \
  --phase pending --output-dir "$soc_review_dir"
printf '\n审核目录，请保存：%s\n' "$soc_review_dir"
BASH
```

```bash
bash <<'BASH'
set -euo pipefail
cd "$HOME/deer-flow"
read -r -p '本次审核目录完整路径：' soc_review_dir </dev/tty
read -r -p '审核并发（1～8，回车用8）：' soc_review_concurrency </dev/tty
soc_review_concurrency="${soc_review_concurrency:-8}"
[[ "$soc_review_concurrency" =~ ^[1-8]$ ]]
test -f "$soc_review_dir/plan.json"
backend/.venv/bin/python scripts/soc_bulk_review_learning.py apply \
  --plan "$soc_review_dir/plan.json" --concurrency "$soc_review_concurrency" \
  --confirm APPROVE-FALSE-POSITIVE
BASH
```

普通续审再次执行上面一块，填写原目录。若出现 `draft_failed`、起草中断或超时，需要明确重试失败项：

```bash
bash <<'BASH'
set -euo pipefail
cd "$HOME/deer-flow"
read -r -p '原审核目录完整路径：' soc_review_dir </dev/tty
backend/.venv/bin/python scripts/soc_bulk_review_learning.py apply \
  --plan "$soc_review_dir/plan.json" --concurrency 8 --retry-failed \
  --confirm APPROVE-FALSE-POSITIVE
BASH
```

并发8可改成1～8。已完成的自动跳过；已有旧计划中的 `blocked` 不强行审核。

查看进度使用第9.3节，填本次新审核目录。待审为0则直接到13.2。

### 13.2 用扩大后的经验库重新跑第二批

1. 确认首次报告已按11.5保存。
2. 页面选第二批 → “重新配置并全部重跑”。默认语义核对开启，其他三个关闭，并发16。
3. 点击全部重新运行。当前批次统计从这次完整重跑开始，历史结果保留，不会混入新结果。
4. 跑完执行11.2补跑，若仍有队列执行11.3继续。
5. 到第14节导出。无需查新编号；第一批不重跑。

本次衡量学习过的场景在经验扩充后的复用效果，不等同于未见告警效果。

## 14. 导出最终报告，把精简统计发到外网总结

### 14.1 导出当前完整第二批并生成summary.json

```bash
bash <<'BASH'
set -euo pipefail
umask 077
cd "$HOME/deer-flow"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py pause --batch validation
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py status --batch validation --watch
soc_report_dir="$HOME/soc-stage-report-$(date +%Y%m%d-%H%M%S)-$$"
backend/.venv/bin/python backend/scripts/soc_corpus_experiment.py export --batch validation --output-dir "$soc_report_dir"
backend/.venv/bin/python scripts/soc_stage_report_summary.py \
  "$soc_report_dir" --output "$soc_report_dir/summary.json"
pbcopy < "$soc_report_dir/summary.json"
printf '\n报告目录：%s\nsummary.json已复制到剪贴板。\n' "$soc_report_dir"
BASH
```

### 14.2 已有报告时，仅生成精简统计

```bash
bash <<'BASH'
set -euo pipefail
cd "$HOME/deer-flow"
read -r -p '已有报告目录完整路径：' soc_report_dir </dev/tty
soc_summary_file="$soc_report_dir/summary-$(date +%Y%m%d-%H%M%S)-$$.json"
backend/.venv/bin/python scripts/soc_stage_report_summary.py "$soc_report_dir" --output "$soc_summary_file"
pbcopy < "$soc_summary_file"
printf '\n统计文件：%s\n已复制到剪贴板。\n' "$soc_summary_file"
BASH
```

### 14.3 发哪些内容

把剪贴板里的JSON发到外网即可。它只包含汇总数、运行设置、后台来源标识，不包含原始日志、经验正文或模型推理。重点看两组忽略比例、实际未使用经验时的转交比例、直接复用数、平均单条耗时。

每次导出保存为独立目录。首次与复测报告分别保存；当前批次自动汇总补跑，但不把两次完整验证相加。后台的 `source_rounds` 仅用于追溯实际设置和经验快照，不需要你选择或合并。
