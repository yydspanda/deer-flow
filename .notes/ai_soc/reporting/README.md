# SOC Agent Reporting Pack / 汇报材料入口

本目录服务于内网汇报、产品介绍、技术评审和现场演示。材料已经把产品闭环、模块化能力体系、真实性边界和下一阶段要求
整理成可独立阅读的版本；汇报人员不需要再跳转研发台账、工程契约或源代码。

## 使用顺序

| 场景 | 先读 | 配套材料 |
|---|---|---|
| 项目阶段汇报 | [`project-brief.md`](project-brief.md) | [`reporting-faq.md`](reporting-faq.md) |
| 现场能力演示 | [`capability-demo-runbook.md`](capability-demo-runbook.md) | Web `SOC 运营 -> 语料验证 -> 场景导览` |
| 架构/研发评审 | [`technical-solution.md`](technical-solution.md) | [`reporting-faq.md`](reporting-faq.md) |
| 核对“哪些已经真实接通” | [`project-brief.md`](project-brief.md) 的当前成熟度 | [`technical-solution.md`](technical-solution.md) 的演示边界 |

## 汇报口径规则

1. **演示链路真实**：Runtime、Adapter、LLM、证据校验、Decision、Pattern、Candidate、Memory、审核和审计均走完整产品流程，不使用预制结论冒充运行结果。
2. **演示环境不是生产**：当前语料工作台是 DEV 历史回放、SQLite、内网安全能力接口关闭/模拟、企业专属策略关闭、外部动作关闭。
3. **历史运营标签不是独立真值**：可用于对照，但不能直接宣称模型准确率。
4. **不要承诺未经测量的收益**：准确率、节省工时、自动处置率、P95、成本和 SLO 需要内网 Shadow 数据。
5. **不要把 LLM 描述成控制系统**：确定性 Runtime 掌握流程、状态和权限；LLM 处理有界不确定性。

## 当前演示数据

- `4,343` 条历史告警。
- `7` 类 canonical source：NDR、NIDS、EDR、HIDS、SIEM、Threat Intel 和 Other。
- `310` 个检测规则标识，形成 `1,280` 个行为组。
- `4,082` 条具备行为指纹，`3,069` 条满足当前决策型 Pattern 基础条件。
- `3,566` 条带历史运营处置标签，其中 `2,798` 条忽略、`768` 条转交；这些是 operational outcome，不等价于独立 ground truth。

Web 场景导览会在打开时核对固定案例。告警缺失或行为指纹升级导致重新分组时，页面会标记语料漂移，
阻止继续使用过期演示脚本。
