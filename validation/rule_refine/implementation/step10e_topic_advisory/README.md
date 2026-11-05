# 简化为运营审核的小规则建议

按用户最新澄清，模型根据突增样本的共性提出一条小规则，运营负责审核、采用或驳回。
通常组合2～3个有依据的条件；完整命令等单条件已经表达具体场景时，不为凑数加条件。
不要求模型证明整个topic、所有未来事件或其他检测场景都安全；缺少正常标签或其他检测对照不单独否决建议。

只修改 `my_workflows/zeus/rule_refine/topic_tree_ai.py` 的提示词、末尾提醒与提示版本，增加一个回归测试文件。
新的提示版本是 `topic-before-detection-advisory-v2`，条件树版本仍为 `topic-condition-tree-v1`。
解析、编译、字段/引用检查、模型调用、NAS精简存储及ZEUS接口保持原逻辑。
复核检查原文依据、组合是否表达该重复场景及说明与条件是否一致，简短指出需运营确认的事项。
不预设topic字段、不加入固定条件数门槛、不在代码中自动补条件。

用户示例是表达方式参考，不直接作为本批日志证据：

- 使用 `Eq/Ne/Contains/NotContains/StartsWith/EndsWith`，不恢复旧操作符拼写。
- 使用实际topic原拼写，不推导STG topic或替换连字符/下划线。
- `executeType` 仍由当前告警材料确定，不照搬示例的 `[0,3]` 或 `excuteType` 拼写。
- 规则名、命中结果等字段边界保持此前用户确认的设置。

## 内网操作

复制 [INSTALL-AND-CHECK-TOPIC.sh](INSTALL-AND-CHECK-TOPIC.sh) 全部内容到内网终端。
它先核对源码、执行离线回归，通过后备份并更新2个文件；安装失败会停止，不进入试测。
随后使用原真实CSV工具预览；输入 `LIVE` 后最多2次SDK请求，复核通过后最多1次真实ZEUS提交。
提交后由ZEUS和运营管理，默认启用状态未知。客户端不自动重试提交。
手工试测是新进程，无需先重启worker；命令不启动服务，不开启监测，不修改队列或STG配置。
重跑试测会新做模型分析，相同目标/条件沿用现有提交回执去重。

单独命令也保留：[安装](INSTALL-TOPIC-ADVISORY.sh)、[试测](CHECK-TOPIC-REAL-CSV.sh)、
[后续显式启用](ENABLE-TOPIC-FILTER.sh)。启用命令仅更新本版本摘要，行为与原版一致；此次安装不会执行它。
旧10D诊断命令固定旧源码摘要，不能用它验证新提示版本；旧诊断结果和已交付文件保持不变。

## 验证

435项离线回归通过；实际安装Shell在临时项目首次安装/重复安装通过，目标摘要、备份、未目标文件保持验证通过。
其中新增6项使用人工响应验证跨topic单/双/三条件与嵌套OR、旧提示版本隔离、复核上下文及executeType来源。
这些检查没有调用真实模型、Redis、业务数据库或ZEUS，不证明真实模型建议质量。
两阶段正式判断仍由模型提出，运营负责是否采纳；不能把格式ready称作已证明业务无害。

```bash
backend/.venv/bin/python -B validation/rule_refine/implementation/step10e_topic_advisory/offline_preflight.py
```

Step10/10A/10B/10C/10D交付文件冻结；本次不重新解释、重跑或提交已有诊断建议。
