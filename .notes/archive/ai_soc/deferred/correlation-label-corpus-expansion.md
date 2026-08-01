# Correlation Label Corpus Expansion

> 状态：Deferred / `PI-03` data-dependent。当前 8-pair fixture 只用于工程基线与 scorer 回放，
> 不代表真实生产分布，也不能支持自动去重、抑制或关单。

## 为什么保留

SOC Correlation 需要区分三类关系：

- `same_incident`：同一安全事件的不同告警投影。
- `related_distinct`：有关联，但仍是不同事件。
- `unrelated`：不应关联。

只有真实、脱敏、经过分析师审阅的 pair corpus，才能判断当前 scorer 是否在跨来源、跨规则和不同
时间窗口下仍然有效。现有受控样本能验证代码和报告结构，不能代替生产标签。

## 为什么现在不做

- 当前没有获批的真实 pair 数据集和稳定 ground truth。
- 需要安全分析师提供 label、rationale 和争议复核，不能由模型或代码自行制造真值。
- 数据脱敏、用途范围、保留周期和 reviewer owner 尚未形成可执行流程。
- 当前主线是 `PI-01` 真实只读 provider 接入；该工作不能插队，也不阻塞 Runtime 完整性。

## 未来范围

版本化 corpus 至少应覆盖：

- 同来源与跨来源告警。
- 同规则不同事件，以及跨规则同一事件。
- 短时间窗口、长时间窗口和周期性重复行为。
- endpoint、network、identity、asset 等不同 evidence 组合。
- 正例、困难负例和 reviewer 明确标记的争议样本。

每个 pair 必须保留可审计的：

- 稳定样本引用或内容 hash，而不是在文档中复制原始敏感告警。
- label、rationale、label source、reviewer、review time。
- tenant/source/time-window cohort 和 corpus version。
- superseded label lineage；标签修订不得覆盖历史版本。

## 固定边界

- 历史 `agent_response`、LLM 推断和相似度高分都不是人工真值。
- 扩充 corpus 不自动改变 production scorer、threshold 或 `shadow_dedup_allowed=false`。
- scorer v2 必须与当前版本做离线 replay diff，并分别报告 retrieval 与 duplicate-identity 指标。
- Correlation 结果仍是调查上下文，不能直接修改 Runtime Decision、关闭 ReviewQueue 或写 confirmed memory。

## 重新启动条件

- 数据 owner 批准脱敏样本用于离线评测。
- 明确至少一名 SOC analyst reviewer 和争议仲裁方式。
- label schema、版本、provenance、保留和访问策略确定。
- `delivery-roadmap.md` 将该项排入 `PI-03` 的当前执行切片。

## 验收标准

- corpus 可版本化重放，且每条 label 都可追溯到人工 reviewer 与 rationale。
- 关键 source/time/rule cohorts 的覆盖和样本缺口可见。
- 当前 scorer 与候选 scorer 的 precision/recall、fan-out、unrelated exposure 和 replay diff 可比较。
- 任何候选策略只能先进入 shadow/governed rollout，不能因一次离线报告自动启用抑制。

