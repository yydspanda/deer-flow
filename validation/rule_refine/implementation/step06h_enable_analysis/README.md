# 第6步H：开启本机后台自动分析

6G内网反馈已确认真实定时派发与消费：2026-10-28 15:34:11和15:34:40两次tick执行成功，15:34:40包含beat派发日志，结果均disabled/analysis_disabled/automatic_apply=false。

在另一个终端完整粘贴CONTROL-STEP06H.sh，输入ENABLE。命令只检查现有配置、模型策略与CSV，原子更新独立分析配置为true，保留模型、保存目录及其他文件；不安装新模块，不重启服务，不修改监测命名空间。以0600临时文件、fsync和replace避免worker读到半份JSON；修改前核对原配置内容。该命令没有分布式配置写入锁，不用于多人同时修改。

本次明确确认的是后续自动分析：worker下一次tick读到开启配置后，有待处理信号及可用材料时会向当前模型代理请求建议。命令本身不调用模型、不连接数据库/Redis，不调用run_background作为预检。当前终端的配置预检不证明worker环境或服务连通性；这些依然由实际后台日志验证。此前LOCAL业务表为空，因此开启或出现idle不能证明真实业务材料或建议质量已通过。

保持原后台运行，贴回连续4条rule_refine_background日志（约两分钟，繁忙时更久）。idle/processed=0仅说明该次最多256个作用域没有可执行待办；911个作用域约4次无分析耗时tick才能轮转一圈。retained+materials_pending为等待材料，最多3次后needs_attention；capacity_wait不消耗次数。acknowledged表示结果存档并确认信号，不保证一定生成建议，可能为证据不足或无需变更。failed需保留error_type/reason排查。

关闭：重新执行同一段，输入DISABLE。关闭不依赖CSV或模型策略有效，也不要求终端LOCAL；不会删除信号、游标、结果或重置次数。下一tick停止新增分析；已经开始的调用继续结束，结果保留。LocalRecommendationSink始终mock/applied=false。

本地验证仅使用临时配置、人工模块和终端输入替身：8个分支通过，覆盖开启、取消、关闭绕过无效监测/非LOCAL环境、无效模型、监测未开启、非LOCAL拒绝开启、重复开启和操作期间配置变化；写入权限0600，其他字段保留，临时文件清理。Bash语法通过。未开启真实分析或连接外部服务；内网启用及后台后续结果待用户回传。

内网首次执行在open("/dev/tty", "r+")报UnsupportedOperation: File or stream is not seekable，未进入配置写入，自动分析仍关闭。旧测试用StringIO替身，未覆盖终端不可seek的特性。现将提示写到stdout并flush，仅以r模式读取/dev/tty；不修改业务模块，不要求服务重启。新增test_terminal_control.py以真实PTY执行完整Bash/heredoc脚本，临时配置与人工依赖隔离业务服务；旧版本复现相同错误，修复后开启、无CSV配置时关闭、取消及EOF四项均通过（0.208秒），Bash语法通过。修复后由用户重新粘贴完整CONTROL脚本验证。

修正版内网反馈：configuration_check=passed，模型DeepSeek_V32，提供器csv_snapshot，911条规则，命名空间sec-know-rule-monitor-mac-zhangjianming627-v1；开关status=enabled、effective_on=next_background_tick、restart_required=false、automatic_apply=false、model_calls_this_command=0。配置保存通过；开启后的worker日志尚未回传，不据此认定后台依赖、真实材料或模型分析成功。

后续内网日志已回传：2026-10-28 18:20:11、18:20:41、18:21:11、18:21:41四次tick成功，idle、processed=0、catalog_scopes=911、scanned=256、deferred=0、needs_attention=0，耗时0.3627/0.3576/0.4206/0.4012秒。已验证开启后的定时轮询、规则目录读取及Redis待办查询；当前无信号进入取样和模型调用，不代表完整业务链路验收。下一步复用`.venv/bin/python test/rule_refine/check_local_task_source.py`确认本机当前样本，用于准备隔离回放。

## 切换规则分析模型

用户已选择将规则分析路由切换为 `Pub_DeepSeek_V4_Flash`。打开 [SWITCH-TO-PUB-DEEPSEEK-V4.sh](SWITCH-TO-PUB-DEEPSEEK-V4.sh)，复制全文到内网新终端执行，保持原代理运行。

脚本读取现有模型策略，并通过本机8003的`/health`确认运行中的适配器已加载目标别名；只修改`config/rule_refine.analysis.local.json`中的`model`，保留分析开关、保存目录及其他设置。原子保存，重复执行不重复修改；不调用模型或Redis，不修改全局研判模型、代理映射、容量配置或已有报告。

预期输出为`status=updated`或`already_configured`，`model=Pub_DeepSeek_V4_Flash`，`restart_required=false`。后台下一次新调用及下次6X历史试跑读取新配置；已开始的调用保留原设置，已保存成功结果不会自动重跑。若`analysis_enabled=false`，切换模型不会擅自开启自动分析。

上传源码包中该路由转入内网OpenAI兼容服务，发送的上游模型名称为`deepseek-v4-flash`；不能据此宣称它已升级为官网最新V4.1。配置切换和路由检查也不代表模型请求已经成功，仍以首次实际调用为准。当前128KiB等取样策略仍保留，后续容量与分批覆盖改进另行处理。

本地已用临时配置和代理替身核对切换、重复执行、路由缺失时不写入、关闭状态保留四种情况；Bash语法通过，没有真实网络或模型调用。内网执行结果待回传。

## 将运行配置纳入内网Git

用户已确认模型配置发生改变，并要求同事能够获得运行配置。原3G安装脚本添加了`/config/rule_refine.local.json`忽略项，6F添加了`/config/rule_refine.analysis.local.json`；6W没有添加limits文件的忽略项。当前用户选择将三个规则功能JSON全部纳入内网Git。

打开 [TRACK-RULE-CONFIGS.sh](TRACK-RULE-CONFIGS.sh)，复制全文到当前内网项目的终端执行。脚本仅移除这三个文件的精确忽略项，追加相应允许跟踪规则，然后暂存`.gitignore`及三个JSON；不改配置内容、不自动提交、不连接模型/Redis。原项目的`.env`、`private.py`、CSV、数据库、运行结果和虚拟环境等忽略项保持不变。已跟踪文件不再受旧安装器重复追加忽略项影响，所以不重写以前已交付的摘要快照。

提交这四个文件可使用：

```bash
git commit --only -m "chore(rule-refine): track runtime configuration" -- \
  .gitignore \
  config/rule_refine.local.json \
  config/rule_refine.analysis.local.json \
  config/rule_refine.limits.local.json
```

同事首次启动前，把CSV放在其自己的`~/sec-rule-refine-data/rules.csv`；默认`csv_path`和`state_dir`均支持`~`。各自独立验证时，同事应在其监测JSON中将`namespace`改成自己的稳定值，避免共用本机用户的去重计数和待办；后台锁是本机文件锁，不能据此保证跨笔记本共用namespace时不重复分析。用户正在运行的namespace不变。先检查分析开关是否符合其本次测试意图，再按原项目启动服务。本次只分享现有LOCAL功能配置，不表示已支持PRD部署或改变模型容量策略。
