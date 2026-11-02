# 6U：数字等值条件的离线复核

内网6T确认原回答有两个equal条件：第一个字符串字段得到样本支持；第二个字段在被引用2条日志及全部4条可见日志中均为number，被原字符串限定拒绝。尚不能据此确认其数字值与建议相等。

本步新增共享纯函数 `condition_values.evaluate_condition`，在内存中让原单条件检查和完整组合试算共同使用：

- 原日志字符串保持区分大小写的equal/contain/notcontain，不自动转换为数字。
- 原日志int或有限float仅支持equal；条件的`value`仍是现有JSON契约的字符串，严格符合JSON number语法才按Decimal比较。无首尾空白、前导零、非有限数、容差或先转float。数字1可等于条件`"1.0"`，字符串`"1"`仍不等于`"1.0"`。
- bool不当作数字；缺失/null/数组/对象未知，数字contain/notcontain未知。超过2^53的整值float因精度问题保持未知；大整数直接精确比较。有限float按已保存的十进制表示比较，不能恢复历史解码之前丢失的精度。
- 保留完整组合表达式逐日志检查，以及每个被引用告警至少一条日志满足组合的检查。不会跨日志拼接条件，也不会只因类型被支持就通过。

打开 `REPLAY-NUMERIC-EQUAL.sh`，复制全文到内网执行。原目录已填好，无需LIVE；不会重新调用模型、连接Redis/数据库、回调Zeus、提交建议或修改应用。原6R/6S/6T文件和旧报告保持不变；摘要、完整复核结果和运营可读报告写入新的私有目录。只回传末尾JSON，原文与字段值留内网。

本次始终使用原3条告警/4条日志，新预算的11条告警未参与复核。先验证原回答摘要、原提示词及原失败，再使用新比较语义检查同一回答。原提示词明确要求字符串字段，本次保留该事实；新增数字语义通过不代表原模型遵守了原提示词，也不证明ZEUS引擎兼容或场景可忽略。正式安装时必须同步更新新请求提示词及运行代码，当前不安装。

运行本地测试和重新生成完整复制命令：

```bash
backend/.venv/bin/python validation/rule_refine/implementation/step06u_numeric_equal/test_numeric_values.py
backend/.venv/bin/python validation/rule_refine/implementation/step06u_numeric_equal/test_replay.py
backend/.venv/bin/python validation/rule_refine/implementation/step06u_numeric_equal/build_command.py
bash -n validation/rule_refine/implementation/step06u_numeric_equal/REPLAY-NUMERIC-EQUAL.sh
```

后续先依据内网数字值及组合复核结果确定安装；正式变更需要同时更新比较函数、分析提示词、完整请求预算与新版本来源清单，避免仅改一个校验入口。原比较版本结果不覆盖。
