# 内网：候选数量诊断与两阶段批量审核

这套独立工具适用于当前内网 Mac DEV，分为两种显式模式：

- 首次审核：`plan --phase initial`，默认模式；第一批已全部完成、第二批尚未开始，按第3～5节执行。
- 增量审核：`plan --phase incremental --report-dir 报告目录`；第一批已完成、第二批已有固定导出报告，两批暂停且活动任务为0，按第6节执行。

工具通过现有接口提交审核，服务保持运行，无需重置数据库或重跑告警。
新建计划默认 180 天复核、365 天有效期，需先部署本次配套应用，解除旧服务端重复模式经验
90 天有效期上限；仅更新独立脚本不足以支持新期限。恢复旧计划仍沿用原期限。
若旧版应用起草出现本文后述的 HTTP 504 或标识格式误判，需先安装包含对应修复的应用包；
只替换独立工具不会修改 nginx 或服务端校验。

本次运营选择是：将固定名单中的待审经验批量指定为 **误报、精确匹配时复用结论**，
核心行为沿用页面默认勾选项，已有附加条件保持原样，不额外勾选可选 IP、主机等条件。
这是一项明确的批量运营判断，不表示脚本已逐条证明每条告警都是误报。

## 当前数量差异能确认什么

旧报告目录为 `datas/report/second-batch-effect-report-20260924T074830Z-0c12d247/`。

| 对照项 | 旧报告保存事实 | 本次提供的信息 |
| --- | --- | --- |
| 第一批告警 | 2997 条，全部完成 | 2997 条，全部成功 |
| 语义核对 | apply；applied 366、partial 2586、failed 37、skipped 8 | 已开启，具体状态需读取 |
| 候选库存 | 累计 358 条：已确认 206、已放弃 151、待审 1 | 待审 117，尚未审核 |

旧报告中的库存包含后续累计记录，未保存“第一次跑完、尚未审核”时的待审数量，
因此不能直接用 206 或 358 减去 117，认定丢失了多少候选。

对比第一轮前的 `2b4d3e43` 与本次应用交付 `9e0de953`，自动积累门槛所在的
`core/memory_patterns.py` 和 `contracts/memory_patterns.py` 没有变更：仍要求至少
5 条观察、5 个独立来源和 5 条明确结论。正常第一批允许满足其他条件的混合结论进入人工审核。

需要优先核对的是实际分组：新版修复了 HTTP 服务行为与 HIDS 进程/命令目标的投影，
真正积累使用本次研判的实际行为；第一批分批仍来自冻结索引，每个原始组最多取 10 条。
例如原来的 10 条被新的核心行为分成 4、3、3 条，新组均不足 5 条，就不会自动形成候选。
也可能因消除不稳定字段而合并。数量变化方向和影响条数需要本次已保存证据证明。

研判成功也不保证存在合格观察；需区分正常未达门槛、核心行为不足、语义核对失败以及配置分段。
按实际积累组看最终支持数，不能把每组最初几条的“暂未达到门槛”重复统计成永久缺失。
诊断不与原始处置标签比较，也不重新计算行为或调整门槛。

## 1. 传输并校验

将 `READY-TO-TRANSFER/LEARNING-REVIEW-TOOLS/` 整个目录复制到内网：

```text
$HOME/READY-TO-TRANSFER/LEARNING-REVIEW-TOOLS/
```

执行：

```bash
cd "$HOME/READY-TO-TRANSFER/LEARNING-REVIEW-TOOLS"
shasum -a 256 -c SHA256SUMS
```

所有文件应显示 `OK`。脚本只依赖 Python 标准库，使用内网已有的 Python 3.12 即可。

## 2. 先导出只读诊断

在审核前执行，保留这 117 条仍待审时的数量快照：

```bash
python3.12 "$HOME/READY-TO-TRANSFER/LEARNING-REVIEW-TOOLS/soc_learning_candidate_audit.py" \
  --root "$HOME/deer-flow" \
  --output-dir "$HOME/soc-learning-audit-$(date +%Y%m%d-%H%M%S)"
```

工具只读 `soc_agent_dev.db`，不修改数据库、不调用模型、不重新分组。
若存在多个实验，按提示添加 `--experiment 实验ID`，不要自动选最近一个猜测。
补充学习后的新实验也使用这个参数：工具会核验指定实验的第一批完成状态和待审名单，
不会把它与旧网页快捷批次混为一组；已经继承的已确认经验不会重复审核。
新实验流程见[保留数据的补充学习手册](soc-corpus-rebalance.md)。
将输出目录中的 `summary.json` 用于核对数量差异；其中没有原始告警正文或模型推理正文。

## 3. 首次审核：冻结本次批量审核名单

保持服务启动，不要同时在页面审核这些候选，也不要启动第二批。
本次 117 条的命令如下：

