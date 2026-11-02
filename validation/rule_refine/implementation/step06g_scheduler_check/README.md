# 第6步G：验证真实后台定时调度，分析保持关闭

输入：用户6F反馈为12项后台适配测试通过（0.019秒），轮转演示processed_per_tick=[1,1,0]，Celery5.4.0独立注册通过。用户这次没有另贴21项轮转测试汇总，不记录为内网已确认。

本步不安装新模块、不修改开关。在原local_run.sh终端按一次Ctrl+C，等待warm shutdown和脚本wait返回提示符，再完整粘贴START-STEP06G.sh。脚本检查原启动脚本、6F入口与模块摘要、独立分析配置enabled严格为false，并用ps检查同应用worker/beat/flower已退出；只打印PID/角色，不打印命令或凭据。不导入应用检查任务。

若仍有同名后台，脚本停止，不擅自杀进程。进程检查保守覆盖本机tasks.task/tasks.task.app同名程序，不断言属于本项目；其他项目同名进程也需另行确认。该检查不是跨终端启动锁，不得同时执行多份启动命令。

检查通过后，以env_profile=LOCAL和ENV_PROFILE=LOCAL执行原local_run.sh，由原脚本继续管理全部子进程。run.py与模型代理不重启；不另起脱离原脚本管理的periodic worker或beat。脚本本身会启动原有全部worker和监控进程，原有任务仍可能处理；beat原有启动钩子还会派发Skills同步。本步只保证新增规则分析关闭，不能称整个重启零外部操作。

观察原后台终端：beat应出现Sending due task rule_refine_pending；worker应出现rule_refine_background，status=disabled、reason=analysis_disabled。约30秒调度一次，启动及共享worker繁忙可能延迟。两类日志一起证明真实定时派发和任务消费；独立注册测试不能替代它。贴回这两行或实际异常摘要，服务继续保留运行。

本地仅验证脚本语法、检查分支与离线夹具；未在本机启动原项目后台或连接真实broker。

内网已确认：2026-10-28 15:34:11、15:34:40两次worker收到并成功执行tasks.task.rule_refine_tick，均返回disabled/analysis_disabled/automatic_apply=false；15:34:40同时有Scheduler: Sending due task rule_refine_pending。真实定时派发及消费通过，自动分析尚未开启。下一步见../step06h_enable_analysis/。
