# Topic 回答重复 JSON 键修复

本补丁基于已安装的 `step10_topic_filter`。首次真实测试在生成阶段发现
`duplicate_json_key`，没有进入复核、没有创建 ZEUS 小规则。旧版精简记录只保存了
错误码和回答摘要，不能据此确认重复的是哪个字段，或重复值是否相同。

本补丁保留既有校验，补充有限的重复键诊断；提示词明确要求每个键只出现一次。
重复键只有在其 JSON 值完全一致时才允许折叠。值或类型不同的重复键继续阻断，
不会采用后值覆盖前值，也不额外调用模型修复。正常的字段、引用、范围及两阶段复核
检查仍然执行；安装通过不代表某次模型建议在业务上正确。

## 内网执行

1. 全选复制 [INSTALL-TOPIC-JSON-FIX.sh](INSTALL-TOPIC-JSON-FIX.sh) 到 Mac 终端。
   它校验当前 Step10 源码、运行隔离离线回归，通过后备份再更新。不会重新补数，
   不调用模型、Redis、业务数据库或 ZEUS，不启动服务，也不修改监控启用配置。
2. 安装成功后，继续复制原来的
   [CHECK-TOPIC-REAL-CSV.sh](../step10_topic_filter/CHECK-TOPIC-REAL-CSV.sh)。
   新进程读取修复后的源码，无需为了手工测试先重启 worker。该命令会创建新结果目录，
   保留旧失败报告；输入 `LIVE` 后最多两次 SDK 请求，复核通过后最多一次实际 ZEUS
   小规则提交。它不会把旧失败回答补成成功，也不会自动重新提交不确定的请求。
3. 日后需要启用后台监测时，使用本目录的
   [ENABLE-TOPIC-FILTER.sh](ENABLE-TOPIC-FILTER.sh)。它仅更新了已安装源码摘要，
   配置和启用流程沿用 Step10；原 Step10 启用命令会因源码摘要已改变而停止。
   安装或手工测试本身均不启用后台监测。

新的诊断记录只保存受限的键位置、数量和摘要，不保存整批原始日志、提示词或完整
模型回答。ZEUS 新增后的启用和禁用仍由 ZEUS 管理，不保证默认禁用。

## 离线维护

```bash
backend/.venv/bin/python validation/rule_refine/implementation/step10a_topic_json_fix/test_delivery.py
backend/.venv/bin/python validation/rule_refine/implementation/step10a_topic_json_fix/test_enable.py
backend/.venv/bin/python validation/rule_refine/implementation/step10a_topic_json_fix/offline_preflight.py
backend/.venv/bin/python validation/rule_refine/implementation/step10a_topic_json_fix/enable_preflight.py
```

预检会在临时目录还原已安装 Step10，再执行实际粘贴命令两次，核对回归、备份、
幂等、目标摘要和未涉及文件不变；不修改工作中的 legacy 项目。已交付 Step10 载荷、
脚本和历史报告保持不变。

2026-11-05 最终安装预检通过381项回归，临时项目更新4个文件；重复安装为
`already_installed`。安装事务7项、使用新源码摘要的启用流程13项测试通过。
这些验证均未调用模型、Redis、ZEUS或业务数据库，没有启动任何服务。
