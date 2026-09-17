# Memory 适用条件与行为特征设计

> 2026-09-16 已实施后续版本：[复用范围与经验细分整改方案](memory-reuse-scope-remediation.md)。
> 本文保留 v2/v3 逐项子集匹配的历史设计；新审核 v4 增加完整覆盖、范围优先和分页实体目录。
> 当前语义以新方案第 16 节为准；旧经验不会被静默改写。

## 1. 结论与范围

本方案已按用户确认的语义实施两轮：类型化直接复用限制、参考召回分离，以及核心行为逐项
勾选。分组指纹不变；新审核的直接复用按选中条件匹配，不再要求原完整指纹相等。
未变更指纹成分/排名、未迁移已审核 Memory。最初核查基于提交
`133501ad9dd9f774edc3aec4ac7093aab6238492` 和 2026-09-15 本地 DEV 数据。
它补充 [日志语义核对方案](normalization-assistance-design.md) 的下游消费设计，不另建一套 Memory 系统。

问题不是“核心行为太长”这一项，而是把三种用途混成了一个可勾选字段清单：

1. 识别同类事件的特征，包括用于生成指纹的成分。
2. 寻找相关经验的相似特征，包括强、弱特征以及重复索引。
3. 运营明确指定的适用限制，例如只适用于某个源 IP、目标主机或业务资产组。

实施方案：**固定范围只读；核心行为全部展开、默认全选；附加条件只限制直接复用。**
先修正解释和编辑契约，再单独评估指纹粒度。不能用折叠界面掩盖匹配问题，也不能借清理界面
静默改变已审核经验的范围。

## 2. 两个真实候选

### 2.1 Windows 更新组件场景

候选 `MC-8E22698F7BA7`，来源告警 `2651342`，来源运行 `RUN-07946DB2FF24`。
实际必需指纹由 11 项组成，其中包含进程名称、进程映像、路径、父服务、模块和参数。
可选条件又完整提供这 11 项 `behavior_component_core`，并提供其中 1 项的弱特征副本。

`process:wuaucltcore.exe`、`process_image:wuaucltcore.exe` 和进程路径是同一执行对象的不同描述，
不是三个独立业务事件。父进程、祖先进程和当前进程则必须保持角色差异，不能简单按文件名合并。
之前与 `2577758` 对比发现主进程名称和路径指向不同对象，该问题应在标准事实及其消费边界解决，
不能靠放宽匹配、删除 `services.exe` 特征，或只改中文标题解决。[^1]

### 2.2 OpenVPN / SIP 场景

候选 `MC-19AAC4B26DFB` 是 `MEM-52B94F38659F v10` 的待审核修订，修订输入来自告警 `2480991`、
运行 `RUN-FA3BAA861FB0`。重新投影后的必需指纹包含：

| 业务含义 | 保存的技术成分 |
|---|---|
| 检测分类：代理隧道活动 | `attack_family:proxy_tunnel_activity` |
| 服务观察 | `network_service:sip/5060` |
| 服务观察 | `network_service:udp/1194` |
| 协议 | `protocol:udp` |
| 上游技术标签 | `technique:t1190` |

这 5 项同时出现在 `behavior_component` 和 `behavior_component_core`。
其中前 4 项又出现在 `behavior_component_weak`；最后 1 项进入 `behavior_component_strong`。
因此“辅助行为特征”不是额外发现的新证据，而是同一组证据的内部分类。[^1]

这个候选仍是修订草稿，正文引用的是旧 Business Lesson，不能当作已确认的新解释。
旧正文写 UDP/5060，而重新投影的范围还包含 UDP/1194，展示应并列保留差异，不能为了生成一个
好看的“OpenVPN 业务”标题而改写服务观察。这里不重新审判上游 SIP 标签，只区分原始检出标签、
标准服务观察和人工业务解释。

## 3. 已定位的根因

### 3.1 相同事实使用不同字段名，去重只认识其中一份

