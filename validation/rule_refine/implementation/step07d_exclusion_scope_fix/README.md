# 第7D步：按具体行为生成建议，复核实际过滤范围

用户在第7C真实手工测试中发现，模型只按子规则名称提出排除，同时在正文声称保留命令或父进程变体。JSON并没有实现这些限制。取样已生效，问题转为生成目标与AI复核判断。

经讨论，本步不按topic配置固定字段，不靠字段名称猜测身份与业务含义，也不要求至少两个条件。字段由AI结合当批原日志选择。此前暂停的字段名单式拦截方案未实现、未打包。

## 内网实测：流程通过，建议范围仍未达到要求

用户回传安装成功：71项测试、4文件、installed_state_verified=true，备份 `surge-exclusion-scope-fix-7gq4y3mt`。随后执行 `surge-real-csv-runtime-v_iaiphq`：`received/proposal`、两阶段完成、analysis_reused=false，SDK2次；新scope_policy_version和draft_explanation_included=false均在结果中。后台重启尚无本次确认，手工新进程已使用更新源码。

真实材料仍2205告警/2217日志、11历史。生成28当前+11历史；复核25当前=13新增+12原引用+11历史，未展示原引用16个，累计41个不同当前告警。复核使用第一轮补样请求。生成输入86489/输出1391tokens、29.295秒；复核输入85405/输出885tokens、22.914秒；总174170tokens，SDK耗时合计52.210秒。当前同一job的新record为 `fbf6ce19b4628540fb32b3cb66f3b16bd290941d10d0c42c585e32d35f7244e6`。这些数据来自用户回传，不表示助手访问了内网结果文件。

最终AI条件为检测标签 `detail_hit_rule_name contain [恶意命令执行]Windows使用net查询密码策略` AND 进程 `detail_process_name contain net1.exe`。它没有限定具体命令。复核正文明确承认同检测下其他net1.exe命令也会被排除，却因可见样本尚未出现这些命令而保留建议；这不符合保留其他行为检测的目标。`recommendation_ready=true`仍是结构检查结果，不能把这次记录为范围质量通过，也不能称此问题已修好。

用户进一步指出检测标签可能是命中后加工的信息：需核对原规则定义和字段来源，区分待优化规则的输出与上游检测产品的输入标签。暂不继续代码改造或模型重跑，不在缺乏数据来源证据时按字段名推断时序。旧报告保持原样。

只读源码核对：上传legacy ZIP中 `my_workflows/zeus/flows/alert_schema.py` 的 `HidsAlertHitLog` 将 `detail_hit_rule_name` 描述为“命中的青藤云的规则名”；同包 `hids_prompts.py` 描述为“命中青藤HIDS的内置规则名称”。外层待优化规则为 `RPAADM_000598 / 【青藤云】WEB命令执行`。原/模拟ZEUS配置的条件字段是datatype（标准字段映射为metadata__product__feature__name）、detail_process_tree、event_content、detail_process_name，并无detail_hit_rule_name。

据此应区分两层：该字段是传入的上游HIDS检测标签，不能直接称为当前ZEUS规则自身生成的名称，也不能说A已被当前ZEUS条件恒等蕴含。它可以帮助限定上游检测类别，但不能代替具体行为的过滤依据；当前A与net1.exe组合仍未限定accounts。来源侧生成/标准化的完整代码未提供，不能从名称进一步推断所有生产时序或所有topic字段角色。

## 内网执行一次即可

打开 [INSTALL-AND-RETEST.sh](INSTALL-AND-RETEST.sh)，复制全部内容到内网终端执行：

1. 先执行离线回归，备份并安装2个运行时文件及2个新增测试文件。安装阶段不调用模型、不重新补数。
2. 安装成功后接着运行原第7B真实CSV手工脚本。默认读取 `nas/original/20261102175803453.csv`、只读LOCAL库及小PKL中的真实历史，沿用当前模型和预算。
3. 确认预览后输入 `LIVE`，重新执行生成与AI复核，最多2次SDK请求；代理内部可能重试。报告另存到新的 `surge-real-csv-runtime-*` 目录，并直接打印正文。

手工脚本启动新的Python进程，立即读取安装后的代码，不需要先重启worker才能测试。测试结束后按原方式重启 `local_run.sh`，让后台进程也加载新代码。安装失败会停止，不会进入模型重测；取消LIVE不撤销已经成功的安装。再次执行LIVE会创建另一份结果并再次请求模型，不要为查看报告重复运行。

