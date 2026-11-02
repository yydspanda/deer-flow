# 第一步 A 新增排除建议的数据格式

交付日期：2026-10-27。当前状态：本地验证完成；用户已反馈内网执行完成、测试符合预期。

本小步提供新增排除建议的数据模型、表达式语法检查和独立 unittest。修改既有条件组，以及材料查询和建议接收的契约，在后续小步继续补充。本次模型明确拒绝 `modify_conditions`，避免将已有条件的修改误解释为新增排除。

## 内网执行

打开 [APPLY-STEP01.sh](APPLY-STEP01.sh)，整段复制到内网 Mac 终端执行。命令进入 `$HOME/sec_know_model`，使用项目 `.venv/bin/python`，新增：

- `my_workflows/zeus/rule_refine/__init__.py`
- `my_workflows/zeus/rule_refine/contracts.py`
- `test/rule_refine/test_contracts.py`

执行前统一检查目标文件。不存在则新增，内容一致则复用，存在不同内容则停止且不覆盖。使用 Python 内置 unittest，无需安装 pytest。

预期输出末尾：

```text
Ran 10 tests in ...

OK
```

请回传新增文件提示和测试摘要；失败时贴完整错误栈即可。此命令不调用真实模型或 ZEUS，也不加载原应用配置。

重复运行测试：

```bash
cd "$HOME/sec_know_model"
.venv/bin/python test/rule_refine/test_contracts.py
```

## 格式范围

- `FilterDefinition` 保留平铺的 `topic`、`expression` 和 A/B/C 等动态条件。
- 条件值当前支持字符串；保留值前后空格，不擅自改变匹配含义。
- 操作符支持 `equal`、`contain`、`notcontain`。包含类操作不接受空字符串。
- 表达式支持条件别名、`&&`、`||` 和括号，只验证语法与引用，不执行求值。
- 建议绑定企业、规则、配置版本及证据时间窗。时间带时区，样本 ID 不重复且数量不能超过告警总数。
- 建议编号默认自动生成；后续交付重试将复用已保存编号，而不是重新构造建议生成新编号。
- `automatic_apply` 只接受布尔值 `false`。

这些检查证明数据格式与语法成立；字段是否存在、操作符与 ZEUS 是否一致、过滤建议是否适当，在后续数据适配及试算步骤验证。

## 已完成的本地验证

- Python 3.12.7 / Pydantic 2.13.3：10 项 unittest 通过，包含多组负面子用例。
- 首次安装和相同命令重复安装均通过；已有业务文件不变。
- 同名文件内容不同则拒绝覆盖。
- Ruff 静态检查通过。

用户目标环境为 Python 3.11.7 / Pydantic 2.11.0；用户已确认执行完成、符合预期。此记录依据用户反馈，未直接访问内网环境。

本目录的 `payload/` 是待复制到 sec_know_model 的独立源码，不是 DeerFlow 运行时依赖。
