# 第7B步：真实突增CSV接回已安装流程

第7步人工DEV数据证明了历史召回和两阶段调用能够串联，但不能代替用户指定的真实案例。本步骤把 `20261102175803453.csv` 接入已经安装的 `SurgeTaskSource`、取样器、`SurgeProcessor` 和模型适配器，重新进行真实材料的生成与AI复核。

## 内网结果已回传

用户已执行 `surge-real-csv-runtime-qru5afd_`：`received/proposal`、`ai_review_completed=true`、`recommendation_ready=true`，SDK请求2次。真实CSV原始告警完整召回为2205条/2217日志；内网214行小PKL提供同规则11条真实历史/15日志，4月1日至30日，距本批约140天。LOCAL原有43行中的42条人工记录已排除，没有人工历史进入模型材料。

模型自行建议同时满足以下两项时新增排除：`detail_hit_rule_name` 包含 `Windows使用net查询密码策略`，且 `event_content` 包含 `net1 accounts`。这是与用户期望场景相符的真实AI输出；过滤JSON与正文描述的 `原规则 && !(A && B)` 一致。该条件还包含子规则名称约束，不能等同于仅按命令文本过滤。实际规则未更改，原报告和模型回答保留。

| 阶段 | 当前可见告警 | 历史可见告警 | 输入tokens | 输出tokens | SDK耗时 |
|---|---:|---:|---:|---:|---:|
| generate | 28 | 11 | 86170 | 872 | 20.696秒 |
| review | 28 | 11 | 87004 | 826 | 22.172秒 |

两次总tokens为174872，SDK耗时合计约42.867秒。未设置输出token额度，预估206261输入tokens不等于生成实际输入86170tokens；仍是原字节估算法，不据单次比例改为通用精确分词。

**该次复核的范围：** `cited_current_reused=28`、`new_current_count=0`，两阶段展示的是同一组28条当前告警和11条历史。取样器先保留生成回答引用的所有当前样本，剩余估算预算未容纳新样本。这次是新上下文中的同材料AI复核，没有验证另一批告警，更不能声称模型看过全部2205条。后续[第7C取样修复包](../step07c_review_sampling_fix/README.md)已由用户确认内网安装并重启；本次原回答保留。

**新版手工重测已回传：** 用户执行 `surge-real-csv-runtime-tcpan7hp`，生成28当前+11历史；复核26当前=13新增+13原引用，另保留11历史。新策略 `whole-alert-review-new-and-cited-interleaved-v1` 已出现在实际selection；原草案另15个当前引用未展示并明确标记。两阶段累计看到41个不同当前告警，不是全部2205条。`analysis_reused=false`，两次实际SDK请求，`received/proposal`、`ai_review_completed=true`、`recommendation_ready=true`。record为 `46c351bbc8bf52daba34edaa77edd8de73e15ece4eddd83ffad7a205448c7069`；job ID与旧信号相同但结果目录、record与模型请求均为新一轮。用户本次明确要求手工测试，未扩展为后台自动触发。

新版生成输入86170/输出968 tokens、24.630秒，复核输入85358/输出1365 tokens、34.847秒，总173861 tokens（输入171528/输出2333）、SDK耗时合计59.477秒。历史仍11条，约140天缺口；模型和输入预算未改，没有输出token参数。

**新版条件的语义复核：** AI只建议 `detail_hit_rule_name contain Windows使用net查询密码策略`。与同事的 `event_content contain net1 accounts` 是两个不同字段上的集合，不能笼统说等价、包含或覆盖率一样；在已观察的主要场景上可能重合。相比上次AI的“子规则名 AND 命令”，本次删去命令条件，逻辑上放宽该子规则内的排除。只有业务意图为整类子规则豁免时，单名称才准确表达该意图；限定本次固定命令场景时，优先保留名称与命令的联合条件。

模型正文提出仅限severity_id=1、保留父进程非空/命令变体/modify活动，但新增JSON不检查这些字段，不能依靠文字使其生效。结构、字段及引用检查通过不证明上述范围承诺；`recommendation_ready=true`不等于语义无误或已经批准。正文“历史39条”也与实际11条不符，原文保留、不静默修正。此轮确认取样修复和手工链路生效，也暴露了AI复核未解决正文与条件不一致的问题。此次仅记录审阅意见，不改变应用、条件或模型回答，不重调模型、不做过滤试算。

