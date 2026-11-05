# 规则配置检视与告警突增优化

本目录保存规则配置检视资料，以及在内网 `sec_know_model` 项目逐步实现告警突增过滤建议的方案。

设计、接口、实施步骤和验收记录统一见 [规则告警突增过滤建议实施方案](规则告警突增过滤建议_实施方案.md)。

## 当前实施入口

**2026-11-05：第9步及[完整回答修复](implementation/step09a_complete_output/README.md#内网手工验收已回传)已完成内网手工验收。** 操作符安装288项/23文件、完整回答修复308项/8文件均回传成功。`surge-real-csv-runtime-qbiqd34t` 生成和复核均为proposal，实际2次SDK请求，最终条件使用进程名与命令内容的Contains组合，没有规则命中名称条件。生成26条当前、复核23新增+3复用，两阶段累计49条不同当前及共用11条历史；不是全量2205条验证。用户确认结果可以，停止重复调用。建议仅本地保存，未提交或应用规则。安装与重测命令保留供复现，不必为查看结果再次执行LIVE。

**[第9步操作符统一更新](implementation/step09_operator_contract/README.md)。**
用户更新了 `validation/sec_know_model.zip`，确认它是昨晚成功部署的生产包。
本轮按新接口把生成/复核提示词、内部结构、比较器、提交客户端与测试统一为
`Eq / Ne / Contains / NotContains / StartsWith / EndsWith`；提供先离线回归、再备份安装的
增量脚本，不自动提交规则或重启服务。旧报告原文保持不变，历史兼容与新输出校验分开。
下方早期步骤保留为历史记录，不能作为覆盖新版的安装入口。

**多轮完整历史实测已回传：第一轮180039 tokens成功，第二轮360063 tokens因262144上限被拒绝。[记录](implementation/context_input_probe/MULTITURN.md)。** context-multiturn-qq4et5v8/probe两次均传max_input_tokens=1048576，完整保留前序原文与实际回复，无输出额度；客户端2POST/0重试，第二轮主备同错，第三轮未发。此限制针对本次携带历史的完整输入，不能理解为每条消息各有256K或会话生命周期累计上限。尚未到500K，未验证语义记忆质量，无需原样补测第三轮。[已执行脚本](implementation/context_input_probe/RUN-MULTITURN.sh)。

**显式max_input_tokens=1048576对照已实测，仍被256K限制拒绝：[对照记录](implementation/context_input_probe/README.md)。** context-max-input-p1spxwrj/probe实际请求顶层已携带该整数，消息和路由与基线一致；新请求640245字节，去参hash仍为7d020...f7185。服务再次报告320029输入/262144上下文，主备同错、HTTP400、约3.245秒；客户端1POST/0重试，未去参重发。此次参数没有解除限制，是否被远端忽略、丢弃或未改变部署限额仍未知；交平台核对参数转发和主备配置，无需原样再测。[已执行脚本](implementation/context_input_probe/RUN-WITH-MAX-INPUT.sh)。

**独立长输入实测已回传：实际320029 tokens被远端262144上下文限制拒绝。[实测记录](implementation/context_input_probe/README.md)。** context-input-probe-pkl6y8v4/probe直连配置中的DeepSeek上游，绕过本地代理和规则demo预算；HTTP400，主路由与备用路由均报相同容量。客户端1次POST、0重试、约3.790秒，远端尝试次数未知。输入tokens来自服务错误原文，不是字节估算；未发送max_input_tokens/max_tokens或修改配置。当前远端链路拒绝该长输入已确认，具体限额所在层及是否可扩至1M仍需平台方核对；无需原样重跑。[原测试脚本](implementation/context_input_probe/RUN-ONCE.sh)。

**当前状态：第7F已安装，AI已把过宽草案修正为“进程名＋命令内容”的待审候选。[第7F实测记录](implementation/step07f_repair_first_review/README.md#内网结果已回传)。** 安装129测试/4文件；结果surge-repair-review-82a34ffc34a8复用第7E生成草案及原26当前＋11历史，未新增取样，复核v5调用1次SDK，输入84388、输出1177tokens，25.410秒。最终received/proposal，实际JSON为detail_process_name equal net1.exe AND event_content contain net1 accounts，没有再以规则命中名称作条件。这轮手工候选修正目标已达到；本次没有重新测试生成v4。正文声称的路径限制不在JSON中，contain也不是完整命令等值；未验证全量效果或其他威胁一定保留。旧报告保留，无过滤试算或规则应用，无需为查看结果重跑LIVE。[第7E撤回结果](implementation/step07e_event_field_boundary/README.md)保留为历史记录。

**新版真实CSV手工生成与复核已完成，正在核对建议范围：[实测记录](implementation/step07b_real_csv_acceptance/README.md#内网结果已回传)。** 用户回传 `surge-real-csv-runtime-tcpan7hp`：2205告警/2217日志及11真实历史；生成28当前+11历史，复核13新增+13原引用+11历史，累计41个不同当前告警。SDK2次、未复用旧分析、received/proposal、ai_review_completed与recommendation_ready均true；总173861tokens，SDK耗时59.477秒。第7C安装、重启和新取样实际调用均已有用户回传证据；当前只验收手工流程，未做自动触发。

本轮AI识别目标场景，但JSON只按子规则名称过滤；同事按event_content命令过滤，两者不能当作等价替换。相比上轮AI的“子规则名 AND 命令”，本轮缺少命令约束。正文声称severity_id=1且保留父进程/命令/modify变体，JSON未落实，历史39条的措辞也与实际11条矛盾。结构通过不证明语义一致；本轮保留原文并说明范围，不改规则、不重跑模型、不增加程序试算。

[第7C修复说明](implementation/step07c_review_sampling_fix/README.md)：先保留完整可放的新原文，再交替取新旧样本，原草案不改写、未展示引用明确标记。56项运行时＋8项安装兼容检查通过；真实CSV构造全部引用回归时，从新增0变为14新＋13原引用，历史11不变，预算不增加。这是离线对照，实际重测样本数随新草案变化。旧报告保留。

**真实CSV已完成建议生成与AI复核：[第7B步内网结果](implementation/step07b_real_csv_acceptance/README.md#内网结果已回传)。** 用户回传 `surge-real-csv-runtime-qru5afd_`：received/proposal、两阶段完成、recommendation_ready=true，SDK2次。AI自主提出“子规则名称包含Windows使用net查询密码策略 AND event_content包含net1 accounts”的排除建议。真实当前2205告警/2217日志完整召回，真实历史11告警/15日志；人工42条排除。该次两阶段各看到同一组28当前+11历史，复核新增样本0，后续取样修复见上方第7C；历史距当前约140天。实际输入86170/87004、输出872/826tokens，总174872tokens。仅本地保存、未修改规则；没有全量试算或线上降量证明，Celery自动触发仍待验收。22项离线测试结果保留，不把本地替身测试与本次真实模型调用混算。

地址归属已核对上传ZIP：`PRD_MODEL_CONFIGS`中的地址是模型调用目标；`util/root_config.py`注释另列本项目自身生产服务地址，两者不同。ZIP没有远端模型组的8条映射或256K部署设置；下一步应查模型入口背后的实际部署配置，而非从该客户端配置分组推断服务端限额。

**第7步DEV人工历史与两阶段真实模型验证已通过。** 用户回传 `surge-dev-history-live-_uyz_4fk`：当前6条、历史20条，`received/proposal`、`ai_review_completed=true`、`recommendation_ready=true`，本次模型调用2次。终端确认修复已安装，18项回归通过。正文已回传：AI比较当前6条与历史20条的task_source差异，推荐task_source equal planned_inventory；人工历史对照确实参与建议。该条件涵盖所有该来源的活动，范围比单个inventory-probe命令更宽。真实业务材料验证另见上方第7B步；Celery派发和线上降量仍未由人工验证证明。详见[第7步说明](implementation/step07_surge_runtime/README.md)。

本次预览的 `reported_rejected_input_tokens=336423` 是11月3日拒绝请求的旧记录，不是本次报错或用量。`context_tokens=262144`、`input_fraction=0.8` 是安装器写入的本地估算取样预算；请求代码没有传 `max_input_tokens` 或输出token参数，也没有修改远端容量。两阶段实际用量各在 `generate/response.json`、`review/response.json` 的 `transport.usage`，本次用户摘要尚未包含具体数量。

上下文调查转由平台核对，**未实际修改远端 `max_input_tokens` 后复测**。该元数据允许覆盖，可能来自LiteLLM内置目录；只有启用相关预检查时才在该层拦截，单凭262144不能断言后端不可扩大。此前 `decode_bad_request` 仍提示更下游拒绝过该输入。新流程的 `context_tokens` 可调，SDK不设置输出token额度；详见[调查记录](implementation/step06z_surge_recommendation/README.md#210-当前排查256k上下文限制来自哪一层)。

**[6Z独立分析已在内网返回建议](implementation/step06z_surge_recommendation/README.md#29-内网实测已返回目标场景的建议)。** `surge-independent-d2lva4bc`复用38条原材料、历史0条，未输入旧草案或人工预期条件；模型自主提出进程名包含`net1.exe`且`event_content`包含`net1 accounts`，并识别混合日志中的`mshta`活动。JSON及字段/引用检查通过，实际输入85667、输出791 tokens，约22.096秒，1次SDK请求。此次已取得与目标场景相符的AI建议，无需再次执行同一分析。正文仍有包含/等值表述不一致和未经统计核对的数量，保留原文，不静默改写模型条件。正式接线已整理为第7步安装包；未完成该次原两阶段复核、未验证线上降量，内网后台的新安装结果待回传。

最新新增告警检查（2026-11-03）：`datas/20261102175803453.csv` 内为9月17日2236条唯一告警；其中2205条来自 `RPAADM_000598【青藤云】WEB命令执行`，36分12秒集中产生，跨2205个主机标识重复出现net1 accounts行为。用户随后明确要求假设尚未配置排除，验证系统主动推荐过滤。已在忽略目录 `datas/counterfactual-net1-20261103/` 准备模拟规则与全部样本、隔离的预期建议及早期本地试算；只移除对应排除，实际配置未改。早期本地试算能排除2204条纯该场景告警，保留1条混合告警的其他行为；这不是后来AI生成条件的试算结果。后续内网AI实测进展见上方，未重新程序试算。原检查明细在 `datas/inspection-20261103-b407ab3b/`。

当前已完成 [6W统一修复安装](implementation/step06w_unified_limits/README.md) 和 [6X历史规则试跑](implementation/step06x_historical_review/README.md)。内网后台恢复轮询；历史试跑的模型请求、输出格式与配置核对通过，模型返回证据不足，未形成过滤条件。真实突增后的完整自动建议链路和ZEUS接口联调仍待验证。

当前复测使用 [6Y上下文预算试跑工具](implementation/step06y_historical_batches/README.md)。按用户指定，假定内网上下文1M tokens，输入使用80万估算tokens；全部有效材料放得下则一次分析，超过才分批，不再受128KiB、20个告警或100条日志的模型分批限制。不向SDK设置输出token上限，也移除64KiB回答接收预算。模型读取已确认的 `Pub_DeepSeek_V4_Flash` 配置，实际token用量单列。内网首轮已将50条告警、51条日志纳入一次请求，接口返回输入229,652 tokens；离线诊断确认仅缺少 `certainty` 判断把握说明。现已用原回答离线复核通过，模型结论 `no_change`，缺失说明保留明确标记；新增模型调用0。新报告正文已回传：模型认为缺少正常业务归属佐证，不建议新增宽泛排除。历史分析功能已完成本案例验证，不必重跑；不由此宣称规则正确、已确认失陷或已验证线上降量效果。详见 [6Y说明](implementation/step06y_historical_batches/README.md)。

以下早期步骤保留为交付版本和后续安装器的摘要依赖，不应按列表重新执行一遍，也不要批量格式化已交付的固定版本文件。

已完成步骤：[第 1 步 A 说明与复制命令](implementation/step01_contracts/README.md)。CSV 内网目标位置为 `$HOME/sec-rule-refine-data/rules.csv`。

已完成步骤：[第 2 步 A CSV 查询说明与复制命令](implementation/step02_csv_source/README.md)。

已完成步骤：[第 2 步 B 本地模拟接收说明与复制命令](implementation/step02b_local_receipt/README.md)。

已完成核对：[第 3 步入口核对命令](implementation/step03_ingress_check/README.md)。

已完成步骤：[第 3 步 A 提取规则观察与复制命令](implementation/step03a_observations/README.md)。

已完成步骤：[第 3 步 B Redis 计数与 EVAL 兼容修复](implementation/step03b_counter/README.md)。

已完成步骤：[第 3 步 C 串联检查](implementation/step03c_chain_check/README.md)。

已通过串联检查：[第 3 步 D 查询接口、批量读取与复制命令](implementation/step03d_preparation/README.md)。

已通过入口隔离检查：[第 3 步 E 默认关闭的入口钩子与复制命令](implementation/step03e_ingress_hook/README.md)。

已完成 API 启动：[第 3 步 F 临时启用](implementation/step03f_enable_monitor/README.md)。

已完成普通启动配置：[第 3 步 G 保存配置与普通启动](implementation/step03g_persistent_settings/README.md)。

协作方式：一次推进一步，用户在内网执行并贴精简结果，助手检查后给下一步。每次继续先读取方案末尾的“当前协作位置”，根据已验证结果更新。

## 资料

- [规则配置 CSV](202610221741结果集.csv)：配置查询的本地替代来源，不包含告警流。
- [过滤条件格式示例](输出优化建议.txt)：`topic + A/B/C + expression`。
- [既有运营修改建议](audit-20261026-729b0892/operations-report/运营修改建议.json)：问题、原因、修改片段与前提。
- [静态检视报告目录](audit-20261026-729b0892/operations-report/)：此前完成的规则配置检视报告与附件。

实现位置是内网原项目；本目录用于方案、可复制安装脚本和离线验证代码，不作为 DeerFlow 生产模块的运行时依赖。上面的CSV、格式资料及审计目录是本地私有依赖，未随Git提交；原项目源码ZIP同样独立提供。含原始告警、配置值及运行结果的材料按 `validation/AGENTS.md` 管理。安装/诊断脚本可能调用内网服务，应按各步骤说明执行，不能直接把全部shell脚本作为测试运行。
