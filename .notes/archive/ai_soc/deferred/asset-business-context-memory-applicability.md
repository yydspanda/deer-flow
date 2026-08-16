# Asset And Business Context For Memory Applicability

Status: Deferred

## Why This Is Deferred

当前 Memory 的 `environment` 是服务端控制的 SOC 运行/数据隔离边界，例如 `dev`、`stg`、`prd`。
它不能表达平安所有业务系统、终端、服务器、网络区域和资产重要性，也不能替代 CMDB 或资产标签。

本项已确认有价值，但当前没有冻结的通用资产分类契约、真实 CMDB 响应样本和人工标注集。现在直接把
PingAn 字段名写进通用 Memory，或把所有维度拼成联合主键，都会造成不可移植和过窄匹配。因此本轮
只修正 `rule_code` 与 behavior 的 Memory 权限边界，不实现资产/业务范围扩展。

## Target Design

保持两个概念严格分离：

- `runtime_environment`：SOC 服务运行和数据隔离边界，由 operator 配置；
- `asset/business context`：当前告警涉及的业务和资产事实，由 canonical Adapter、CMDB Provider 或
  governed context 提供。

候选的通用 canonical facets 可按真实数据逐步增加：

- `business_system_id`、`business_unit`；
- `asset_class`：server、employee_endpoint、network_device 等；
- `asset_environment`：生产、测试、开发等业务属性；
- `asset_criticality`；
- `network_zone`、`boundary_direction`；
- `identity_scope`、`authorized_activity_scope`。

这些字段不是固定联合键。Memory Candidate 必须展示哪些维度在 cohort 中稳定；Reviewer 只把真正
决定经验成立的维度提升为 required applicability，其余维度只参与召回、排序和解释。缺少必要资产
上下文时，Memory 可以作为 reasoning context，但不得获得确定性改判或动作权限。

PingAn 原始别名和 CMDB wire format 只能存在于 PingAn Adapter/Provider；通用 Memory Kernel 只消费
canonical facets。其他客户通过自己的 Adapter/Profile 接入，不修改通用 Runtime。

## Re-entry Conditions

同时满足后才进入正式路线图：

1. 获得稳定、版本化的 PingAn DEV CMDB/资产标签响应契约和可回放样本；
2. 明确业务系统、BU、资产类型、业务环境和重要性的 canonical 定义及 owner；
3. 至少有一组人工标注样本能说明哪些资产维度会改变同 rule/behavior 的最终结论；
4. 明确字段缺失、冲突、过期和 Provider 不可用时的 fail-closed 行为；
5. 证明这些维度改善 held-out Memory precision，而不是仅降低 recall。

## First Slice After Activation

1. 增加版本化 `AssetBusinessContext` contract，不直接修改通用 Alert 原始字段语义；
2. 由 PingAn Adapter/只读 Provider 生成带 provenance、trust、validity 的 canonical facts；
3. Memory Profile 仅投影可验证 facets，并允许 Reviewer 将候选 optional facet 提升为 required；
4. 用同 rule/behavior、不同业务系统或资产环境、相反人工结论的样本做 replay diff；
5. 只开放 shadow retrieval，验收前不允许新的 scope 触发 deterministic Memory Decision。

## Non-goals

- 不从 topic、IP 网段或任意字符串推断业务环境；
- 不把所有资产字段强制加入每条 Memory；
- 不让 LLM 自行声明资产归属后获得决策权限；
- 不把 CMDB 查询结果直接写成永久 Memory；
- 不在本项重复实现 Provider、tenant policy 或 authorized-activity lifecycle。

## Acceptance

- 同一 rule/behavior 在不同资产上下文下可以形成不同适用范围和不同结论；
- IP、UM 等普通变化不会无条件拆散可复用模式；
- required scope 缺失或冲突时只保留 context，不执行 Memory override；
- 新客户只新增 Adapter/Profile，不修改通用 Memory Kernel；
- 报告能解释每个匹配/拒绝由哪些 canonical facets 决定。
