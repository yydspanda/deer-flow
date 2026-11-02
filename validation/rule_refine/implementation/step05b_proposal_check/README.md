# 第 5 步 B：建议、样本与配置的关联检查

前一步 5A 内网 13 项测试已通过。本步将既有分析结果、5A 试算器和 RuleConfigSource 接起来；安装两个新增文件，不改已有业务入口或在线配置。测试用人工回复和假提供器，不调用模型、数据库、Redis。

## 执行

在内网另一个终端完整复制 [APPLY-STEP05B.sh](APPLY-STEP05B.sh)。脚本先校验依赖与待写入文件摘要，不覆盖不同版本；同版本可重复执行。无需重启服务。

预期 13 项测试通过，最后人工样例 status=reviewable、1 条日志匹配、recommendation_id_preserved=true、automatic_apply=false。贴回测试汇总及最后 JSON 即可。reviewable 表示可交运营审阅，尚未保存到接收 mock 或应用规则；engine_compatibility=not_verified 仍是预期。4D 的真实模型返回 insufficient_evidence，本步不会把它强行改为建议。

## 接口

`check_proposal(materials, analysis_result, load_rules)` 返回独立校验记录，不修改材料、原分析或建议编号。输入分析结果来自项目自己的 analyze_materials/analyze_with_model，不接受外部直接提交的“校验通过”声明。当前摘要用于数据关联，不是数字签名或上游模型身份认证。

load_rules 是重新加载配置的工厂。接 CSV 时使用 `lambda: CsvRuleConfigSource(csv_path)`，不能使用 `lambda: cached_source`；后续 ZEUS 适配器沿用 RuleConfigSource，需自行保证读取时效与有界网络超时。只有建议、材料和组合证据通过后才查询。按 topic+rule_code 查唯一规则，再核对 company，避免企业筛选掩盖身份歧义。

- 重建与模型生成时相同版本的提示词，核对 prompt 版本、摘要、材料模式/来源和规则来源。无法重建或不一致时停止，不拿新材料替旧建议背书。
- 校验规则身份、名称、版本、topic、窗口、计数、引用及说明字段；缺失建议 ID 时拒绝，不能调用默认 UUID 生成新建议。
- 在模型实际看到的日志上试算，每个引用告警必须至少有一条明确匹配完整表达式的日志。仅有单条件支持、跨日志拼接或未知结果不足以支持引用。其他未引用样本的未知结果仍显示，不自动当作匹配。
- coverage 区分触发信号请求的样本、取到的样本、模型看到的样本/日志、省略日志及未取到数量；per_alert 保留全部被省略的样本。evaluation.log_index 指向投影后的日志顺序，不是原日志下标；不推算全窗口误报率或降量率。
- 重新读取的配置须身份、版本、内容和来源种类一致。即使提供器复用了版本号，配置内容变化仍停止。CSV 其他行变化导致文件摘要变化，不影响当前行内容一致时的检查；不把快照一致宣称为在线 ZEUS 一致。
- 查询错误不回退到旧快照，记录静态错误类型，不回显异常正文。

## 结果

| status | 含义 |
|---|---|
| reviewable | 本地关联、组合证据和提供器配置检查通过；保留建议，交运营审阅 |
| not_applicable | 无新增排除建议，不做过滤试算/配置查询；保留原分析状态，例如 insufficient_evidence |
| blocked | 分析失败、材料/建议不对应、组合证据不足或配置变化/查询失败；reason 说明原因，不产生可交付建议 |

有有效提示词的非建议结果也检查材料关联。生产者因材料不可用而未调用生成器时，允许无 prompt 摘要，但必须明确为 insufficient_evidence、generation_calls=0 和对应无材料错误码。失败分析和未知状态不会作为正常无建议处理。

该函数尚未接入在线任务或 LocalRecommendationSink。第 6 步再串联后台保存、失败恢复与本地接收；自动应用始终关闭。字段目录验证及 ZEUS 原生引擎回放仍待其接口与语义就绪。

## 本地验证记录

完整安装脚本在隔离项目目录执行，13 项测试通过（0.044 秒）；人工建议 reviewable，1 条投影日志匹配，原建议 ID 保持，零模型/数据库/Redis 调用。重复安装通过，不同依赖或不同已有文件均停止且未覆盖。Ruff、Python 3.11 语法、Bash 语法检查通过。独立复核发现并关闭了缺失 ID 时 schema 自动补号的问题，回归测试确认拒绝该输入。

用户已确认内网测试通过、显示与预期一致。下一步：[6A 保存与模拟交付](../step06a_saved_delivery/README.md)。
