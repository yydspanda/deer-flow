# 第 3 步 F：给已有 API 运行项启用监测

用户第 3 步 E 回传：7 项路由测试 OK（0.007 秒），末尾 JSON passed，hook_installed=true，monitor_enabled=false，无 Redis/数据库/模型调用，real_traffic_tested=false。16 项监测模块测试摘要未单独贴出；完整脚本会先执行它再执行路由测试，不要求重复跑全部步骤。

用户描述当前已运行 run.py、start_proxy.sh、run_local.sh；第三个名字尚不确定，源码包名称为 local_run.sh。此步只改 run.py 的运行配置并重启 API，不启动或重启模型代理和后台 worker。

## 启动入口证据

- 源码 ZIP 的 run.py 是 API 入口，使用 util.root_config.SERVER_PORT（包内为 8090），监听 0.0.0.0。
- app.py 是 Gradio 页面（8080）。local_run.sh 启动 Celery workers、beat、Flower，未启动 API。
- run.sh 是容器启动脚本，含初始化、清理积压任务和 STG 注入验证任务，不用于此步。
- root_config 读取 env_profile，默认 LOCAL；没有有效的 dotenv 加载。因此应配置真正启动 API 的进程环境，保留原运行项的其他环境变量。
- run.py 启动会加载原项目嵌入模型和 Milvus 等依赖，不称作“无模型加载/无外部连接”。本步不主动发送告警或发起 LLM 研判。

## IDE 启动

在原 run.py 运行配置中追加以下三项，保留原解释器、工作目录及 env_profile 等其他变量，然后停止并重新运行该配置：

```text
RULE_REFINE_MONITOR_ENABLED=1
RULE_REFINE_CSV_PATH=/Users/zhangjianming627/sec-rule-refine-data/rules.csv
RULE_REFINE_NAMESPACE=sec-know-rule-monitor-mac-zhangjianming627-v1
```

不需要把这三项加到代理或 Celery worker 的配置中。命名空间是本台 Mac 的稳定统计隔离值，重启时保持相同，不作为人工填写的分析编号。

## 终端启动

在原来运行 run.py 的终端停止 API，等待退出，然后复制 [ENABLE-TERMINAL.sh](ENABLE-TERMINAL.sh)。在同一环境下执行，避免丢失原有 env_profile 或其他启动变量。已有端口监听时脚本停止，不自动杀进程。

脚本将开关传给随后启动的 run.py，预检只加载 CSV 和调用 Redis PING，不创建测试告警或计数键；预检输出不是实际流量已接通的证明。完整脚本不持久修改 .env 或项目配置；以后重新启动仍需带入这三个变量。

## 查看实际接收结果

正常告警到达后，在 run.py 控制台或对应环境日志中查找 `rule_refine_monitor`。LOCAL 日志路径为 `$HOME/sec_know_model/nas/log/info.log`；其他环境沿用既有日志位置。正常日志包含 status、counted_rules、added_rules、duplicate_rules、pending_signals、rule_providers、request_key。

尚无告警则没有这类日志，不为验证而随意向 /workflow/task 发送人工告警，因为原接口会继续派发研判。此时只回传启动就绪或预检信息，真实接收验证保持待完成。

遇到 degraded/partial，贴相应结构化行定位，不将其当全量计数成功。当前功能登记数量和保存突增信号，后续才增加场景分析和建议生成。

关闭方式：在原 API 运行配置将 RULE_REFINE_MONITOR_ENABLED 改成 0，重启 run.py；已经累计的隔离 Redis 数据不因关闭而删除。

## 内网回传与后续替代

用户已在终端完成预检：LOCAL/8090、911 条、Redis PING 成功，2026-10-27 17:48:12 API 启动完成。尚无实际接收登记日志。用户要求以后直接 python run.py；当前继续 [第 3 步 G 保存本地配置](../step03g_persistent_settings/README.md)，完成后不必再使用临时环境变量启动脚本。