PingAn `build_applicability()` 把 core/strong/weak 等集合一起放入 `optional_facets`。
指纹解释器却只把组成成分展开成 `behavior_component`。通用 `scope_view` 依照字段名和值判断
是否已被必需条件覆盖，于是 core 和 weak 被误报为新的独立选项。前端恰好又把两种集合都叫
“核心行为”，把 weak 叫“辅助行为特征”。[^2]

这不是两个场景各缺一个特判，而是缺少“同一个语义条件有哪些内部表示、分别用于什么”的契约。

### 3.2 弱特征不等于不参与精确匹配

当前指纹使用全部 core 成分。强、弱分类发生在另一条消费分支，不决定某一项是否进入哈希。
OpenVPN 例子中 4 项被标成弱特征，却全部参与精确匹配；唯一 strong 项是 T1190。
运营无法从“辅助”二字推断匹配作用。[^3]

### 3.3 重复选项可能无益于精确匹配，却影响相似召回

修复前勾选 optional 条件会把它提升为 required，同时加入 context-only 的必需条件。
所以“该条件已经被指纹覆盖”只在精确匹配路径上成立，不一定在相似召回路径上成立。[^4]

因此不能把所有重复项从保存的契约里删掉。要清理的是技术集合充当业务控件的方式；对已审核的
附加限制必须继续执行，对确有参考范围限制需求的条件，应以业务字段重新表达。

### 3.4 一组内任意命中，不是不同业务字段同时命中

`role_entity` 把 `source:36.103.173.7` 和 `destination:61.241.23.134` 放在同一组。
修复前两项都勾选时，含义是源或目标命中任意一个；不是源和目标同时命中。
`entity` 同样可能混合 IP、主机、账号、资产组，直接把它们变成 checkbox 容易产生错误预期。[^4]

### 3.5 相同事实重复参与相似度计分

当前评分按字段和值分别累加。上述 5 个事实在四套 behavior 集合中贡献 5+5+1+4 次匹配计数，
这些键当前都使用每项 1.0 的评分权重。它不是 15 条独立佐证，也不是相关概率。[^5]
本次只证明重复计分存在，尚未证明它改变了真实 Top-K 或最终结论。

### 3.6 新版特征的展示解释没有跟进

Profile 8 / v6 已支持语义核对补充，但 scope 解释器仍固定用 v5 哈希结构验证成分。
构造合法 v6 指纹进行只读检查，得到 `unresolved_fingerprint_keys=["behavior_fingerprint"]`。
这说明扩展不仅要新增字段，还要一起覆盖事实、指纹、展示解释和匹配验证。[^2]

## 4. 修复前的只读复现结果

本次没有运行新告警，没有 LLM 调用，没有确认、修订或启停任何 Memory。
以下是从真实候选复制的内存对象，对生产函数的定向模拟；不等于完整检索或模型质量评测。

| 检查 | 结果 | 意义 |
|---|---|---|
| 原候选范围与原 facets | `applicable` | 原条件全部满足 |
| 添加 `core:udp/1194` 再匹配原 facets | 仍为 `applicable` | 对本次精确匹配没有新增区分 |
| 把一个服务从 UDP/1194 改成 UDP/443，重算指纹 | `partial`，允许相似参考 | SIP/5060、分类和 T1190 仍相同；Profile 冲突检查未拒绝 |
| 同样变体，加上 `core:udp/1194` 限制 | `partial`，不允许相似参考 | 重复控件实际上改变了参考范围 |
| 勾选源、目标两个角色 IP，查询仅保留其中一个 | 仍为 `applicable` | 当前同组是 OR，而不是源和目标的 AND |
| 5 个核心事实计算行为别名匹配数 | 15 次 | 同事实重复计分 |
| 合法 v6 指纹进入当前解释器 | 未能展开指纹 | 展示版本支持缺口 |

本地候选表当时共有 13 条，5 条待审核；其中 2 条存在“已覆盖的 core/weak 又作为 additional”问题，
正是上面两个候选。这个比例只描述当前小规模演练库，不代表生产发生率。

## 5. 面向运营的页面

### 5.1 直接复用条件：展示当前保存的条件

