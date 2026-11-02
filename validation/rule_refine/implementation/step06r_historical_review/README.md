# 6R：真实历史样本的单规则建议验证

6Q已完成一条真实告警的接口接收、监测计数、原研判任务执行和结果落库；任务状态successed、正文与原请求相等、回答存在、失败次数0。本步骤开始核对建议内容是否能服务运营。

## 操作

打开`REVIEW-HISTORICAL-RULE.sh`，全选复制到内网终端执行。无需更新部署包或修改应用源码。脚本固定读取已核对的200条小样本文件和rules.csv，从ptp-nids/RPAADM_002627的候选中选取最多20条告警供模型分析。查看预览中的实际数量，输入`LIVE`发起最多一次SDK请求；其他输入退出。

输出目录自动生成：`~/sec-rule-refine-data/historical-rule-review-*`。先回传终端最后的精简JSON，再按需要阅读其中`REPORT.md`。原始日志、提示词、模型回答留在内网。

报告包含：具体问题、修改建议、适用前提、结构化过滤条件、展示样本的匹配情况。合法结果包括proposal、no_change、insufficient_evidence、needs_existing_condition_change；不会为了输出建议而强行放宽校验。脚本状态passed表示本次调用及本地校验通过，不代表运营已认可或规则引擎已兼容。

## 来源和处理边界

- 历史文件SHA256：`7dcf39081920a8531f7458149f1c98c9d256e297c62c10ee9d385c39a091f2cf`。CSV SHA256：`729b0892ce3bd455d09f8d8b93d40afcf2d78a6f28d25e7753b640c1ac7e1976`。完整配置重新读取前后核对；来源有变化则停止或阻断结果。
- 只遍历前200行，保留真实告警身份，排除同ID不同正文；不改ID、不复制告警制造数量。按源文件顺序选择最多20条有合格日志的告警，不按旧模型结论挑选；旧研判回答不作为标签或提示材料提供。
- 保留完整规则配置，最多32KiB；每告警最多3条完整日志，单日志8KiB，总上下文60KiB。过大的日志整条省略，不截短字段后判断“不包含”。coverage.json记录选择依据、可用/展示样本及省略数量。没有可用样本时不调用模型。
- 明确material_source=historical_export、trigger=manual_sample_review、observed_surge=false、rate_window=null。没有虚构触发窗口、突增信号或线上速率；不会构造正式RuleRecommendation的received_unique_alerts证据。
- 复用已安装版本的模型传输检查、AnalysisDraft、完整JSON代码框解析和组合条件试算。只在本工具独立导入的Python包内给传输函数注入历史分析收集器，不修改线上模块、生产提示词、任务或持久配置。
- proposal必须引用模型实际看到的样本，条件使用实际字符串字段，且每个引用告警至少有一条日志满足完整组合。试算结果是这些历史日志的匹配数，不是误报率、全部50条候选的效果或线上降量率。
- 最多一次SDK请求，SDK重试为0，超时240秒；代理内部尝试次数未知。沿用原模型并发槽和熔断器，因此会访问原有Redis容量控制键，但不创建或改动实时告警计数/突增信号，不访问业务数据库、不提交告警、不回调Zeus、不提交正式建议、不自动改规则。

## 验证及剩余工作

```bash
backend/.venv/bin/python -B validation/rule_refine/implementation/step06r_historical_review/test_materials.py
backend/.venv/bin/python -B validation/rule_refine/implementation/step06r_historical_review/test_review.py
backend/.venv/bin/python -B validation/rule_refine/implementation/step06r_historical_review/test_command.py
```

28项本地测试通过：材料11项、回答/传输10项、命令及执行7项。涵盖身份与重复冲突、前200/最多20×3及完整日志预算、无虚构时间窗、原JSON代码框解析、未知引用与跨日志组合拒绝、单次模拟SDK调用、取消/非LOCAL/无材料不调用、容量不足不调用、配置漂移阻断、私有报告保存和完整命令自校验。未在外网调用真实模型或内网服务。

实际历史样本结果待内网回传。真实量达到阈值后的Celery自动建议链路、运营对具体建议的认可，以及Zeus配置/建议API联调仍是独立事项；本次手工历史分析不能代替这些证据。
