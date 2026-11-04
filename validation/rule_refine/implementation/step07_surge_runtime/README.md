# 第7步：后台突增建议与 DEV 历史对照

这一步把6Z实测中使用的原文取样、独立初次分析、JSON模式和AI复核接入内网 `sec_know_model` 的既有后台。安装目标是内网Mac的 **LOCAL配置（用户的DEV开发环境）**；不是 DeerFlow Runtime，也不配置远端PRD/STG数据库。

内网已回传安装和补数成功：147项测试、18文件更新、当前6条/历史20条召回通过。LIVE工具在确认前遇到终端 `UnsupportedOperation`，本次SDK为0；使用[第7A步修复并继续脚本](../step07a_terminal_confirmation_fix/FIX-AND-CONTINUE.sh)，无需重做安装或补数。下面保留首次交付步骤。

最新内网结果：终端修复后已执行 `surge-dev-history-live-_uyz_4fk`，`received/proposal`、两阶段完成且 `recommendation_ready=true`，本次模型调用2次，当前6条/历史20条，原业务库没有新增写入。job_id为 `ee8924976ab4600c13b09a8a18da2d9be0324e485bfee5ccf253934a70c84542`，record_id为 `9f237c8ed37b2a40a92739935c7ea8242ad3d4bdf4d2e5f34170bf5da6142e4f`；建议正文已回传，模型明确对比当前6条与历史20条的task_source，推荐 `task_source equal planned_inventory`。该次不通过Celery派发，不证明真实业务历史或降量效果。

预览中的 `reported_rejected_input_tokens=336423` 是2026-11-03旧失败的来源记录；本次没有token报错。本地配置的 `context_tokens` 仅决定估算取样预算，不作为 `max_input_tokens` 发送到API；SDK不设置输出token额度。实际用量在每阶段 `response.json` 的 `transport.usage`，不把预算、旧拒绝数量或UTF-8估算当成本次实际token。

正文核对：首轮3条当前+20条历史，复核6条当前+20条历史；最终条件采用实际字段，说明与JSON一致。条件作用于所有 `planned_inventory`，比本次单个巡检命令更宽，因此确认人工差异对照功能通过，不据此批准真实业务范围。

报告中的“receipt_window不完整”来自工具 `current_receipt_window_complete=false` 标记。本次是人工手工验证，没有实时收件窗口完整性证明；这不等于6条当前原文缺失（`all_current_target_logs_retained=true`）。这里应解释为“实时窗口完整性未验证”，不要把保守标记当成已确认的材料丢失。记录此展示歧义，保留原模型回答，不为修改措辞重调模型。

## 内网执行

1. 在运行 `local_run.sh` 的终端按一次 Ctrl+C，确认旧worker退出，避免安装期间混用两个版本。模型代理保持运行。
2. 打开 [INSTALL-COMMAND.sh](INSTALL-COMMAND.sh)，复制全文到内网Mac终端执行。先在隔离目录执行离线测试，随后校验现有6W文件版本、备份并安装。不会调用模型或修改业务数据库。安装结果应为 `installed` 或 `already_installed`。
3. 复制 [CHECK-DEV-HISTORY.sh](CHECK-DEV-HISTORY.sh) 全文执行。只在已有项目SQLite补充人工测试材料并检查历史召回，不创建数据库/表。首次 `inserted=42`，再次 `reused=42`；应为 `status=passed`、当前6条、历史20条。
4. 按原方式启动一次 `local_run.sh`。没有待办时仍显示 `idle`。新信号进入两阶段流程；已有被暂停的旧任务不会自动重发。
5. 要验证真实模型能读取DEV历史并完成两阶段流程，复制 [CHECK-DEV-LIVE.sh](CHECK-DEV-LIVE.sh)。它先只读预览，再由终端的 `LIVE` 明确触发最多2次SDK请求；代理内部可能重试。沿用 `rule_refine.analysis.local.json` 选择的模型（本机已选 `Pub_DeepSeek_V4_Flash`），不改模型路由。

第五步使用42条中召回的 **6条当前人工告警、20条历史人工告警**，不是2205条真实net1样本。它验证数据库召回及两步模型串联；不能据此声称真实业务效果、Celery派发或线上降量通过。每次再次输入LIVE都会新建验证任务。无需为诊断已保存结果重跑模型。

真实后台使用原任务表中已有的告警原文。DEV没有真实历史时会明确返回0条历史；不会自动把人工fixture当作真实规则的对照。合成数据有独立企业/topic/规则/ID，不进入真实计数器或规则CSV。

## 当前与历史如何选择

- 当前：读取Redis待办信号所指收件窗口 `(start,end]` 中仍保留的全部ID，再从任务表取原文。不是只取信号里的最近20个ID。当前材料按这些ID精确选择。
- Redis查询是读取时的集合。同一毫秒新收件可能增加ID，报告单列差额，`frozen_signal_membership_verified=false`；不冒称还原触发瞬间的精确集合。保留期导致数量不足或引用缺失时停止该信号，不静默缩成完整批次。
- 对照边界：当前原文里最早的 `alert.createAt`。明确标记 `cutoff_basis`，不把它当成已证明的真实突增起点。
- 历史：同企业/topic/规则、严格早于边界的最近20个不同原始告警；可以跨几天，没有前1小时下界。相同ID同内容去重、冲突隔离；不使用任务插入时间、导入时间或历史模型回答充当证据。
- 只在有突增待办时，使用一个只读SQLite快照扫描相关workflow任务，不在每条告警入口做全库聚类。当前实现面向LOCAL验证；大库的耗时与索引方案需实测后再推广。
- 按时间分散选择完整告警；AI可请求按实际字段补看含/不含某文本的原文。第二阶段使用新上下文、引用原文、新选当前材料及共用历史。记录可用数量和模型实际可见数量；不执行filter，不计算覆盖率。

