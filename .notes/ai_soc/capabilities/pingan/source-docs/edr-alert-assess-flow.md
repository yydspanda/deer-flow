# EDR 研判流程详细文档

> 核心文件：`my_workflows/zeus/flows/edr_alert_assess.py`
> 引擎：LlamaIndex `Workflow` + `@step` 异步方法
> 大模型：平安内部 LLM（OpenAI compatible 接口）

---

## 一、整体架构

EDR 研判是一个**漏斗式决策 + 后置闭环处置**的两阶段流水线：

```
第一阶段：研判决策（忽略 / 转交）
┌──────────────────────────────────────────────────────────────────┐
│ StartEvent                                                       │
│   │ 原始告警数据 (AlertRoot)                                      │
│   ▼                                                              │
│ [1] 数据预处理 → JudgeDataEvent    提取ID、日志、路径、关联预警    │
│   │                                                              │
│ [2] 关联预警历史判断              ← 能快速判定的立刻返回           │
│   ├─ 能判断 → FinalEvent（直接结束）                              │
│   └─ 不能判断                                                      │
│       │                                                          │
│ [3] 知识库研判 → MainStartEvent    （预留扩展，当前透传）          │
│   │                                                              │
│ [4] 规则分流                                                       │
│   ├─ LoginData/System 类规则 → [5] judge_by_read_sys            │
│   ├─ 提权类规则 RPAADM_002042 → [6] judge_by_privilege          │
│   └─ 其他所有规则 → [7] judge_by_main_llm（通用兜底）             │
│   │                                                              │
│   ▼ 三个分支收敛                                                   │
│   ▼ FinalEvent ← 研判结论：忽略 或 转交                            │
└──────────────────────────────────────────────────────────────────┘
                            │
                            ▼
第二阶段：处置闭环（转交后才执行）
┌──────────────────────────────────────────────────────────────────┐
│ [8] 生成攻击详情 + 资产定位 + 研判合并                             │
│   ├─ 8a. 资产归属定位（AssetExtractor → search_asset_info → 工作流）│
│   ├─ 8b. 攻击详情生成子工作流                                      │
│   └─ 8c. 研判结果合并（双重判断融合）                               │
│   │                                                              │
│ [9] 后续动作提取与抑制处置                                         │
│   ├─ 9a. 渗透测试名单检查（可能将转交改为关闭）                     │
│   ├─ 9b. FollowUp 提取（pa_code + bu_name）                      │
│   ├─ 9c. UM 账号封禁（仅转交+预警时）                              │
│   ├─ 9d. IP 地址隔离（基于 attacker/target 角色）                  │
│   ├─ 9e. 攻击链提取（必然执行，Markdown 报告）                     │
│   └─ 9f. pa_code/bu_name 一级字段设置                             │
│   │                                                              │
│   ▼ StopEvent → JudgeAnalysisRes（完整研判结果）                   │
└──────────────────────────────────────────────────────────────────┘
```

---

## 二、模型配置

| 参数 | 值 |
|------|------|
| 默认模型 | `Deepseek_Qwen_32B_My` |
| LLM 客户端 | `OpenAI(base_url=PINGANGPT_OPENAI_SERVER, api_key=APP_KEY_DICT["sec_model_online"])` |
| 模型参数 | `top_p=0.5, temperature=0.85, frequency_penalty=0.2` |
| 调用超时 | 240s（主流程 LLM）、7min（攻击详情子工作流） |
| need_trans | `False`（需返回 reasoning_content，前端暂未单独解析） |

---

## 三、研判决策阶段（第一阶段）

### 3.1 步骤 1：数据预处理

**代码位置**：`EdrAlertWorkflow.prepare_msgs()` → `my_workflows/zeus/flows/edr_alert_assess.py:248`

**职责**：从原始 hit log 中解出研判所需的全部必要数据。

**处理步骤**：

| 步骤 | 操作 | 产出 |
|------|------|------|
| 1 | 提取 `alert_id` | alert_id |
| 2 | 获取关联预警情况 | `related_status_count`（详见 §五.1） |
| 3 | 获取研判入模数据 | `flow_datas`：从 hitLog 中提取 zeusRawLogs，取前 4 条 |
| 4 | 提取 3 级预警类型 ID | `alter_full_type_id` |
| 5 | 路径提取 | `paths`：safe_paths + other_paths；`in_pingan_paths` 布尔值 |
| 6 | 提取执行类型 | `execute_type`：=3 为告警，其他为预警 |

**路径提取子流程**（`get_path_from_hit_log`）：

1. 用 `EXTRACT_PATH_PROMPT` 调用 LLM，从原始日志中提取所有程序/文件路径
2. 解析 LLM 返回的 JSON，得到 `safe_paths`（安全路径）和 `other_paths`（非安全路径）
3. 判断所有路径是否都在平安安全软件路径列表中：
   - `C:\Program Files (x86)\`, `C:\Program Files\`, `C:\Windows\`, `D:\CCMcache\`, `C:\System Volume Information\`, `C:\Windows\System32\`, `C:\Windows\SysWOW64\`, `C:\Windows\Temp\`, `C:\Windows\winsxs\`, `C:\Recovery\Windows\`, `C:\Boot\`, `C:\PerfLogs\`
4. 安全路径 + 所有 `.exe` 文件路径都需要匹配平安已知路径

**产出**：`JudgeDataEvent` 包含 `alert_id`, `related_status_count`, `flow_datas`, `rule_code`, `alter_full_type_id`, `execute_type`, `paths`, `in_pingan_paths`

---

### 3.2 步骤 2：关联预警历史判断

**代码位置**：`EdrAlertWorkflow.judge_by_related_alert()` → `my_workflows/zeus/flows/edr_alert_assess.py:286`
**核心逻辑**：`judge_by_related_data_adjustment()` → `my_workflows/zeus/utils/util_tools.py:184`

**职责**：基于历史关联预警的处置结论，尝试直接得出结论，避免不必要的 LLM 调用。

**前置处理**（`get_related_alerts_dict`）：

1. 筛选**半年内**、**同三级类型**的关联预警
2. 按时间升序排序
3. 统计状态分布（已忽略 / 已关闭 / 待审阅 / 退回中 / 待确认 / 处理中 / 待复核 / 待关闭 / 子单处理中 / 子单已关闭 / 编辑）
4. 统计忽略理由分布
5. 提取每条关联预警的原因描述
6. 状态合并：将所有进行中的状态（退回中、待确认、处理中、待复核、待关闭、子单处理中、子单已关闭、编辑）合并到"已关闭"

**判定逻辑**：

```
待审阅状态：直接过滤（正在处理中，不参考）
忽略理由分类：
  ├── 准确理由集 accurate_reasons = {"规则准确-其他", "规则准确-待加白", "规则准确-预警重复", None}
  └── 误报理由集 misreport_reasons = {"误报-规则识别不准", "误报-规则配置不成功", "误报-预警重复"}

判定流程：
  if ignore_status == {"已关闭"}:
      → 转交（历史全部已转交）
      → 立即调用处置推荐

  elif ignore_status == {"已忽略"}:
      if 忽略数 >= 3:
          → 忽略（忽略证据充分）
      elif 忽略理由 ⊆ misreport_reasons:
          → 忽略（都是误报）
      else:
          → continue（混合情况，AI 再研判）

  elif ignore_status == {"已忽略", "已关闭"}:
      if 忽略数 > 4 且 最近2次均为忽略:
          → 忽略（稳定忽略模式）
      elif 忽略理由 ⊆ accurate_reasons:
          → 转交（规则很准，忽略理由是规则准而非误报）
      else:
          → continue（规则时准时不准）

  else:
      → continue（其他无法判断的情况）
```

**产出**：能判断 → `FinalEvent`（直接返回结果）；不能判断 → `KnowledgeEvent`

---

### 3.3 步骤 3：知识库研判（预留）

**代码位置**：`EdrAlertWorkflow.judge_by_knowledge()` → `my_workflows/zeus/flows/edr_alert_assess.py:335`

**当前状态**：未实现 LLM 调用，直接透传数据到 `MainStartEvent`，标记为预留扩展节点。

---

### 3.4 步骤 4：规则分流

**代码位置**：`EdrAlertWorkflow.judge_by_main()` → `my_workflows/zeus/flows/edr_alert_assess.py:353`

**职责**：根据 `rule_code` 将告警分发到不同的研判流程，不同告警类型使用不同的 Prompt 和策略。

**分流规则**：

| 规则代码 | 流向 | 说明 |
|----------|------|------|
| `RPAADM_002031`, `RPAADM_002010`, `RPAADM_002051`, `RPAADM_002025`, `RPAADM_002275`, `RPAADM_002259` | `ReadLDAndSysEvent` | 读取 Login Data / System 文件类规则 |
| `RPAADM_002042` | `ElevationPrivilegesEvent` | 提权类规则（将用户加入管理员组等） |
| 其他所有规则 | `LLMEvent` | 通用 LLM 研判（兜底策略） |

---

### 3.5 步骤 5：LoginData/System 路径判断

**代码位置**：`EdrAlertWorkflow.judge_by_read_sys()` → `my_workflows/zeus/flows/edr_alert_assess.py:417`

**职责**：针对 LoginData / System 文件读取类告警，基于路径安全情况直接判定，不经过 LLM。

**处理逻辑**：

```
if 存在 safe_paths 或 other_paths:
    if in_pingan_paths == True:       ← 所有路径都在平安安全软件列表
        → 忽略
        → 理由："程序路径都在平安安全软件列表"
    else:                              ← 存在不在安全列表的路径
        → 转交
        → 调用 disposal_processing() 获取处置推荐
        → 理由："发现可疑程序路径，不在平安安全软件列表"
else:                                   ← 无路径信息
    → 降级到 LLMEvent（通用研判）
