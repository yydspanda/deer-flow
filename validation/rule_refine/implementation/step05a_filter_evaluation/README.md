# 第 5 步 A：条件组合的本地试算

前置：4D 内网真实 SDK 请求成功，人工材料返回有效的 insufficient_evidence。此步新增 `filter_evaluation.py` 与独立 unittest，不改变在线入口、建议生成或交付流程。

## 操作

在内网 Mac 的另一个终端完整执行 [APPLY-STEP05A.sh](APPLY-STEP05A.sh)。脚本校验既有 contracts/materials/provider 的摘要，再新增两个文件；发现不同版本会停止，不覆盖已有修改。同版本可重复执行。无需重启服务，无模型、数据库、Redis 调用。

随后自动执行测试及人工示例。贴回测试汇总和最后的 JSON。人工例子共 4 条日志，预期 1 条匹配、2 条不匹配、1 条未知；A、B 分别出现在两条日志时，不能合并为 A&&B 匹配。

## 接口和含义

`evaluate_filter(definition, samples)`：definition 沿用 `contracts.FilterDefinition`；samples 使用既有材料的 `[{alert_id, raw_logs}]` 结构，可携带其他元数据。最多 20 个唯一告警 ID，每个最多 100 条日志；输入 JSON 总量不超过 2MiB，并复用材料编码器的深度和节点限制。格式无效或超限报 ValueError，不截断成通过。

- `equal`、`contain`、`notcontain` 只比较顶层字符串，区分大小写；带点的字段名按字面键处理，不自动展开嵌套、不转换数字/数组。equal 支持空字符串，另外两个操作符不支持空目标值。
- `&&` 优先于 `||`，括号可改变顺序；复用原契约的完整语法和规模校验，不执行表达式代码。
- 缺字段、null、非字符串均为未知。未知且假为假，未知或真为真，其余按三值逻辑组合。诊断仍保留所有被引用条件的未知原因，因此最终已知结果也可能含 unknown_conditions。
- 每条日志独立计算，返回 matched/not_matched/unknown 数量和 alert_id/log_index 引用（下标从 0 开始），不回显日志内容。空日志列表列入 empty_alert_ids，不计入日志分母；完全无日志返回 insufficient_evidence。
- input_sha256 覆盖规范化后的过滤定义及全部输入样本。此摘要只是本步输入标识，不证明与某次模型提示词或在线规则一致。

输出 scope=provided_logs_only、source_completeness=not_asserted、engine_compatibility=not_verified、automatic_apply=false。不计算可忽略告警数、误报率或总体降噪率。调用方负责提供同一 topic 的日志，本步不检查身份/规则版本与字段目录；schema 校验不等于支持 ZEUS 原生执行。

## 后续衔接

5B 将核对模型结果与实际提示材料摘要、明确原始/投影/省略样本范围，并重新读取 provider 核对配置版本。CSV 版本核对只说明快照一致，不能声称在线 ZEUS 规则未变。后台任务与接收 mock 在第 6 步串联。本步只用人工日志确认求值逻辑。

## 本地验证记录

完整安装脚本在隔离项目目录执行，13 项测试通过（0.007 秒），人工示例为 1 匹配 / 2 不匹配 / 1 未知；重复安装通过，不同已有文件与不同依赖均拒绝且未覆盖。另由独立复核验证 108 组表达式/三值组合通过。Ruff 格式与静态检查、Python 3.11 语法、Bash 语法通过；未调用模型或外部服务。

用户内网已通过 13 项测试（0.002 秒），人工示例数量相同，cross_log_and_matched=false，模型/数据库/Redis 调用均为 0。下一步：[5B 关联检查](../step05b_proposal_check/README.md)。
