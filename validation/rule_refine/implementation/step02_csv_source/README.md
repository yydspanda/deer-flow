# 第二步 A CSV 规则配置查询

交付日期：2026-10-27。当前状态：用户已在内网执行通过，10 项测试成功，真实 CSV 查询结果与预期一致。

前置已确认：内网第一步 A 测试通过；`$HOME/sec-rule-refine-data/rules.csv` 的 SHA-256 与已确认快照一致，共 911 条规则记录。

## 执行

打开 [APPLY-STEP02.sh](APPLY-STEP02.sh)，整段复制到内网 Mac 终端执行。命令使用原项目 `.venv/bin/python`，只新增：

- `my_workflows/zeus/rule_refine/csv_config_source.py`
- `test/rule_refine/test_csv_config_source.py`

命令先核对前一步文件和 CSV 版本，拒绝覆盖已有不同内容；随后运行 10 项 unittest，并以 `RPAADM_002624` 为例查询真实 CSV。示例编号只用于本次验证，查询模块本身支持任意规则编号。

预期结果：

- 测试摘要为 `Ran 10 tests`、`OK`。
- `loaded_rules` 为 911。
- 查询规则为 `RPAADM_002624`，topic 为 `sec_guard_apt`，`term_count` 为 14。
- `rule_config_version` 为 `csv-row-v1:sha256:fc4ae18997f01b8330be8f8c45be418fc81232df80562ee219a20d74492a2275`。

请贴回测试摘要和最后一段 JSON；报错时贴完整错误栈。输出只显示配置来源与规则摘要，不展示过滤条件的原始值。

以后可以单独执行：

```bash
cd "$HOME/sec_know_model"
.venv/bin/python test/rule_refine/test_csv_config_source.py
.venv/bin/python my_workflows/zeus/rule_refine/csv_config_source.py \
  --csv "$HOME/sec-rule-refine-data/rules.csv" --rule RPAADM_002624
```

遇到重名规则，可进一步提供 `--company` 和 `--topic`。同一范围仍重复则明确报错，不取第一条。

## 当前查询接口

`CsvRuleConfigSource(path)` 读取并校验一次完整快照；后续查询使用同一份内存快照，不在查询中重新读取文件。

- `count`：原始规则记录数，不自动去重。
- `list_rules()`：返回企业、topic、规则编号和名称的摘要列表。
- `get_rule(rule_code, company_code=None, topic=None)`：范围参数以关键字传入；精确匹配，零条报不存在，多条报歧义。

查询结果包含：

| 字段 | 含义 |
|---|---|
| `company_code/topic/rule_code/rule_name` | 规则身份与展示名称 |
| `rule_config_version` | 当前原始配置行的快照版本 |
| `raw_record` | 原 CSV 全部列的字符串值，原样保留 |
| `configuration` | 保留全部列；仅解析 `terms`、组内 `config/standard_field_config` 及顶层 `standard_field_config` |
| `source` | CSV 来源、文件名、文件摘要和数据行序号 |

原空字符串、null 等在解析视图中可以归一为空值，原始区别保留在 `raw_record` 中；未出现的组内标准配置字段不补造。`duplicate_removal` 等普通文本保持原样。读取层不解释 `standard_status`、不判断哪个字段分支生效，也不执行规则。

## 快照版本约定

首版对完整原始 CSV 行按字段名排序，使用 UTF-8 紧凑 JSON 计算 SHA-256，前缀为 `csv-row-v1:sha256:`。行号、文件路径、加载时间不进入该版本；CSV 文件摘要单独记录。

这是保守的原始配置快照版本，不是“语义等价”判断：审计字段或嵌套 JSON 的空格变化也会改变版本。先保留所有原字段参与摘要，避免未知字段的实际作用被忽略。以后若采用语义版本，单独升级算法标识。

## 已完成的验证

- 10 项 unittest，含多组子用例：范围匹配、重复歧义、缺失输入、JSON 类型、重复键、非有限数值、原值保留、返回副本隔离、版本稳定性。
- 已确认 CSV 的全部 911 条可查询，返回的 `raw_record` 与原始 26 列逐字段一致。
- 首次和重复安装均通过，同名内容不同则停止，前一步文件保持原样。
- Ruff 检查及 Python 3.11 语法检查通过；独立测试在本地 Python 3.12.7 通过。用户反馈内网 Python 3.11.7 运行 10 项测试成功（0.051 秒），查询得到 911 条规则、示例规则 14 个条件组及预期快照版本。

该步骤只完成规则配置查询；突增统计、模型建议生成、ZEUS HTTP 接口与建议接收仍待后续步骤。
