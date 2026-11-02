# 6S：历史建议失败的离线诊断

用户6R已成功收到一次模型回答，但输出校验返回 `invalid_model_output`；51条目标日志仅4条入选，47条被合并记作无效或过大。本步定位这两件事，不重调模型，不安装或修改应用代码，不提交建议。

打开 `DIAGNOSE-HISTORICAL-REVIEW.sh`，复制全文到内网终端执行。脚本中的 `soc_report_dir` 已填写本次目录 `~/sec-rule-refine-data/historical-rule-review-tz8x5cs4`；后续检查其他6R报告只需修改这一行。无需输入LIVE。

工具读取该目录的原回答、分析结果、上下文、请求和源码摘要。使用内嵌的已审阅纯模块，核对其与原记录的摘要；不依赖当前业务模块是否已升级。依次定位JSON解析、结构、引用、topic、单条件证据和完整组合匹配的失败位置，同时用原6R校验器离线复核一致性。不会提取多段回答、修补模型字段或放宽条件校验。

另外读取固定小PKL及CSV，验证摘要，沿用原受限反序列化器重建相同上下文。日志统计拆分非对象、空对象、无法编码和超过8KiB；完整日志不截断、不展开为新字段。只输出数量与大小分布。`eligible_alerts_not_selected`对应6R旧字段`omitted_alerts`，只表示符合日志预算后未选入的告警，不能用它表示全部目标告警的遗漏数。

末尾JSON及新建的私有目录内`diagnostic.json`只有诊断元数据；不导出告警ID、原文、字段值或模型回答。原报告和业务配置保持原样。`status=diagnosed`表示诊断执行完成，不表示建议有效；需同时看`response_check.format_valid`和`saved_result_reproduced`。PKL/CSV不可用时，保留已完成的回答诊断，日志审计单独标记不可用。

本地检查：

```bash
backend/.venv/bin/python validation/rule_refine/implementation/step06s_historical_diagnostic/test_diagnostic.py
backend/.venv/bin/python validation/rule_refine/implementation/step06s_historical_diagnostic/test_log_audit.py
backend/.venv/bin/python validation/rule_refine/implementation/step06s_historical_diagnostic/build_command.py
bash -n validation/rule_refine/implementation/step06s_historical_diagnostic/DIAGNOSE-HISTORICAL-REVIEW.sh
```

人工测试不证明内网这份回答的具体根因；待用户返回诊断JSON再确定修复。