不再把 fingerprint/core/weak/strong 当作四个业务概念展示。默认用一张简洁的描述表：

| 项目 | OpenVPN / SIP 候选的展示内容 |
|---|---|
| 检测规则 | 红队IP监控 / RPAADM_000558 |
| 告警来源 | sec_guard_apt / 360天眼APT |
| 识别的行为 | 代理隧道活动；UDP；服务观察 SIP/5060、UDP/1194；技术标签 T1190 |
| 数据使用范围 | 告警演练数据 |

Windows 场景用相同组件的类型化行：执行程序、启动上下文、调用方式、操作目标。
11 个参数级片段按语义分行全部展示，不折叠。每个值独立勾选，默认全选；已经审核过的
选择则恢复原选择，不擅自全选。至少保留一项行为，避免退化为同 rule code 无条件套用结论。
所有条件值来自已保存且可核对的范围，不从 Business Lesson 文案倒推机器条件。

hash、特征版本、原始键和值、同一事实的索引别名留在技术详情；正常页面不要求运营认识它们。
未知版本使用原保存范围和“组成未能展开”说明，绝不拿当前版本猜旧 hash。

### 5.2 进一步限定：只编辑运营真正想限制的内容

默认不展开全部可选 checkbox。提供“添加限制”，按实际可用字段选择，添加后显示为条件行：

```text
源 IP      是其中之一     36.103.173.7
且 目标 IP 是其中之一     61.241.23.134
```

同一业务字段多个值为 OR，不同字段为 AND。源和目标不能再落到一个混合 OR 集合里。
来源类型、主机、账号、资产组等也按业务字段区分，保留移除和修改值的能力。
固定规则、来源、产品与数据范围不提供 checkbox。核心行为可在本次审核中取消，取消表示
不要求出现，不表示禁止出现。多个勾选行为为 AND，包括 SIP/5060 与 UDP/1194 两个服务值。
附加条件内多值仍为 OR，来源 IP 与目标 IP 两类条件为 AND。新增草稿附加限制可移除；
已保存附加限制不因取消草稿勾选而被放宽，已有确认/修订仍受版本与范围校验。

以本次网络候选为例，源 IP、目标 IP、资产组是优先提供的限制。
NDR、场景、MITRE 战术等可以放在“更多字段”，不能伪称它们比已有条件更有信息量。
已被指纹保证的端口不再作为重复“核心行为”选项。core/strong/weak 是内部算法分类，
不是三个由运营控制的条件集合。

**用户已确认：新增限制只收窄直接复用结论，不收窄相似参考。**
额外 IP/主机条件未命中时，只要符合原有相关性与数据范围门槛，Memory 仍可交给模型比较。
这不保证一定入选 Top-K，也不保证模型必定采用其结论。租户、环境、版本、排除条件、
现有语义冲突门槛仍执行；不是让所有 Memory 都能跨范围召回。
旧 v1 已审核 required/context 限制保留原含义，并在页面标注；不做静默迁移。

“仅供研判参考”不是“不能改变模型判断”：模型结合当前告警重新判断，可以采纳经验里的
结论；区别是程序不会直接套用历史结论。直接复用则按审核条件自动沿用结论，仍保留企业
处置策略及原有执行权限边界。

这里借鉴 PatternFly 的属性-值筛选、持续显示已选条件和明确布尔关系的设计，而不照搬其外观。
筛选是一个可解释的条件表达式，不是一张内部数据结构清单。[^9]

### 5.3 后续：待审核草稿的只读试匹配（本轮未实现）

复用已有 record `match-test`，向待审核候选及修订草稿扩展只读预览。
输入一个已运行的告警，后端使用指定 run 的冻结事实和同一套生产匹配逻辑，展示：

- 范围全部匹配；当前使用方式允许直接复用，或仅供参考。
- 仅相似匹配；具体相同点、差异点，以及只能参考的原因。
- 不会使用；明确指出不符合的字段或停用、到期等原因。

