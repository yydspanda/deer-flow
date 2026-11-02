# 第三步：告警入口与字段核对

第 1 步 A、第 2 步 A、第 2 步 B 已获内网验证。开始去重计数前，用原项目源码 ZIP 定位入口，再与内网实际版本核对。

用户已完成本步：8 个文件全部与 ZIP 一致；内网 redis 5.0.5、SQLAlchemy 2.0.22、celery 5.4.0。下面保留检查命令备查，当前继续 [第 3 步 A：提取规则观察](../step03a_observations/README.md)。真实验证数据来源仍待确认，先用人工样例验证独立模块。

## 当前执行

将 [INSPECT-STEP03.sh](INSPECT-STEP03.sh) 整段复制到内网 Mac 终端执行，并贴回输出。脚本只读取 8 个源码文件，比较 SHA-256，输出目标类字段名和 redis/SQLAlchemy/celery 已安装版本。不读取环境配置或原始告警，不导入应用，不连接数据库、Redis 或模型，不修改文件。

“与源码包不同”表示需要按实际文件继续定位，不要求用户回退、更新或重新部署。校验值来自 `validation/sec_know_model.zip`，只用于此次源码对照，不属于运行时版本限制。

用户另需确认可用验证数据：已有导出 JSON、项目任务数据库，或者先用人工样例。尚未获得真实数据时，不宣称真实突增统计已完成。

## 从源码 ZIP 确认的事实

以下行号均指 ZIP 内原项目文件，不是 DeerFlow Runtime：

| 位置 | 事实及影响 |
|---|---|
| `service/routers/workflows.py:32–46,135–156` | `POST /workflow/task` 接收 `WorkFlowTaskInput` 并创建工作流任务，是拟议接入位置 |
| `service/io_models.py:674–680` | 请求包含 app_code/flow_id/session_id/alert_id/alert_data，alert_data 可为空 |
| `model/db/crud/workflow_task.py:242,261–282` | 每次提交生成新任务 ID；先派发 Celery，再添加数据库任务行 |
| `model/db/session.py:33–44` | 装饰器在函数返回后提交事务；异常回滚 |
| `model/db/orm_models.py:292–311` | 任务表保存原始 alert_data、状态和时间，只有任务 ID 为主键；同告警重推可能多行 |
| `tasks/task.py:296–348,485–569` | 排队超时、模型失败或重试影响执行结果；不能据成功研判数统计接收量 |
| `my_workflows/zeus/flows/alert_schema.py:1223–1231` | 规则身份在 hitLog 的 topic/ruleCode，应遍历所有命中项并去重 |
| 同文件 `:1288,1338–1342` | profileCode 不是 hitLog.ruleCode；relatedAlertList 不是本次新增的接收事件 |
| `util/redis_client.py:15–25` | 已有 get_redis 连接池可复用，避免另配一套服务 |

## 下一步实现约束

- 在接收入口、模型排队之前观察告警。观察失败可见，但监测不应把原研判链路变成不可用；具体降级和补偿在接入时测试。
- 同企业作用域、topic、rule_code、alert ID 去重，重复发送和模型重试不增量；多个命中规则分别计数，但不将各规则数量相加声称独立总量。
- 内外告警 ID 一致性、alert_data 缺失、多命中、接收时间与原事件时间需明确处理。
- app_code 在接口中是子系统编码，不能未经确认映射为配置 company_code；实际请求缺少企业字段时，需要明确解析规则，不能对歧义配置取第一条。
- 任务数据库只代表留存任务，可用于有来源说明的历史回放。它可能遗漏派发或事务失败的接收请求，不能替代完整实时入口计数。
- CSV 提供规则配置，不提供告警量。实时、回放、人工样例分别标注，监测阈值独立于上游规则引擎阈值。

当前交付只核对接入依据；尚未接入真实请求、运行计数器、调用模型或连接 ZEUS。
