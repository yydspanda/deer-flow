# 第7E步：区分规则命中信息与过滤条件字段

第7D的真实AI回答把检测名称和进程名组成排除条件，仍未限定具体行为。用户明确要求优化对应检测本身，不能将命中结果反过来当成排除依据。本步修改应用源码及两阶段请求，不改写旧模型回答。

## 内网结果：已安装，复核撤回过宽草案，尚无最终建议

用户回传第7E安装成功：103测试/6文件、installed_state_verified=true，备份 `surge-event-field-fix-8zqqndr0`。手工结果目录 `surge-real-csv-runtime-z4l8mtrg`，新record `2af41ee92c4f306997ea8d798a3bb9a5c31c9c6f6522915af442bb4ae605c6ed`。两轮正常返回、analysis_reused=false、SDK2次；最终not_applicable/insufficient_evidence、filter=null、recommendation_ready=false，字段用途版本和检查标记均已生效。

真实材料仍2205当前/2217日志、11历史/15日志。生成27当前＋11历史；复核26当前=13新＋13原引用＋11历史，累计40个不同当前告警。生成输入85304/输出930tokens、20.410秒；复核输入84003/输出644tokens、15.474秒；合计170881tokens、SDK耗时35.884秒。以上为用户回传，不代表助手读取过内网文件。无业务库写入/告警提交/ZEUS回调/程序试算/规则应用；后台重启尚无本次确认。

复核报告描述草案只有detail_process_name equal net1.exe，正确指出这会排除同进程的其他活动，但未继续利用已看到的具体操作尝试修正，转而因缺少不同活动样本退出。同时把进程名称称为事件类型/检测类别，混淆了字段可用性与条件过宽：进程字段本身不是本步声明的禁用命中信息。当前原规则configuration以web_command及既有排除为基础，不能从样本一致推断整条规则只检测一个命令。

结论：字段用途约束及两阶段流程完成本次运行，没有形成可用建议，不能作为业务质量验收通过。后续[第7F](../step07f_repair_first_review/README.md)保留用途硬检查，明确先尝试修正再决定撤回；准备复用原草案与同一批复核原文，只重做一次复核，不重新调用生成。

## 内网执行

打开 [INSTALL-AND-RETEST.sh](INSTALL-AND-RETEST.sh)，将全文复制到内网终端执行。

脚本先核对已安装第7D版本、执行离线回归，备份并安装3个运行时文件和3个测试文件；随后使用原第7B真实CSV手工工具。读取 `nas/original/20261102175803453.csv`、只读LOCAL库和原小PKL中的真实历史，预览后输入 `LIVE` 才调用生成与复核，最多2次SDK请求，代理内部可能重试。报告另存，不重新补数、不执行过滤、不改规则。安装失败会停止，不进入重测；重跑LIVE会再次请求模型。

本次手工测试由新Python进程加载新源码，无需先重启worker。测试后按原方式重启 `local_run.sh` 才让后台加载新代码。只安装可用 [INSTALL-COMMAND.sh](INSTALL-COMMAND.sh)。原步骤和原第7B命令保持不变。

## 修复内容

- 规则身份保留在 `rule` 中；原始日志完整保留。`visible_field_names`仍只表示看到哪些字段，不再作为过滤条件字段目录。
- 新增来源明确的字段用途清单。上传legacy源码已将青藤的 `detail_hit_rule_name`、`detail_rule` 解释为命中信息；相关APT、EDR、NIDS字段也只采用已有schema明确的含义，按确切topic与路径记录来源。不按 `name`、`rule` 等单词猜测用途，不全局禁止同名字的其他topic字段。
- `candidate_field_names`从本轮实际字段中扣除这些已确认的检测结果。模型仍能看到命中信息理解背景，但不能用它做排除条件。实际包含检测结果子字段的父对象及其子路径也不能绕过检查。
- 解析器对上述字段返回 `condition_uses_detection_metadata`，保留原回答与字段位置。第一阶段的问题交给第二阶段重新判断；不会自动删掉A或替换字段。第二阶段仍有问题，结果为 `manual_review`、`recommendation_ready=false`，报告列出原因。
- 新提示词明确：不能换成匹配message中同一命中名称来绕过；不能因为样本里暂未出现其他行为，就接受会放过其他行为的条件；如果候选仅重复原规则触发条件、近似关闭该检测，应重新找实际区别，缺证据则返回证据不足。