```

**产出**：`FinalEvent`（直接得出结论）

---

### 3.6 步骤 6：提权研判

**代码位置**：`EdrAlertWorkflow.judge_by_privilege()` → `my_workflows/zeus/flows/edr_alert_assess.py:479`

**职责**：针对提权类规则（`RPAADM_002042`）进行专门的 LLM 研判。

**使用的 Prompt**：`EDR_ELEVATION_PRIVILEGE_PROMPT`（`my_workflows/zeus/prompt/edr_prompts.py:172`）

**Prompt 核心内容**：

**背景知识**：
- 安全办公环境：普通域用户 = 姓名拼音+3位数字，外包 = EX-前缀
- 系统安全路径列表（不可写入）：`C:\Windows\*`, `C:\Program Files\*` 等
- 不安全路径：桌面、D盘、E盘等用户可写路径

**分析步骤**（固定决策逻辑）：

| 情况 | 条件 | 结论 |
|------|------|------|
| 1 | 将普通域用户加入 `Direct Access Users` / `OpenVPN Administrators` / `DFS_Server_AdminsGroup` 组，由超管（Administrator）执行 | **忽略**（正常运维） |
| 2 | `net localgroup administrators VIP_PCadmin /add`（明确的安全管理员） | **忽略** |
| 3 | 其他用户提权操作（如 `net localgroup administrators ex-zhangjianming323 /add`） | **转交** |
| 4 | 不符合情况 1 和 2 | LLM 详细分析（结合用户、命令、路径等） |
| 5 | 有历史关联预警 | 结合历史结论综合判断 |

**Prompt 字段描述**：与通用 Prompt 一致（str_title, str_desc, str_user_oslogon, str_process_full, str_cmd, str_parent_path_full, str_parent_cmd, str_suspicious_process_ancestor_full 等）

**输出格式**：

```json
{
  "action": "转交" | "忽略",
  "summary": "核心总结，30字以内",
  "rationale": [{"key": "风险点/研判维度名称", "value": "该维度描述，30字以内"}]
}
```

---

### 3.7 步骤 7：通用 LLM 研判（核心节点）

**代码位置**：`EdrAlertWorkflow.judge_by_main_llm()` → `my_workflows/zeus/flows/edr_alert_assess.py:488`
**核心调用**：`aigc_judge()` → `judge_by_llm()` → `my_workflows/zeus/utils/util_tools.py:316`

**职责**：所有无法被规则分流或前序节点判断的告警，统一进入通用 LLM 研判。这是**最核心**的研判节点，覆盖了绝大多数告警类型。

**使用的 Prompt**：`EDR_WITH_BACKGROUND_PROMPT`（`my_workflows/zeus/prompt/edr_prompts.py:266`）

**Prompt 完整内容**：

**背景知识**：
1. 联软 EDR 系统：平安集团办公电脑终端安全软件
2. 安全办公环境：
   - 用户特征：普通域用户 = 拼音+3位数字，外包 = EX-前缀
   - 安全行为：普通域用户无管理员权限，无法修改系统核心路径
   - 系统安全路径列表：`C:\Windows\System32\`, `C:\Program Files\*`, `D:\CCMcache\*` 等（所有子路径也安全）
   - 不安全路径：桌面、D盘、E盘等用户可写路径 → 威胁程度显著提高
3. 工作时间：工作日 08:00-21:00
4. Galaxy 开头告警 = 平安自建安全规则
5. 角色：平安集团安全事件分析专家

**字段描述**（完整字段表）：

| 字段 | 含义 | 分析方法 |
|------|------|----------|
| str_title | 告警名称 | 初步判断事件类型和风险 |
| str_desc | 告警描述 | 获取详细信息和上下文 |
| str_dept_name | 部门名称 | 判断是否涉及关键部门 |
| str_source_host | 主机名称 | 定位具体设备 |
| str_source_ip | 主机源 IP | 确定网络位置 |
| str_attack_ip | 攻击方 IP | 识别攻击来源 |
| str_user_oslogon | 系统用户名 | 分析用户行为和权限 |
| t_detect_time | 检测时间 | 判断是否工作时间 |
| str_user_process | 进程用户名 | 确认进程权限合理性 |
| str_process_full | 可疑进程全路径 | 判断是否为系统软件/恶意工具 |
| str_cmd | 可疑进程命令行 | 分析是否有可疑参数 |
| str_parent_path_full | 父进程全路径 | 判断父进程合法性 |
| str_parent_cmd | 父进程命令行 | 分析父进程参数 |
| str_parent_user | 父进程用户名 | 确认父进程权限 |
| str_suspicious_process_ancestor_full | 祖先进程全路径 | 追溯进程启动链上游 |
| str_suspicious_process_ancestor_cmd | 祖先进程命令行 | 分析祖先进程命令 |

**分析步骤**（基于 `all_paths_safe` 分流）：

```
已知路径信息：safe_paths / other_paths / all_paths_safe
    │
    ├─ all_paths_safe == true（所有路径安全）
    │   └─ 流程 1：
    │       1.1 程序功能分析
    │           - 功能用途确认（从 str_cmd / str_parent_cmd / str_ancestor_cmd 提取）
    │           - 进程链分析（父进程/祖先进程是否可疑）
    │           - 异常行为识别（敏感操作、权限提升、绕过安全机制）
    │           - 命令行参数分析（加载恶意 DLL、执行可疑脚本、访问敏感文件）
    │       1.2 攻击行为分析
    │           - 基于 str_title / str_desc / str_cmd / str_parent_cmd / str_ancestor_cmd
    │           - 还原 Windows 操作现场，分析用户行为意图
    │           - 判断是否符合常规使用模式，是否存在误报可能
    │       1.3 时间分析
    │           - 查看 t_detect_time，判断是否工作时间
    │           - 非工作时间操作可能被视为异常
    │       1.4 误判排除
    │           - 生产环境中 90% 以上流程 1 告警可能为误报
    │           - 必须列出证据确认是否误报
    │       1.5 综合判断
    │           - 真实攻击 → 转交
    │           - 误报 → 忽略
    │
    ├─ all_paths_safe == false（存在不安全路径）
    │   └─ 流程 2：
    │       - 文档类后缀 (csv/chm/xlsx/txt/doc/docx) → 95% 概率忽略
    │       - 可执行脚本后缀 (xml/bat/exe/dll/xll/xlsm) → 转交
    │
    └─ 综合判断
        - 结合历史关联预警处置结论
```

**输出格式**：

```json
{
  "action": "转交" | "忽略",
  "summary": "核心总结，30字以内（攻击类型、是否成功、影响范围）",
  "rationale": [
      {"key": "风险点/研判维度名称", "value": "该维度描述，30字以内"}
  ]
}
```

**LLM 调用流程**（`judge_by_llm` 函数）：

```python
# 1. 渲染 prompt
question = Template(user_prompt).render({
    "related_history": 历史关联预警原因描述,
    "alert_event": 原始日志 JSON
})

# 2. 调用 LLM
answer = client.chat.completions.create(
    model=model_name,
    messages=[{"role": "user", "content": question}],
    extra_body={"timeout": 240, "need_trans": False},
    top_p=0.5, temperature=0.85, frequency_penalty=0.2
)

# 3. 解析 JSON 结果
judge_json = extract_json(answer.choices[0].message.content, "action")
evaluation_action = judge_json["action"]       # 默认"忽略"，包含"转交"则转
evaluation_rationale = judge_json["rationale"]
evaluation_summary = judge_json["summary"]

# 4. 写入跟踪
evaluation_trace.append({
    "node": "llm 研判",
    "query": flow_datas,
    "content": LLM完整响应
})

# 5. 如果 action=转交，调用处置推荐
if evaluation_action == "转交":
    disposal_action, disposal_rationale, disposal_trace = disposal_processing(...)

# 6. 返回结果
return {
    "evaluation_conclusion": {action, rationale, summary, trace},
    "disposal_conclusion": {action, rationale, trace}
}
```

**处置推荐流程**（`disposal_processing` / `disposal_recommend`）：

```
if evaluation_action != "转交":
    → disposal_action = None

else:
    if alter_full_type_id is None:
        → disposal_action = None，理由："三级类型ID为None"
    else:
        获取候选处置模板列表（带4小时缓存）:
            → get_candidate_templates_from_tertiary_id(alter_full_type_id)

        if 模板数 == 1:
            → 直接用该模板ID
        elif 模板数 == 0:
            → None，理由："无候选模板"
        else:  # 多个模板
            → 调用 LLM 从候选中推荐最优模板
              (使用 ZEUS_DISPOSAL_TEMPLATE)
              Prompt 包含场景映射规则：
              - 钓鱼邮件 → 恶意附件类/二维码类
              - 账号密码泄漏/主机失陷 → 失陷账号抑制/失陷设备抑制
              - 红队情报 → 封堵IP/封堵域名
              - 未授权访问 → 未授权组件模板
              - 反弹SHELL/webshell/文件上传 → 主机隔离
              - 权限提升 → 禁用账户
              - 拒绝服务攻击 → 流量清洗
              - 异常流量 → 阻断内网异常流量
              - 目录遍历 → 敏感信息
              - 勒索病毒 → 病毒溯源
```

**模板获取缓存**：`_template_cache`（TTL 4小时，线程安全 Lock 保护）

---

### 3.8 研判阶段总结

```
数据预处理 → 关联预警判断(能直接判就返回) → 知识库(透传) → 规则分流
    │                                         │
    │     LoginData/System类                    提权类
    │     → judge_by_read_sys                   → judge_by_privilege
    │     → 路径安全=忽略，不安全=转交             → EDR_ELEVATION_PRIVILEGE_PROMPT
    │                                         │
    │     其他所有规则                          │
    │     → judge_by_main_llm                   │
    │     → EDR_WITH_BACKGROUND_PROMPT          │
    │     → 基于all_paths_safe分流(流程1/流程2)   │
    │                                         │
    ▼ 三个分支收敛，产出 FinalEvent              ▼
       FinalEvent ← 研判结论: 忽略 或 转交
       含: evaluation_conclusion + disposal_conclusion
```

---

## 四、处置闭环阶段（第二阶段）

> **注意**：第一阶段的 `FinalEvent` 只包含"忽略/转交"结论和处置模板推荐。第二阶段负责：资产定位、攻击详情生成、研判合并、后续动作提取、抑制处置执行。

### 4.1 步骤 8a：资产归属定位

**代码位置**：`EdrAlertWorkflow.generate_alert_description()` → `my_workflows/zeus/flows/edr_alert_assess.py:506`
**核心函数**：`locate_asset_bu()` → `my_workflows/zeus/flows/disposition_tools/asset_locator.py:221`

**职责**：从告警数据中定位资产归属（哪个 BU / 哪个 PA 代码管理），并识别攻击方与失陷方角色。

**三层降级策略**：

```
[1] AssetExtractor（LLM + Skill 驱动）提取资产
    │
    │  get_all_assets() → [{"type": "IP"/"DOMAIN"/"WEB"/"HOST", "value": "...", "role": "attacker"/"target"/""}]
    │  enriched_assets 包含:
    │    - role_assignments: {attacker: [...], target: [...]}
    │    - disposal_target: "attacker" | "target" | "-"
    │    - recommended_bu: "-" | "target"（推荐分单策略）
    │    - reason: 推荐理由
    │
    │  如果提取失败 → 直接用 [5] ums 兜底
    │
    ▼
[2] 按 recommended_bu 驱动查询优先级排序
    │
    │  recommended_bu == "-":
    │    → attacker 和 target 同级，谁先查到用谁
    │  recommended_bu == "target":
    │    → target 优先，attacker 次之
    │
    ▼
[3] search_asset_info（Zeus 资产库查询）
    │
    │  遍历所有资产（按排序），调用 search_asset_info：
    │    → keyword=资产值, asset_type_list=[资产类型]
    │    → 返回 {code, data: [{type, data: [{companyCode, bizGroup, ...}]}]}
    │    → 提取 companyCode / bizGroup
    │    → DOMAIN ↔ WEB 互相兜底（DOMAIN查不到则用WEB查，反之亦然）
    │
    │  按 recommended_bu 选择最终 BU:
    │    recommended_bu == "-": 第一个查到BU的角色优先
    │    recommended_bu == "target": target 优先
    │
    │  如果查到 → 直接返回
    │  如果全查不到 → 进入 [4]
    │
    ▼
[4] asset_to_bu 工作流兜底
    │
    │  根据资产类型调用不同工作流：
    │    IP  → locate_datacenter(ip) → locate_terminal(ip)
    │    HOST → locate_datacenter(host) → locate_terminal(host)
    │    DOMAIN → locate_datacenter(domain)
    │    WEB → 暂不支持工作流定位
    │
    │  如果查到 → 返回
    │  如果查不到 → 进入 [5]
    │
    ▼
[5] ums 兜底（locate_user）
    │  如果传了 UM 账号，调用 locate_user 定位用户 BU
    ▼
```

**产出**：`AssetBuInfo`

| 字段 | 类型 | 说明 |
|------|------|------|
| `found` | bool | 是否找到归属 |
| `company_code` | str | PA 代码（PA0XX） |
| `biz_group` | str | 业务组/部门 |
| `source` | str | `zeus_search_asset_info` / `agent_asset_to_bu` / `agent_locate_user` |
| `extracted_assets` | Dict | 按类型分组：`{"IP": [...], "DOMAIN": [...]}` |
| `role_result` | Dict | LLM 角色分析：`{role_assignments, disposal_target, recommended_bu, reason}` |
| `disposal_target` | str | 处置目标：`attacker` / `target` / `-` |
| `search_results` | List | 每个资产的查询结果详情 |

---

### 4.2 步骤 8b：攻击详情生成

**代码位置**：`EdrAlertWorkflow.generate_alert_description()` → `my_workflows/zeus/flows/edr_alert_assess.py:521`
**核心类**：`AlertDescriptionWorkflow` → `my_workflows/zeus/flows/alert_description_generation.py`

**职责**：生成攻击详情，包含攻击动作、合并结果、摘要。

**产出**：`{action, merged_result, summary}`

---

### 4.3 步骤 8c：研判结果合并

**代码位置**：`alert_action_merge()` → `my_workflows/zeus/process/alert_helper.py:12`

**职责**：合并 `evaluation_conclusion`（研判推理，来自 LLM）和 `alert_description`（攻击详情，来自子工作流）两个独立判断。

**合并逻辑**：

| evaluation_action（研判推理） | alert_detail_action（攻击详情） | 最终 alert_action | 说明 |
|-------------------------------|--------------------------------|-------------------|------|
| 转交 | 转交 | **转交** | 两者一致，确认转交 |
| 忽略 | 忽略 | **忽略** | 两者一致，确认忽略 |
| 转交 | 忽略 | **忽略** | 攻击详情认为可忽略，优先忽略 |
| 忽略 | 转交 | **忽略** | 研判推理认为可忽略，优先忽略 |

**核心原则**：两个判断中有一个是"忽略"，最终就是"忽略"（宁可漏报不可误判）。

**产出**：`final_conclusion = {alert_action, alert_rationale}`，`warning_flag = 0(忽略) | 1(转交)`

---

### 4.4 步骤 9a：渗透测试名单检查

**代码位置**：`EdrAlertWorkflow.extract_follow_up()` → `my_workflows/zeus/flows/edr_alert_assess.py:547`
**核心类**：`BlackWhiteTagClient` → `my_workflows/zeus/flows/disposition_tools/black_white_tag_client.py:23`

**触发条件**：仅在 `alert_action == "转交"` 时执行。

**处理步骤**：

```python
attacker_ips = _get_attacker_ips(judge_analysis_res)  # 从 asset_bu_info.role_result 提取
if attacker_ips:
    tag_result = BlackWhiteTagClient().search_content(
        keywords=attacker_ips,
        label="渗透测试"
    )
    # search_tag_content 接口: POST /public/searchTagContent
    # 返回: {found, is_valid, has_active, summary, results}

    if tag_result["has_active"] == True:  # 查到且有效
        → 将 alert_action 改为 "关闭"
        → 追加理由："发现攻击源IP({ips})在渗透测试名单里，直接关闭预警"
