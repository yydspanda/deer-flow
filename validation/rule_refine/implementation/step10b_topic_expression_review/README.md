# Topic 条件表达式复核修复

本补丁基于已安装的 `step10a_topic_json_fix`。内网第二次手工测试在生成阶段报
`invalid_expression_reference`，实际一次模型请求、零次 ZEUS 提交。该错误可能表示
引用了未定义的条件，或两个条件之间缺少运算符；旧精简记录没有保留表达式，不能
确定该次具体是哪一种情况。

修复明确提示词中的表达式写法，并把完整、可读取且条件校验通过的生成草案中的
表达式问题交给原有第二轮复核。程序不补条件、不猜 `&&` 或 `||`，草案原文保持
不变；复核必须重新输出完整、合法的最终建议，未通过就不提交。仍最多两次 SDK
请求，不增加第三次修复请求，不重试 ZEUS 提交。已有规则级解析默认行为保留，
新行为只由 topic 生成阶段显式启用。

## 内网执行

1. 全选复制 [INSTALL-TOPIC-EXPRESSION-FIX.sh](INSTALL-TOPIC-EXPRESSION-FIX.sh)
   到 Mac 终端。命令校验当前源码、执行隔离离线回归，通过后备份再更新。不调用
   模型、Redis、业务数据库或 ZEUS，不启动服务，不修改监控或队列配置。
2. 安装成功后继续使用原
   [CHECK-TOPIC-REAL-CSV.sh](../step10_topic_filter/CHECK-TOPIC-REAL-CSV.sh)。新进程
   会读取已更新代码，手工测试无需先重启 worker。输入 `LIVE` 后最多两次模型
   请求，复核通过后最多一次 POST，在 ZEUS 实际创建小规则。旧失败报告保留，
   不会自动把旧结果改成成功或重新提交不确定的请求。
3. 后续若需启用后台监测，使用本目录的
   [ENABLE-TOPIC-FILTER.sh](ENABLE-TOPIC-FILTER.sh)，它仅更新源码摘要，启用逻辑
   沿用原版。安装和手工验证本身均不启用后台监测。

新的表达式诊断受大小限制，不保存整批原始告警、完整提示词或完整模型回答。
ZEUS 的启用、禁用仍由 ZEUS 管理，不能保证新增后默认禁用。离线回归验证的是
流程和校验，不证明某次建议在业务上正确，也不代表已完成新的内网实测。

## 离线维护

```bash
backend/.venv/bin/python validation/rule_refine/implementation/step10b_topic_expression_review/test_delivery.py
backend/.venv/bin/python validation/rule_refine/implementation/step10b_topic_expression_review/test_enable.py
backend/.venv/bin/python validation/rule_refine/implementation/step10b_topic_expression_review/offline_preflight.py
backend/.venv/bin/python validation/rule_refine/implementation/step10b_topic_expression_review/enable_preflight.py
```

预检在临时目录重建已安装 Step10a，执行实际粘贴安装命令两次，核对回归、备份、
目标摘要、幂等和未涉及文件不变。已交付 Step10、Step10a 的脚本与载荷保持冻结。

2026-11-05 最终安装预检通过398项回归，临时项目更新5个文件，重复安装返回
`already_installed`。安装事务9项、新摘要启用流程13项测试通过。这些离线检查
没有模型、Redis、业务数据库、ZEUS调用，也没有启动任何服务。
