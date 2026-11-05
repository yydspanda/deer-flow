# Topic 条件结构与字段说明统一

本补丁基于已安装的 `step10b_topic_expression_review`。前一版复核仍出现了条件定义、
expression 与字段说明不一致。此次调整模型输出结构：每个条件随附字段用途及原文依据，
多个条件通过 `all`（全部满足）或 `any`（任一满足）组合。模型不再同时维护 A/B 定义、
另一份表达式和另一份字段说明清单。

程序仅按该结构编译 ZEUS 的顶层 A/B 条款和 expression，保持原有逻辑；不猜测 AND/OR、
不丢弃未匹配条件，也不根据正文补充条件。生成与复核均使用新结构，复核仍独立检查
整个 topic 的实际过滤范围。条件无法校验时不提交；这不构成业务无害性或引擎兼容性保证。

原规则级流程、历史已保存结果和旧手工工具保持兼容。新后台 topic 路径和本目录的新
手工命令显式选择 `topic-condition-tree-v1`，已完成旧记录不会被重新解释或重跑。

## 内网操作

1. 复制执行 [INSTALL-TOPIC-CONDITION-TREE.sh](INSTALL-TOPIC-CONDITION-TREE.sh)。
   先校验源码并执行隔离离线回归，通过后备份再更新。不调用模型、Redis、业务数据库
   或 ZEUS，不启动服务，不改变队列、STG或监控启用配置。
2. 安装后使用本目录的 **新** [CHECK-TOPIC-REAL-CSV.sh](CHECK-TOPIC-REAL-CSV.sh)。
   手工新进程无需先重启 worker；预览显示 `ai_contract: topic-condition-tree-v1`。
   输入 `LIVE` 后最多两次 SDK 请求，复核通过后最多一次真实 ZEUS 小规则提交。
   旧版同名 CHECK 仍使用旧输入输出约定，本次验证必须使用这里的新命令。
3. 如后续需要启用后台监测，使用本目录的
   [ENABLE-TOPIC-FILTER.sh](ENABLE-TOPIC-FILTER.sh)。该命令沿用原配置逻辑，仅更新
   安装摘要，不启动服务；安装或手工验证本身不启用监测。

ZEUS 接口仍收到批量列表和顶层 A/B 条款，没有 `items`/`terms` 包装。executeType
仍来自全部当前告警，模型不决定它；NAS精简存储、两次模型请求与最多一次POST、
未知回执不自动重试、旧报告保留等边界不变。启停由ZEUS管理，不能保证默认禁用。

## 离线维护

```bash
backend/.venv/bin/python validation/rule_refine/implementation/step10c_topic_condition_tree/test_delivery.py
backend/.venv/bin/python validation/rule_refine/implementation/step10c_topic_condition_tree/test_enable.py
backend/.venv/bin/python validation/rule_refine/implementation/step10c_topic_condition_tree/test_manual_runner.py
backend/.venv/bin/python validation/rule_refine/implementation/step10c_topic_condition_tree/offline_preflight.py
backend/.venv/bin/python validation/rule_refine/implementation/step10c_topic_condition_tree/enable_preflight.py
backend/.venv/bin/python validation/rule_refine/implementation/step10c_topic_condition_tree/manual_preflight.py
```

这些检查使用临时项目及人工回答，验证安装事务、手工确认、真实解析和回执链路。
不调用模型或外部服务，不证明新模型回答的业务正确性；仍需内网手工复测。
已交付 Step10/10A/10B 的脚本和载荷保持冻结。

2026-11-05 最终安装预检通过429项回归，临时项目更新6个文件；重复安装为
`already_installed`。安装事务9项、启用流程13项、新手工命令17项检查通过，
其中包括两轮人工条件结构经真实解析后编译成flat条款并复用提交回执链路。
实际粘贴手工命令的取消预检也通过。上述检查均无真实模型或外部服务调用。