```

**查询结果标准化**：

| 字段 | 含义 |
|------|------|
| `found` | 是否查到记录（不管有效无效） |
| `is_valid` | 至少有一条有效记录 |
| `has_active` | 有查到且有效（最终综合判断） |
| `summary` | "未查到" / "查到X条且有效" / "查到X条但已过期" |

---

### 4.5 步骤 9b：后续动作提取（FollowUpExtractor）

**代码位置**：`EdrAlertWorkflow.extract_follow_up()` → `my_workflows/zeus/flows/edr_alert_assess.py:568`
**核心类**：`FollowUpExtractor` → `my_workflows/zeus/utils/follow_up_extractor.py:88`

**触发条件**：所有转交告警都会提取，但最终 `follow_up` 字段仅在 `alert_action == "转交"` 时设置。

**提取流程**：

```
1. 准备输入数据（AttackChainDataPreparer）
   → raw_logs, soar, asset, alert_info, content, bu_name_to_pa_code 映射表

2. 调用 LLM 提取（同步调用，FOLLOW_UP_EXTRACTION_PROMPT）

3. 解析结果：FollowUpAction(action_code, action_name, details)
```

**Prompt 核心内容**（`FOLLOW_UP_EXTRACTION_PROMPT`）：

**任务**：从以下数据源提取 pa_code（PA 代码）和 bu_name（BU 名称）：

| 数据源 | 说明 | 优先级 |
|--------|------|--------|
| content 数据 | 人工填写的告警描述 | **最高**（人工填写最真实） |
| zeusRawLogs | 系统自动提取的原始日志 | 高 |
| SOAR 数据 | 安全编排自动化响应数据 | 中 |
| 资产数据 | 资产归属信息 | 中 |
| 告警基本信息 | 基本信息字段 | 低 |

**搜索策略**：
- 不要局限于特定字段名，全面搜索所有字段和嵌套 JSON
- 可能字段：`befaked_company`, `company_name`, `bu_name`, `target_company`, `affected_bu`, `business_unit`, `zeus_company_dst`, `Dst_Branch_temp`, `device__org__name`, `device__org__ou_name`
- PA 代码格式：`PA001` / `PA-001` / `PA_001` 等
- BU 名称：公司名称如 "平安科技"、"平安产险"、"平安银行" 等
- BU 名称到 PA 代码映射表作为辅助（支持模糊匹配）

**action_code 判断**：

| 条件 | action_code | action_name |
|------|-------------|-------------|
| pa_code 或 bu_name 至少一个非空 | 1 | 转BU |
| pa_code 和 bu_name 都为空 | 0 | 无 |

**兜底逻辑**：

```python
if follow_up is None or follow_up.action_code == 0 or (无 pa_code/bu_name):
    if asset_bu_info.found:
        → 使用 asset_bu_info 的 company_code/biz_group 兜底
```

**兜底 BU 映射表**：内置 56 个 BU 到 PA 代码映射（如 "平安科技" → "PA011"，"平安产险" → "PA003" 等）

---

### 4.6 步骤 9c：UM 账号提取与封禁

**代码位置**：`EdrAlertWorkflow.extract_follow_up()` → `my_workflows/zeus/flows/edr_alert_assess.py:589`
**核心类**：`UmExtractor` → `my_workflows/zeus/utils/um_extractor.py:37`

**触发条件**：`alert_action == "转交"` **且** `execute_type == 0`（预警时）。

**UM 账号提取流程**（两级策略）：

```
[1] 规则提取（快速匹配）
    │
    │  正则：(?i)((?:ex[-_])?)([a-z]{2,4}(?:[._]?[a-z]{2,4}){0,3})[._]?(\d{3})\b
    │  匹配规则：
    │    - 中文姓名拼音：2-4个单词组合（如 zhangsan, lixiaoming）
    │    - 后接 3 位数字
    │    - 支持外包格式：ex-zhangsan123 / ex_zhangsan123（大小写不敏感）
    │
    │  遍历原始日志的所有字段，检查字段名是否为用户标识键（USER_IDENTIFIER_KEYS / USER_KEY_PATTERNS）
    │  去重（大小写不敏感），按置信度排序
    │
    ▼
[2] 规则未找到 → LLM 提取
    │  如果规则找到 1 个 → 直接返回
    │  如果规则找到多个 → LLM 验证排序
    ▼
```

**UM 账号正则特征**：
- 普通域用户：姓名拼音（2-4个单词）+ 3位数字（如 `ZHANGWU233`, `zhangsan123`）
- 外包人员：前缀 `EX-` + 姓名拼音 + 3位数字（如 `EX-ZHANGWU233`）
- 输出统一大写（包括 EX- 前缀）

**UM 封禁操作**（`EdrUmAdDispatcher`）：

```
触发：找到 UM 账号 + 转交 + 预警(execute_type==0)
    │
    ▼ 同时执行 3 种封禁：
    │  1. AD 账号锁定（blockAccountOperateType=1）
    │  2. UM 账号锁定（blockAccountOperateType=3）
    │  3. 快乐平安账号锁定（blockAccountOperateType=5）
    │
    ▼ 请求体格式：
       {
           "name": "封禁UM账号",
           "status": 0,
           "operateType": 5,            ← 固定为5
           "invokeParam": {
               "blockUm": ["zhangsan001", ...],
               "blockAccountOperateType": 1|3|5,
               "blockAccountReason": "账号失陷，{alertCode}，{alertName}",
               "umNotice": "..."          ← 可选
           },
           "followUpUms": []
       }
    │
    ▼ 执行 execute_and_end_contain()
       只有全部成功才结束抑制状态
```

---

### 4.7 步骤 9d：IP 地址隔离

**代码位置**：`EdrAlertWorkflow.extract_follow_up()` → `my_workflows/zeus/flows/edr_alert_assess.py:649`
**核心函数**：`_extract_ips_by_disposal_target()` → `my_workflows/zeus/flows/edr_alert_assess.py:157`
**核心工具**：`EdrIpDispatcher` → `my_workflows/zeus/flows/disposition_tools/edr_ip_disposition_tool.py:93`

**触发条件**：`alert_action == "转交"`

**IP 提取规则**（严格按 `disposal_target` 驱动，不 fallback）：

| disposal_target | 行为 | 说明 |
|-----------------|------|------|
| `attacker` | 只取 attacker 角色下的 IP | 隔离攻击方 |
| `target` | 只取 target 角色下的 IP | 隔离失陷方 |
| `-` 或空 | **不隔离** | 返回空列表，记录 skip_reason |

**IP 隔离操作**：

```
提取：从 asset_bu_info.role_result.role_assignments[disposal_target] 中提取 type=="IP" 的值
    │
    ▼ 调用 EdrIpDispatcher
       {
           "name": "隔离失陷标机",
           "status": 0,
           "operateType": 4,              ← 固定为4（IP隔离）
           "invokeParam": {
               "scriptIsolationIp": ["1.1.1.1,2.2.2.2"],  ← 逗号分隔
               "isolationReason": "IP失陷，{alertCode}，{alertName}"
           },
           "followUpUms": []
       }
    │
    ▼ 执行 execute_and_end_contain()
```

---

### 4.8 步骤 9e：攻击链提取

**代码位置**：`EdrAlertWorkflow.extract_follow_up()` → `my_workflows/zeus/flows/edr_alert_assess.py:717`
**核心类**：`AttackChainExtractor` → `my_workflows/zeus/utils/attack_chain_extractor.py:19`

**触发条件**：**必然执行**，与转交/忽略无关。

**输入数据**：原始日志 + 关联告警日志（`include_related_alerts=True`）

**输出**：Markdown 格式攻击链报告，包含 4 大部分：

```
一、告警基础信息
   - 告警名称、级别（emoji）、来源、时间、命中规则（名称/编号/ID）、ATT&CK 战术

二、攻击时间线
   - 攻击画像：手法(T编号)、阶段、发起者、载荷、关联分析(AI研判结果)
   - 失陷资产：主机名、IP、MAC、OS、AgentID、部门、部署/上线时间
   - ASCII 时间轴：按时间顺序展示攻击过程
   - 进程链树状图：展示进程关系、路径、MD5、用户、命令行

三、影响资产情况
   - 情报信息：归属、运营商、地区、ASN、威胁标签(emoji)、恶意判定、情报评分
   - 开放端口表、数字证书
   - 资产风险：类型、用户信息、职位、业务单元、状态(emoji)、风险标签
   - 黑白名单：安全软件白名单、恶意IP黑名单、其他标签

四、综合研判与建议
   - 风险判断：攻击可信度(emoji)、凭证泄露风险、横向移动风险、数据窃取风险
   - 处置建议：立即措施、后续排查步骤、长期改进建议
```

---

### 4.9 步骤 9f：pa_code / bu_name 一级字段设置

**触发条件**：所有转交告警。

**优先级**：

```
follow_up.details.pa_code/bu_name
    ↓ (如果为空)
asset_bu_info.company_code/biz_group
    ↓ (如果都为空)
"" (空字符串)
```

---

### 4.10 处置闭环阶段总结

```
FinalEvent ← 研判结论(忽略/转交) + 处置模板推荐
    │
    ▼ generate_alert_description（子流程合并）
    │  ├─ 资产归属定位（AssetExtractor → search_asset_info → 工作流 → ums）
    │  ├─ 攻击详情生成（子工作流）
    │  └─ 研判结果合并（双判断融合，有一个忽略则忽略）
    │
    ▼ extract_follow_up（后续动作与抑制处置）
    │  ├─ [9a] 渗透测试名单检查 ← 可能将转交改为关闭
    │  ├─ [9b] FollowUp 提取（pa_code + bu_name）← 转交才设置
    │  ├─ [9c] UM 账号封禁 ← 转交 + 预警(execute_type=0) 才执行
    │  ├─ [9d] IP 隔离 ← 基于 disposal_target(attacker/target) 驱动
    │  ├─ [9e] 攻击链提取 ← 必然执行
    │  └─ [9f] pa_code/bu_name 一级字段设置
    │
    ▼ StopEvent → JudgeAnalysisRes（完整结果）
```

---

## 五、核心工具函数详解

### 五.1 关联预警处理（`get_related_alerts_dict`）

**代码位置**：`my_workflows/zeus/utils/util_tools.py:85`

**职责**：从告警数据中提取关联预警信息，为历史关联判断提供数据基础。

**处理步骤**：

```
1. 筛选关联预警
   - 时间范围：半年内
   - 类型范围：同三级类型（tertiaryType）
   - 排序：时间升序

2. 统计状态分布（Counter）
   - 状态映射：已关闭/退回中/待确认/处理中/待复核/待关闭/子单处理中/子单已关闭/编辑 → "转交"
   - 状态映射：已忽略 → "忽略"
   - 待审阅：过滤（不参考）

3. 统计忽略理由分布（Counter）
   - accurate_reasons = {"规则准确-其他", "规则准确-待加白", "规则准确-预警重复"}
   - misreport_reasons = {"误报-规则识别不准", "误报-规则配置不成功", "误报-预警重复"}

