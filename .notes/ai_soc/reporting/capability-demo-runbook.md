# SOC Agent Capability Demo Runbook / 能力演示手册

## 1. 演示目标

现场只证明五件事：

1. **告警接得住**：多来源、脏字段和原始 message 能进入统一事实模型，原始输入仍可追溯。
2. **模型真正在研判**：主模型集中判断风险、场景、方向和角色，不是传统规则系统换皮。
3. **结果可以复盘**：模型原判、Memory、企业策略和最终结论分别记录。
4. **经验能够复用**：重复 Pattern 经运营确认后形成 Business Lesson，不是每条告警都写一条 Memory。
5. **能力可以组合和迁移**：Skill 管方法、企业知识管稳定事实、Memory 管审核经验、MCP/Provider 管实时能力、Policy 管处置权限；平安定制不会污染通用 Runtime。

## 2. 汇报前检查

内网 Mac 项目默认位于 `/Users/zhangjianming627/deer-flow`。从项目根目录执行：

```bash
python3.12 scripts/soc_pingan_macos_host_dev.py \
  --python "$(command -v python3.12)" check

python3.12 scripts/soc_pingan_macos_host_dev.py \
  --python "$(command -v python3.12)" start --daemon
```

打开：

```text
http://localhost:2026/workspace/soc/corpus-validation
```

若同事从局域网访问，使用启动日志给出的 `http://<Mac-IP>:2026`；防火墙必须允许该端口。

页面检查：

- 顶部显示 `DEV`、`SQLite`、真实模型名和当前 reasoning/role-verifier 开关。
- 安全带显示“内网安全能力接口关闭/模拟、企业专属策略关闭、外部动作关闭”。
- `场景导览` 显示 `5 组主线 / 2 组备选 / 语料校验通过`。
- 不在汇报前清空已经彩排好的数据库；先保留 Candidate 和 confirmed Memory 演示状态。

## 3. 推荐现场形态

采用“预置状态 + 一次实时运行”，不要现场连续调用十几次模型：

| 内容 | 汇报前状态 | 现场动作 |
|---|---|---|
| APT 全链路 | 已跑过也可以 | 重跑 `1965449` 或直接打开完整审计 |
| 同规则不同语义 | 无需运行 | 在两个演示目标间切换并对比行为摘要 |
| GalaxyLab 误报经验 | Memory 已确认并启用 | 运行下一条精确匹配样本，展示 Base -> Memory -> Effective |
| Sliver 风险经验 | Candidate 待审或 Memory 已确认 | 展示审核内容；时间充足时现场确认并跑下一条 |
| 反弹 Shell / 弱证据 | 预跑 | 作为答疑备用 |

## 4. 十分钟主线

### 0:00-0:50 项目定位

页面：`语料验证` 顶部与数据摘要。

话术：

> 我们做的不是一个会聊天的安全助手，而是一套 SOC 执行底座。它接住不同来源告警，用固定 Runtime 管住流程和权限，用大模型处理场景、方向、角色和风险判断，再把运营确认沉淀成可审计、可失效的经验。

补充口径：

> 系统没有把所有能力塞进一个 Prompt。Skill 告诉模型怎么分析，企业知识提供稳定事实，Memory 提供审核后的历史经验，MCP/Provider 查询当前系统，Policy 决定最终能如何处置；每一层都能独立配置、版本化和审计。

### 0:50-3:20 主线 01：完整 Runtime

点击：`APT 弱口令：一条告警如何形成可审计结论 -> 定位案例 -> Alert 1965449`。

现场展示：

1. `运行轨迹 / Runtime Trace`：Normalize、Entity、Fact、Skill、LLM、Validation、Decision、Memory。
2. `打开完整审计`：重点切换 `02 Source Input`、`03 Canonical Normalization`、`06 Bounded Analysis Input`、`07 Model Output`、`09 决策来源与演变（Decision Lineage）`。
3. 强调原始 payload 保留；模型看到的是裁剪、类型化、有引用 ID 的上下文。

话术：

> 模型负责给出有价值的安全判断，但它不能说“完成了”就算完成。Runtime 会校验 JSON、恢复证据引用、检查引用是否存在，并把模型原判、Memory 调整、企业专属策略和最终结论分开留痕。某个可选区块失败，也不会拖垮已经有效的核心结论。

### 3:20-5:00 主线 02：同规则不同语义

