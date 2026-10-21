# 内网：批量审核当前待审经验

更新配套应用后，第一批、第二批及单条补跑产生的经验，统一使用 **当前待审名单**。
不用填写 ROUND-ID、实验编号或报告路径，也不用先数待审条数。下列命令使用项目自带脚本，服务保持运行。

本次运营选择：**误报、精确匹配时复用结论**；核心行为使用页面默认勾选，保留已有附加条件。
新计划默认复核180天、有效期365天。这是你指定的批量判断，不表示脚本逐条证明了误报。

## 1. 建立本次名单

第一批完成后、第二批积累后，都执行同一个命令。每次建立新目录：

```bash
bash <<'BASH'
set -euo pipefail
umask 077
cd "$HOME/deer-flow"
soc_review_dir="$HOME/soc-review-pending-$(date +%Y%m%d-%H%M%S)-$$"
python3.12 scripts/soc_bulk_review_learning.py plan \
  --phase pending --output-dir "$soc_review_dir"
printf '\n审核目录，请复制保存：%s\n' "$soc_review_dir"
BASH
```

看到 `candidates=N` 即已保存名单，尚未审核。`candidates=0` 表示没有待审经验，跳过本次审核即可。
可选加 `--expected-count N` 核对数量；不填则自动读取。

名单只包含当前实验、当前积累代次的待审经验，可能来自第一批、第二批和单条补跑。
已审核经验不会重复加入；建立计划后新增的候选留给下一次新计划。无需让第一批全部成功或让第二批保持未运行。

## 2. 执行批量审核

将第1节打印的目录填入提示；并发1～8，默认8：

```bash
bash <<'BASH'
set -euo pipefail
cd "$HOME/deer-flow"
read -r -p '审核目录完整路径：' soc_review_dir </dev/tty
read -r -p '审核并发（1～8，回车用8）：' soc_review_concurrency </dev/tty
soc_review_concurrency="${soc_review_concurrency:-8}"
[[ "$soc_review_concurrency" =~ ^[1-8]$ ]]
test -f "$soc_review_dir/plan.json"
python3.12 scripts/soc_bulk_review_learning.py apply \
  --plan "$soc_review_dir/plan.json" --concurrency "$soc_review_concurrency" \
  --confirm APPROVE-FALSE-POSITIVE
BASH
```

每条先起草经验，再提交审核。不要同时在其他终端或页面审核同一名单。
只处理计划中的固定候选；新候选增加不会中断审核或偷偷加入本次名单。
重新开始第一批导致积累代次改变、切换数据集、候选内容被修改时，脚本会停止相应提交并保留回执。

## 3. 查看进度

可另开终端执行；只读本地回执，不调用模型：

```bash
bash <<'BASH'
set -euo pipefail
cd "$HOME/deer-flow"
read -r -p '审核目录完整路径：' soc_review_dir </dev/tty
python3.12 scripts/soc_bulk_review_learning.py status --plan "$soc_review_dir/plan.json"
BASH
```

`completed`是审核完成；`draft_failed`是起草失败；`blocked`是该候选暂不能按本计划审核。
缺少核心行为的新告警只保留样本，不生成新的待审经验。历史遗留的无核心行为候选仍可能显示`blocked`，不强行改为精确复用。

## 4. 中断后接续、明确重试失败

保留原目录。已完成项自动跳过。仅继续尚未开始的项，可再次执行第2节。
遇到 `draft_failed`、`drafting` 或超时后明确决定重试，使用下列命令：

```bash
bash <<'BASH'
set -euo pipefail
cd "$HOME/deer-flow"
read -r -p '原来的审核目录完整路径：' soc_review_dir </dev/tty
read -r -p '重试并发（1～8，回车用8）：' soc_review_concurrency </dev/tty
soc_review_concurrency="${soc_review_concurrency:-8}"
[[ "$soc_review_concurrency" =~ ^[1-8]$ ]]
test -f "$soc_review_dir/plan.json"
python3.12 scripts/soc_bulk_review_learning.py apply \
  --plan "$soc_review_dir/plan.json" --concurrency "$soc_review_concurrency" \
  --retry-failed --confirm APPROVE-FALSE-POSITIVE
BASH
```

成功的草稿会复用，不重新生成；完成的审核不会重复提交。原有适用范围阻断继续保留。
`confirmed_unverified`表示接口无法证明这次请求的提交结果，先到页面核对，不自动重复确认。
HTTP 504表示超时且完成情况不确定，不能当作模型拒答；明确重试前先处理代理或上游故障。

