# 第三步 A：提取规则观察和去重标识

内网入口核对已通过：8 个文件与 ZIP 相同；redis 5.0.5、SQLAlchemy 2.0.22、celery 5.4.0。本步先使用人工样例，不需连接现有服务。

用户已在内网执行通过：12 项测试成功（0.018 秒），3 个命中项生成 2 条观察、1 个重复，重推标识保持一致。下一步为 [第 3 步 B：Redis 计数与触发信号](../step03b_counter/README.md)。

## 执行

整段复制 [APPLY-STEP03A.sh](APPLY-STEP03A.sh) 到内网 Mac 终端。新增 `my_workflows/zeus/rule_refine/ingress.py` 和 `test/rule_refine/test_ingress.py`，依赖前一步 CSV 查询模块。同内容可重复执行，不覆盖已有不同内容。

预期：`Ran 12 tests`、`OK`，随后打印：

```json
{
  "mode": "demo",
  "status": "ready",
  "input_hit_count": 3,
  "unique_rule_count": 2,
  "duplicate_hit_count": 1,
  "repush_keys_unchanged": true,
  "company_scope_source": ["csv_unique_match", "csv_unique_match"]
}
```

含义：同一告警中 3 个命中项对应 2 条不同规则，重复命中消除；换时间和会话重推后仍生成相同去重标识。本步没有跨请求存储，不表示已经累计真实告警量。请贴回测试摘要和最后 JSON。

单独重跑：

```bash
cd "$HOME/sec_know_model"
.venv/bin/python test/rule_refine/test_ingress.py
.venv/bin/python test/rule_refine/test_ingress.py --demo
```

## 接口和语义

`normalize_received_alert(payload, rules, *, received_at, mode="demo", company_code=None)` 接收原接口请求字典和规则查询对象，返回规则观察及问题列表。received_at 必须带时区，统一输出 UTC，不拿原事件时间替代接收时间。

- 内外告警 ID 必须一致；整数 101 与字符串 "101" 可对应，不抹去字符串前导零。
- 只读取主告警 hitLog[*].topic/ruleCode；不将 profileCode 当规则，不计 relatedAlertList。
- 同一告警内相同 topic/ruleCode 只生成一条观察。单个命中项无效、配置未知或歧义会列出原因与位置，保留其他有效项。
- ready 表示全部有效；partial 表示存在有效观察和问题；skipped 表示无观察。提供器故障抛出，不伪装成无命中。
- 标识字段最多 512 字符，hitLog 最多 1000 项；超限明确报告。原始日志不进入观察输出。

company_code 表示配置企业归属。demo/replay 可从 CSV 唯一匹配确定，标记 csv_unique_match；它不证明真实告警企业。app_code 是调用系统，不能拿来当企业。

实时 live 必须由可信上下文显式提供 company_code，缺失返回 company_scope_required。不能直接采用请求中任意同名字段。真实来源或目录与接入流的完整对应关系在接入前确认，本步不修改 `/workflow/task`。

## 后续计数

occurrence_key 使用版本化 JSON 数组计算 SHA-256，包含 mode、调用系统、配置企业、topic、rule_code、告警 ID。接收时间、会话、flow 和配置版本不参与，防止重试新增计数。

第 3 步 B 消费这些观察，实现持久去重、首次接收时间、窗口计数及触发判断。独立回放由系统生成外层存储命名空间，避免回放间相互去重，不增加运营手填编号。统计范围同时展示提取失败或配置缺失情况。

## 验证

12 项本地测试覆盖重复/多规则、关联排除、ID、异常输入、企业歧义、live 范围、标识稳定与隔离、提供器失败。使用实际 CSV 模块和人工规则夹具。首次/重复安装成功，冲突文件保留，前一步源码不变；符号链接临时目录也已验证。

Ruff 检查与格式检查（120 列）通过，Python 3.11 语法通过。本地 Python 3.12.7 和内网 Python 3.11.7 测试均通过。无数据库/Redis 连接、模型调用或真实告警写入。
