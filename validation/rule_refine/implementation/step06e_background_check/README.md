# 第 6 步 E：后台启动只读检查

6D 内网新增 16 项测试与演示已通过。下一步要把消费者接到现有后台执行环境；本步先确认内网启动方式，避免按照猜测的脚本名修改或重复启动进程。

## 执行

完整粘贴 `CHECK-BACKGROUND.sh`，回传最后 JSON。脚本只读文件和 `ps` 输出，不改文件、不重启、不提交业务任务，不导入 `tasks.task`、应用配置、数据库或模型模块；Redis/数据库/模型调用均为 0。

读取源码包中已有的 `tasks/task.py`、`config/celery_config.py`、`local_run.sh`、`local_run.detector.sh`，另外检查用户提到的 `run_local.sh` 是否存在。文件摘要与源码包逐字节比较；用 AST 获取导入、队列、周期任务名和函数名。启动脚本只提取 Celery 命令的应用、队列、并发和池，不执行脚本。

进程检查只输出 PID、父 PID 和选定的 Celery 参数，不打印完整命令、Redis 地址或凭据。最多展示 30 个进程。它不能证明进程属于当前项目、使用何种环境配置、worker 能连接 broker 或实际消费任务；被重写的进程标题也可能无法识别。`shell_env_profile` 仅指当前检查终端。

## 源码事实与后续方向

源码包有 `local_run.sh`，无 `run_local.sh`。前者启动 `tasks.task.app`、`periodic_queue` 的单并发 threads worker，以及独立 beat。Celery 只导入 tasks.task，beat 当前只有缓存定时任务；脚本不启动消费 default 队列的 worker。内网是否一致待本步回传。

后续优先沿用既有 Celery，暂不在 API 内另起模型循环。后台须独立加载持久配置、使用独立默认关闭的自动分析开关，并复用现有模型并发控制。当前 6B/6C 只支持 LOCAL 单机 SQLite 和文件锁，不声称可直接迁至多机环境。待处理信号发现、轮转、重试与周期调度仍需实现。

## 本地检查

在从源码 ZIP 提取的临时目录执行脚本，4 个文件摘要匹配，识别出 local_run.sh 的 10 条 Celery 启动提示，包括 periodic_queue 和 beat。源文件字节未改变，带凭据的人工命令参数未进入摘要；注释和普通 API 启动命令不会被当作 Celery。Python 3.11 与 Bash 语法通过。该结果不替代内网进程检查。

## 内网反馈（2026-10-28）

用户回传四份文件摘要全部与源码 ZIP 一致，run_local.sh 不存在；当前检查终端为 LOCAL。进程摘要中可见 tasks.task.app 的 9 个 worker 与 1 个 beat，均由6722启动，包括 periodic_queue 的并发1/threads，配置与 local_run.sh 提示相符。没有通过此检查确认这些进程的项目工作目录、环境或 broker 消费连接。下一步交付6F默认关闭的后台接入。