如提示当前积累代次已改变，保留旧目录，回到第1节建立当前名单；不要手改计划或回执。

## 5. 审核后继续验证

审核不会自动启动告警任务。需要验证新增经验时，在页面选择第二批，按当前设置重新配置并全部重跑。
第一批、第二批默认都是语义核对开启，企业策略、安全软件路径策略、LLM策略建议关闭。
完整操作顺序、失败补跑和统计导出见部署教程相应章节。

## 6. 高级兼容：旧计划与固定报告审核

原有 `initial`、`incremental` 模式继续可用；v1～v3计划保持原来的来源、期限与幂等信息，不能改成新模式。
现有审核目录可直接按第2～4节执行，脚本自动识别其版本；旧计划仍须满足建立时的范围检查。
日常操作优先使用第1节的 `pending`。

### 6.1 首次审核的严格模式

要求第一批全部完成、第二批尚未开始，且没有活动任务。仅在需要这种检查时使用：

```bash
bash <<'BASH'
set -euo pipefail
cd "$HOME/deer-flow"
read -r -p '预计待审核条数：' soc_pending_count </dev/tty
[[ "$soc_pending_count" =~ ^[1-9][0-9]*$ ]]
soc_review_dir="$HOME/soc-review-initial-$(date +%Y%m%d-%H%M%S)-$$"
python3.12 scripts/soc_bulk_review_learning.py plan \
  --phase initial --expected-count "$soc_pending_count" --output-dir "$soc_review_dir"
printf '\n审核目录：%s\n' "$soc_review_dir"
BASH
```

### 6.2 绑定固定报告的增量模式

要求第一批全部完成，报告对应完整第二批范围，来源非运行、未被替代，实验任务全部静止。
来源只因`memory_governance_changed`停止时仍可用于审核，但不会恢复原任务或把报告改成整批完成。
保留`report.json`和`manifest.json`，不能换成仅33条等局部补跑报告：

```bash
bash <<'BASH'
set -euo pipefail
cd "$HOME/deer-flow"
read -r -p '完整第二批报告目录：' soc_report_dir </dev/tty
read -r -p '预计待审核条数：' soc_pending_count </dev/tty
[[ "$soc_pending_count" =~ ^[1-9][0-9]*$ ]]
test -f "$soc_report_dir/report.json"
test -f "$soc_report_dir/manifest.json"
soc_review_dir="$HOME/soc-review-incremental-$(date +%Y%m%d-%H%M%S)-$$"
python3.12 scripts/soc_bulk_review_learning.py plan \
  --phase incremental --report-dir "$soc_report_dir" \
  --expected-count "$soc_pending_count" --output-dir "$soc_review_dir"
printf '\n审核目录：%s\n' "$soc_review_dir"
BASH
```

名单仍是当前实验、当前积累代次的共享待审项，报告用于固定来源，不证明所有候选都来自第二批。
计划和执行均核验报告清单、已保存结果和来源状态。其他停止原因、被替代来源或报告变化仍拒绝。
执行、进度和重试使用第2～4节相同命令。

## 7. 可选：只读积累诊断

需要调查“为什么候选变少”时再执行，无需每次审核都跑诊断：

```bash
bash <<'BASH'
set -euo pipefail
cd "$HOME/deer-flow"
soc_audit_dir="$HOME/soc-learning-audit-$(date +%Y%m%d-%H%M%S)"
python3.12 scripts/soc_learning_candidate_audit.py \
  --root "$HOME/deer-flow" --output-dir "$soc_audit_dir"
printf '\n诊断结果：%s/summary.json\n' "$soc_audit_dir"
BASH
```

诊断只读数据库；若提示采集期间变化，请暂停任务和审核后重试。区分未达次数门槛、缺少核心行为、语义核对失败和不同积累代次，不用历史标签直接评判候选数量。

## 认证、版本与文件

默认连接本机 `http://127.0.0.1:2026`，仅接受回环地址。`--demo-no-auth`模式使用已有管理员身份。
正常登录时通过 `SOC_REVIEW_COOKIE` 和 `SOC_REVIEW_CSRF_TOKEN` 提供会话；不要把会话值写入报告。

`pending`需要配套新版应用提供批次接口；只替换脚本不会更新 Runtime 或 nginx。
如使用独立工具目录，先执行该目录的`shasum -a 256 -c SHA256SUMS`，再将上述脚本路径换成对应文件。
脚本不写SQL、不改适用范围、不自动启动第二批。计划、状态和回执保存在你指定的审核目录。
