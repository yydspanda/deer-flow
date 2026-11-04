# 6Y：按1M上下文的80%试跑历史规则建议

本次按用户指定，假定内网模型上下文为1,000,000 tokens，输入使用其中80%，即 **800,000估算tokens**。材料能放下就一次分析，超过才分批；不再按128KiB、20个告警ID或每告警100条日志拆分模型请求。

模型仍读取 `config/rule_refine.analysis.local.json`，当前用户已确认是 `Pub_DeepSeek_V4_Flash`。本次工具直接使用上述输入策略，不需要修改原limits JSON。它不安装应用补丁、不改变后台任务设置，历史报告保留。超时使用600秒。

按用户后续要求，不向SDK发送 `max_tokens`、`max_completion_tokens` 或 `max_output_tokens`，移除本次试跑中的64KiB回答接收预算。模型输出能力仍取决于服务自身配置。本地原有40MiB记录结构边界保留，属于JSON及文件处理边界，不是给模型设置的生成配额。

共享适配器允许新验证工具显式传入 `response_format={"type":"json_object"}`，并记录请求模式及配置摘要。现有6Y调用默认不传，SDK参数与原配置摘要保持不变。源码包的4001 LiteLLM代理配置为 `drop_params: true`，可能丢弃不支持的参数，因此审计始终标记 `response_format_enforcement_verified=false`；发出该参数或收到有效JSON都不代表已验证上游强制执行。此选项不增加重试、输出token上限或温度设置，也不绕过原有回答校验。

## 当前内网结果与离线复核

本次内网报告 `historical-rule-batches-y00ewc9u` 已回传：214行全部扫描，50条目标告警、51条有效日志进入一次请求。接口返回输入229652、输出227 tokens，耗时约25.1秒，正常结束。离线诊断已确认唯一格式错误：模型未返回 `certainty`（判断把握及依据）字段。

离线复核现已通过：`status=passed`、`analysis_status=no_change`，仍使用同一份50告警/51日志。缺失的certainty明确标为missing，未补写模型把握；复核没有新增模型、Redis或数据库调用。`sample_match_counts=null` 表示未提出过滤条件，无匹配试算，不是零匹配。

报告正文已回传，可用以下命令重新查看：

```bash
cat "$HOME/sec-rule-refine-data/historical-certainty-replay-enni6tnf/REPORT.md"
```

`no_change` 仅表示模型基于本次材料未提出过滤调整，不能由此证明规则正确。原回答、原报告和内网应用均未改；不需要重跑或重启。报告理由是：模型认为部分命中存在业务解释可能，但缺少业务归属佐证，因此不建议新增宽泛排除。原文“高置信”“已确认失陷”仅为模型措辞，本次没有独立验证失陷或误报；不把这些说法当作事实。此处业务核验建议不新增脚本必填门槛。

[REPLAY-SAVED-REPLY.sh](REPLAY-SAVED-REPLY.sh) 保留供复现。本地后续试跑已同样兼容缺少certainty，重新执行新版试跑命令时生效；内网应用未安装此修改。

[DIAGNOSE-SAVED-REPLY.sh](DIAGNOSE-SAVED-REPLY.sh) 保留用于检查旧版报告格式；其 `contract_valid` 只代表输出结构通过，不代表证据或建议条件通过。所有复制命令第一行都是 `bash <<...`，避免zsh把开头注释当成命令。

## 1. 复制执行

保持4001/8003模型代理运行。打开 [REVIEW-HISTORICAL-BATCHES.sh](REVIEW-HISTORICAL-BATCHES.sh)，复制全文到内网Mac新终端执行。

脚本先扫描整份 `~/sec_know_model/nas/original/full_alert_2026_month_forth_sample_200.pkl`，查询 `~/sec-rule-refine-data/rules.csv` 中的 `ptp-nids / RPAADM_002627`，然后显示：

- 请求模型、上下文假定1,000,000、输入估算预算800,000。
- 扫描行数、有效告警和日志数、无效或身份冲突材料数量。
- `batch_estimated_input_tokens`：各次请求的保守token估算。
- `planned_sdk_requests`：实际计划调用次数；放得下全部材料时为1。
- `model_output_token_limit: null`：工具没有设置输出token上限。

输入 `LIVE` 开始。每批顺序调用一次；代理内部可能重试，工具自身不自动重试。调用或校验失败会停止后续批次并保留已有结果；有效的“证据不足”或“暂无修改建议”会继续下一批。重复执行并再次输入LIVE会建立新目录、重新分析，当前没有断点续跑。

本次仅分析历史材料；不提交告警、不回调ZEUS、不修改实时计数或线上规则。调用模型仍使用项目原有Redis容量槽及熔断。API与Celery不参与本次试跑。

## 2. 看结果、贴回摘要

结果目录会显示为：

```text
~/sec-rule-refine-data/historical-rule-batches-随机后缀/
```