```bash
python3.12 "$HOME/READY-TO-TRANSFER/LEARNING-REVIEW-TOOLS/soc_bulk_review_learning.py" plan \
  --phase initial \
  --expected-count 117 \
  --output-dir "$HOME/soc-review-117"
```

这一步只读接口，不调用模型、不提交审核。它确认第一批已完成，分页读完当前实验的待审候选，
再保存固定名单和默认适用条件。实际待审数不是 117 时会停止，避免把范围变化悄悄带入本次操作。
以后使用时将 `--expected-count` 改成那次页面显示的数量，并使用新的输出目录。

## 4. 一次执行整份名单

确认本次名单都采用你指定的“误报／精确复用”后执行：

```bash
python3.12 "$HOME/READY-TO-TRANSFER/LEARNING-REVIEW-TOOLS/soc_bulk_review_learning.py" apply \
  --plan "$HOME/soc-review-117/plan.json" \
  --concurrency 8 \
  --confirm APPROVE-FALSE-POSITIVE
```

工具默认最多同时处理 8 条候选，每条依次调用页面使用的经验起草与审核接口。
一条完成后再补入下一条，不必等同组的其他候选全部结束。`--concurrency` 可设为 1～8，
需要串行执行时使用 `--concurrency 1`。已用旧版脚本生成的计划和进度可直接继续使用。
新版计划会冻结 180 天复核周期与 365 天有效期。旧版 v1 计划继续使用原来的
30 天复核周期与 60 天有效期；已有审核请求、幂等键及已完成记录保持原样。
升级工具或重试旧计划不会延长已经审核的经验，也不会重新审核已完成项。

每条候选执行以下步骤：

- 以运营指定的误报结论生成完整草稿，保留来源、待核实事项和生成记录；不虚构业务授权理由。
- 保留默认核心行为与既有范围，提交精确复用、启用经验和匹配时清除复核标记。
- 新版重复行为候选采用 365 天有效期、180 天复核周期；其他来源仍受候选有效期限约束，
  若有效期不足以覆盖复核周期，会保留失败原因，不会自动放宽。
- 服务端拒绝的冲突、过期、证据或范围问题会记录失败；不会为了通过而放宽条件或替换已有经验。

起草会调用已配置的模型，所以整个过程需要时间，并非瞬时 SQL 修改。
终端按完成顺序显示每条进度；请保留终端直到结束。8 路并发是工具的上限，实际速度也取决于模型服务。
结果保存在 `soc-review-117` 中，包括 `receipts.json`、`receipts.csv` 和可恢复的本地状态。
不会改写历史告警的研判结论，也不会启动第二批或执行外部处置。

## 5. 中断恢复及结果检查

按一次 Ctrl+C 会停止派发新候选，并等待已发出的请求结束、保存回执后退出；
网络请求尚未返回时可能需要等待。认证失败也会停止新请求，保留在途结果。
普通中断后，重新执行步骤 4 的同一条命令。已完成项会跳过，成功生成的草稿会复用；
仍待审的项目使用同一幂等键和完整原请求恢复，不重新设置有效期。
如果审核响应丢失，而候选已经变成已确认，工具会标记 `confirmed_unverified` 并停止重审该条，
请在页面核对其结论和启用状态。这能避免把别人同时完成的审核覆盖掉；该条不计入脚本确认成功数。

如果模型请求超时，不能确认模型是否完成，工具不会盲目自动再调。
处理错误后，明确重试失败项时使用：

```bash
python3.12 "$HOME/READY-TO-TRANSFER/LEARNING-REVIEW-TOOLS/soc_bulk_review_learning.py" apply \
  --plan "$HOME/soc-review-117/plan.json" \
  --concurrency 8 \
  --confirm APPROVE-FALSE-POSITIVE \
  --retry-failed
```

失败重试可能再次调用模型。来源或候选范围变化的项目应重新核对，不用旧计划强行审核。
刷新经验页面，对照成功、失败和跳过数量。只有实际确认且精确复用已启用的经验计为成功；
确认完毕后再从页面启动第二批，以冻结这次已审核的经验。

查看计划本地进度时，可另开终端执行；不提交审核、不调用模型：

```bash
bash <<'BASH'
set -euo pipefail
read -r -p '审核目录完整路径：' soc_review_dir </dev/tty
test -f "$soc_review_dir/plan.json"
python3.12 "$HOME/READY-TO-TRANSFER/LEARNING-REVIEW-TOOLS/soc_bulk_review_learning.py" status \
  --plan "$soc_review_dir/plan.json"
BASH
```

### 117 条计划中剩余 31 条的恢复

本次返回 `completed=86、blocked=16、draft_failed=15`。保留整个 `soc-review-117` 目录，
不要重建计划或修改 `plan.json`、候选适用范围和审核幂等键。

