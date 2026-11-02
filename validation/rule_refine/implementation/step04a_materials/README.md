# 第 4 步 A：组装规则与告警材料

内网第 4 步只读检查已确认 LOCAL 任务表完全为空，total_task_rows=0。这不影响独立材料模块的开发；本步使用人工规则、告警和已有研判样例，不向业务库写样本，也不调用模型、Redis 或业务 API。

## 操作

保持现有服务运行，在另一个终端完整复制 [APPLY-STEP04A.sh](APPLY-STEP04A.sh)。新增两个文件：

- `my_workflows/zeus/rule_refine/materials.py`
- `test/rule_refine/test_materials.py`

脚本先验证前一步规则查询契约和粘贴内容，拒绝覆盖不同版本；同内容可重复执行。随后运行 15 项 unittest 和人工演示。预期演示 status=ready、input_task_rows=4、requested_samples=3、material_samples=3、duplicate_task_rows=1、issue_count=0；model_calls/database_calls/redis_calls 均为 0。贴回测试摘要与最终 JSON 即可。

## 本步接口

`build_rule_materials(signal, rules, task_rows, *, source_kind)` 是纯函数：输入现有 v1 突增信号、共享 RuleConfigSource 和已解码任务字典，输出 `sec_know.rule_materials.v1`。配置使用共享查询契约，后续从 CSV 切换到 ZEUS 时不改组装逻辑。

人工源为 synthetic_fixture，只配 demo；workflow_task_store 只配 live/replay。来源由可信调用方指定，不能从告警任意字段取值。本步没有接入实时读取或待处理信号派发。正式数据适配器后续负责限定查询范围、读取字节上限和 JSON 解码。

- 先按 topic/ruleCode 唯一查询配置，再核对信号中的规则和公司；缺配置或歧义明确返回材料不足，提供器故障传播。资产公司不作为规则归属证明。
- 只取信号中最多 20 个告警引用，同一调用系统、alert_agent 工作流及一致的内外告警 ID；只保留匹配 topic/ruleCode 的原始日志。
- 同一告警原文一致的重复任务合并。告警原文冲突或存在无效记录时排除该告警，附问题代码；不同模型参考响应只标记参考冲突，保留一致原日志。
- 存储的 agent_response 是未经此模块验证的参考，既不是人工标签，也不证明正常业务。
- 每次最多 100 条任务；规则配置最多 512KiB，每条告警数据最多 1MiB，每样本最多 100 条、合计 64KiB 原日志，参考响应最多 16KiB。编码前限制 JSON 深度 32、节点 10000 和内容预算。超限明确报告，不默默截断成可过滤证据。
- 保留主告警内容 SHA-256、来源任务 ID、查询到的配置版本和来源。配置版本属于此次取材料时的版本；现有计数信号没有冻结配置版本，不冒称触发时快照。
- 信号窗口与 time_basis 保留，任务 created_date 不用于证明或否定接收窗口归属。材料仅覆盖触发引用样本，不代表整个窗口或主要场景。

`ready` 表示此次引用材料组装完整；`partial` 表示有可用材料但有缺失、冲突或省略；`insufficient_evidence` 表示本次没有可用原日志或配置。它们不表示建议已生成或可以修改规则。后续才做场景归纳、模型建议和条件校验，并单独限制模型上下文大小。

## 本地验证与待验证边界

15 项测试覆盖正常材料、不同来源隔离、配置缺失和歧义、错误公司、重复任务、原文/参考冲突、无日志、身份不一致、边界和超限、输入不变、时间依据及提供器故障。完整脚本首次/重复安装、拒绝未知修改、人工演示均通过。另用真实 911 条 CSV 提供器配合人工告警验证共享接口；不构成真实告警效果证据。Ruff 和 Python 3.11 语法检查通过。

内网已回传 15 项测试全部通过（0.004 秒），人工演示 ready：4 条任务、3 个引用、3 份材料、合并 1 条重复、问题 0，模型/数据库/Redis 调用均为 0。继续第 4 步 B 场景分析与建议。真实任务材料读取、入口流量登记、后台信号派发和实际模型建议仍待接入或验证。
