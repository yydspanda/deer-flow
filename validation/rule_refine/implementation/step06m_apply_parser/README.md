# 6M：应用JSON代码框兼容修复

用户已确认6L原回答离线复核passed：proposal、received、local_checks_passed，validation_error=null；正文SHA10dc131e79722ae54191572a0518cd9523b2e8b597c01c5b2b92c788faa69000，promptSHAb2b46524201c991afe41dffddf5767579371672db58080e8a94fdd1945f0d15e。来源目录fenced-json-replay-6nth7amy，模型/Redis/数据库调用0，项目源码及原报告未改。

APPLY-PARSER-FIX.sh是完整、可直接复制的命令。它核对该离线结果、4个纯函数/测试依赖和2个待修改文件摘要，在临时树运行18项测试；通过后仅备份和原子替换analysis.py及test_analysis.py。备份在~/sec-rule-refine-data/parser-fix-*，旧文件0600；不备份数据库/整个项目，不改配置或原报告，不调用任何服务。运行中的源码文件最后替换；前一步失败可回滚测试文件。未知编辑拒绝覆盖，旧/新明确版本允许重复执行和中断恢复。

修复的运行源码SHA为c8e73780c98a0ae6e02611220965050be07e0dcb3b30b133d4419a03176f00e0；测试SHA为fe9e13b15690b8947968e7f203f0f1af4c66828c79cd516514514c5a1ac65992。current-pins.json是后续验证的版本清单；6J/6K/6L旧证据及其固定版本命令不重写，也不要求用户放宽版本核验。

安装后后台进程须重启才读取新解析器：在原来运行local_run.sh的终端按一次Ctrl+C，等该脚本停止自己的子进程并回到提示符，再执行：

```bash
cd "$HOME/sec_know_model"
env env_profile=LOCAL ENV_PROFILE=LOCAL bash local_run.sh
```

已有run.py及模型代理继续运行；不另起worker或beat。不要重跑6G启动检查，它要求分析关闭，与用户已开启的6H不符。local_run.sh的现有trap负责TERM自身jobs并等待，脚本会统一重启worker和beat，不需猜PID或强制结束其他进程。

本地6项安装测试通过，成功、重复、恢复分支均真实运行18项分析测试；覆盖备份、未知修改拒绝、坏证据拒绝、中途源码替换失败回滚。模型/Redis/数据库调用0。完整复制命令与同一安装源码生成，语法检查通过。

2026-10-29内网已回传installed/tests_passed=18，运行源码和测试摘要均一致；备份目录为~/sec-rule-refine-data/parser-fix-w5fh4wsy。安装确认完成；后台重启结果见下文。用户随后贴出的Flower栈显示请求先404，错误页面再因缺少认证内容触发401；不属于analysis.py解析异常，也不能据此判断worker健康。local_run.sh同时启动Flower、worker、beat，共用终端输出，暂不改Flower认证或重新安装补丁。

在原后台终端搜索rule_refine_background，取重启后最近记录。也可在另一终端只读查询文件：

```bash
cd "$HOME/sec_know_model"
tail -n 5000 nas/log/info.log 2>/dev/null | grep -F 'rule_refine_background' | tail -n 5
```

文件可能没有Celery输出，查不到不代表任务停止，应回到原终端查找；旧日期的idle也不能证明本次重启成功。此步不重新调用模型，不据此宣布真实告警链路通过。

维护：

```bash
backend/.venv/bin/python validation/rule_refine/implementation/step06m_apply_parser/test_install.py
backend/.venv/bin/python validation/rule_refine/implementation/step06m_apply_parser/build_command.py
bash -n validation/rule_refine/implementation/step06m_apply_parser/APPLY-PARSER-FIX.sh
```

2026-10-29后续确认：用户在原local_run.sh终端Ctrl+C后重启，10:16:50、10:17:20、10:17:50三次tick均成功，分别耗时0.4088/0.3577/0.3632秒；均idle、processed=0、catalog_scopes=911、scanned=256、deferred=0、needs_attention=0、automatic_apply=false。beat按30秒派发，worker接收并完成。nas/log/info.log查询为空，原终端有记录；以实际终端记录确认后台定时轮询正常，不要求修改日志配置。6M安装及重启轮询验证通过，带材料的后台模型分析仍待后续验证。