未运行过或缺少同版本冻结事实的告警，明确提示不能作完整判断；原始 PKL 索引的对比只能标为
索引估计。预览不能重新调用模型，也不产生候选、Pattern 计数或 Memory 使用记录。
候选尚未确认时，结果必须写成“按当前草稿确认后”，不能宣称已经能影响新告警。[^6]

“范围全部匹配”不保证所有后续决策都相同；经验使用方式、有效期、当前反证和企业处置策略
仍沿用已有流程。这里不再引入新运营状态或审批流程。

## 6. 统一的后端设计

### 6.1 复用现有边界

保留 `facets`、`SocMemoryApplicabilitySpec`、Profile、检索服务和治理服务。
扩展现有 scope 投影，不新建服务、数据库、模型调用或可编程规则平台：

```text
Adapter + 已启用的语义核对
  -> 标准对象、行为、来源关系
  -> PingAn Profile 的稳定特征与用途说明
  -> 已保存的适用范围 + 人工附加限制
  -> 同一个匹配器
       -> 检索 / 直接复用检查
       -> 已有 Record 试匹配 / 修订范围比较
       -> 页面解释
```

通用 Memory 只认识类型化条件、集合关系和匹配结果；PingAn 的别名和分类知识留在 Integration。
前端不重建 hash、猜字段包含关系或另写一套匹配器。

### 6.2 同一个特征需要有用途与身份，而不是多个可编辑副本

Profile 提供语义特征身份、值、对象/角色、来源、是否参与精确签名、是否用于相似度、是否可人工
限定等说明。这里的身份是解释内部重复表示的映射，不是新建“原子证据系统”。

例如，同一条 UDP/1194 服务观察可在 core、weak、network_service 中出现，但业务条件只有一项。
不能仅按显示字符串去重：主进程、父进程、不同网络观察的相同值可能代表不同对象。
多条日志里的源 IP、目标 IP 或文件哈希，不能通过集合拼接变成从未存在过的一条连接/文件。

当前 `scope_view` 保留已有三种投影：已覆盖、可增加限制、内部相似度特征；未知 hash 不展开。
字段组成只在同版本重新核验成功后呈现。页面显示业务条件，不要求运营理解这些投影分类。

### 6.3 限制表达必须能承载业务 AND

现有 `required_facets[key]=[values]` 是键间 AND、键内 OR，不足以把混合 `role_entity/entity`
可靠拆成多个独立限制。仅把前端分成两栏仍会在保存时回到 OR，属于假修复。

已在现有 applicability 增加 `reuse_conditions`，使用 `soc.memory_applicability_policy.v2`。
旧 JSON 不含该字段时按空列表读取，无新表或迁移。字段示例：

```json
{
  "reuse_conditions": [
    {"facet_key": "role_entity", "value_prefix": "source", "values": ["source:36.103.173.7"]},
    {"facet_key": "role_entity", "value_prefix": "destination", "values": ["destination:61.241.23.134"]}
  ],
  "policy_version": "soc.memory_applicability_policy.v2"
}
```

只允许候选已经观察到的值，不支持自由表达式、任意 JSONPath、正则或自动 CIDR 泛化。
不同条件 AND；同条件值 OR。这里保证告警内源/目标条件都存在，不声称多条网络观察的两端
已被组合成同一条连接；连接级约束需独立对象绑定设计。

匹配器先执行原有基础范围，再检查附加限制。基础精确命中但额外限定不匹配时，返回
`partial + context_only_allowed=true`；基础已是合法相似参考时，额外限定不再阻断它。
报告记录 `matched_reuse_conditions` / `missing_reuse_conditions`，缺失条件进入模型 Memory
对照上下文。只有 `applicable` 才可进入原有 Directive 流程。治理范围身份与冲突比较也纳入
附加限制，不能把两个不同 IP 限制的记录误认成完全同范围。

已有保存的混合 OR 限制继续按旧含义执行和显示，不能自动变成更严格的 AND。
新草稿才使用新编辑器；接口升级后不认识新条件的旧客户端应拒绝编辑，不能静默丢字段。
签名和冲突比较需纳入完整的新增条件，不能允许同范围相反结论绕过既有治理。[^7]