4. 提取原因描述
   - 已忽略：取 content[0] 中的"原因"字段
   - 其他状态：取 content[-1]（最新一条）中所有 field_content
```

**返回结构**：

```python
{
    "related_status_dict": {"已忽略": 5, "已关闭": 3},
    "related_ignore_reason_dict": {"规则准确-其他": 3, "误报-规则识别不准": 2},
    "related_status_list": ["已忽略", "已忽略", "已关闭", ...],
    "related_reason_descriptions": [{"忽略": "规则准..."}, {"转交": "处置..."}]
}
```

---

### 五.2 关联预警判定微调（`judge_by_related_data_adjustment`）

**代码位置**：`my_workflows/zeus/utils/util_tools.py:184`

**职责**：基于关联预警状态分布，微调判定逻辑。

**状态初始化**：

```python
# 过滤"待审阅"（处理中，不参考）
related_status_dict.pop("待审阅", 0)
related_ignore_reason_dict.pop(None, 0)
related_status_list = [x for x in related_status_list if x != "待审阅"]

# 合并所有进行中的状态到"已关闭"
if "已关闭" in related_status_dict:
    related_status_dict["已关闭"] += sum([
        related_status_dict.get("退回中", 0),
        related_status_dict.get("待确认", 0),
        related_status_dict.get("处理中", 0),
        related_status_dict.get("待复核", 0),
        related_status_dict.get("待关闭", 0),
        related_status_dict.get("子单处理中", 0),
        related_status_dict.get("子单已关闭", 0),
        related_status_dict.get("编辑", 0),
    ])
```

**判定流程**（与 §三.2 相同，此处展示代码视角）：

```python
ignore_status = {k for k, v in related_status_dict.items() if v > 0}
ignore_reasons = set(related_ignore_reason_dict.keys())

if ignore_status == {"已关闭"}:
    → 转交 + 调用处置推荐
elif ignore_status == {"已忽略"}:
    if 忽略数 >= 3:
        → 忽略
    elif ignore_reasons ⊆ misreport_reasons:
        → 忽略
    else:
        → continue
elif ignore_status == {"已忽略", "已关闭"}:
    if 忽略数 > 4 且 最近2次均为忽略:
        → 忽略
    elif ignore_reasons ⊆ accurate_reasons:
        → 转交 + 调用处置推荐
    else:
        → continue
else:
    → continue
```

---

### 五.3 LLM 研判调用（`judge_by_llm`）

**代码位置**：`my_workflows/zeus/utils/util_tools.py:316`

**职责**：封装 LLM 调用、结果解析、处置推荐调用。

**完整流程**：

```python
async def judge_by_llm(client, model_name, user_prompt, related_reason_descriptions,
                       flow_datas, alter_full_type_id, evaluation_trace, alert_id, **kwargs):

    # 1. 渲染 prompt
    question = Template(user_prompt).render({
        "related_history": json.dumps(related_reason_descriptions),
        "alert_event": json.dumps(flow_datas)
    })

    # 2. 长度检查
    if len(question) > 25000:
        logger.info("输入超过25000个汉字")

    # 3. 调用 LLM
    answer = client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": question}],
        extra_body={"timeout": 240, "need_trans": False},
        **kwargs  # top_p=0.5, temperature=0.85, frequency_penalty=0.2
    )

    # 4. 解析 JSON
    judge_json = extract_json(answer.choices[0].message.content, "action")

    # 5. 提取研判结论
    evaluation_action = judge_json.get("action", "忽略")
    evaluation_action = "转交" if "转交" in evaluation_action else evaluation_action
    evaluation_rationale = judge_json.get("rationale", {"key": "研判失败", "value": "AI推理研判未成功"})
    evaluation_summary = judge_json.get("summary", "").strip()

    # 6. 写入跟踪
    evaluation_trace.append({
        "node": node_name,
        "query": flow_datas,
        "content": answer.choices[0].message.content  # 完整LLM响应
    })

    # 7. 如果转交，调用处置推荐
    if evaluation_action == "转交":
        disposal_action, disposal_rationale, disposal_trace = await disposal_processing(
            alert_id, json.dumps(flow_datas), alter_full_type_id,
            evaluation_action, client, model_name
        )

    # 8. 返回
    return {
        "evaluation_conclusion": {action, rationale, summary, trace},
        "disposal_conclusion": {action, rationale, trace}
    }
```

---

### 五.4 处置模板推荐（`disposal_processing` / `disposal_recommend`）

**代码位置**：`my_workflows/zeus/utils/util_tools.py:361` / `:389`

**职责**：为"转交"告警推荐处置模板。

**完整流程**：

```python
async def disposal_processing(alert_id, alert_log, alter_full_type_id, evaluation_action, client, model_name):
    if evaluation_action != "转交":
        return None, "", []

    if alter_full_type_id is None:
        return None, "三级类型ID为None", []

    # 获取候选模板（带4小时缓存）
    candidate_templates = await get_candidate_templates_from_tertiary_id(alter_full_type_id)

    if len(candidate_templates) == 1:
        return candidate_templates[0]["templateId"], f"唯一模板ID", []
    elif len(candidate_templates) == 0:
        return None, "无候选模板", []
    else:
        # 多模板，调用 LLM 推荐
        return await disposal_recommend(alert_id, alert_log, alter_full_type_id,
                                        candidate_templates, [], client, model_name)

async def disposal_recommend(alert_id, alert_log, alter_full_type_id, candidate_templates, disposal_trace, client, model_name):
    # 渲染处置推荐 prompt
    disposal_question = Template(ZEUS_DISPOSAL_TEMPLATE).render({
        "alert_event": alert_log,
        "candidate_templates": json.dumps(candidate_templates)
    })

    # 调用 LLM
    disposal_answer = client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": disposal_question}],
        extra_body={"timeout": 240}
    )

    # 解析
    disposal_json = extract_json(disposal_answer.choices[0].message.content, "templateId")
    disposal_action = disposal_json.get("templateId", -1)
    disposal_rationale = disposal_json.get("RecommendationReason", "处置推荐失败")

    # 验证：推荐的模板是否在候选列表中
    candidate_ids = [t["templateId"] for t in candidate_templates]
    if disposal_action not in candidate_ids:
        logger.error(f"推荐模板{disposal_action}不在候选列表中{candidate_ids}")

    disposal_trace.append({"node": "llm 处置推荐", "query": candidate_templates, "content": ...})
    return disposal_action, disposal_rationale, disposal_trace
```

**ZEUS_DISPOSAL_TEMPLATE 场景映射**：

| 告警类型 | 映射到的处置模板 |
|----------|------------------|
| 钓鱼邮件（恶意附件类） | 恶意附件处置模板 |
| 钓鱼邮件（二维码类） | 二维码处置模板 |
| 账号密码泄漏/主机失陷 | 失陷账号抑制 / 失陷设备抑制 |
| 红队情报 | 封堵IP / 封堵域名 |
| 未授权访问 | 未授权组件模板 |
| 反弹SHELL / webshell / 文件上传 | 主机隔离 |
| 权限提升 | 禁用账户 |
| 拒绝服务攻击 | 流量清洗 |
| 异常流量 | 阻断内网异常流量 |
| 目录遍历 | 敏感信息 |
| 勒索病毒 | 病毒溯源 |

---

### 五.5 UM 账号提取（`UmExtractor`）

**代码位置**：`my_workflows/zeus/utils/um_extractor.py:37`

**职责**：从告警数据中提取 UM 账号信息。

**UM 账号特征**：
- 普通域用户：姓名拼音（2-4个单词组合）+ 3位数字
- 外包人员：`EX-` 前缀 + 姓名拼音 + 3位数字
- 输出统一大写

**两级提取策略**：

```
[1] 规则提取（正则匹配）
    │
    │  正则：(?i)((?:ex[-_])?)([a-z]{2,4}(?:[._]?[a-z]{2,4}){0,3})[._]?(\d{3})\b
    │
    │  只遍历原始日志（raw_logs），避免误匹配
    │  只检查字段名匹配用户标识键的数据：
    │    USER_IDENTIFIER_KEYS（精确匹配）：
    │      user, username, user_name, uid, um, name, realname, fullname,
    │      account, account_name, login, operator, employee, owner, creator,
    │      author, requester, target_user, victim, suspect, attacker,
    │      pingan_user, pa_user, staff_name, emp_name, sender, recipient,
    │      logon_user, identity, person...
    │    USER_KEY_PATTERNS（模糊匹配）：
    │      r"user", r"username", r"realname", r"account", r"operator",
    │      r"employee", r"owner", r"creator", r"author", r"victim",
    │      r"attacker", r"logon", r"identity"...
    │
    │  递归遍历数据字典/列表，找到值匹配 UM 模式的字段
    │  去重（大小写不敏感），统一大写，按置信度排序
    │
    ▼
[2] 如果规则找到：
    │  找到 1 个 → 直接返回
    │  找到多个 → LLM 验证排序（选最可能的一个）
    ▼
[3] 如果规则未找到 → LLM 提取
    │  构建 prompt 要求从 raw_logs 提取 UM 账号
    │  输出 JSON：{found, accounts: [{um_account, field_key, confidence, context}]}
    ▼
```

---

## 六、抑制手段汇总

| 抑制手段 | 触发条件 | 工具 | operateType | 说明 |
|----------|----------|------|-------------|------|
| 渗透测试名单关闭 | 转交 + attacker IP 在渗透测试名单 | `BlackWhiteTagClient.search_content()` | - | 将 `alert_action` 改为"关闭" |
| UM 账号封禁 | 转交 + execute_type=0 + 找到 UM 账号 | `EdrUmAdDispatcher` | 5 | AD锁定(1)/UM锁定(3)/快乐平安锁定(5) |
| IP 地址隔离 | 转交 + disposal_target 有 IP | `EdrIpDispatcher` | 4 | 基于 attacker/target 角色隔离 |

**处置结果记录**：所有抑制动作的执行结果（请求体 + 响应 + 成功状态）记录在 `DispositionResults` 中。

---

## 七、关键数据模型

### JudgeAnalysisRes

| 字段 | 类型 | 说明 |
|------|------|------|
| `evaluation_conclusion` | Dict | 研判结论：`{action, rationale, summary, trace}` |
| `disposal_conclusion` | Dict | 处置结论：`{action, rationale, trace}` |
| `alert_description` | Dict | 攻击详情：`{action, merged_result, summary}` |
| `final_conclusion` | Dict | 最终结论：`{alert_action, alert_rationale}` |
| `follow_up` | FollowUpAction | 后续动作：`{action_code, action_name, if_ignore, details}` |
| `asset_bu_info` | AssetBuInfo | 资产归属定位结果 |
| `disposition_results` | DispositionResults | 抑制动作执行结果：`{batches, overall_all_success}` |
| `attack_chain` | AttackChainDetails | 攻击链分析结果：`{raw_markdown}` |
| `pa_code` | str | 一级字段，始终有值（PA0XX） |
| `bu_name` | str | 一级字段，始终有值 |
| `ip_risk_info` | IPRiskInfo | IP 风险评分（可选） |
| `warning_flag` | int | 0=忽略，1=转交 |

### AssetBuInfo

| 字段 | 类型 | 说明 |
|------|------|------|
| `found` | bool | 是否找到归属 |
| `company_code` | str | PA 代码（PA0XX） |
| `biz_group` | str | 业务组/部门 |
| `source` | str | 数据来源 |
| `extracted_assets` | Dict | 按类型分组：`{IP: [...], DOMAIN: [...]}` |
| `role_result` | Dict | LLM 角色分析结果 |
| `disposal_target` | str | 处置目标 |
| `search_results` | List | 查询结果详情 |

### FollowUpAction

| 字段 | 类型 | 说明 |
|------|------|------|
| `action_code` | int | 1=转BU, 0=无 |
| `action_name` | str | "转BU" / "无" |
| `if_ignore` | int | 1=忽略不转BU |
| `details` | FollowUpDetails | `{pa_code, bu_name, fix_suggestions}` |

---

## 八、完整事件流转

```
StartEvent(AlertRoot)
  │
  ▼
[1] prepare_msgs()
  │ → 提取 alert_id, related_status_count, flow_datas, rule_code,
  │   alter_full_type_id, paths, in_pingan_paths, execute_type
  ▼