其他可见字段仍是候选，不受“必须已经在原规则中使用”的限制；同topic不同日志形状继续支持。候选不代表引擎已经支持或字段一定在检测前产生。当前没有完整引擎字段目录，未知字段来源与嵌入字符串的业务含义仍需AI和人工核对；此补丁不能宣称自动证明所有过滤范围正确。

## 版本与兼容

生成提示词 `rule-surge-runtime-independent-generation-v3`，复核提示词 `rule-surge-runtime-review-v4`。字段用途版本 `event-fields-not-detection-results-v1`；原行为范围版本保持 `behavior-specific-exclusion-v1`。不增加模型回答必填键，不预填期望的net1过滤条件，不增加调用次数、输入预算或输出token额度。

用途清单随每次请求冻结，程序根据来源和实际字段复核清单；不能由模型或请求附加字段取消禁用。旧请求仍按其冻结版本复核，不篡改旧报告的通过状态，也不把旧结果冒充经过新检查。新报告说明只加到确实携带新清单的任务，旧任务完成/写完报告后中断恢复均保持原字节。

确定性检查的范围是已审阅的字段用途、格式、字段可见性及引用，不是命令语义、引擎执行、过滤效果或业务正常性证明。实际AI是否给出合理条件，以本次内网重测原回答为准。

## 离线验证

103项运行时回归通过（原71项＋新增32项），覆盖同topic异构字段、跨topic字段含义、已知命中字段的AND/OR与未引用条件、目录校验、生成问题进入复核、修正/撤回/仍需修改，以及无新增调用恢复。旧D版本对新增测试的核心场景会错误返回ready=true，已确认回归能暴露原缺陷。

13项安装/升级检查通过：首次及重复安装均通过103项隔离运行时测试；旧D完成与REPORT后中断恢复保持原字节、无新调用；未知安装版本、离线测试失败和已有未知新增文件会停止。两份脚本Bash语法检查通过。合并脚本165303字节，SHA-256 `de33ca62ca6d855ef843558656ff0e7a2384fc88fdd120c11004dade0b89ce88`；仅安装脚本145050字节，SHA-256 `ed4db7b18a2c6c27dc28eaf47e1b4053270aaec1a1a7c20efafc827b04ffa8ed`。

真实CSV离线检查已通过：完整召回2205条当前告警/2217条日志及11条真实历史/15条日志。生成取27当前＋11历史；脚本构造的草案引用生成样本后，复核取13新＋13原引用＋11历史。输入分别223329B/221816B，估算206863/205578tokens，均在既有209715预算内；取样数量变化来自完整新提示词和字段目录计入预算，没有新增样本条数限制。

两组**脚本回答对照**分别验证：两轮都使用命中字段时最终manual_review/ready=false；第二轮改用事件字段时流程received/ready=true。共4次本地替身函数调用，真实模型/SDK/Redis/业务数据库调用均0，没有程序执行过滤。这证明字段检查与流程工作，不证明模型会自主修正，更不证明脚本条件具有业务价值。原始材料、源文件摘要保持不变，临时SQLite已清理。

离线证据在忽略目录 `validation/rule_refine/datas/event-field-preflight-b6oj5avc/summary.json`；复现工具为 [offline_preflight.py](offline_preflight.py)。本地PKL为210行版本，内网现有文件为214行，内网工具仍会重新统计真实历史与取样。内网安装和本次真实模型结果已回传，见上方记录；旧离线对照不替代真实AI质量结果。

```bash
backend/.venv/bin/python -m unittest discover -s validation/rule_refine/implementation/step07e_event_field_boundary -p test_patch.py -v
backend/.venv/bin/python validation/rule_refine/implementation/step07e_event_field_boundary/build_command.py
backend/.venv/bin/python validation/rule_refine/implementation/step07e_event_field_boundary/offline_preflight.py
```