### 6.4 审核选中的行为条件（policy v3，已实施）

`SocMemoryApplicabilitySpec.selected_behavior_components` 为可空有序列表，接收时去重、排序、
统一大小写；每项必须来自注册 Profile 验证过的原指纹组成。共享 Memory 不猜测厂商字段。

- `null`：保留旧的完整指纹匹配语义。
- 非空列表：保留原 `required_facets.behavior_fingerprint` 作为来源审计；实际直接复用检查
  改为选中集合全部包含在当前 `behavior_component_core` 中（旧无 core 投影使用 component）。
- 未选行为可缺失、变化或仍存在；新告警额外出现其他行为不自动使所选条件失败。
- 原固定范围、Profile、数据范围、有效期、排除条件仍执行。不得以选行为为由修改它们。
- 原有 Profile 行为冲突过滤继续约束相似参考。显式审核的行为范围全部匹配且满足附加限制时，
  按选择后的范围执行，不再用已经取消的旧服务/类型去否决直接复用。
- 审核确认生成的 Directive 要求选中行为匹配，不再额外要求原 hash；未匹配的附加 IP 仍阻止
  Directive，但不阻止原本符合门槛的 context-only 参考。
- 同一组选择调换顺序或重复输入，匹配结果与治理范围身份相同。原 PingAn 分组指纹已经使用
  `sorted(dict.fromkeys(...))`；通用 hash 的 `sort_keys=True` 只处理字典，列表排序仍由特征生产者负责。

示例：取消 SIP/5060 后，其他已选条件满足、只有 UDP/1194 的新告警可匹配；保留 SIP 勾选则
不允许直接复用。来源 IP 与目标 IP 若都选择，两项都必须匹配，不能只命中其中一个。

同一套选择传入页面审核、AI 经验草稿、治理预览与最终确认。审核前不落库；确认后使用原有
版本、审计、启用和修订流程。范围身份与重叠检查忽略作为来源的旧 hash，比较实际固定范围、
选择集合和附加条件，防止不同来源指纹掩盖相反经验的重叠。不同正向行为集合不能自动证明
互斥，因为同一告警可能同时包含它们；本轮没有增加“禁止出现某行为”的编辑器。

报告增加 `selected_behavior_components`、`matched_behavior_components`、
`missing_behavior_components`，LLM 比较投影携带有界选中项和缺失项。它们解释复用边界，
不把历史 Memory 差异伪装成当前告警的证据缺失。

## 7. 指纹粒度的后续改造

指纹本身继续保留。hash 不是导致难读或难匹配的原因，问题是它背后的成分选择、对象身份和
稳定性。不能为了提高命中率把“同 rule code”直接当成相同行为。

| 特征类别 | 建议方向 |
|---|---|
| 检测规则、来源、数据范围 | 延续明确作用域，不要求模型重新猜测 |
| 当前进程、父进程、祖先进程 | 按对象和角色统一，消除错误回填及同对象重复表达 |
| 行为与目标 | 用实际操作、模块/服务、检测对象、漏洞等区分，而非背景进程或泛标签主导 |
| 命令参数 | 参数名与作用分开评估；随机值不进核心，改变行为含义的参数不能盲删 |
| 路径 | 保留有意义的受管目录/目标类型；随机安装目录和身份噪声与默认模式分离 |
| IP、UM、哈希 | 原始证据保留；需要限定时由运营选择，不把每条告警的身份差异默认固化 |
| 模型补充的检测描述 | 绑定具体对象，优先稳定的上游标记与标准枚举；自由改写的措辞不能直接造成新模式 |
| 相似度 | 按独立语义特征计分，避免 core/strong/weak 多次奖励同一个事实 |

强弱分类只服务算法，不作为“必要/可选”的替代词。不能根据这一条只有 T1190 为强特征就直接
增加更多拒绝门槛，仍应评估相关经验是否能帮助模型。精确范围与相似度是不同职责；检索系统
区分 yes/no 过滤与相关性排序的做法可借鉴，但不要求项目引入 Elasticsearch。[^10]