JudgeDataEvent
  │
  ▼
[2] judge_by_related_alert()
  │ → get_related_alerts_dict() 获取关联预警统计
  │ → judge_by_related_data_adjustment() 判定
  ├─ ignore_status == {"已关闭"} → 转交
  ├─ ignore_status == {"已忽略"} 且 >=3 → 忽略
  ├─ ignore_status == {"已忽略"} 且理由⊆误报 → 忽略
  ├─ ignore_status == {"已忽略","已关闭"} 且 忽略>4且最近2次忽略 → 忽略
  ├─ ignore_status == {"已忽略","已关闭"} 且 理由⊆准确 → 转交
  └─ 其他 → continue
  ▼
FinalEvent  ← 或直接继续
KnowledgeEvent → MainStartEvent（透传）
  │
  ▼
[4] judge_by_main() → 规则分流
  ├─ RPAADM_002031/002010/002051/002025/002275/002259 → ReadLDAndSysEvent
  ├─ RPAADM_002042 → ElevationPrivilegesEvent
  └─ 其他 → LLMEvent
  │
  ▼
[5] judge_by_read_sys()          [6] judge_by_privilege()       [7] judge_by_main_llm()
  → 路径安全判断                   → EDR_ELEVATION_PRIVILEGE     → EDR_WITH_BACKGROUND_PROMPT
  → in_pingan_paths?              → PROMPT                      → all_paths_safe?
  → True=忽略, False=转交                              → 流程1(安全)/流程2(不安全)
  → disposal_processing()                            → LLM 研判
  → FinalEvent                                        → disposal_processing()
  │                                                        │
  ▼ 三个分支收敛，产出 FinalEvent（含 evaluation + disposal_conclusion）
  │
  ▼
[8] generate_alert_description()
  │  ├─ 资产归属定位 locate_asset_bu()
  │  │    → AssetExtractor → search_asset_info → asset_to_bu工作流 → ums兜底
  │  ├─ 攻击详情生成 sub_workflow.run()
  │  └─ alert_action_merge() 合并 double judgment
  │       → 有一个忽略则忽略
  ▼
FollowUpEvent (含 asset_bu_info + final_conclusion)
  │
  ▼
[9] extract_follow_up()
  │  ├─ [9a] 渗透名单检查 → attacker IP → BlackWhiteTagClient → 可能关闭
  │  ├─ [9b] FollowUp 提取 → pa_code + bu_name（转交才设置）
  │  │    → LLM 从多数据源搜索，兜底到 asset_bu_info
  │  ├─ [9c] UM 封禁 → 转交+预警 → UmExtractor → EdrUmAdDispatcher(AD/UM/快乐平安)
  │  ├─ [9d] IP 隔离 → 转交 → disposal_target 驱动 → EdrIpDispatcher
  │  ├─ [9e] 攻击链提取 → AttackChainExtractor → Markdown 报告
  │  └─ [9f] pa_code/bu_name 一级字段设置
  ▼
StopEvent(JudgeAnalysisRes)
  │  → model_dump() → 完整 JSON 输出
  │    alert_title, alert_action, alert_rationale,
  │    disposal_action, warning_flag, attack_detail, evaluation, disposal,
  │    follow_up, pa_code, bu_name, ip_risk_info, attack_chain,
  │    disposition_results, asset_bu_info
```

---

## 九、关键 Prompt 索引

| Prompt | 文件位置 | 用途 |
|--------|----------|------|
| `EXTRACT_PATH_PROMPT` | `edr_prompts.py:58` | 提取程序/文件路径 |
| `EDR_LOGINDATA_SYSTEM_PROMPT` | `edr_prompts.py:103` | LoginData/System 路径判断 |
| `EDR_ELEVATION_PRIVILEGE_PROMPT` | `edr_prompts.py:172` | 提权研判 |
| `EDR_WITH_BACKGROUND_PROMPT` | `edr_prompts.py:266` | 通用 LLM 研判（核心） |
| `EDR_LD_SYSTEM_PROCESS_PROMPT` | `edr_prompts.py:407` | 备用路径研判 |
| `ZEUS_DISPOSAL_TEMPLATE` | `disposal_template.py:10` | 处置模板推荐 |
| `FOLLOW_UP_EXTRACTION_PROMPT` | `follow_up_prompts.py:5` | 后续动作提取 |
| `ATTACK_CHAIN_GENERATION_PROMPT` | `attack_chain_prompts.py:5` | 攻击链报告生成 |

---

## 附录：全部 Prompt 原文

### 附录 A：EXTRACT_PATH_PROMPT

> 用途：从原始日志中提取程序/文件路径，用于判断路径安全性。
> 文件：`my_workflows/zeus/prompt/edr_prompts.py:58`

```
从JSON中提取程序和文件路径。

这些为后缀。

提取结果以json格式输出。

## 输出格式
- 指定输出格式如下：
{
    "paths":[path1,path2...]
}

## 提取步骤
1. 遍历每个JSON对象，检查每个字段中的是否存在程序或文件路径（`str_desc`、`str_cmd`、`str_process_full`、`str_parent_cmd`、`str_parent_path_full`、`str_suspicious_process_ancestor_cmd`、`str_suspicious_process_ancestor_full`这些字段中可能包含需要提取的路径）。
2. 如果字段中存在路径，请提取出来。
3. 对提取出来的所有路径去重。
4. 最后检查一遍，确认没有漏掉路径（`.bat`、`.dll`、`.xll`、`xml`等容易被忽略）
4. 整理成指定的JSON格式输出。

请务必参考`提取步骤`提取，以下是输入json：
{{alert_event}}
```

---

### 附录 B：EDR_LOGINDATA_SYSTEM_PROMPT

> 用途：针对 LoginData/System 文件读取类告警的路径判断（步骤 5）。
> 文件：`my_workflows/zeus/prompt/edr_prompts.py:103`
> 注入变量：`{paths_str}` — 平安安全软件路径列表

```
## 背景知识

1. **联软 EDR 系统**：安装在平安集团办公电脑上的终端安全软件，用于监控和告警异常行为。
2. **安全办公环境**：
   - **用户特征**：
     - 普通域用户账号命名规则为用户姓名拼音（不区分大小写）+3位数字（例如："ZHANGWU233"、"zhangwu233"）。
     - 外包人员账号前缀为 "EX-"（不区分大小写）（例如： "EX-ZHANGWU233"、"ex-zhangwu233"）。
   - **系统安全路径列表**：
     - 在 Windows 域环境中，普通域用户默认不具备写入权限且无法修改以下系统核心路径及其内部的所有子路径和文件。这些路径及其子路径被视为安全受控的：
       {paths_str}
     - **安全性说明**：
       - **子路径的安全性**：所有上述系统安全路径列表的所有子路径也都是安全合法的【注意：千万不要将他们的子路径误判为可疑不安全】。
       - **例子说明**：例如，如果业务软件位于 `C:\\Windows\\System32\\业务软件\\业务软件可执行.exe`，这类路径属于受控路径，但该软件的执行是合法且正常的，不应被视为风险。
       - **不安全路径**：告警事件一旦中出现其他路径（如桌面、D盘、E盘等用户可写路径），威胁程度将显著提高，考虑转交。

## 字段描述

| 字段名称  | 字段含义 | 分析方法                     |
| :-------- | :------- | :--------------------------- |
| str_title | 告警名称 | 初步判断事件类型和风险。     |
| str_desc  | 告警描述 | 获取告警的详细信息和上下文。 |

## 历史关联预警处理结论

以下为历史关联预警处理结论，包括处置结论与原因描述：

```json
{{related_history}}
```

## 输出格式

**告警的研判结果输出格式如下**：

```json
{
  "action": "转交", // 或 "忽略"
  "summary": "核心总结，一句话概括。严格控制在30字以内",
  "rationale": [
    {
    "key": "风险点/研判维度名称",
    "value": "该维度的具体描述或理由，严格控制在30字以内"
    }
  ]
}
```

**字段说明**：

- `action`：处置动作，"转交" 或 "忽略"。
- `summary`：核心总结，一句话概括告警核心内容（攻击类型、是否成功、影响范围），**严格控制在30字以内**。
- `rationale`：风险点列表，对象数组。
- `rationale[].key`：风险点/研判维度名称。
- `rationale[].value`：该维度的具体描述或理由，**严格控制在30字以内**。
- 若转交，请列出所有相关的风险点和转交理由；若忽略，请说明忽略原因（可包含多个维度）。

- 忽略`str_desc`中`Login Data`和`SYSTEM`文件所在的路径，只分析可执行文件(即`.exe`)所在的路径是不是安全路径；
- 若历史关联预警处理结论不为空，还需结合它的处置结论和原因描述，综合判断预警需要转交还是忽略。

以下是待分析的告警：

{{alert_event}}
```

---

### 附录 C：EDR_ELEVATION_PRIVILEGE_PROMPT

> 用途：提权类告警研判（步骤 6）。
> 文件：`my_workflows/zeus/prompt/edr_prompts.py:172`
> 注入变量：`{paths_str}` — 平安安全软件路径列表

```
## 背景知识

1. **安全办公环境**：
   - **用户特征**：
     - 普通域用户账号命名规则为用户姓名拼音（不区分大小写）+3位数字（例如："ZHANGWU233"、"zhangwu233"）。
     - 外包人员账号前缀为 "EX-"（不区分大小写）（例如： "EX-ZHANGWU233"、"ex-zhangwu233"）。
   - **系统安全路径列表**：
     - 在 Windows 域环境中，普通域用户默认不具备写入权限且无法修改以下系统核心路径及其内部的所有子路径和文件。这些路径及其子路径被视为安全受控的：
       {paths_str}
     - **安全性说明**：
       - **子路径的安全性**：所有上述系统安全路径列表的所有子路径也都是安全合法的【注意：千万不要将他们的子路径误判为可疑不安全】。
       - **例子说明**：例如，如果业务软件位于 `C:\\Windows\\System32\\业务软件\\业务软件可执行.exe`，这类路径属于受控路径，但该软件的执行是合法且正常的，不应被视为风险。
       - **不安全路径**：告警事件一旦中出现其他路径（如桌面、D盘、E盘等用户可写路径），威胁程度将显著提高，考虑转交。

## 字段描述

| 字段名称                             | 字段含义                 | 分析方法                                                     |
| :----------------------------------- | :----------------------- | :----------------------------------------------------------- |
| str_title                            | 告警名称                 | 初步判断事件类型和风险。                                     |
| str_desc                             | 告警描述                 | 获取告警的详细信息和上下文。                                 |
| str_dept_name                        | 部门名称                 | 了解事件的组织范围，判断是否涉及关键部门。                   |
| str_source_host                      | 风险发生所在主机名称     | 定位事件发生的具体设备。                                     |
| str_source_ip                        | 主机源IP                 | 确定主机的网络位置，结合IP地址分析网络行为。                 |
| str_attack_ip                        | 行为攻击方IP地址         | 识别潜在的攻击来源，判断是否为内部或外部攻击。               |
| str_user_oslogon                     | 系统用户名               | 分析登录用户的活动和权限，判断用户行为是否合理。             |
| t_detect_time                        | 检测时间                 | 判断事件是否发生在工作时段内，非工作时间的事件可能被视为异常行为。 |
| str_user_process                     | 进程用户名               | 确认进程运行的用户权限是否合理。                             |
| str_process_full                     | 可疑进程全路径           | 检查进程路径是否合法，判断是否为系统正常软件、用户自行下载、运维操作或恶意工具。 |
| str_cmd                              | 可疑进程的命令行         | 分析进程执行的命令，判断是否有可疑操作或参数。               |
| str_parent_path_full                 | 可疑进程的父进程全路径   | 检查父进程的路径是否合法，判断是否为系统正常软件、用户自行下载、运维操作或恶意工具。 |
| str_parent_cmd                       | 可疑进程的父进程命令行   | 分析父进程的命令行参数，判断是否有可疑操作或参数。           |
| str_parent_user                      | 父进程用户名             | 确认父进程的用户权限，判断用户行为是否合理。                 |
| str_suspicious_process_ancestor_full | 可疑进程的祖先进程全路径 | 追溯进程启动链上游，判断是否为系统正常软件、用户自行下载、运维操作或恶意工具。 |
| str_suspicious_process_ancestor_cmd  | 可疑进程的祖先进程命令行 | 分析祖先进程的执行命令，判断是否有可疑操作或参数。           |

