# Corpus Builders

该目录负责把 `datas/source/` 的权威 PKL 与 `datas/legacy_demos/` 合并为唯一、可追踪的
canonical 验证语料，并生成 Zeus raw-log 上下文压缩报告。

- `build_alert_validation_corpus.py`：生成 212 条 canonical corpus 与 manifest，验证 ID、
  lineage、payload hash、Normalizer 与 LLM projection 契约。
- `build_zeus_compaction_artifacts.py`：基于统一 corpus 生成本地 HTML/Excel 压缩报告。
- `test_build_alert_validation_corpus.py`：覆盖受限 pickle 读取、合并和完整性契约。

输入只读；输出写入 `validation/compact_zeus/data/corpus/` 与 `data/compaction/`，均包含或
派生自内部告警并被 Git 忽略。

```bash
backend/.venv/bin/python validation/compact_zeus/corpus/build_alert_validation_corpus.py
backend/.venv/bin/python validation/compact_zeus/corpus/build_zeus_compaction_artifacts.py
backend/.venv/bin/python -m pytest -q validation/compact_zeus/corpus
```
