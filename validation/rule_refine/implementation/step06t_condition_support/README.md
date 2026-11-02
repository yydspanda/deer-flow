# 6T：条件支持检查与完整请求预算预览

6S内网确认：原回答通过JSON、结构、样本引用和topic检查，失败在`individual_conditions / condition_not_supported`。51条日志都能正常编码，其中47条仅因超过工具8KiB上限被省略，最大10286字节；没有空日志、非对象或编码无效日志。用户确认平时单次告警输入加输出约100KB**文本**，不是100K tokens。

本步分开检查两件事：

- 原回答逐条件检查，仅使用原模型看到的4条日志，区分字段缺失、字段类型不支持、字符串比较不成立、只在未引用的可见样本成立。嵌套位置和字段大小写差异只提供提示，不自动展开、转换或修改建议。终端不显示原字段名、值、告警ID或模型文字。
- 离线预览新取样：取消单日志8/16KiB资格限制，使用完整序列化messages的UTF-8字节预算。默认输入128KiB，可在命令开头修改为64～512KiB；这是工具预算，并非模型上下文上限或token数。保留完整日志及源序；超过预算整条省略，分别记格式无效与预算省略。仍保留最多20告警、每告警3日志、前200行检查和源数据冲突排除。预算增加不代表50条全部进入模型。

打开 `CHECK-HISTORICAL-CONDITIONS.sh`，复制全文在内网终端执行。`soc_report_dir`已填写本次6R目录；`soc_input_budget_kib=128`控制离线预览。无需LIVE，不调用模型、Redis、数据库或Zeus，不安装应用，不更新服务配置，也不修改原6R/6S文件或报告。只回传末尾JSON。

新私有目录的`diagnostic.json`可回传；`preview-context.json`和`preview-request.json`含原始资料，只留内网。它们是尚未发送的预览材料，不能用来证明原回答通过。真实条件不匹配的细分原因及更大预算实际纳入数量仍待内网输出。

本地检查：

```bash
backend/.venv/bin/python validation/rule_refine/implementation/step06t_condition_support/test_support.py
backend/.venv/bin/python validation/rule_refine/implementation/step06t_condition_support/test_budget_preview.py
backend/.venv/bin/python validation/rule_refine/implementation/step06t_condition_support/build_command.py
bash -n validation/rule_refine/implementation/step06t_condition_support/CHECK-HISTORICAL-CONDITIONS.sh
```