## DEV补数的边界

42条任务包括32条历史、6条当前及4条干扰记录（不同规则、不同topic、未来和重复）。历史每隔6小时，当前集中在5分钟；原始告警时间与任务插入时间不同。当前为人工 `inventory-probe` 巡检，历史为多种人工交互命令。

脚本直接写入已有 `uas_app_workflow_task_status`，不提交 `/workflow/task`，不派发Celery、不回调ZEUS、不访问Redis。测试记录标记为终态以避免原任务重试扫描；`agent_response`为空，不冒充完成过真实研判。固定任务ID、事务和内容核对保证重复执行幂等；发现内容变化或冲突会停止并回滚写入。已有真实数据保留。

可选清理只删除仍完全匹配的42条自有记录：

```bash
cd "$HOME/sec_know_model"
env env_profile=LOCAL .venv/bin/python test/rule_refine/check_surge_dev_history.py --cleanup
```

## 配置、结果与恢复

新增 `config/rule_refine.surge.local.json`：

```json
{
  "enabled": true,
  "context_tokens": 262144,
  "input_fraction": 0.8,
  "timeout_seconds": 600,
  "operator_context": ""
}
```

输入预算是可改的策略值，默认262144×80%=209715估算tokens，来自当前路由曾返回的容量错误，不是不可修改的模型定律。平台确认更大容量后可调 `context_tokens`；例如1000000对应800000。UTF-8估算不是精确token计数。**SDK不设置 `max_tokens`、`max_completion_tokens` 或其他输出token额度。** 32MiB请求/40MiB响应是序列化结构保护，不是模型token容量。

这个配置只控制新突增流程；旧6W的128KiB/64KiB配置继续属于旧流程，不再限制新流程。已有模型选择、监测配置和旧限额文件不覆盖。新配置已存在时先验证并原样保留；缺失或 `enabled=false` 时保留原后台路径。若只想暂停自动分析，仍使用既有 `rule_refine.analysis.local.json` 的总开关。保存文件需要重启加载新代码；随后配置按每次tick读取。

通常不需要填写全局 `operator_context`。它适用于所有规则，不能把某一批“已确认巡检”的结论填成全局背景。

正常结果位于原后台 `state_dir/<namespace摘要>/processing_surge_v1/jobs/<job_id>/`：

- `input.json`、`case.json`：固定信号、规则版本、原始材料和历史来源。
- `generate/`、`review/`：实际请求、开始标记、原始回答、传输统计、解析结果。
- `REPORT.md`、`result.json`：可读报告、结果和文件摘要。
- `recommendation.json`：有建议时保存；`delivery_scope=local_record_only`，使用独立 `sec_know.rule_surge_recommendation.v1`，尚未映射ZEUS接收接口。

`received`表示两阶段完成且本地记录可校验；`not_applicable`表示复核后暂无建议或证据不足；`manual_review`保留待调整字段或规则变化；`blocked`表示请求/结构等失败；`materials_pending`等待当前原文；`analysis_unconfirmed`表示请求开始后未保存到确定回答。成功或待人工结果的完整证据校验通过才确认Redis待办。收到结果后重试复用记录，不重复请求；未确认的调用也不自动重发。

模型返回的JSON结构、引用和可见字段检查不证明业务结论正确。即使 `recommendation_ready=true`，仍是待运营采纳的建议；不自动发布规则、不运行程序过滤试算。后台继续使用原模型信号量及熔断器。

安装备份在 `$HOME/sec-rule-refine-data/surge-runtime-install-*`，记录变更前内容和摘要。安装中失败会尝试回滚并保留并发修改；安装器不会自动停止或启动服务。

## 本地验证

```bash
backend/.venv/bin/python validation/rule_refine/implementation/step07_surge_runtime/run_payload_tests.py
backend/.venv/bin/python validation/rule_refine/implementation/step07_surge_runtime/test_install.py
backend/.venv/bin/python validation/rule_refine/implementation/step07_surge_runtime/build_command.py
bash -n validation/rule_refine/implementation/step07_surge_runtime/INSTALL-COMMAND.sh
```

当前147项流程/回归测试和26项安装器测试通过，共173项。另用上传ORM同结构的临时SQLite完成6W基线安装、重复安装、42条补数、重复补数、6/20召回、LIVE前只读准备和定向清理；没有模型/Redis调用。Python3.11语法、Ruff和三份Bash脚本检查通过。

测试使用临时SQLite、替身SDK/Redis及人工材料。包括历史排序/去重/冲突/大日志、两阶段请求与恢复、输出截断、内容持久化、ACK超时复用和证据篡改拒绝、后台接线、补数幂等和安装回滚。内网安装及DEV补数召回已通过；终端问题修复后，两阶段真实模型人工验证也已回传通过；建议正文的人工差异对照已核对；真实业务范围与效果仍待验收。