这部分不是第一轮 UI 去重的隐含变更，必须单独验证并版本化。当前 v6 还会合入旧成分，
不能认为启用 LLM 核对就自动解决了重复或过细的指纹。[^8]

## 8. 实施顺序与验收

### 第一轮：纠正适用条件页面的语义（已实施）

- 完整解释当前 v5/v6；按语义身份去重，技术 core/weak/strong 不直接作为控件。
- 两个真实候选共用页面，网络和终端只按字段类型分行，不按 alert_id 特判。
- 显示现有已保存附加限制，保留旧 OR 含义；不迁移 hash、不删除 Memory、不改变召回或排名。
- 新条件明确只影响直接复用；旧版共享范围限制单独标注，不扩大其原审核权限。

验收：同一个事实不再出现两次“核心行为”；所有固定条件可展开；旧数据完整；只读刷新和查看
不增加模型调用。未知版本不伪造解释。

### 第二轮：真正可编辑的业务限制（已实施）；草稿试匹配另行实施

- 服务端支持源/目标、主机、账号、资产组等独立条件，更新有限的 schema 和生产 matcher。
- 生成经验、确认、修订预览、冲突治理、检索和 record match-test 共用同一条件处理。
- 已有 Record match-test 消费新 matcher；候选草稿新增试匹配入口尚未实现。
- 确认前明确新旧范围变化，提交时检查候选/前置 Memory 版本；不自动确认任何候选。

验收：源和目标都限定时只匹配一个必须不通过；同一源 IP 可允许多个候选值；删除草稿新增限制
恢复基础范围；旧混合 OR 完全不变；新增限定只影响直接复用，不意外关闭相似参考。

| 输入与条件 | 新结果 |
|---|---|
| 基础指纹匹配，额外 IP 也匹配 | `applicable`，仍需已审核 Directive 才能直接复用 |
| 基础指纹匹配，额外 IP 不匹配 | 仅供模型参考，不直接复用 |
| 核心行为有差异，但原相似门槛满足，额外 IP 不匹配 | 仍可作为参考候选 |
| 源与目标都被限定，仅源匹配 | 不直接复用，目标列入缺失限定 |
| 不同租户/环境/版本或命中原有排除条件 | 原门槛继续拒绝，不因新规则而放宽 |

### 第二轮补充：逐项选择核心行为（已实施）

用户确认固定条件不可编辑、核心行为默认全选且全部展开。Policy v3 按审核选择进行逐项
匹配，保留完整来源 hash，避免页面取消后后台仍要求完整指纹相同。至少保留一个行为；
未知/无法核验的原指纹不开放此编辑。旧已审核记录不自动升级或修改。

- 167 项后端聚焦回归通过，包含确认、检索、模型投影、Directive 改判及新旧范围冲突治理。
- 7 项前端组件测试和 3 项 Playwright 交互测试通过；TypeScript、ESLint 通过。
- 两个真实候选在 1920/390 宽度四次只读检查通过，核心行为无折叠、无重复附加选项、无横向溢出。
- 六个只读模拟范围变体通过：四个附加限制变体，以及缺少 SIP 的查询在全选/取消 SIP 两种设置
  下分别不能/可以直接复用；选择顺序和重复项不改变结果。
- 没有调用真实模型、审核或修改业务 Memory、清空数据库或升级分组索引。

结果：`backend/.deer-flow/soc-validation/memory-behavior-selection-20260915/`。
这些验证证明契约和交互按选择执行，不代表取消具体行为条件后就一定具有更高业务准确率。

### 第三轮：提高同类识别与相似召回质量

- 用 `2651342/2577758` 检查同事件因进程角色映射不同被拆散。
- 用 `2448412/2448580/2464210/2455416` 检查“同背景进程、不同检测对象/类型”被错误合并。
- 用 OpenVPN/SIP 与 UDP/1194、HTTP/8080、不同 CVE 检查服务和攻击行为差异仍保留。
- 用地址、UM、随机编号变化的配对样本检查泛化；用真实已运行结果评估别名去重前后的 Top-K。
- 数据集原始分组是估计基线，不能把 15,286 条全部宣称已做过模型补充或新指纹验收。