## 历史关联预警处理结论

以下为历史关联预警处理结论，包括处置结论与原因描述：

```json
{{related_history}}
```

## 输出格式

```json
{
  "action": "转交", // 或 "忽略"
  "summary": "核心总结，一句话概括。严格控制在30字以内",
  "rationale": [
    {
    "key": "风险点/研判维度名称",
    "value": "该维度的具体描述或理由，严格控制在30字以内"
    }
  ]
}
```

**字段说明**：

- `action`：处置动作，"转交" 或 "忽略"。
- `summary`：核心总结，一句话概括告警核心内容（攻击类型、是否成功、影响范围），**严格控制在30字以内**。
- `rationale`：风险点列表，对象数组。
- `rationale[].key`：风险点/研判维度名称。
- `rationale[].value`：该维度的具体描述或理由，**严格控制在30字以内**。
- 若转交，请列出所有相关的风险点和转交理由；若忽略，请说明忽略原因（可包含多个维度）。

## 分析步骤

1. **检查是否符合情况1**：
   - 如果告警中提到的操作是"将普通域用户加入 `Direct Access Users`、`OpenVPN Administrators`、`DFS_Server_AdminsGroup` 组"，并且操作是由超管（如 `Administrator`）执行的，则直接 **忽略告警**。
   - 理由：这是正常的运维操作，无需进一步分析。
2. **检查是否符合情况2**：
   - 如果告警中提到的操作是 `net localgroup administrators paicdom\\VIP_PCadmin /add`，即明确将 `VIP_PCadmin` 用户添加到 `administrators` 组，则直接 **忽略告警**。
   - 如果操作类似（如 `net localgroup administrators <username> /add`），但操作的对象不是 `VIP_PCadmin`，而是其他用户（例如 `ex-zhangjianming323,wangwenbin001`），则直接 **转交告警**。
   - 理由：只有 `VIP_PCadmin` 的操作是安全的，其他用户的提权操作可能存在风险。
3. **其他情况**：
   - 如果告警不符合情况1和情况2，再根据用户、操作命令、进程路径、背景知识等进行详细分析，判断是否需要 **转交** 或 **忽略**。
4. **历史关联预警处理结论**：
   - 若历史关联预警处理结论不为空，还需结合它的处置结论和原因描述，综合判断预警需要转交还是忽略。

以下是待分析的告警：

{{alert_event}}

请判断符合哪种情况，再根据具体情况进行研判。
```

---

### 附录 D：EDR_WITH_BACKGROUND_PROMPT

> 用途：通用 LLM 研判（步骤 7，覆盖绝大多数告警）。
> 文件：`my_workflows/zeus/prompt/edr_prompts.py:266`
> 注入变量：`{paths_str}` — 平安安全软件路径列表

```
## 背景知识

1. **联软 EDR 系统**：安装在平安集团办公电脑上的终端安全软件，用于监控和告警异常行为。
2. **安全办公环境**：
   - **用户特征**：
     - 普通域用户账号命名规则为用户姓名拼音（不区分大小写）+3位数字（例如："ZHANGWU233"、"zhangwu233"）。
     - 外包人员账号前缀为 "EX-"（不区分大小写）（例如： "EX-ZHANGWU233"、"ex-zhangwu233"）。
   - **安全行为**（默认受控）：
     - 普通域用户无本地管理员权限，无法访问本地管理员文件，也无法进行系统设置的修改。
     - 文件系统操作安全区：
       - 无法修改系统核心路径（如 `System32`、`SysWOW64` 等）。
       - 可以在授权盘符根路径创建文件（如 `C:/`、`D:/`、`E:/`）。
       - 无法篡改域策略相关文件（如 `GPO`、登录脚本）。
     - 系统配置安全区：
       - 无权安装系统服务/驱动。
       - 禁止修改注册表 `HKEY_LOCAL_MACHINE` 子树。
       - 无法变更组策略对象（GPO）。
     - 网络行为安全区：
       - 无法访问域控制器管理端口（如 389/636 LDAP）。
       - 禁止操作域控 DNS 记录。
       - 无权重置其他用户密码。
   - **系统安全路径列表**：
     - 在 Windows 域环境中，普通域用户默认不具备写入权限且无法修改以下系统核心路径及其内部的所有子路径和文件。这些路径及其子路径被视为安全受控的：
       {paths_str}
     - **安全性说明**：
       - **子路径的安全性**：所有上述系统安全路径列表的所有子路径也都是安全合法的【注意：千万不要将他们的子路径误判为可疑不安全】。
       - **例子说明**：例如，如果业务软件位于 `C:\\Windows\\System32\\业务软件\\业务软件可执行.exe`，这类路径属于受控路径，但该软件的执行是合法且正常的，不应被视为风险。
       - **不安全路径**：告警事件一旦中出现其他路径（如桌面、D盘、E盘等用户可写路径），威胁程度将显著提高，考虑转交。
3. **工作时间**：工作日 08:00-21:00。非工作时间包括周末和法定节假日，未经批准的系统操作可能被视为异常行为。
4. **告警特征**：
   - 告警（`str_title`）"Galaxy" 开头表示平安集团自建的安全规则触发。
   - 这些规则用于检测特定类型的威胁或异常行为，例如未经授权的进程启动、可疑的网络连接或潜在的恶意软件活动。

## 角色

你是一名平安集团安全事件分析专家，负责对联软 EDR 系统的告警进行精准研判。你的核心职责包括：

1. **行为与进程链分析**：基于系统日志和进程关联性，识别异常行为模式及攻击路径。
2. **风险量化评估**：结合上下文证据链，输出威胁等级和处置优先级建议。
3. **决策赋能**：生成可操作结论（攻击类型、影响范围、溯源线索），支撑应急响应快速闭环。

## 字段描述

| 字段名称                             | 字段含义                 | 分析方法                                                     |
| :----------------------------------- | :----------------------- | :----------------------------------------------------------- |
| str_title                            | 告警名称                 | 初步判断事件类型和风险。                                     |
| str_desc                             | 告警描述                 | 获取告警的详细信息和上下文。                                 |
| str_dept_name                        | 部门名称                 | 了解事件的组织范围，判断是否涉及关键部门。                   |
| str_source_host                      | 风险发生所在主机名称     | 定位事件发生的具体设备。                                     |
| str_source_ip                        | 主机源IP                 | 确定主机的网络位置，结合IP地址分析网络行为。                 |
| str_attack_ip                        | 行为攻击方IP地址         | 识别潜在的攻击来源，判断是否为内部或外部攻击。               |
| str_user_oslogon                     | 系统用户名               | 分析登录用户的活动和权限，判断用户行为是否合理。             |
| t_detect_time                        | 检测时间                 | 判断事件是否发生在工作时段内，非工作时间的事件可能被视为异常行为。 |
| str_user_process                     | 进程用户名               | 确认进程运行的用户权限是否合理。                             |
| str_process_full                     | 可疑进程全路径           | 检查进程路径是否合法，判断是否为系统正常软件、用户自行下载、运维操作或恶意工具。 |
| str_cmd                              | 可疑进程的命令行         | 分析进程执行的命令，判断是否有可疑操作或参数。               |
| str_parent_path_full                 | 可疑进程的父进程全路径   | 检查父进程的路径是否合法，判断是否为系统正常软件、用户自行下载、运维操作或恶意工具。 |
| str_parent_cmd                       | 可疑进程的父进程命令行   | 分析父进程的命令行参数，判断是否有可疑操作或参数。           |
| str_parent_user                      | 父进程用户名             | 确认父进程的用户权限，判断用户行为是否合理。                 |
| str_suspicious_process_ancestor_full | 可疑进程的祖先进程全路径 | 追溯进程启动链上游，判断是否为系统正常软件、用户自行下载、运维操作或恶意工具。 |
| str_suspicious_process_ancestor_cmd  | 可疑进程的祖先进程命令行 | 分析祖先进程的执行命令，判断是否有可疑操作或参数。           |

## 历史关联预警处理结论

以下为历史关联预警处理结论，包括处置结论与原因描述：

```json
{{related_history}}
```

## 输出格式

**告警的研判结果输出格式如下**：

```json
{
  "action": "转交", // 或 "忽略"
  "summary": "核心总结，一句话概括。严格控制在30字以内",
  "rationale": [
    {
    "key": "风险点/研判维度名称",
    "value": "该维度的具体描述或理由，严格控制在30字以内"
    }
  ]
}
```

**字段说明**：

- `action`：处置动作，"转交" 或 "忽略"。
- `summary`：核心总结，一句话概括告警核心内容（攻击类型、是否成功、影响范围），**严格控制在30字以内**。
- `rationale`：风险点列表，对象数组。
- `rationale[].key`：风险点/研判维度名称。
- `rationale[].value`：该维度的具体描述或理由，**严格控制在30字以内**。
- 若转交，请列出所有相关的风险点和转交理由；若忽略，请说明忽略原因（可包含多个维度）。

## 分析步骤

严格按照以下逻辑处理：

1. **已知**：\n{paths}\n
   其中，`safe_paths`表示属于系统安全路径列表；`other_paths`表示不属于系统安全路径列表；`all_paths_safe`布尔值，表示所有路径是否安全，请根据`all_paths_safe`判断执行后续哪个流程：
   - 如果`all_paths_safe`为 true，则所有路径安全，请执行流程 1。
   - 如果`all_paths_safe`为 false，说明`other_paths`中存在不安全路径，请执行流程 2。

2. **流程1**：适用于安全路径。
   - 步骤1.1，**程序功能分析**，尝试从`str_cmd`、`str_parent_cmd`、`str_suspicious_process_ancestor_cmd` 中提取信息并进行：
     - **功能用途确认**：描述调用程序在该告警中的功能，判断其合理性。
     - **进程链分析**：判断进程链是否正常，是否存在可疑的父进程或祖先进程。
     - **异常行为识别**：识别敏感操作、可疑模式、权限提升、绕过安全机制等。
     - **命令行参数**：分析命令行参数是否存在可疑操作，如加载恶意 DLL、执行可疑脚本或访问敏感文件。
   - 步骤1.2，**攻击行为分析**：基于`str_title`、`str_desc`、`str_cmd`、`str_parent_cmd`和`str_suspicious_process_ancestor_cmd`等字段，对Windows操作系统中的告警事件进行现场还原，深入分析用户行为意图，评估该操作是否符合常规使用模式，并判断是否存在误报可能。
   - 步骤1.3，**时间分析**：查看`t_detect_time`，判断事件是否发生在工作时段内，非工作时间的事件可能被视为异常行为。
   - 步骤1.4，**误判排除**：在实际生产环境中，执行流程 1的告警，90%以上的告警可能被判定为误报。根据告警数据和背景信息，需列出证据以确认告警是否为误判。
   - 步骤1.5，**综合判断**：结合以上分析，如果判断告警是真实攻击行为，请给出核心证据。最后，如果是真实攻击则需要`转交`，如果是误报则需要`忽略`。

3. **流程2**：适用于非安全路径。
   - 如果告警中的非安全路径涉：
     - 以csv/chm/xlsx/txt/doc/docx为后缀的文档：那么95%的概率需要`忽略`。
     - 以xml/bat/exe/dll/exe/xll/xlsm为后缀的可执行脚本：请帮我`转交`。

4. **综合判断**：
   - 若历史关联预警处理结论不为空，还需结合它的处置结论和原因描述，综合判断预警需要转交还是忽略。

## 初始化

重点分析告警的严重性、威胁类型、行为、软件功能和合法性信息、进程路径、时间、进程链、命令行等关键信息。

以下是待分析告警：

```json
  {{alert_event}}
```

务必严格按照上述`分析步骤`进行处理，判断应该执行`流程 1`还是`流程 2`，再根据具体流程规定进行分析，最后给出处置建议。
```

---

### 附录 E：EDR_LD_SYSTEM_PROCESS_PROMPT

> 用途：备用路径研判（与 D 类似但字段不同）。
> 文件：`my_workflows/zeus/prompt/edr_prompts.py:407`
> 注入变量：`{paths_str}`

```
## 背景知识

