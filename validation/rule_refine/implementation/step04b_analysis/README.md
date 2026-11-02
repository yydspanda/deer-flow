# 第 4 步 B：场景分析与过滤建议

前一步内网 15 项测试通过，4 条人工任务形成 3 份材料并合并 1 条重复。LOCAL 任务表仍为空，本步用人工回答验证生成和校验流程，不代表真实模型效果。

## 操作

保持服务运行，在另一个终端完整复制 [APPLY-STEP04B.sh](APPLY-STEP04B.sh)。新增 `my_workflows/zeus/rule_refine/analysis.py` 和 `test/rule_refine/test_analysis.py`，不改现有入口或启动方式。脚本验证前序文件和粘贴内容的摘要，不覆盖不同版本；相同内容可重复执行。

预期 13 项测试 OK，人工演示输出 status=proposal，并展示问题、修改建议和既有 topic/A/expression 条件格式。generation_calls=1 是一次人工生成函数调用；model_calls/database_calls/redis_calls 均为 0。贴回测试摘要及最后 JSON。

## 接口和输出

`build_analysis_request(materials)` 仅构建一次有界投影：保留解析后的完整配置，去掉重复的原始 CSV 行；每个告警最多展示 3 条完整原日志，单条最多 8KiB，已有研判参考最多 2KiB，规则视图最多 32KiB，用户材料 JSON 最多 64KiB。超大日志整条省略，不截断后把字符串缺失误判为 notcontain；省略数进入模型输入。无样本或完整配置超限时不调用生成函数。编码前复用同模块包的有界 JSON 检查。

`analyze_materials(materials, generate, *, response_source="synthetic_reply")` 把两个 role/content 消息传给注入的 generate，最多调用一次。当前测试用人工函数；后续真实适配使用原项目现有 OpenAI 同步客户端风格，显式配置模型、超时和重试策略。源码包没有通用 get_llm 工厂；本步不导入完整 Zeus 业务工作流，也不新增模型供应商配置。

生成状态：proposal、no_change、insufficient_evidence、needs_existing_condition_change。最后一种明确表示发现需要修改既有条件，当前新增排除契约不能表示该修改；不强行套用排除取反关系。调用故障、非法输出、虚构身份或样本引用另记 analysis_failed，不当成无问题。

模型只能给问题、建议文字、确定性说明、样本引用及过滤条件。代码填入企业、规则、版本、时间窗口、计数、建议 UUID 和 automatic_apply=false。模型额外返回这些身份字段会被拒绝。记录提示词版本/摘要、回答摘要、材料模式/来源、规则来源，错误只给类别，不回显原始服务异常。

JSON 回答最多 16KiB，拒绝 Markdown 包装、重复键、非标准数值、未知字段和无效表达式。条件只能使用实际投影且被引用样本中的顶层字符串字段；逐条检查 equal/contain/notcontain 的字符串关系，缺字段不支持负条件。这里尚未检查所有条件是否在同一条日志共同成立，结果明确标记 structure_and_individual_conditions_only、engine_compatibility=not_verified；完整表达式试算和 ZEUS 字段/操作符兼容在第 5 步实现。

配置和日志中的文字只是待分析资料，已有模型忽略结论不是人工标签。提示词禁止把告警量大、认证头或某类接口使用单独当成正常业务证明；不得宣称真实误报率或整体降量率。新增排除只是建议，不调用 LocalRecommendationSink、不发 ZEUS、不改规则。

交付重试应保存并复用同一份生成结果及 recommendation_id，不能为重发再次生成；持久化和恢复由后续完整链路实现。

## 验证

本地 13 项测试通过：正常建议、材料不变、无材料零调用、非建议状态、虚构字段/ID/topic、坏 JSON/重复键/超长回答、生成故障、三类操作符、缺字段负条件、未投影证据、非法输入形状及仅单条件检查的边界。Ruff 与 Python 3.11 语法检查通过。

内网用户已确认本步 13 项测试通过（0.012 秒），人工示例返回 proposal，generation_calls=1，模型、数据库及 Redis 调用均为 0。真实模型调用及效果尚未验证。下一步见 [模型适配器](../step04c_model_adapter/README.md)；后台派发、实际数据读取、组合条件试算和建议保存仍按方案继续接入。