指标：应同组却拆分、应分组却合并、独立特征数、相似召回差异、人工范围修改次数、预览与
实际匹配一致率。人工未标注配对前，不报告未经验证的“准确率提升”。

## 9. 风险与不做事项

第一轮解释优化不应触发 Profile 升级或索引重建。第三轮改变签名成分时则必须保留旧版本读取，
给出迁移差异预览，再由明确发布流程决定旧 Memory 如何修订；不能重新 hash 已审核记录。

不新建归一化运维告警队列，不让运营处理 core/weak 冗余；问题通过现有 run 审计及按需核查暴露。
不新增每次打开页面的 LLM 调用，不用模型自由生成具有执行效力的匹配表达式，不改企业策略优先级。
不把“全部匹配”与“已授权外部处置”混为一谈，也不把参考经验降格成对研判无用的附件。

## 10. 证据、边界与复核入口

最初只读审计时浏览器连接失败，仅核对 API 与代码；不能将其当作视觉验收。
实施后的测试与截图另记月度归档，明确区分模拟的条件变体与实际告警。

只读模拟复用了 `promote_memory_applicability_facets()`、`evaluate_memory_applicability()`、
`score_memory_record()`、Profile 冲突检查和 `build_memory_scope_view()`。
除两条真实候选原始状态外，端口变体、角色缺失和 v6 输入均为内存构造，不是另一条真实告警。
没有对内网 Provider、端到端 LLM 结论或生产误报率作出新验证声明。

取证文件摘要：对 `{candidate_id, facets, applicability}` 使用 UTF-8、`ensure_ascii=False`、
`sort_keys=True`、`separators=(',', ':')` 序列化后 SHA-256：

- `MC-8E22698F7BA7`：`e20430253911fbf80d3ad6c3b0b9f85bc54d28cb72161019ccc397f42700aa70`。
- `MC-19AAC4B26DFB`：`2e249e7f14676c7e3c80e5346fb4d3eac4052d81ad8b2a69984f624f0a305c09`。

独立核查可使用下面的只读入口；真实数据后来发生人工修订时，摘要和展示可能变化：

```bash
curl -fsS http://localhost:2026/api/soc/memory/candidates/MC-8E22698F7BA7
curl -fsS http://localhost:2026/api/soc/memory/candidates/MC-19AAC4B26DFB
```

### 最小行为复现

从项目根目录执行，只读取候选并构造内存查询，不写数据库、不调用 LLM。
该片段验证适用条件函数，不包含启停、有效期、完整召回排名及最终指令决策。

```bash
env PYTHONPATH=backend backend/.venv/bin/python - <<'PY'
import copy
import json
from types import SimpleNamespace
from urllib.request import urlopen
from soc_agent.contracts import (
    SocMemoryApplicabilitySpec, SocMemoryCandidateType, SocMemoryQuery,
)
from soc_agent.memory.lessons import promote_memory_applicability_facets
from soc_agent.memory.scoring import evaluate_memory_applicability
from soc_agent.utils.hashing import stable_hash

url = 'http://localhost:2026/api/soc/memory/candidates/MC-19AAC4B26DFB'
with urlopen(url, timeout=15) as response:
    data = json.load(response)
spec = SocMemoryApplicabilitySpec.model_validate(data['applicability'])
facets = data['facets']
metadata = {
    'memory_profile_id': spec.profile_id,
    'memory_profile_version': spec.profile_version,
    'memory_feature_schema_version': spec.feature_schema_version,
}

def evaluate(scope, values):
    record = SimpleNamespace(
        applicability=scope, memory_type=SocMemoryCandidateType.DETECTION_LESSON,
    )
    query = SocMemoryQuery(facets=values, metadata=metadata)
    result = evaluate_memory_applicability(record, query, {})
    return {
        'status': result.status.value,
        'context_only_allowed': result.context_only_allowed,
        'missing': result.missing_required_facet_keys,
    }

variant = copy.deepcopy(facets)
for key in ('behavior_component', 'behavior_component_core',
            'behavior_component_weak', 'network_service'):
    variant[key] = [v.replace('udp/1194', 'udp/443') for v in variant[key]]
variant['behavior_fingerprint'] = [stable_hash({
    'schema_version': 'pingan.soc.memory_behavior_fingerprint.v5',
    'components': sorted(variant['behavior_component_core']),
})]
limited = promote_memory_applicability_facets(
    spec, [], {'behavior_component_core': ['network_service:udp/1194']},
)
role_limited = promote_memory_applicability_facets(
    spec, [], {'role_entity': facets['role_entity']},
)
one_role = copy.deepcopy(facets)
one_role['role_entity'] = [facets['role_entity'][0]]
print(json.dumps({
    'original': evaluate(spec, facets),
    'original_with_core_limit': evaluate(limited, facets),
    'variant': evaluate(spec, variant),
    'variant_with_core_limit': evaluate(limited, variant),
    'one_role': evaluate(role_limited, one_role),
}, ensure_ascii=False, indent=2))
PY
```