1. **联软 EDR 系统**：安装在平安集团办公电脑上的终端安全软件，用于监控和告警异常行为。
2. **安全办公环境**：
   - **用户特征**：
     - 普通域用户账号命名规则为用户姓名拼音（不区分大小写）+3位数字（例如："ZHANGWU233"、"zhangwu233"）。
     - 外包人员账号前缀为 "EX-"（不区分大小写）（例如： "EX-ZHANGWU233"、"ex-zhangwu233"）。
   - **安全行为**（默认受控）：
     - 普通域用户无本地管理员权限，无法访问本地管理员文件，也无法进行系统设置的修改。
     - 文件系统操作安全区：
       - 无法修改系统核心路径（如 `System32`、`SysWOW64` 等）。
       - 可以在授权盘符根路径创建文件（如 `C:/`、`D:/`、`E:/`）。
       - 无法篡改域策略相关文件（如 `GPO`、登录脚本）。
     - 系统配置安全区：
       - 无权安装系统服务/驱动。
       - 禁止修改注册表 `HKEY_LOCAL_MACHINE` 子树。
       - 无法变更组策略对象（GPO）。
     - 网络行为安全区：
       - 无法访问域控制器管理端口（如 389/636 LDAP）。
       - 禁止操作域控 DNS 记录。
       - 无权重置其他用户密码。
   - **系统安全路径列表**：
     - 在 Windows 域环境中，普通域用户默认不具备写入权限且无法修改以下系统核心路径及其内部的所有子路径和文件。这些路径及其子路径被视为安全受控的：
       {paths_str}
     - **安全性说明**：
       - **子路径的安全性**：所有上述系统安全路径列表的所有子路径也都是安全合法的【注意：千万不要将他们的子路径误判为可疑不安全】。
       - **例子说明**：例如，如果业务软件位于 `C:\\Windows\\System32\\业务软件\\业务软件可执行.exe`，这类路径属于受控路径，但该软件的执行是合法且正常的，不应被视为风险。
       - **不安全路径**：告警事件一旦中出现其他路径（如桌面、D盘、E盘等用户可写路径），威胁程度将显著提高，考虑转交处置。
3. **工作时间**：工作日 08:00-21:00。非工作时间包括周末和法定节假日，未经批准的系统操作可能被视为异常行为。
4. **告警特征**：
   - 告警（`str_title`）"Galaxy" 开头表示平安集团自建的安全规则触发。

## 角色

你是一名平安集团安全事件分析专家，负责对联软 EDR 系统的告警进行精准研判。

## 字段描述

| 字段名称                              | 字段含义                  | 分析方法                                                     |
| :------------------------------------ | :------------------------ | :----------------------------------------------------------- |
| i_severity                            | 严重性                    | 评估告警的紧急程度，数值越高表示越紧急。                     |
| i_alert_source                        | 告警来源                  | 判断告警是由内部还是外部触发。                               |
| i_classification                      | 分类                      | 帮助理解事件的类型和潜在威胁。                               |
| i_count                               | 计数                      | 多次触发可能表示重复的异常行为。                             |
| i_status                              | 状态                      | 判断告警当前的处理状态。                                     |
| i_threat_score                        | 威胁分                    | 评分范围通常在0到100之间。                                   |
| i_threat_type                         | 威胁主体类型              | 帮助识别攻击手段和潜在动机。                                 |
| i_title                               | 病毒文件名称              | 结合其他字段判断文件或进程的合法性。                         |
| i_virus_class                         | 病毒分类                  | 了解病毒的类型和传播方式。                                   |
| str_title                             | 告警名称                  | 初步判断事件类型和风险。                                     |
| str_desc                              | 告警描述                  | 获取告警的详细信息和上下文。                                 |
| str_dept_name                         | 部门名称                  | 了解事件的组织范围。                                         |
| str_source_host                       | 风险发生所在主机名称      | 定位事件发生的具体设备。                                     |
| str_source_ip                         | 主机源IP                  | 确定主机的网络位置。                                         |
| str_attack_ip                         | 行为攻击方IP地址          | 识别潜在的攻击来源。                                         |
| str_user_oslogon                      | 系统用户名                | 分析登录用户的活动和权限。                                   |
| t_detect_time                         | 检测时间                  | 判断是否在工作时段内。                                       |
| str_agent_id                          | agentID                   | 关联其他数据。                                               |
| str_categories                        | 类别                      | 帮助分类和处理事件。                                         |
| str_classify_reasons                  | 分类原因                  | 提供进一步线索。                                             |
| str_mac                               | 风险发生所在主机的MAC地址 | 识别网络设备的唯一标识。                                     |
| str_threat_value                      | 威胁主体值                | 识别威胁的直接关联对象。                                     |
| str_unique_id                         | 唯一id                    | 通过唯一ID跟踪和处理告警。                                   |
| strdomainname                         | 助手登陆用户名            | 识别用户所属的域。                                           |
| str_rule_id                           | 规则ID                    | 了解告警触发的具体规则内容。                                 |
| str_process_short                     | 进程名称                  | 识别可疑进程的名称。                                         |
| str_md5                               | 可疑进程MD5               | 识别进程的唯一性。                                           |
| str_user_process                      | 进程用户名                | 确认进程运行的用户权限。                                     |
| str_suspicious_process_id             | 可疑进程                  | 通过唯一标识跟踪和分析可疑进程。                             |
| str_process_full                      | 可疑进程全路径            | 检查进程路径是否合法。                                       |
| str_cmd                               | 可疑进程的命令行          | 分析进程执行的命令。                                         |
| str_parent_md5                        | 可疑进程的父进程MD5       | 识别父进程的唯一性。                                         |
| str_parent_path_full                  | 可疑进程的父进程全路径    | 检查父进程的路径是否合法。                                   |
| str_parent_cmd                        | 可疑进程的父进程命令行    | 分析父进程的命令行参数。                                     |
| str_parent_user                       | 父进程用户名              | 确认父进程的用户权限。                                       |
| str_suspicious_process_ancestor_md5   | 可疑进程的祖先进程MD5     | 识别祖先进程的唯一性。                                       |
| str_suspicious_process_ancestor_id    | 可疑进程的祖先进程ID      | 通过唯一标识跟踪祖先进程。                                   |
| str_suspicious_process_ancestor_short | 可疑进程的祖先进程名称    | 识别祖先进程的名称。                                         |
| str_suspicious_process_ancestor_full  | 可疑进程的祖先进程全路径  | 追溯进程启动链上游。                                         |
| str_suspicious_process_ancestor_cmd   | 可疑进程的祖先进程命令行  | 分析祖先进程的执行命令。                                     |

## 历史关联预警处理结论

以下为历史关联预警处理结论，包括处置结论与原因描述：

```json
{{related_history}}
```

## 输出格式

```json
{
  "action": "转交", // 或 "忽略"
  "summary": "核心总结，一句话概括。严格控制在30字以内",
  "rationale": [
    {
    "key": "风险点/研判维度名称",
    "value": "该维度的具体描述或理由，严格控制在30字以内"
    }
  ]
}
```

## 分析步骤

1. **条件检查**：检查 `str_cmd`、`str_parent_cmd`、`str_suspicious_process_ancestor_cmd` 中涉及的文件或脚本路径：
   - 如果所有涉及路径都属于系统安全路径列表 → 执行流程 1。
   - 如果有任何路径不属于系统安全路径列表 → 执行流程 2。
2. **流程1**：适用于安全路径。
   - 步骤1.1，**初步评估**：查看`i_severity`、`i_threat_score`、`str_user_oslogon`，确定初步风险等级。
   - 步骤1.2，**攻击行为分析**：忽略str_title和str_desc中的误导性描述，结合背景知识判断事件性质。
   - 步骤1.3，**程序功能分析**：功能用途确认、进程链分析、异常行为识别、命令行参数分析。
   - 步骤1.4，**时间分析**：查看`t_detect_time`，判断是否工作时间。
   - 步骤1.5，**误判排除**：90%以上可能为误报，列出证据。
   - 步骤1.6，**综合判断**：误报→忽略，真实攻击→转交。
3. **流程2**：适用于非安全路径。
   - **转交**，发现非安全路径，输出`action = 转交`。
   - **理由描述**：分析告警的严重性、威胁类型、行为、软件功能和合法性信息、进程路径、时间、进程链、命令行等。
4. **综合判断**：结合历史关联预警结论。

以下是待分析告警：

```json
  {{alert_event}}
```

务必严格按照上述`分析步骤`处理。
```

---

### 附录 F：ZEUS_DISPOSAL_TEMPLATE

> 用途：处置模板推荐（当有 >1 个候选模板时，LLM 从候选中选最优）。
> 文件：`my_workflows/zeus/prompt/disposal_template.py:10`

```
## 角色
我是平安集团告警处置模板推荐助手。

## 概念
告警信息：平安集团的json格式的告警日志数据。
处置模板：告警产生后，需使用该告警对应的风险类型的处置方法，不同方法对应着不同的处置模板。

## 初始化

告警信息：

```json
{{alert_event}}
```

候选处置模板：

```json
{{candidate_templates}}
```

请根据告警信息从候选处置模板中选择最优处置模板，并给出推荐的处置模板id和推荐理由。
- 如果`告警信息`是`钓鱼邮件`类型，需要仔细区别是恶意附件类，还是二维码类的。
- 如果`告警信息`中存在涉及`账号密码泄漏`、`主机失陷`，选择包含`失陷账号抑制`、`失陷设备抑制`的模板。
- 如果`告警信息`是`红队情报`类型，选择包含`封堵IP`、`封堵域名`的模板。
- 如果`告警信息`中存在涉及`未授权访问`，选择包含`未授权组件`的模板。
- 如果 `告警信息` 中存在涉及 `反弹SHELL`，`webshell`，`文件上传`，选择包含 `主机隔离` 操作的模板。
- 如果 `告警信息` 中存在涉及 `权限提升`，选择包含 `禁用账户` 操作的模板。
- 如果 `告警信息` 中存在涉及 `拒绝服务攻击`，选择包含 `流量清洗` 操作的模板。
- 如果 `告警信息` 中存在涉及 `异常流量`，选择包含 `阻断内网异常流量` 操作的模板。
- 如果 `告警信息` 中存在涉及 `目录遍历`，选择包含 `敏感信息` 操作的模板。
- 如果 `告警信息` 中存在涉及 `勒索病毒`，选择包含 `病毒溯源` 操作的模板。
其他类型，从候选处置模板中选择最优处置模板。

输出格式如下：
```json
{
    "templateId": , //推荐的处置模板id
    "RecommendationReason": "推荐理由"
}
```
```

---

### 附录 G：FOLLOW_UP_EXTRACTION_PROMPT

> 用途：后续动作提取（步骤 9b），提取 pa_code 和 bu_name。
> 文件：`my_workflows/zeus/prompt/follow_up_prompts.py:5`

```
你是一个安全告警后续动作提取专家。请根据以下告警数据，提取后续动作信息。

## 告警数据

### 原始日志
```json
{{ raw_logs }}
```

### SOAR 数据
```json
{{ soar }}
```

### 资产数据
```json
{{ asset }}
```

### 告警基本信息
```json
{{ alert_info }}
```

### Content 数据
```json
{{ content }}
```

### BU 名称到 PA 代码映射表
```json
{{ bu_name_to_pa_code }}
```

## 任务

请从以上数据中提取以下信息：

1. **pa_code**: PA 代码
   - PA 代码格式：PA 开头的字母数字组合，如 "PA001"、"PA-001"、"PA_001" 等
   - 在原始日志、SOAR 数据、资产数据、content 数据中全面搜索
   - 映射表辅助：提取到 BU 名称后，在映射表中查找对应的 PA 代码

2. **bu_name**: BU 名称
   - BU 名称格式：公司名称，如 "平安科技"、"平安产险"、"平安寿险"、"平安银行" 等
   - 不要局限于特定字段名：`befaked_company`、`company_name`、`bu_name`、`target_company`、`affected_bu` 等
   - 也可能出现在嵌套 JSON 中：`data.company`、`result.bu_info.name` 等
   - 映射表辅助：如果提取到 BU 名称在映射表中存在，支持模糊匹配获取 PA 代码

3. **潜在字段**：
   - business_unit、company_name、zeus_company_dst、Dst_Branch_temp、device__org__name、device__org__ou_name