依次点击：

- `红队IP监控 A · OpenVPN UDP/1194`
- `红队IP监控 B · PLC CVE-2017-7924`

话术：

> 两组都是 `RPAADM_000558 / 红队IP监控`。如果只按 rule_code 记忆，OpenVPN 的无风险经验可能错误覆盖 PLC 漏洞。系统先用 rule_code 找大类，再以攻击族、协议、端口、CVE、进程等 canonical 行为形成强指纹，所以同规则可以产生不同 Pattern 和不同 Business Lesson。

验收点：规则名相同，但行为摘要与 `CG-*` 不同。

### 5:00-7:30 主线 03：误报经验闭环

点击：`GalaxyLab SAM Dump · Windows 更新进程链`。

预演序列：

```text
1974113 -> 1980607 -> 1980502 -> 1980722 -> 1982981
  -> Candidate 审核为 false_positive
  -> AI 生成并由运营确认 Business Lesson
  -> 开启检索和“未来精确匹配”
  -> 运行 1984426
```

建议 Business Lesson 核心口径：

> `wuaucltcore.exe` 由 `wuauserv` 服务链以一致模块和参数访问受保护注册表，已确认属于 Windows 更新部署行为；只有规则、强行为指纹、进程链和适用范围一致且无当前反证时，才复用误报结论。

现场看：

- `M-*` Confirmed Memory 引用。
- `Base Decision` 与 `Effective Decision` 前后对比。
- `Memory Decision` 的来源 ID、版本、适用条件和使用记录。

### 7:30-9:00 主线 04：真实风险经验

点击：`Sliver 远控木马 · HTTP C2 心跳`。

预演序列：

```text
1979525 -> 1979543 -> 1979582 -> 1979692 -> 1979731
  -> Candidate 审核为 true_positive
  -> 运行 1979722 验证复用
```

话术：

> Memory 不等于白名单。真实风险经验同样可以复用，帮助后续告警更快转交或进入响应。但研判结论、处置策略、动作授权和外部执行是四层记录，Memory 自己不能越权封禁或隔离。

### 9:00-10:00 收口

话术：

> 现在已经走通的是完整产品闭环和可审计链路；当前屏幕仍是 DEV 历史回放、SQLite，内网安全能力接口处于关闭/模拟状态，不能冒充生产效果。下一阶段是在内网接入 ZEUS/CMDB/TI 等真实只读能力，做 Shadow 评测，再用真实数据决定自动化范围。

## 5. 备选演示

### 反弹 Shell：方向与角色

目标：`2452775`。

适合回答：为什么不能写死 `source=attacker`、模型如何处理反向连接、角色不确定是否拖垮整条告警。

### 可疑邮件：弱证据边界

目标：`1965802`。

适合回答：是否每条告警都会写 Memory。答案是不；Runtime 仍给当前结论，但缺少强行为指纹时不自动产生决策型经验。

### HIDS：换厂商怎么接

目标：`1965448`。

适合回答：PingAn 特有字段是否污染通用系统。展示 raw message、Adapter provenance 和 canonical process facts。

## 6. 现场故障预案

| 现象 | 处理 | 口径 |
|---|---|---|
| 模型慢 | 使用已跑 Run，直接打开完整审计 | 历史 Run 是持久化真实结果，不是截图或伪造数据 |
| 模型失败 | 展示 failure kind、provider journal 和 retryability | 可观测失败比静默 fallback 更适合生产 |
| Candidate 未自动生成 | 使用单告警“提炼 Candidate”入口 | 这是明确人工采纳，不冒充自动质量门 |
| 演示目标显示语料漂移 | 停止使用该目标，改用备选组 | 指纹或数据版本已变化，清单故意 fail closed |
| 页面不可用 | 检查本地服务、nginx、Gateway 和 3000/8001/2026 端口 | 不在汇报现场临时绕过错误 |

## 7. 不要这样说

- 不说“已经达到生产准确率”，当前没有独立、具名审核的代表性真值集。
- 不说“已经接通全部平安系统”，当前演示 Provider 是 off/mock。
- 不说“模型会自主封禁一切”，动作仍受策略、目标一致性、授权和执行审计约束。
- 不说“4343 条就是线上吞吐压测”，它是历史语料产品/兼容性演示。
- 不说“同 rule_code 结论都一样”，这正是行为指纹要解决的问题。
