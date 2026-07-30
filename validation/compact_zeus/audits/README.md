# Adapter Field Audits

该目录对统一 corpus 中各 PingAn topic 做批量字段流向审计，回答“解析出的字段进入了
canonical provenance、fact、scenario、LLM evidence 中的哪一条通道”。它用于发现覆盖
缺口和错误语义，不是检测准确率评测，也不会修改 Runtime 决策。

当前审计组：

- `build_pingan_nids_field_audit.py`：NIDS 字段、五元组、HTTP 与编码压缩。
- `build_pingan_edr_field_audit.py`：EDR endpoint/process/file/hash 语义。
- `build_pingan_ndr_hids_field_audit.py`：NDR/APT 与 HIDS message-first 字段流向。
- `build_pingan_ti_siem_field_audit.py`：Threat Intel 与可信 SIEM subtype。

输出写入 `validation/compact_zeus/data/audits/`，包含内部语料统计或派生信息，已 Git
忽略。对应代表样本由相邻的 `reviews/` 构建。

```bash
backend/.venv/bin/python -m pytest -q validation/compact_zeus/audits
```