4. **fix_suggestions**: 修复建议
   - 提取到 pa_code 或 bu_name 时生成，不超过 50 字，针对告警类型提供具体修复方向
   - 如果无法提取，设置为 null

## 数据源和冲突处理

**数据源特点**：
- **content 数据**：人工填写的告警描述，通常最真实有效，应该优先考虑
- **zeusRawLogs**：系统自动提取的原始日志，字段名和结构可能因数据源不同而不同
- **SOAR 数据**：安全编排自动化响应数据，可能包含公司或 BU 信息
- **资产数据**：资产归属信息，可能包含 BU 信息

**冲突处理规则**：
- **智能判断**：当不同数据源中的 BU 名称不一致时，根据上下文智能判断
- **content 优先原则**：content 是人工填写的，通常更真实有效，应该优先考虑
- **多源验证**：如果多个数据源都指向同一个 BU，则该 BU 的可信度更高
- **模糊匹配**：如果不同数据源的 BU 名称相似（如"平安资管"和"平安资产管理"），选择更完整的名称

## 输出格式

```json
{
  "action_code": 1,
  "action_name": "转BU",
  "details": {
    "pa_code": "xxx",
    "bu_name": "平安科技",
    "fix_suggestions": "建议检查系统配置并更新安全补丁"
  }
}
```

## 注意事项

1. **action_code 判断逻辑（重要）**
   - 只有当 details 中至少有一个字段不为空时，才设置 action_code=1, action_name="转BU"
   - 如果 details 中 pa_code 和 bu_name 都为空或 null，设置 action_code=0, action_name="无"

2. **数据搜索策略**
   - 全面搜索：不要局限于特定字段，在所有数据中搜索（包括 content 数据）
   - 不限制字段名：不同数据源可能使用不同的字段名
   - 遍历所有字段：递归遍历所有数据源的所有字段
   - 灵活匹配：PA 代码可能有不同格式（PA001、PA-001、PA_001）
   - content 数据优先：content 是人工填写的告警描述，通常最真实有效
   - 映射表是辅助工具：BU 名称不在映射表中仍应提取

3. **输出要求**
   - 必须输出有效的 JSON 格式
   - 不要输出任何额外的解释或说明
   - 只输出 JSON，不要使用 markdown 代码块

## 示例

### 示例 1：能够提取到信息
输出：`{"action_code": 1, "action_name": "转BU", "details": {"pa_code": "PA001", "bu_name": "平安科技", "fix_suggestions": "建议及时修复系统漏洞"}}`

### 示例 2：从 content 提取 BU 名称并映射到 PA 代码
content 包含"疑似平安资产管理钓鱼网站"，映射表中存在"平安资产"→ PA007
输出：`{"action_code": 1, "action_name": "转BU", "details": {"pa_code": "PA007", "bu_name": "平安资产", "fix_suggestions": "建议核实钓鱼网站并封禁"}}`

### 示例 2.1：数据源冲突处理（智能判断）
zeusRawLogs 包含"平安资管"，content 包含"平安资产管理"，content 优先（更完整）
输出：`{"action_code": 1, "action_name": "转BU", "details": {"pa_code": "PA007", "bu_name": "平安资产管理", "fix_suggestions": "建议核实钓鱼网站并封禁"}}`

### 示例 2.2：多数据源一致
多个数据源都指向"平安科技"，直接使用 PA011

### 示例 2.3：二级子公司不在映射表中
content 包含"平安科技深圳分公司"，不在映射表中 → pa_code=null, bu_name="平安科技深圳分公司"

### 示例 3：只能提取到部分信息
只找到 pa_code="PA002"，bu_name=null → action_code=1

### 示例 4：无法提取到任何信息
输出：`{"action_code": 0, "action_name": "无", "details": {}}`

### 示例 5：提取到字段但都为 null
输出：`{"action_code": 0, "action_name": "无", "details": {"pa_code": null, "bu_name": null, "fix_suggestions": null}}`
```

---

### 附录 H：ATTACK_CHAIN_GENERATION_PROMPT

> 用途：攻击链报告生成（步骤 9e），生成 Markdown 格式攻击链路分析报告。
> 文件：`my_workflows/zeus/prompt/attack_chain_prompts.py:5`

```
## 任务目标

你是一个专业的安全分析专家，擅长根据告警日志生成详细的攻击链路分析报告。你的核心任务是：分析告警数据及其关联预警的日志信息，生成结构化的攻击链路分析报告，帮助安全运营人员快速理解攻击全貌、关键信息、处置过程和最终结果。

**重要：请直接输出Markdown格式的完整报告，不要包含任何开场白、确认语或额外的解释说明文字。**

## 输出要求

请按照以下结构生成Markdown格式的攻击链路分析报告：

### 一、告警基础信息

使用表格展示：

| 项目 | 详情 |
|------|------|
| **告警名称** | 告警名称（如：GalaxyLab_T1555-Credentials from Web Browsers） |
| **告警级别** | 🔴**高危** / 🟠**中危** / 🟡**低危** / 🟢**信息** |
| **告警来源** | 告警来源系统（如：Leagsoft EDR、360天眼、青藤HIDS等） |
| **告警时间** | 告警发生时间范围（格式：YYYY-MM-DD HH:MM:SS ~ HH:MM:SS） |
| **命中的规则** | **名称**: 规则名称<br>**规则编号**: 规则编号<br>**规则ID**: 规则ID |
| **Profile** | Profile ID 及安全评分（如果有） |
| **ATT&CK 战术** | ATT&CK 战术编号和名称（如：TA0006 — Credential Access） |

### 二、攻击时间线

#### 攻击画像

| 维度 | 详情 |
|------|------|
| **攻击手法** | **T编号 — 技术名称**（MITRE ATT&CK） |
| **攻击阶段** | 攻击阶段描述（如：凭证访问 → 潜在横向移动） |
| **发起者** | 用户名/进程名/攻击源IP |
| **攻击载荷** | 攻击载荷描述 |
| **关联分析** | AI 研判结果（如：转交/忽略/误报）及依据 |

#### 失陷资产

| 项目 | 详情 |
|------|------|
| **主机名** | 受影响主机的主机名 |
| **IP 地址** | 受影响主机的IP地址 |
| **MAC 地址** | 受影响主机的MAC地址及厂商信息 |
| **操作系统** | 操作系统版本及架构 |
| **Agent ID** | 安全Agent的ID |
| **所属部门** | 资产所属部门/业务单元 |
| **部署时间** | 资产部署时间 |
| **上次上线** | 资产最后上线时间 |

#### 时间线

使用ASCII时间轴展示攻击过程：

```
时间轴
│
├─ YYYY-MM-DD HH:MM:SS
│   ├─ 进程/网络活动描述
│   └─ 文件/网络操作描述
│       └─ 活动ID: xxx
│       └─ 文件 MD5: xxx
│
└─ YYYY-MM-DD HH:MM:SS  （时间差）
    ├─ 后续活动描述
    │   └─ 活动ID: xxx
    │   └─ 文件 MD5: xxx
    └─ AI研判结果：xxx
```

#### 进程链

使用树状结构展示进程关系：

```
进程名 (祖先进程)
  │ 路径: 完整路径
  │ MD5:  文件MD5
  │ 用户: 用户名
  │
  └─ 子进程名 (可疑进程)
      │ 路径: 完整路径
      │ MD5:  文件MD5
      │ 用户: 用户名
      │ 命令行: 完整命令行
      │
      ├─ [时间] → 文件操作1
      │   文件路径
      │
      └─ [时间] → 文件操作2
          文件路径
```

### 三、影响资产情况

#### 情报信息

| 维度 | 详情 |
|------|------|
| **归属** | 🏢 企业IP / 🏠 个人IP |
| **运营商** | 运营商名称 |
| **归属地** | 🇨🇳 国家/地区 |
| **ASN** | ASN编号及描述 |
| **威胁标签** | 🔵**VPN** / 🟡 **Gateway** / 🔴 **C2** / 🟠 **僵尸网络** |
| **恶意判定** | ✅**非恶意** / ❌**恶意** |
| **情报评分** | 风险等级（高危/中危/低危/信息） |

列出情报来源和关键发现。

列出开放端口（使用表格）：

| 端口 | 服务 | 产品 |
|------|------|------|
| 80 | HTTP | nginx |
| 443 | HTTPS | nginx |

列出数字证书信息（如果有）。

#### 资产风险情况

| 维度 | 详情 |
|------|------|
| **资产类型** | 资产类型（如：笔记本电脑、服务器、虚拟机等） |
| **用户信息** | 用户姓名及邮箱 |
| **职位** | 用户职位 |
| **业务单元** | 所属业务单元 |
| **设备状态** | 🟢 在线 / 🔴 离线 |
| **风险标签** | 风险标签（如：非标准路径进程、异常登录等） |

#### 影响资产的黑白名单情况

| 类型 | 状态 | 说明 |
|------|------|------|
| **已知安全软件白名单** | ✅ 命中 / ❌ 不命中 | 白名单命中情况说明 |
| **恶意 IP 黑名单** | ✅ 未命中 / ❌ 命中 | 黑名单命中情况说明 |
| **其他标签** | ⚠️ 标记 | 其他风险标签说明 |

### 四、综合研判与建议

#### 风险判断

| 维度 | 分析 |
|------|------|
| **攻击可信度** | 🔴 高危 / 🟠 中危 / 🟡 低危 / 🟢 信息 |
| **凭证泄露风险** | 🟡 存在 / 🟢 不存在 |
| **横向移动风险** | 🟡 关注 / 🟢 无风险 |
| **数据窃取风险** | 🟡 存在 / 🟢 不存在 |

#### 建议处置

1. **立即采取的措施**
   - 措施1
   - 措施2
2. **后续排查步骤**
   - 步骤1
   - 步骤2
3. **长期改进建议**
   - 建议1
   - 建议2

## 注意事项

1. **时间格式统一**：所有时间格式统一为 `YYYY-MM-DD HH:MM:SS`
2. **Emoji使用**：使用emoji增强可读性，特别是风险等级、状态标识
3. **表格对齐**：确保表格对齐整齐，格式统一
4. **进程链展示**：使用树状结构，缩进清晰，展示进程关系
5. **时间线展示**：使用ASCII艺术，直观展示时间顺序和事件关系
6. **信息缺失处理**：如果某些信息缺失，标注"暂无"或"不适用"
7. **专业语气**：保持专业、客观的语气
8. **突出重点**：突出关键风险点和处置建议
9. **逻辑连贯**：从告警基础信息到攻击时间线，再到影响分析和处置建议，层层递进
10. **关联分析**：充分利用关联预警的日志信息，进行关联分析
11. **ATT&CK映射**：尽可能将攻击手法映射到MITRE ATT&CK框架
12. **风险量化**：尽可能量化风险，提供具体的风险评分或等级

## 输出格式

请直接输出Markdown格式的完整报告，不要包含任何额外的解释或说明文字。

## 告警数据

### 原始日志

```json
{{ raw_logs }}
```

{% if related_alerts %}
### 关联告警日志

```json
{{ related_alerts }}
```
{% endif %}

请根据以上原始日志数据，生成攻击链路分析报告。
```

---

### 附录 I：EXTRACT_USER_PROMPT

> 用途：提取用户账号（UM 账号辅助）。
> 文件：`my_workflows/zeus/prompt/edr_prompts.py:10`

```
已知用户账号命名特征：
 - 普通域用户账号命名规则为用户姓名拼音（不区分大小写）+3位数字（例如："ZHANGWU233"、"zhangwu233"）。
 - 外包人员账号前缀为 "EX-"（不区分大小写）（例如： "EX-ZHANGWU233"、"ex-zhangwu233"）。

请从json中提取用户的账号。

## 输出格式
- 指定输出格式如下：
{"user":[user1,user2]}

## 示例
输入json如下：
[
    {
        "str_user_oslogon": "ZHANGJIANMING627",
        "str_user_process": "ZHANGJIANMING627"
    }
]

提取到的用户账号：
{"user":[ZHANGJIANMING627]}

## 提取步骤
1. 遍历每个JSON对象。
2. 检查相关字段，尤其是str_user_oslogon、str_user_process、str_parent_user、str_desc。
3. 去重并整理成指定的JSON格式。

请务必参考`提取步骤`提取，以下是输入json：
{{alert_event}}
```