把最后的精简JSON贴回来。`planned_estimated_input_tokens` 是计划输入估算，`reported_prompt_tokens` 是已调用请求返回的实际输入用量；若部分调用没有返回usage，则实际总量为null，不以0代替。完整审计和各批用量保存在 `summary.json`。

建议正文在同目录 `REPORT.md`：

```bash
bash <<'SOC_SHOW_BATCH_REPORT'
set -euo pipefail
read -r -p '本次报告目录完整路径：' soc_report_dir </dev/tty
cat "$soc_report_dir/REPORT.md"
SOC_SHOW_BATCH_REPORT
```

报告保留每次分析的问题、建议及业务前提；不额外调用模型综合，不自动拼接不同批次的过滤条件。试算只反映所提供日志的匹配，不是误报率或线上降量率。

## 3. 估算方法与覆盖口径

工具不要求内网额外下载tokenizer。当前采取保守代理估算：**完整消息正文UTF-8字节数之和，加每消息64及整个请求256的封装余量**。这通常比BPE实际token数大；它不是精确分词结果，也不是已验证的严格上界。预览和审计明确记录估算方法、`token_count_exact=false`、`context_capacity_basis=operator_assumption`。SDK返回的实际usage始终另存，用于本次试跑后的校准，不能把两者混称真实token数。

扫描整个已确认的小样本PKL（此前确认实际214行）；旧工具前200行发现50条目标告警、51条日志，因此本次实际总数以预览为准。全部有效且无身份冲突的目标日志进入计划，保留原ID和原日志内容；相同告警重复导出只纳入一次，同ID内容冲突明确排除。历史模型回答不作为正常业务标签输入。

模型分批只根据完整请求的估算token预算，不按告警/日志条数截取。试算器内部仍可分块调用原有函数，但会恢复原日志索引并汇总全部结果，这不会增加模型调用。超过20个引用的回答同样经过原有身份、重复引用、条件、组合逻辑及来源检查。

若一条完整日志和规则本身超过800,000估算输入tokens，预检会在调用前说明所需量；不会截断该日志或悄悄漏掉它。保留2MiB单告警JSON、有限深度及32MiB请求结构处理边界，无效、冲突等排除项单列；“完整覆盖”仅指该文件中该规则有效无冲突的材料。

## 4. 本地验证

本次新增缺少certainty兼容10项、离线复核及整段复制命令10项测试通过；与原58项和诊断13项合计91项。保存回答诊断13项离线测试通过，覆盖具体类型/长度错误、固定字段组合错误、50条样本引用、源码/schema/回答摘要漂移，以及不写文件、不连接服务。可复制命令已通过Python3.11、Bash语法及内嵌源码一致性检查。

本地58项测试通过（预算14、材料16、SDK串联4、编排14、报告6、完整命令4），Ruff及Python3.11/Bash语法检查通过。覆盖人工50告警约0.5MB一次请求、225条小告警一次请求、1106条小日志一次请求、50引用真实解析和试算、超过64KiB有效回答、实际SDK参数不包含输出token限制、预算冻结、配置变化停止、取消0调用、来源去重和完整复制命令。测试使用人工数据及替身服务，未调用真实模型、Redis或业务数据库；内网原回答已离线复核通过，模型为no_change；报告正文已回传，未提出新增过滤；真实业务准确性仍未独立验证。

```bash
backend/.venv/bin/python validation/rule_refine/implementation/step06y_historical_batches/test_budget.py
backend/.venv/bin/python validation/rule_refine/implementation/step06y_historical_batches/test_materials.py
backend/.venv/bin/python validation/rule_refine/implementation/step06y_historical_batches/test_adapter.py
backend/.venv/bin/python validation/rule_refine/implementation/step06y_historical_batches/test_runner.py
backend/.venv/bin/python validation/rule_refine/implementation/step06y_historical_batches/test_report.py
backend/.venv/bin/python validation/rule_refine/implementation/step06y_historical_batches/test_command.py
backend/.venv/bin/python validation/rule_refine/implementation/step06y_historical_batches/build_command.py
bash -n validation/rule_refine/implementation/step06y_historical_batches/REVIEW-HISTORICAL-BATCHES.sh
backend/.venv/bin/python -B validation/rule_refine/implementation/step06y_historical_batches/test_diagnose_saved_reply.py
backend/.venv/bin/python -B validation/rule_refine/implementation/step06y_historical_batches/test_certainty.py
backend/.venv/bin/python validation/rule_refine/implementation/step06y_historical_batches/build_replay_command.py
backend/.venv/bin/python -B validation/rule_refine/implementation/step06y_historical_batches/test_replay_saved_reply.py
bash -n validation/rule_refine/implementation/step06y_historical_batches/REPLAY-SAVED-REPLY.sh
backend/.venv/bin/python validation/rule_refine/implementation/step06y_historical_batches/build_diagnostic_command.py
bash -n validation/rule_refine/implementation/step06y_historical_batches/DIAGNOSE-SAVED-REPLY.sh
```
