# Topic 过滤建议与 ZEUS 提交

本步骤面向内网 `sec_know_model`，基于已安装的第9步操作符更新和9A完整回答修复。
发现某条规则突增后，仍按该规则召回当前日志及此前最近20条历史；生成的候选改为
针对整个 topic 的检测前过滤。复核通过的建议直接调用现有 `syncSmallRule` 客户端提交。
新增后的启用、禁用由 ZEUS 管理，本项目不宣称接口新建时默认禁用。

## 行为与保存

- 生成和复核使用两次独立模型请求，复核检查 topic 范围影响。触发规则只负责定位证据，
  其原有条件不会隐式限制新小规则。没有其他规则的完整材料时如实标记，不能声称已验证全 topic。
- 沿用已审阅的字段用途检查，命中规则名、编号等检测结果不能成为条件；不写死每个 topic 的字段。
- `executeType` 由程序从本次原始告警读取，不交给模型猜，也不根据 PRD/LOCAL 运行环境填写。
  当前材料缺失、无效或混有多种类型时保留待确认结果，不提交、不默认扩大为 `[-1]`。
- 网关请求是外层规则数组，规则内部使用顶层 `A/B/...`、`topic/expression/executeType`，
  不增加 `items` 或 `terms` 包装；六种新操作符和已有签名客户端继续使用。
- 生产保存使用 `root_config.DATA_DIR / "rule_refine/topic_filters" / <命名空间摘要>`；
  按现有 PRD 配置位于 `/mnt/nas_prd/glm/nas/data/rule_refine/topic_filters/` 下。
  每任务保存候选、摘要、少量告警引用、短字段片段和提交回执，另有用于防重复的交付记录。
  不保存整批 `case.json`、全量历史原文、完整模型提示词或原始模型回答，不新增数据库表。
  单份结构化记录限制64KiB；这不是整个任务目录的总大小承诺。
- 同一交付范围内相同条件重复到达时复用成功回执；提交开始后超时、断连或进程中断标为
  `submission_unknown`，不盲目再 POST，需到 ZEUS 按记录核查。成功保存不等于 ZEUS 已接收，
  ZEUS 返回成功也不表示本项目已验证规则启用状态或实际降量。
- topic 配置关闭时保留原来的规则建议路径。STG不启用这个新路径；既有模型队列、并发和路由不变。

## 内网按顺序执行

1. 全选复制 [INSTALL-TOPIC-FILTER.sh](INSTALL-TOPIC-FILTER.sh) 到 Mac 终端。
   默认项目是 `$HOME/sec_know_model`，可通过 `SEC_KNOW_MODEL_ROOT` 指定其它目录。
   命令先校验已安装源码、执行隔离离线回归，通过后备份更新；不启用监控、不启动服务，
   不调用模型或 ZEUS。未知源码版本明确停止，不覆盖用户改动或降级到旧基线。
2. 全选复制 [CHECK-TOPIC-REAL-CSV.sh](CHECK-TOPIC-REAL-CSV.sh) 手工验证同一份真实CSV。
   先预览，输入 `LIVE` 才允许最多2次SDK模型请求和1次真实小规则提交；复核未通过不提交。
   工具复用现有导出材料读取流程，保留原ID和时间、使用临时SQLite，不给业务表补数。
   该命令沿用项目当前 ZEUS 目标，LOCAL 不代表一定连接 ZEUS 测试环境，执行前看预览目标。
   历史缺口和事后规则条件仅内存模拟的限制继续披露。
3. 确认手工结果后，再全选复制 [ENABLE-TOPIC-FILTER.sh](ENABLE-TOPIC-FILTER.sh)。
   它创建/更新 `config/rule_refine.topic.json`，备份后设置下次启动使用的配置，不启动服务。
   LOCAL沿用已有规则CSV、模型和输入预算；首次命名空间为 `topic-v1:local`。
   PRD首次需通过 `SEC_TOPIC_CSV_PATH` 指向现有规则CSV，命名空间为 `topic-v1:prd`；
   该工具不会假设生产NAS上已有 `rules.csv`。已配置的命名空间保持不变。

生产启用前可在当前终端设置，例如以下路径中的CSV需要换成实际已有文件：

```bash
export env_profile=PRD
export SEC_KNOW_MODEL_ROOT=/usr/src/app
export SEC_TOPIC_CSV_PATH=/实际已有的规则配置.csv
export SEC_RULE_REFINE_DATA_ROOT=/mnt/nas_prd/glm/nas/data/rule_refine/install_backups
```

随后复制启用命令。PRD启用命令只会把 `run.sh` 中唯一且精确的
`export RULE_REFINE_MONITOR_ENABLED=0` 改为 `1`，保留其它字节并备份。
不识别的标记、冲突的环境覆盖或STG环境会停止；当前进程继承的关闭值不会被假称已改变。
下一次按正常发版流程重启后，新配置才进入已有入口监测及 periodic worker/beat。
仅复制安装命令不会解除生产关闭标记。
独立手工测试和启用命令优先使用项目 `.venv/bin/python`，生产容器没有该目录时使用 `python3`。

## 离线验证与交付

```bash
backend/.venv/bin/python validation/rule_refine/implementation/step10_topic_filter/test_delivery.py
backend/.venv/bin/python validation/rule_refine/implementation/step10_topic_filter/test_enable.py
backend/.venv/bin/python validation/rule_refine/implementation/step10_topic_filter/offline_preflight.py
backend/.venv/bin/python validation/rule_refine/implementation/step10_topic_filter/enable_preflight.py
backend/.venv/bin/python validation/rule_refine/implementation/step10_topic_filter/manual_preflight.py
```

安装预检在临时目录还原用户当前09A已安装版本，执行实际粘贴命令两次，核对幂等、目标摘要、
未涉及文件不变及监控未启用。启用预检分别在临时LOCAL/PRD项目执行两次实际启用命令，验证
配置、生产启动标记范围及幂等。测试使用人工数据和替身，不连接模型、Redis、ZEUS或业务数据库。
手工命令预检执行实际Shell包装，以人工预览和取消确认替身验证模块装载及退出路径，并禁止网络/数据库调用。

2026-11-05安装离线预检通过361项回归，临时项目更新16个文件，重复安装为
`already_installed`；目标摘要、未涉及源码和未启用监控的状态均核对通过。
独立安装事务10项、启用配置12项、手工入口13项测试通过；LOCAL/PRD实际启用粘贴命令各执行两次，
确认幂等且生产启动脚本仅改变监测标记。以上仅为离线验证，没有生产服务或远端引擎验收结论。

生成器只维护本步骤三个命令和manifest；旧步骤的已交付源码、安装载荷、历史报告保持不变。
源ZIP只在本地核对，完整私有项目、凭据和真实日志不进入交付包。