### 来源

[^1]: 本地 DEV 候选详情 API，两个候选及其 source run，2026-09-15 读取；仅为当时的演练状态。进程类成分生成见 [facets.py](../../../backend/soc_agent/memory/facets.py) 的 `_behavior_components()` 和 [profile.py](../../../backend/soc_agent/integrations/pingan/memory/profile.py) 的 `_pingan_canonical_behavior_components()`。
[^2]: [PingAn 范围解释器](../../../backend/soc_agent/integrations/pingan/memory/scope_view.py)、[通用范围投影](../../../backend/soc_agent/memory/scope_view.py)、[前端适用范围组件](../../../frontend/src/components/workspace/soc/soc-memory-scope.tsx)。测试 `test_soc_memory_scope_view.py` 的主要 fixture 没有把 core/weak 同时放进 optional，因此未覆盖本次真实组合。
[^3]: [profile.py](../../../backend/soc_agent/integrations/pingan/memory/profile.py) 的 `_project_pingan_facets()`、`_is_strong_behavior_component()`。
[^4]: [lessons.py](../../../backend/soc_agent/memory/lessons.py) 的 `promote_memory_applicability_facets()`；[scoring.py](../../../backend/soc_agent/memory/scoring.py) 的 `_facet_overlaps()`、`_context_only_applicability_satisfied()`。
[^5]: [scoring.py](../../../backend/soc_agent/memory/scoring.py) 的 `score_memory_record()`、`memory_facet_weight()`。重复计分不等于权限授予，最终仍经过适用范围和使用模式判断。
[^6]: [service.py](../../../backend/soc_agent/core/service.py) 的 `SocMemoryService.test_record_match()`；[soc_memory.py](../../../backend/app/gateway/routers/soc_memory.py) 的 `/records/{memory_id}/match-test`。目前已有 record 只读测试，不应重复开发另一套匹配算法。
[^7]: [governance.py](../../../backend/soc_agent/memory/governance.py) 的 `scope_identity()`、`scope_relation()`；[Memory 工程约束](../../../backend/soc_agent/memory/AGENTS.md)。
[^8]: [semantic_features.py](../../../backend/soc_agent/integrations/pingan/memory/semantic_features.py) 及 [日志语义核对方案](normalization-assistance-design.md) 第 15、18 节。
[^9]: PatternFly, [Filters: Design Guidelines](https://www.patternfly.org/patterns/filters/design-guidelines/)，访问于 2026-09-15。采用属性-值、已选条件反馈及关系可见性的原则，不把通用表格筛选直接视为安全授权设计。
[^10]: Elastic, [Querying and Filtering](https://www.elastic.co/docs/explore-analyze/query-filter)，访问于 2026-09-15。其区分布尔过滤和相关性检索，支持本方案分清确定性限制与相似度的架构取舍。
