# Adaptive Normalization and Parser Evolution

> 状态：Deferred / production-data-dependent。当前 Runtime 已具备 deterministic parser、schema
> fingerprint、coverage gap、maintenance issue 和离线 mapping suggestion；这里保留的是未来的
> “基于真实漂移自动形成候选改动并治理上线”能力，不是当前解析正确性修复。

## 为什么保留

供应商字段和日志格式会持续变化，长期完全依靠工程师逐条查看告警并手写 adapter 成本过高。未来应把
重复出现的 schema、nested decode、high-value gap 和 analyst feedback 聚合为版本化 cohort，再由离线
LLM/规则生成 parser、mapping、field-importance 或测试候选，经过 replay 和人工批准后发布。

## 为什么现在不做

- 当前 deterministic parser、schema monitor、coverage issue 和离线 suggestion 已能支撑产品流程；继续做
  自动候选发布不会关闭任何现有 Real Integration Gate。
- 212 条本地语料能验证兼容性，但不能代表生产格式漂移频率、影响范围或 tenant 分布。
- 自动生成代码/mapping 没有真实 owner、批准流程、回滚包和 shadow 指标时风险高于收益。
- 当前每条告警已有 deterministic fallback、原始输入保留和 fail-closed，不依赖该能力才能工作。

## 未来范围

- 按 tenant/source/parser/version/fingerprint 聚合 maintenance issue，而不是逐告警调用 LLM。
- 区分 outer schema unsupported、nested field damage、routine bounded omission、encoded compaction 和
  true high-value gap，避免把正常 Token 优化当解析事故。
- 离线生成 parser/mapping/importance-rule/test-fixture 候选，不在 Runtime 内动态改代码或配置。
- 对候选版本执行旧/新 parser 双跑、canonical/fact/LLM projection diff、golden sample 和 corpus replay。
- 人工批准、签名版本、灰度、观察窗口、自动回滚和 superseded lineage 完整可审计。

## 固定边界

- 不对每条告警使用 LLM 猜字段；线上 Runtime 继续执行已批准 deterministic adapter。
- 原始 payload、message 和失败 nested string 永远保留；候选 repair 不覆盖 source fact。
- LLM 只能提出候选，不能接受 baseline、发布 mapping、修改 adapter、确认 memory 或改变 verdict。
- `MessageSchemaObservation`、`NestedJsonRepairObservation`、`EvidenceCoverageReport` 和 analyst label 是
  不同信号，不得压成一个“可信度分数”。
- 角色冲突重建是当前 Runtime 契约，不属于本 deferred 项。

## 重新启动条件

- 已接入至少一个真实 provider，并积累可审计的 schema/coverage issue cohort。
- 明确 parser/mapping owner、reviewer、发布窗口、回滚责任和数据使用范围。
- 有版本化 replay corpus 和人工确认的关键字段期望，不使用历史 `agent_response` 充当真值。
- `delivery-roadmap.md` 将该项排入明确 PI task，且不再处于 data-gated 状态。

## 验收标准

- 候选生成按 cohort 触发，线上单告警路径不增加 LLM 调用。
- 每个候选都有 issue lineage、输入 schema hash、目标 contract、测试、diff 和 reviewer。
- 新版本在 shadow/replay 中不丢 high-value evidence、不扩大错误角色映射，且可一键回滚。
- 未批准候选对 Runtime、Decision、ReviewQueue、memory 和 action 均为零影响。
