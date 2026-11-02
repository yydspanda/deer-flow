# 第二步 B 本地模拟接收建议

交付日期：2026-10-27。用户已在内网修正 macOS 测试路径后重跑：10 项测试全部通过（0.018 秒），首次提交与重复提交的模拟回执符合预期。

本步完成 ZEUS 建议接收接口的本地替代实现：保存建议 JSON、返回回执、识别重复提交。实际建议生成由后续步骤实现。

## 执行

打开 [APPLY-STEP02B.sh](APPLY-STEP02B.sh)，整段复制到内网 Mac 终端执行。无需传文件或安装依赖，使用原项目 `.venv/bin/python`。

新增两个文件：

- `my_workflows/zeus/rule_refine/local_sink.py`
- `test/rule_refine/test_local_sink.py`

安装前检查全部目标文件；已存在且内容不同则停止，不覆盖。内容一致时可重复执行。随后运行 10 项 unittest，并连续提交两次人工 DEMO_RULE 样例。

预期输出为 `Ran 10 tests`、`OK`，以及：

```json
{
  "delivery_mode": "mock",
  "first_duplicate": false,
  "second_duplicate": true,
  "applied": false,
  "saved_file": "/Users/zhangjianming627/sec-rule-refine-data/receipt-demo/00000000-0000-4000-8000-000000000001.json"
}
```

重新执行时 `first_duplicate` 也为 true 属于正常，样例只保留一份。演示使用固定测试编号，仅供验证；实际建议的编号由生成模块创建，重试沿用原编号，运营无需手填。

请贴回测试摘要和最后的 JSON。以后单独验证：

```bash
cd "$HOME/sec_know_model"
.venv/bin/python test/rule_refine/test_local_sink.py
.venv/bin/python test/rule_refine/test_local_sink.py \
  --demo-dir "$HOME/sec-rule-refine-data/receipt-demo"
```

## 已安装旧测试的 macOS 修复

如果两项失败均为 `/private/var/...` 与 `/var/...` 不相等，执行 [FIX-MAC-TEST-PATH.sh](FIX-MAC-TEST-PATH.sh)。这两个路径经符号链接指向同一位置，收件模块已使用真实路径，原测试夹具却保留别名。补丁只给测试目录增加 `.resolve()`，随后重跑全部 10 项测试与人工样例；接收模块不变。

首次安装命令已同步修正。已安装旧测试时使用上述短补丁，避免重新安装命令因目标内容不同而停止。由于首次测试失败且脚本启用了 `set -e`，当次人工样例提交未执行。

## 接口与行为

`LocalRecommendationSink(directory).submit(payload)` 接收普通字典，必须包含已生成的 `recommendation_id`，并通过前一步 `RuleRecommendation` 校验。保存内容为模型规范化后的完整建议 JSON，按字段名排序、UTF-8 编码。

| 情况 | 行为 |
|---|---|
| 首次收到编号 | 保存 `UUID.json`，回执 `duplicate=false` |
| 同编号、规范化后内容相同 | 保留原文件，回执 `duplicate=true` |
| 同编号、内容不同 | 抛出 `ReceiptConflictError`，原文件不变 |
| 缺编号或格式错误 | 校验失败，不保存 |
| 写入或发布失败 | 抛出错误，不返回成功回执 |

回执包含编号、`delivery_mode=mock`、`status=received`、`applied=false`、重复标识与保存内容 SHA-256。回执表示本地收到建议，不表示线上已采纳或修改了规则。

模块先完整写入并同步临时文件，再通过同目录硬链接原子发布；并发提交不会相互覆盖。收件目录由部署配置指定，应使用受控的本地目录。当前不承诺掉电持久化（未同步目录元数据），不作为生产消息队列。

接收模块不判断 CSV 是否为最新版本；采纳前的配置版本核对是后续应用环节的职责。本步不连接 ZEUS、数据库或模型，不修改规则。

## 本地验证

- 10 项 unittest，涵盖规范化、重复、冲突、权限、缺编号、格式错误、写入失败、fsync/link 失败、完整文件发布、两个独立实例线程并发。
- 首次安装与重复安装均成功；已有不同内容时拒绝覆盖；前两步文件保持不变。
- Ruff 检查与格式检查通过，Python 3.11 语法检查通过；本地 Python 3.12.7 与内网 Python 3.11.7 的 10 项测试均通过。
- 已在本地通过符号链接临时目录复现原来的 2 项失败；单行修正后 10 项全部通过。短补丁首次和重复执行均通过，接收模块文件未变；用户确认实际 Mac 修复后通过。
- 用户回执：`delivery_mode=mock`、`first_duplicate=false`、`second_duplicate=true`、`applied=false`。样例保存在 `/Users/zhangjianming627/sec-rule-refine-data/receipt-demo/00000000-0000-4000-8000-000000000001.json`。

本步已通过。下一步：[告警入口与字段核对](../step03_ingress_check/README.md)，随后进入突增计数功能。