本次证明了真实材料召回、历史进入模型以及目标场景建议的两阶段链路。未执行程序过滤试算，未测量全量覆盖率或实际降量；140天前的历史不证明突增前夕变化。`received`表示建议已在本地保存，不代表ZEUS已接收；Celery自动派发仍未在本次测试覆盖。

结果定位：job `36ae2550484c7f81ad6b333bbdf69f510da43515c18ad39a5b79b7531f9c9c82`；record `c320be24ba63549db2310c50f03a4c7aa27f950cafdbab94114cb6cb6dfab930`。此节依据用户回传摘要及正文，不表示助手已访问内网结果文件。

## 内网执行

打开 [CHECK-REAL-CSV.sh](CHECK-REAL-CSV.sh)，复制整个文件到内网终端执行。脚本自带验证工具，不需要另外安装或重启应用。默认路径：

- 本次告警：`$HOME/sec_know_model/nas/original/20261102175803453.csv`。
- 补充历史：同目录的 `full_alert_2026_month_forth_sample_200.pkl`；不存在则只使用LOCAL库中实际可用的历史。
- 项目历史：`nas/data/sec_know_model.sqlite`，只读原始告警，不使用旧模型研判。
- 模型和预算：已安装的 `rule_refine.analysis.local.json`、`rule_refine.surge.local.json`，不另设输出token限制。

预览会显示全批材料数、真实历史数量和时间跨度、实际模型取样数。输入 `LIVE` 后最多调用两次SDK：独立生成，再在新上下文中AI复核；代理内部可能重试。完成后直接打印 `REPORT.md` 和各阶段真实 `transport.usage`。结果放在新的 `~/sec-rule-refine-data/surge-real-csv-runtime-*` 目录。

同一命令重新执行会重新准备材料、新建目录并再次请求模型；查看已完成结果直接读取已打印的报告路径，不要为查看报告重复运行。

## 材料与规则

保留CSV全部目标告警的原ID、内容和业务时间。历史候选来自LOCAL原始任务和可选小PKL，按同topic、同规则、严格早于本批最早告警的业务时间取最近20条不同告警。不满20条如实使用实际数量；不修改时间、不混入人工DEV记录。这里的“最近”仅针对当前提供的材料，不能声称是线上完整历史中最近20条。

材料临时写入隔离SQLite，再由已安装的历史召回代码读取。项目数据库保持只读，临时库在准备完成后删除；这验证了真实材料经过同一个召回模块，不等于已经把真实数据导入项目库，或验证了在线接收与Celery自动触发。

按用户要求假设尚未添加对应排除项，只在内存中的规则副本删除后来新增的那项条件，实际规则CSV不变。移除记录只进审计文件，不作为预期答案传给模型。旧模型回答、人工期望过滤JSON也不进入请求。首次生成由AI自行提出条件，不强制返回proposal；AI可以返回证据不足或无需调整。

不执行程序过滤试算，不提交告警、不回调ZEUS、不应用规则，也不把历史导出重新计入实时突增计数。

## 本地验证记录

22项离线测试覆盖原材料保留、最近20条召回、历史不足、重复与冲突、人工数据排除、反事实规则、真实PTY确认、无LIVE不调用、配置读取、两阶段持久化及命令打包。使用临时数据库与替身模型，不代表真实AI效果。

另用实际CSV、真实规则快照和本地小PKL完成离线准备及两阶段替身传输：

- 当前2,205条告警、2,217条原日志完整召回。
- 历史11条告警、15条日志，2026-04-01至04-30；距本批最早2026-09-17约140天，不能代表突增前夕。
- 当前CSV SHA256 `b407ab3ba4139ef456e5646cf8ffdda7951549019f2daffe3f4194607c9d46f9`。
- 本地小PKL为210行，SHA256 `708eac1dfb77351aaad5f8282be04ab6a41b4467487189f23dfd94455d320869`；内网之前提供的是214行的另一版本，内网数量须重新统计。
- 本地生成预览为当前28条、历史11条；复核数量还会随模型实际引用及补样请求变化。完整召回数量不等于每次模型看到全部告警。
- 上述本地离线检查的实际模型、Redis调用均为0；另行完成的内网真实AI结果见上节。

本地检查命令：

```bash
backend/.venv/bin/python validation/rule_refine/implementation/step07b_real_csv_acceptance/build_command.py
backend/.venv/bin/python -m unittest discover -s validation/rule_refine/implementation/step07b_real_csv_acceptance -p 'test_*.py'
```