如果只想安装，使用 [INSTALL-COMMAND.sh](INSTALL-COMMAND.sh)。旧安装包和第7B验证工具保持原样；合并命令直接包含第7B原脚本，不另写第二套召回或调用逻辑。

## 本次变化

- 第一轮先识别具体重复行为，再从实际原文字段和值形成条件。保留主规则及子规则对其他行为的检测，不能仅靠同一检测名称、编号或类别整体放行。
- 第二轮只接收候选 `status/filter/sample_alert_ids` 和真实日志。生成理由、置信判断、复核说明、附加解释不进入第二轮消息，完整生成回答仍保存在原记录中。第一轮的补样请求仍用于现有取样器，原引用可见性也保留。
- 第二轮检查JSON实际排除范围，并检查同一检测类型下的其他行为是否也会被放过。发现放得太宽时应修正、收窄或撤回；正文中的限制必须与最终条件一致。允许明确标注的假设变体作逻辑分析，不伪装成实际告警或程序试算。
- 新报告明确“格式、字段、引用检查通过”只表示可供人工审阅。`recommendation_ready`不代表程序已证明业务范围正确；新增 `recommendation_ready_scope` 和 `filter_scope_program_verified=false` 说明其含义。

模型回答仍采用原来的8个键，解析器不变；没有新增必填输出字段、字段名单、输出token配额、自动修复调用或程序过滤试算。原输入预算、历史召回与第7C取样策略保留，不把已知同事条件或这次预期答案写进提示词。

新请求版本：

- 独立生成：`rule-surge-runtime-independent-generation-v2`
- 复核：`rule-surge-runtime-review-v3`
- 任务说明标记：`behavior-specific-exclusion-v1`
- 复核方法：`filter_scope_and_other_behaviors`

## 验证与兼容

新增10项提示和输入检查、5项两阶段与报告检查，合并56项既有回归共71项运行时测试通过。使用脚本化传输验证调用次数、原答留存、复核纠正或撤回路径以及报告展示；这些输出是测试替身，不是AI效果实测。

另有11项安装、包完整性与兼容测试通过，覆盖重复安装、未知源码保护、安装失败停止及原第7B脚本逐字复用。Python3.11语法、Ruff和Bash检查通过。

实际CSV离线预检完整召回2205告警/2217日志，本地真实小PKL提供11历史/15日志。生成28当前+11历史；以明确标记的脚本草案引用全部28条时，复核13新+13原引用+11历史，原引用15未展示。生成估算207872、复核估算205540，均在既有209715预算内；消息分别224440B、221814B。生成解释未进入复核，源文件、原日志、草案与历史均未改；模型/Redis调用0，临时SQLite已清理。证据位于忽略目录 `datas/behavior-review-preflight-tt1c1uq9/summary.json`。本地PKL210行版本与内网214行版本不同，内网预览仍重新统计；以上计数不预先约束真实AI输出后的取样结果。

新版消息经过完整预算计算后取样，不在预算检查后追加说明。跨topic与同topic异构字段、单一业务条件、原8键输出、原引用校验均覆盖。旧7C已完成任务和REPORT已写但最终记录未写的任务，升级后均可零新模型调用恢复，已保存文件保持原字节。

已有报告作为历史事实保留，升级不重写其中的建议或ready状态，也不把旧任务重标为经过新版AI复核。新方法的说明只根据该任务冻结的新版请求添加，避免旧报告中断恢复时内容冲突。

离线测试证明了输入组织与流程行为；能否让本批真实模型建议更准确，要以接下来内网手工重测的两份回答与最终报告为准。不会因为离线测试通过，就宣称能自动识别所有过宽建议。

本地检查命令：

```bash
backend/.venv/bin/python -m unittest discover -s validation/rule_refine/implementation/step07d_exclusion_scope_fix -p test_patch.py -v
backend/.venv/bin/python validation/rule_refine/implementation/step07d_exclusion_scope_fix/build_command.py
```

本步结论：内网安装及新版真实AI重测已回传；取样/输入组织/两阶段调用通过，建议仍未限定具体行为。后续用户明确命中信息不能作为优化对应检测的排除依据，并授权修复和重试；继续见[第7E字段用途边界](../step07e_event_field_boundary/README.md)。本步交付代码、安装包和旧答案保持原样。
