# 定时检查改为 120 秒

打开 [SET-POLL-120S.sh](SET-POLL-120S.sh)，复制全文到内网 Mac 项目终端执行。
默认项目为 `$HOME/sec_know_model`，可用 `SEC_KNOW_MODEL_ROOT` 指定其他目录。

命令只调整 `config/celery_config.py` 的 LOCAL/PRD 两处
`rule_refine_pending.schedule`，并同步 `test/rule_refine/test_topic_beat.py` 的期望值。
先在临时目录运行现有隔离测试，通过后备份、原子写入；重复执行不会重复修改。
校验的是监测注册片段，片段之外的队列配置和其他改动原字节保留。

STG、其他定时任务、突增数量和增长倍数、窗口、模型并发及 NAS 保存路径不变。
`expires=25` 是尚未被 worker 接收的任务的过期时间，保持原值。
120 秒约每小时 30 次、每天 720 次；911 个范围每轮最多扫描256个，
空闲情况下约8分钟轮转覆盖，实际分析耗时可能延长等待。

脚本不启动或重启服务，不连接模型、Redis、业务数据库或 ZEUS。
修改后须随项目发布并让 **Celery Beat 重新加载**；只重启 periodic worker 不够。
本地生成交付文件和离线验证不代表生产配置已经生效。
不要重跑旧的完整发布准备脚本来覆盖本增量；旧交付保持冻结。

离线验证已通过：5项安装检查（含失败前不写入、失败回滚、无关字节保留、完整Shell与幂等），
每次安装前运行4项现有调度隔离测试；另对当前ZIP中的两个实际文件验证完整Shell首次与重复执行。
运行本目录验证：`python3 validation/rule_refine/implementation/step10i_poll_interval/test_install.py`。