| 状态 | 本次原因 | 处理方式 |
| --- | --- | --- |
| 16 条 blocked | 候选不允许精确复用 | 保留待审；仅作参考的候选应另按参考经验审核，不能强制修改为精确复用 |
| 11 条 draft_failed / 504 | 代理或上游超时；旧应用的 `/api/` 未配置长读超时 | 先更新应用，再重试；可结合 nginx 错误日志确认具体超时层 |
| 3 条 draft_failed / 502 | 输出把目录标签 `detection_key` 与合法原值拼接，触发字面标识校验 | 新应用只在同一条目录项确有标签与完整原值时接受该写法，其他未知标识仍拒绝 |
| 1 条 draft_failed / 502 | `migratevm.sh/chattr` 被识别为未提供的完整路径 | 保持校验；生成与修复提示要求用箭头或顿号连接真实进程名，重试起草 |

上述 15 条都未进入审核确认。HTTP 504 的旧提示 `Request rejected` 是非 JSON 响应的占位文字，
不能据此判断模型拒答。修复后的 nginx 对经验起草接口单独设置 1800 秒读超时并关闭上游重试；
脚本仅对该 POST 使用至少 1810 秒等待，其他调用仍沿用原时限。该预算覆盖本次内网配置的
单次 600 秒模型调用、至多一次输出修复及两次 180 秒入场等待；上游自身失败仍会正常报错，
不保证所有模型请求成功。降低并发能缓解等待，不能替代代理配置修复。

更新时使用发布包的 **保留数据升级教程**，保留本轮数据库与独立审核目录。
应用启动正常、审核工具校验通过后，使用本节前面的 `--retry-failed` 命令，仍可 8 路并发。
已成功的 86 条自动跳过，计划内 16 条 blocked 继续保留，只重试 15 条起草失败项目。
尚不能从这些失败数推导最初候选数量减少的原因；候选数量调查继续以只读积累诊断为依据。

## 6. 第二批已运行：先保存报告，再批量审核增量经验

按部署教程第11.5节导出第二批首次报告，保留目录中的 `report.json` 和 `manifest.json`。
暂停第一批、第二批及单条任务，等待所有运行中为0。服务保持运行，审核期间不开始新任务或同时在页面审核同一名单。
本节使用新计划目录，与第3节首次审核计划分开；已有审核不重复处理。

### 6.1 从固定报告建立增量计划

先按第1节校验新版工具，再执行。待审核为0时跳过审核，按部署教程新建复测轮次：

```bash
bash <<'BASH'
set -euo pipefail
umask 077
read -r -p '第二批首次报告目录完整路径：' soc_report_dir </dev/tty
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

工具从报告读取实验和轮次，不硬编码编号、不选择页面最新轮次。它核验报告清单、第一批完成、来源第二批非运行且未被替代、当前结果与固定报告一致，并确认无活动任务。
只有计划中冻结的待审候选可以提交；报告过时、任务仍运行、数量不符等情况会停止，不会静默切换来源。
没有 `--phase incremental` 时仍执行原来的首次审核限制，不能借此绕过第二批已开始的检查。

### 6.2 执行审核；普通中断后用同一计划续审

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

同样采用运营指定的误报／精确复用、默认核心行为、保留已有附加条件，复核180天、有效期365天。
它不证明每条候选都已逐条人工核实，也不自动启动第二批。已完成项跳过，`blocked`保持阻断。

### 6.3 显式重试失败；查看状态

显示 `draft_failed` 等失败、处理原因后执行；不要重建原计划：

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

只读查看本地状态，另一终端也可运行：

```bash
bash <<'BASH'
set -euo pipefail
read -r -p '本次增量审核目录完整路径：' soc_review_dir </dev/tty
test -f "$soc_review_dir/plan.json"
python3.12 "$HOME/READY-TO-TRANSFER/LEARNING-REVIEW-TOOLS/soc_bulk_review_learning.py" status \
  --plan "$soc_review_dir/plan.json"
BASH
```

保留计划、状态和回执。`confirmed_unverified`先在页面核对，缺少核心行为的 `blocked`不能强行改为精确复用。
若需要不同审核结论或单独处理阻断项，使用 `http://localhost:2026/workspace/soc/review/memory-candidates`，核对当前实验后逐条处理。
本次名单处理完，按部署教程第13.2节对第二批**重新配置并全部重跑**，冻结新增经验；不要点旧轮次“继续”。
首次报告与复测报告分别保存、分别生成精简统计，学习与验证样本重叠用于已见场景的复用验证。

## 认证与执行范围

默认连接本机 `http://127.0.0.1:2026`，仅接受回环地址。
当前 `--demo-no-auth` 模式无需额外配置，后台按现有规则记录管理员身份。
若使用正常登录认证，通过 `SOC_REVIEW_COOKIE` 和 `SOC_REVIEW_CSRF_TOKEN` 提供已登录管理员会话；
不要把会话值写入脚本、报告或发送给他人。脚本不会自己授予角色或关闭认证。

本地验证使用隔离数据和模拟接口；本次工具未在内网实际执行审核。
