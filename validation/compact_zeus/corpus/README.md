# Corpus Builders

该目录负责把 `datas/source/` 的权威 PKL 与 `datas/legacy_demos/` 合并为唯一、可追踪的
canonical 验证语料，并生成 Zeus raw-log 上下文压缩报告。

- `build_alert_validation_corpus.py`：生成 212 条 canonical corpus 与 manifest，验证 ID、
  lineage、payload hash、Normalizer 与 LLM projection 契约。
- `build_dams_labeled_dataset.py`：流式合并 DAMS 告警/运营标签与现有 210 条 PKL，
  按 `updated_date` 选择每个 `alert_id` 的最新记录；无标签告警仍完整保留，只有
  标签而没有告警正文的记录只进入 manifest，不伪造 payload。输出按 Normalizer
  解析出的时区化事件时间升序排列，并生成相邻的轻量 Workbench 索引。
- `build_zeus_compaction_artifacts.py`：基于统一 corpus 生成本地 HTML/Excel 压缩报告。
- `test_build_alert_validation_corpus.py`：覆盖受限 pickle 读取、合并和完整性契约。
- `test_build_dams_labeled_dataset.py`：覆盖最新记录选择、无标签保留、标签原始行、
  ID 一致性和受限 Pickle 往返。

输入只读；输出写入 `validation/compact_zeus/data/corpus/` 与 `data/compaction/`，均包含或
派生自内部告警并被 Git 忽略。

```bash
backend/.venv/bin/python validation/compact_zeus/corpus/build_alert_validation_corpus.py
backend/.venv/bin/python validation/compact_zeus/corpus/build_dams_labeled_dataset.py
backend/.venv/bin/python validation/compact_zeus/corpus/build_zeus_compaction_artifacts.py
backend/.venv/bin/python -m pytest -q validation/compact_zeus/corpus
```

当前 DAMS 合并输出为 Git 忽略的
`validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.pkl`，相邻
`*.workbench-index.json` 是与 PKL SHA-256 绑定的列表索引：只保存规范化时间、规则、
行为指纹、同类组和标签元数据，不保存原始 payload。相邻的
`*.workbench-payloads.sqlite` 按 `alert_id` 保存 zlib 压缩后的原始 payload，且其 SHA-256
由索引锁定；页面点击运行时只读取目标告警，不把 1.2GB PKL 全部载入 Gateway 内存。
manifest 记录每个输入文件的 SHA-256、重复行选择、标签命中、孤立标签和三个输出文件
的摘要。

Workbench index v3 同时冻结当前 `PingAnSocMemoryProfile` 身份和聚合窗口。PingAn Profile
v7 使用 30 天 fixed UTC window，并按目标网络服务、CVE 和规范化攻击行为细分模式；Profile
或窗口变化后必须重建 index，旧 index 会 fail closed，不会让页面预计算分组与 Runtime
Pattern 聚合产生两套口径。

时间与标签边界：

- 排序固定为 `canonical_event_time ASC, alert_id ASC`，不使用 CSV/DataFrame 行号推断时间。
- 同一 Memory 同类组必须按该顺序运行，后来的告警不得读取由未来样本产生的 Memory。
- DAMS 标签只在 Runtime 形成技术结论后揭示；标签时间早于告警或缺失时不计入匹配率。
- 页面比较的是技术结论到“忽略/转交”的确定性运营投影，不把运营处置冒充独立攻击真值。

兼容列 `ground_label` 使用 DAMS 导出的“预警研判结果”；它是运营工作流标签，
并非独立专家真值。历史“模型研判结果”只写入 `predict_label`，完整标签原始行
保存在 `operational_label_record` 以便后续审计和重新解释。
