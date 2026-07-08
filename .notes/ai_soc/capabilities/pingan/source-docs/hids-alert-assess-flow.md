# HIDS 研判流程详细文档

> 核心文件：`my_workflows/zeus/flows/hids_alert_assess.py`
> 引擎：LlamaIndex `Workflow` + `@step` 异步方法
> 大模型：平安内部 LLM（OpenAI compatible 接口）

---

## 一、整体架构

HIDS 研判是一个**基于 event_type 的事件分类 + 漏斗决策**的两阶段流水线：

```
第一阶段：研判决策（忽略 / 转交）
┌──────────────────────────────────────────────────────────────────┐
│ StartEvent                                                       │
│   │ 原始告警数据 (HidsRoot)                                       │
│   ▼                                                              │
│ [1] 数据预处理 → JudgeDataEvent    提取ID、日志、event_type       │
│   │                                                              │
│ [2] 关联预警历史判断              ← 能快速判定的立刻返回           │
│   ├─ 能判断 → FinalEvent（直接结束）                              │
│   └─ 不能判断 → KnowledgeEvent                                   │
│       │                                                          │
│ [3] 知识库研判 → MainStartEvent    （预留扩展，当前透传）          │
│   │                                                              │
│ [4] 主流程 LLM 研判                                               │
│   │  根据 event_type 选择对应 Prompt 进入 LLM 研判                │
│   │  → 9 种 event_type 专属 Prompt                              │
│   │  → 兜底：HIDS_WITH_BACKGROUND_PROMPT_0415（通用兜底）         │
│   │                                                              │
│   ▼ 产出 FinalEvent ← 研判结论：忽略 或 转交                      │
└──────────────────────────────────────────────────────────────────┘
                             │
                             ▼
第二阶段：处置闭环（转交且预警时执行）
┌──────────────────────────────────────────────────────────────────┐
│ [5] 生成攻击详情 + 资产定位 + 研判合并                             │
│   ├─ 5a. 资产归属定位（AssetExtractor → search_asset_info → 工作流）│
│   ├─ 5b. 攻击详情生成子工作流                                      │
│   └─ 5c. alert_action_merge 研判合并                              │
│   │                                                              │
│ [6] 服务器隔离处置（HIDS 特有核心处置）                             │
│   ├─ 从 asset_bu_info.disposal_target 提取 HOST 资产               │
│   ├─ 按 attacker/target 角色隔离                                   │
│   └─ HidsServerDispatcher 执行 operateType=6 服务器隔离            │
│   │                                                              │
│ [7] 后续动作提取                                                  │
│   ├─ FollowUp 提取（pa_code + bu_name）                           │
│   ├─ 兜底分单（asset_bu_info）                                    │
│   └─ attack_chain 提取（必然执行）                                │
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
| 模型参数（主流程） | `top_p=0.5, temperature=0.85, frequency_penalty=0.2` |
| 模型参数（main 入口） | `top_p=0.5` |
| 调用超时 | 7min（攻击详情子工作流） |
| use_history | `True`（启用关联预警判断） |
| default_ignore_parameter | 2（关联预警判断阈值） |

---

## 三、青藤 HIDS 系统背景

### 3.1 系统介绍

- **青藤 HIDS**：安装在平安集团内部云服务器的主机安全监控软件，通过系统层面的行为监控发现异常入侵行为
- **操作系统环境**：Linux 各类发行版 + Windows 各类服务器发行版
- **三类部署环境**：
  - **生产（prd）**：正式上线系统，与测试/开发有严格防火墙限制 → 产生 `security_qthids` 告警
  - **测试（stg）**：上线前功能和稳定性验证 → 产生 `security_qthids-stg` 告警
  - **开发（dev）**：自由部署，限制较少 → 产生 `security_qthids-stg` 告警
- **数据中心机房**：观澜(gl)、宝信(bx)、东莞(dg)、廊坊(lf)、光明(gm)、外高桥(wgq)、重庆两江(lj)
- **内部域名**：`*.paic.com.cn`（互联网不可访问）
- **内部网段**：10.0.0.0/8、30.0.0.0/8、172.16.0.0/16、192.168.0.0/16

### 3.2 支持的事件类型（event_type）

```python
prompt_dict = {
    "malic_opera": PROCESS_PART_MALIC_OPERA,        # 可疑操作
    "backdoor_diagnose": PROCESS_PART_BACKDOOR_DIAGNOSE,  # 后门检测(Linux)
    "backdoor_diagnose_win": PROCESS_PART_BACKDOOR_DIAGNOSE,  # 后门检测(Win)
    "bounce_shell": PROCESS_PART_BOUNCE_SHELL,      # 反弹Shell(Linux)
    "win_bounce_shell": PROCESS_PART_BOUNCE_SHELL,  # 反弹Shell(Win)
    "web_command": PROCESS_PART_WEB_COMMEND,        # Web命令执行(Linux)
    "web_command_win": PROCESS_PART_WEB_COMMEND,    # Web命令执行(Win)
    "bruteforce_ext": PROCESS_PART_BRUTE_FORCE,     # 外网暴力破解
    "bruteforce_inter": PROCESS_PART_BRUTE_FORCE,   # 内网暴力破解
    "anti_virus_detect": PROCESS_PART_VIRUS_DETECT, # 病毒检测
    "privilege_escalation": PROCESS_PART_PRIVILEGE_ESCALATION,  # 系统提权
    "honeypot": PROCESS_PART_HONEYPOT,              # 踩蜜罐
    "webshell": PROCESS_PART_WEBSHELL,              # WebShell
}
# 不在上述列表中 → 使用 HIDS_WITH_BACKGROUND_PROMPT_0415（通用兜底）
```

### 3.3 输出格式

```json
{
    "action": "转交" | "忽略",
    "rationale": "执行了过程x，核心推导依据是：xxx"
}
```

---

## 四、研判决策阶段（第一阶段）

### 4.1 步骤 1：数据预处理

**代码位置**：`HidsAlertWorkflow.prepare_msgs()` → `my_workflows/zeus/flows/hids_alert_assess.py:143`

**职责**：从原始 hit log 中解出研判所需的全部必要数据。

**处理步骤**：

| 步骤 | 操作 | 产出 |
|------|------|------|
| 1 | 提取 `alert_id` | alert_id |
| 2 | 获取关联预警情况 | `related_status_count` |
| 3 | 获取研判入模数据 | `flow_datas`：从 hitLog 提取 zeusRawLogs，取前 2 条 |
| 4 | 提取 3 级预警类型 ID | `alter_full_type_id` |
| 5 | 提取事件类型 | `event_type`：从 zeusRawLogs[0].event_type，默认"未知类型" |
| 6 | 提取执行类型 | `execute_type`：=3 为告警，其他为预警 |

**产出**：`JudgeDataEvent` 包含 `alert_id`, `related_status_count`, `flow_datas`, `alter_full_type_id`, `execute_type`, `event_type`, `evaluation_trace`

---

### 4.2 步骤 2：关联预警历史判断

**代码位置**：`HidsAlertWorkflow.judge_by_related_alert()` → `my_workflows/zeus/flows/hids_alert_assess.py:186`
**核心逻辑**：`judge_by_related_data_adjustment()` → `my_workflows/zeus/utils/util_tools.py:184`

**职责**：基于历史关联预警的处置结论，尝试直接得出结论，避免不必要的 LLM 调用。

**关键差异**：HIDS 的 `default_ignore_parameter=2`，比 EDR 的 3 和 APT 的 4 都更低，意味着更严格（更少历史忽略次数就判定忽略）。

**判定流程**：

```
if use_history == True:
    调用 judge_by_related_data_adjustment(
        related_status_count,
        alter_full_type_id,
        default_ignore_parameter=2    ← HIDS 特有
    )
    if need_continue:
        → continue（知识库节点）
    else:
        → FinalEvent（直接结束）
else:
    → KnowledgeEvent
```

**产出**：能判断 → `FinalEvent`；不能判断 → `KnowledgeEvent`

---

### 4.3 步骤 3：知识库研判（预留）

**代码位置**：`HidsAlertWorkflow.judge_by_knowledge()` → `my_workflows/zeus/flows/hids_alert_assess.py:226`

**当前状态**：未实现 LLM 调用，直接透传数据到 `MainStartEvent`，标记为预留扩展节点。

---

### 4.4 步骤 4：主流程 LLM 研判

**代码位置**：`HidsAlertWorkflow.judge_by_main()` → `my_workflows/zeus/flows/hids_alert_assess.py:242`

**职责**：根据 `event_type` 选择对应的专属 Prompt，调用 LLM 进行研判。

**Prompt 组装方式**（不同于 EDR 和 APT）：

```python
# HIDS 采用碎片化 Prompt 组装模式
prompt_dict = {
    "malic_opera": PROCESS_PART_MALIC_OPERA,
    "backdoor_diagnose": PROCESS_PART_BACKDOOR_DIAGNOSE,
    "backdoor_diagnose_win": PROCESS_PART_BACKDOOR_DIAGNOSE,
    "bounce_shell": PROCESS_PART_BOUNCE_SHELL,
    "win_bounce_shell": PROCESS_PART_BOUNCE_SHELL,
    "web_command": PROCESS_PART_WEB_COMMEND,
    "web_command_win": PROCESS_PART_WEB_COMMEND,
    "bruteforce_ext": PROCESS_PART_BRUTE_FORCE,
    "bruteforce_inter": PROCESS_PART_BRUTE_FORCE,
    "anti_virus_detect": PROCESS_PART_VIRUS_DETECT,
    "privilege_escalation": PROCESS_PART_PRIVILEGE_ESCALATION,
    "honeypot": PROCESS_PART_HONEYPOT,
    "webshell": PROCESS_PART_WEBSHELL,
}

# 每个事件类型的 Prompt = HEAD_PART + PROCESS_PART_* + TAIL_PART
for item in prompt_dict.keys():
    prompt_dict[item] = HEAD_PART + prompt_dict[item] + TAIL_PART
```

**LLM 调用**：`judge_by_llm()` → `my_workflows/zeus/utils/util_tools.py:316`（与 EDR/APT 共享）

---

### 4.5 各事件类型 Prompt 关键研判步骤

以下列出各 `PROCESS_PART_*` 片段的核心研判逻辑：

#### 4.5.1 malic_opera（可疑操作）

**关注字段**：detail_time / detail_src_ip / detail_login_user / detail_uname / detail_cmd / detail_hit_rule_names

**忽略条件**：
1. event_level 为 info → 忽略
2. detail_uname 为非 root 用户清理痕迹 → 忽略
3. event_content 为从 pkg-proxy.yun.paic.com.cn:8080 下载安装软件 → 忽略
4. event_content 为从 github 下载和提交代码 → 忽略
5. detail_rule 是"Linux 远程下载并执行_a"且请求 *.paic.com.cn → 忽略

#### 4.5.2 backdoor_diagnose / backdoor_diagnose_win（后门检测）

**关注字段**：detail_uname / detail_comment / detail_backdoor_type

**忽略条件**：
1. event_level 为 info → 忽略
2. detail_backdoor_type 为"映像劫持"且 event_level=alert → 忽略
3. detail_backdoor_type 为"查看共享场景 net share"且 event_level=danger → 忽略
4. detail_backdoor_type 为"可疑进程参数"且脚本含 *.paic.com.cn → 忽略
5. detail_backdoor_type 为"可疑进程参数"且脚本内容实际是运维/健康检查 → 忽略

**最新扩展（0415 版本）**（共 16 条忽略规则）：
- kernel_update 命名的脚本执行 → 忽略（Linux 内核升级脚本）
- reg query 查询行为 → 忽略
- 特定组（办公技术服务部/办公产品管理组）查询 net user → 忽略
- frp 相关启动行为（证券/总部/基础设施及信息安全组） → 忽略
- net user /domain（信息安全团队） → 忽略
- schtasks 计划任务创建 PAMailHotfix.exe → 忽略
- wevtutil.exe 清除 PowerShell 操作日志 → 忽略
- 特定账号（EX-LUOJUNGANG001 / WANGHAOB33 / HUANGYAJUN330 等）触发的枚举域内操作 → 忽略
- /tmp/ospagent/script 路径 → 忽略
- OfficeClickToRun.exe 模块未找到 → 忽略

#### 4.5.3 bounce_shell / win_bounce_shell（反弹 Shell）

**关注字段**：detail_uname / detail_process_tree / detail_ppname / detail_pcmd / detail_pname / detail_cmd

**忽略条件**：
1. detail_cmd 中脚本路径为运维功能脚本（/etc/iscsi/dpd_client.py 等）或 miniconda3 的 jupyter lab → 忽略
2. group_name 为"证券/总部/基础设施及信息安全组"且 detail_ppname="lizhu_exec" → 忽略
3. group_name 为"平安科技/GBD 数据基础工具部/GBD 平台运营组"且 detail_pname="jupyter-lab" → 忽略

#### 4.5.4 web_command / web_command_win（Web 命令执行）

**关注字段**：detail_hit_rule_names / detail_process_name / detail_process_tree / detail_ppname

**忽略条件**：
1. detail_hit_rule_name="查看主机用户信息"且 event_content 含 `/tmp/CVU_19_resource` → 忽略（运维触发）
2. event_content 含"net localgroup administrators /add ecsuser" → 忽略（运维操作）
3. group_name=终端安全组且 detail_hit_rule_name="[web命令执行]tomcat执行系统命令_b" → 忽略
4. event_content 执行 systeminfo → 忽略
5. group_name 为"证券/总部/基础设施及信息安全组"且 process_tree 含 lizhu_agent/lizhu_exec → 忽略
6. event_content 是添加 ada 用户 → 忽略（ada 是主机运维超管）
7. detail_cmd 涉及 `/usr/bin/chattr +a /var/log/shterm/sysstat_history` → 忽略（运维操作）
8. nmap 扫描使用 -p 指定具体端口 → 忽略（攻击者不会只扫单一端口）
9. event_content 是收集系统配置和运行状态（finalshell_separator 等） → 忽略
10. 触发 AftValidate.vbs/BefImport.vbs 且 group_name="平安科技/系统运营部/集团应用运维组" → 忽略
11. powershell downloadString 从 *.paic.com.cn 下载 → 忽略
12. nvidia-smi → 忽略（属主查看硬件信息）
13. MicrosoftEdgeUpdate.exe → 忽略（微软 Edge 软件升级）
14. PaMailH5App.exe → 忽略（快乐平安 HTML5 小程序）

#### 4.5.5 bruteforce_ext / bruteforce_inter（暴力破解）

**关注字段**：event_type / detail_src_ip / internal_ip

**忽略条件**：
1. event_type="bruteforce_inter"（内网暴力破解）→ 基本都是运维工具或员工触发，**直接忽略**

**注意**：外网暴力破解（bruteforce_ext）无特定忽略规则，进入综合研判。

#### 4.5.6 anti_virus_detect（病毒检测）

**关注字段**：detail_path / detail_rule

**忽略条件**：
1. group_name="基础设施及信息安全组" → 内部病毒测试，忽略
2. detail_path 含 IntelligenceAgentService / FileZilla / pbrc → 杀软误报，忽略

#### 4.5.7 privilege_escalation（系统提权）

**关注字段**：detail_uname / detail_pname / init_priv

**忽略条件**：
1. group_name="平安科技/GBD 数据基础工具部/GBD 平台运营组"，使用 root/hadoop，通过 container-execu 提权 → 忽略
2. group_name="健康险"，使用 dluser 通过 sudo 提权到 root → 忽略

#### 4.5.8 honeypot（踩蜜罐）

**忽略条件**：
1. group_name="证券/总部/基础设施及信息安全组" → 安全正常行为，忽略
2. event_content 来自 26.38.4.2 的蜜罐触发 → 忽略
3. detail_process_name=SearchIndexer.exe → Windows 文件搜索引擎触发，忽略

#### 4.5.9 webshell

**忽略条件**：
1. group_name="证券/总部/基础设施及信息安全组" → 安全测试，忽略
2. 青藤自研检测出的告警 → 基本都是误报，忽略

---

### 4.6 通用兜底 Prompt（HIDS_WITH_BACKGROUND_PROMPT_0415）

当 event_type 不在 prompt_dict 中时，使用 `HIDS_WITH_BACKGROUND_PROMPT_0415` 作为通用兜底。

这是目前最新的版本（0415），包含 **10 个过程**的完整研判逻辑（比 0410 多了 honey_file 蜜罐文件过程）。

**10 个过程**：

| 过程 | event_type | 忽略条件数 |
|------|------------|-----------|
| 1 | malic_opera（可疑操作） | 5 |
| 2 | backdoor_diagnose（后门检测） | 14 |
| 3 | bounce_shell（反弹Shell） | 3 |
| 4 | web_command（Web 命令执行） | 16 |
| 5 | bruteforce_ext/inter（暴力破解） | 1 |
| 6 | anti_virus_detect（病毒检测） | 2 |
| 7 | privilege_escalation（系统提权） | 2 |
| 8 | honeypot（踩蜜罐） | 3 |
| 8（重复编号） | honey_file（蜜罐文件） | 1 |
| 10 | webshell | 2 |

**综合推理**（所有过程共同）：
- **行为分析**：分析进程链中所有进程及对应命令行，观察是否包含攻击入侵相关的恶意命令参数
- **恶意可能性评估**：根据进程链中每个进程完整路径和完整命令行，逐个识别是否存在攻击工具特征
- **核心推导**：一旦进程链或参数出现攻防相关软件或工具 → 判断为恶意，转交
- **判定原则**：
  - 进程来源未知 + 程序用户权限可控 + 存在攻击特征行为 → 转交
  - 即使有疑似测试关键字，如果是攻击工具 → 转交
  - 进程或命令行中涉及目录为用户可读写时视为危险

---

### 4.7 版本演进（HIDS_WITH_BACKGROUND_PROMPT）

| 版本 | 日期 | 变更 |
|------|------|------|
| 0327 | 初始版 | 8 个过程，基础忽略规则 |
| 0410 | v2 | 8 个过程，增加 webshell 过程 |
| 0411 | v3 | 8 个过程，扩展后门检测忽略规则（+7 条） |
| 0414 | v4 | 9 个过程，扩展 web 命令执行忽略规则（+5 条） |
| 0415 | v5 | 10 个过程，新增 honey_file 过程，扩展所有过程忽略规则 |

---

### 4.8 研判阶段总结

```
数据预处理 → 关联预警判断(default_ignore_parameter=2) → 知识库(透传) → 主流程 LLM 研判
    │
    │  根据 event_type 选择 9 种专属 Prompt 之一
    │  Prompt = HEAD_PART + PROCESS_PART_* + TAIL_PART
    │  不在列表中 → HIDS_WITH_BACKGROUND_PROMPT_0415（通用兜底）
    │
    ▼
FinalEvent ← 研判结论: 忽略 或 转交
含: evaluation_conclusion + disposal_conclusion + alert_type(event_type)
```

---

## 五、处置闭环阶段（第二阶段）

### 5.1 步骤 5a：资产归属定位

**代码位置**：`HidsAlertWorkflow.generate_alert_description()` → `my_workflows/zeus/flows/hids_alert_assess.py:295`
**核心函数**：`locate_asset_bu()` → `my_workflows/zeus/flows/disposition_tools/asset_locator.py:221`

**职责**：与 EDR/APT 共享，三层降级策略（AssetExtractor → search_asset_info → asset_to_bu → ums）。

**产出**：`AssetBuInfo`（与 EDR 相同）

---

### 5.2 步骤 5b：攻击详情生成

**代码位置**：`HidsAlertWorkflow.generate_alert_description()` → `my_workflows/zeus/flows/hids_alert_assess.py:316`
**核心类**：`AlertDescriptionWorkflow` → `my_workflows/zeus/flows/alert_description_generation.py`

**职责**：日志去重 → 切割(≤12000 tokens) → LLM 逐条生成 → 合并。
**产出**：`{action, merged_result, summary}`

---

### 5.3 步骤 5c：研判结果合并

**代码位置**：`alert_action_merge()` → `my_workflows/zeus/process/alert_helper.py:12`

**合并逻辑**（与 EDR/APT 相同）：

| evaluation_action | alert_detail_action | 最终 alert_action |
|-------------------|---------------------|-------------------|
| 转交 | 转交 | **转交** |
| 忽略 | 忽略 | **忽略** |
| 转交 | 忽略 | **忽略** |
| 忽略 | 转交 | **忽略** |

**核心原则**：两个判断中有一个是"忽略"，最终就是"忽略"。

---

### 5.4 步骤 6：服务器隔离处置（HIDS 核心处置）

**代码位置**：`HidsAlertWorkflow.extract_follow_up()` → `my_workflows/zeus/flows/hids_alert_assess.py:362`

**触发条件**：`alert_action == "转交"` **且** `execute_type == 0`（预警时）

**处置流程**：

```
[1] 提取 HOST 资产（严格按 disposal_target 驱动）
    │
    │  _extract_servers_by_disposal_target():
    │    disposal_target = asset_bu_info.disposal_target
    │
    │    if disposal_target in ("-", ""):
    │        → skip_reason="disposal_target=-，无法自动处置，需专家进一步判断"
    │        → 不隔离，记录 skip_reason 到 disposal_conclusion.trace
    │
    │    role_assignments = role_result.get("role_assignments", {})
    │    → 从 role_assignments[disposal_target] 提取 type=="HOST" 的 value
    │
    │    if 无 HOST 资产:
    │        → skip_reason="disposal_target={target} 角色下未找到HOST资产"
    │
    │    if 找到:
    │        → hostname_list = 去重后的主机名列表
    │
    ▼
[2] 执行服务器隔离
    │
    │  HidsServerDispatcher(alert_id=alert_id)
    │    add_block_server_action(
    │        hostname_list=block_server_list,
    │        isolation_reason=f"服务器失陷，{alertCode}，{alertName}"
    │    )
    │
    │  请求体格式：
    │    {
    │        "name": "隔离失陷主机",
    │        "status": 0,
    │        "operateType": 6,              ← 固定为6（服务器隔离）
    │        "invokeParam": {
    │            "scriptIsolationHostname": ["HOSTNAME1", "HOSTNAME2"],
    │            "hostnameIsolationReason": "服务器失陷，{alertCode}，{alertName}"
    │        },
    │        "followUpUms": []
    │    }
    │
    │  templateId: LOCAL/STG=181, PRD=93
    │
    ▼
[3] 执行 execute_and_end_contain()
    │  只有全部成功才结束抑制状态
    │  记录 DispositionExecutionBatch 到 disposition_results
    ▼
```

**隔离请求体格式（operateType=6）**：

```json
{
    "name": "隔离失陷主机",
    "status": 0,
    "operateType": 6,
    "invokeParam": {
        "scriptIsolationHostname": ["SZF-APP01", "SZE-WEB02"],
        "hostnameIsolationReason": "服务器失陷，alertCode123，AlertName456"
    },
    "followUpUms": []
}
```

---

### 5.5 步骤 7：后续动作提取

**代码位置**：`HidsAlertWorkflow.extract_follow_up()` → `my_workflows/zeus/flows/hids_alert_assess.py:331`

**触发条件**：

```python
# 无论是否转交，都提取 follow_up
follow_up = self.follow_up_extractor.extract(alert_root=alert_root)

# 但仅转交 + 预警(execute_type==0) 时才设置 follow_up 字段和执行隔离
if alert_action == "转交" and execute_type == 0:
    → judge_analysis_res.follow_up = follow_up
    → 执行服务器隔离
```

**兜底分单**：如果 FollowUp 提取失败（None 或 action_code=0 或 无 pa_code/bu_name），使用 `asset_bu_info` 兜底分单。

**pa_code / bu_name 一级字段设置**：

```
follow_up.details.pa_code/bu_name    ← 优先
    ↓ (如果为空)
asset_bu_info.company_code/biz_group ← 兜底
```

**攻击链提取**（必然执行，与转交/忽略无关）：

```python
attack_chain = self.attack_chain_extractor.extract(alert_root=alert_root, include_related_alerts=True)
```

---

### 5.6 处置闭环阶段总结

```
FinalEvent ← 研判结论(忽略/转交)
    │
    ▼ generate_alert_description（子流程合并）
    │  ├─ 资产归属定位（AssetExtractor → search_asset_info → 工作流 → ums）
    │  ├─ 攻击详情生成（子工作流）
    │  └─ alert_action_merge 合并（有一个忽略则忽略）
    │
    ▼ extract_follow_up（后续动作与隔离处置）
    │  │  条件：alert_action=="转交" and execute_type==0
    │  ├─ [6] 服务器隔离（HIDS 特有）
    │  │    ← _extract_servers_by_disposal_target()
    │  │    ← 严格按 disposal_target 驱动，不 fallback
    │  │    ← operateType=6（服务器隔离）
    │  │    ← templateId: LOCAL/STG=181, PRD=93
    │  ├─ [7a] FollowUp 提取（不管是否转交都提取）
    │  ├─ [7b] 兜底分单（asset_bu_info）
    │  └─ [7c] 攻击链提取（必然执行）
    │
    ▼ StopEvent → JudgeAnalysisRes（完整结果）
```

---

## 六、核心抑制手段详解

### 6.1 服务器隔离动作（`BlockServerAction`）

**代码位置**：`my_workflows/zeus/flows/disposition_tools/hids_server_disposition_tool.py:38`

**operateType**：固定为 6，表示服务器隔离

**请求体格式**：

```json
{
    "name": "隔离失陷主机",
    "status": 0,
    "operateType": 6,
    "invokeParam": {
        "scriptIsolationHostname": ["HOSTNAME1", "HOSTNAME2"],
        "hostnameIsolationReason": "服务器失陷，{alertCode}，{alertName}"
    },
    "followUpUms": []
}
```

**templateId**：
| 环境 | templateId |
|------|-----------|
| LOCAL/STG | 181 |
| PRD | 93 |

---

### 6.2 IP 隔离（无）

**与 APT 不同**：HIDS 不进行 IP 风险评分和 IP 封堵，仅执行 HOST 隔离。

---

### 6.3 UM 账号封禁（无）

**与 EDR 不同**：HIDS 不进行 UM 账号封禁。

---

## 七、关键数据模型

### JudgeAnalysisRes

| 字段 | 类型 | 说明 |
|------|------|------|
| `alert_type` | Optional[str] | 告警类型标记（event_type） |
| `evaluation_conclusion` | Optional[Dict] | 研判结论：`{action, rationale, trace}` |
| `disposal_conclusion` | Optional[Dict] | 处置结论：`{action, rationale, trace}` |
| `alert_description` | Optional[Dict] | 攻击详情：`{action, merged_result, summary}` |
| `final_conclusion` | Optional[Dict] | 最终结论：`{alert_action, alert_rationale}` |
| `follow_up` | Optional[FollowUpAction] | 后续动作（转交+预警时设置） |
| `asset_bu_info` | Optional[AssetBuInfo] | 资产归属定位结果 |
| `disposition_results` | Optional[DispositionResults] | 抑制动作执行结果 |
| `attack_chain` | Optional[AttackChainDetails] | 攻击链分析结果 |
| `pa_code` | str | 一级字段，始终有值 |
| `bu_name` | str | 一级字段，始终有值 |

### FollowUpAction

| 字段 | 类型 | 说明 |
|------|------|------|
| `action_code` | int | 1=转BU, 0=无 |
| `action_name` | str | "转BU" / "无" |
| `details` | FollowUpDetails | `{pa_code, bu_name, fix_suggestions}` |

### AssetBuInfo

| 字段 | 类型 | 说明 |
|------|------|------|
| `found` | bool | 是否找到归属 |
| `company_code` | str | PA 代码（PA0XX） |
| `biz_group` | str | 业务组/部门 |
| `source` | str | 数据来源 |
| `extracted_assets` | Dict | 按类型分组 |
| `role_result` | Dict | LLM 角色分析结果 |
| `disposal_target` | str | 处置目标：`attacker` / `target` / `-` |
| `search_results` | List | 查询结果详情 |

---

## 八、完整事件流转

```
StartEvent(HidsRoot)
  │
  ▼
[1] prepare_msgs()
  │ → 提取 alert_id, related_status_count, flow_datas(前2条),
  │   alter_full_type_id, event_type, execute_type
  ▼
JudgeDataEvent
  │
  ▼
[2] judge_by_related_alert()
  │ → default_ignore_parameter=2（HIDS 特有，更严格）
  │ → 判定逻辑同 EDR/APT
  │ → use_history=True（默认启用）
  ├─ ignore_status == {"已关闭"} → 转交
  ├─ ignore_status == {"已忽略"} 且 >=2 → 忽略  ← HIDS 更严格
  ├─ ignore_status == {"已忽略"} 且 理由⊆误报 → 忽略
  ├─ ignore_status == {"已忽略","已关闭"} 且 忽略>3 且最近2次忽略 → 忽略
  ├─ ignore_status == {"已忽略","已关闭"} 且 理由⊆准确 → 转交
  └─ 其他 → continue
  ▼
KnowledgeEvent → MainStartEvent（透传）
  │
  ▼
[4] judge_by_main() → 根据 event_type 选择 Prompt
  │
  │  prompt_dict 映射（9 种事件类型）：
  │    malic_opera              → PROCESS_PART_MALIC_OPERA
  │    backdoor_diagnose/win    → PROCESS_PART_BACKDOOR_DIAGNOSE
  │    bounce_shell/win          → PROCESS_PART_BOUNCE_SHELL
  │    web_command/win           → PROCESS_PART_WEB_COMMEND
  │    bruteforce_ext/inter      → PROCESS_PART_BRUTE_FORCE
  │    anti_virus_detect         → PROCESS_PART_VIRUS_DETECT
  │    privilege_escalation      → PROCESS_PART_PRIVILEGE_ESCALATION
  │    honeypot                  → PROCESS_PART_HONEYPOT
  │    webshell                  → PROCESS_PART_WEBSHELL
  │
  │  不在列表中 → HIDS_WITH_BACKGROUND_PROMPT_0415（通用兜底）
  │
  │  Prompt 组装：HEAD_PART + PROCESS_PART_* + TAIL_PART
  ▼
  judge_by_llm() → LLM 研判
  ▼
FinalEvent（含 evaluation_conclusion + disposal_conclusion）
  │
  ▼
[5] generate_alert_description()
  │  ├─ 资产归属定位 locate_asset_bu()
  │  ├─ 攻击详情生成 sub_workflow.run()
  │  └─ alert_action_merge() 合并（有一个忽略则忽略）
  ▼
[6] extract_follow_up()
  │  条件：alert_action=="转交" and execute_type==0
  │  ├─ [6a] 服务器隔离（HIDS 特有核心处置）
  │  │    ← _extract_servers_by_disposal_target()
  │  │    ← 按 disposal_target 提取 HOST
  │  │    ← HidsServerDispatcher (operateType=6)
  │  │    ← 只有全部成功才结束抑制状态
  │  ├─ [6b] follow_up 提取（不管转交与否都提取）
  │  ├─ [6c] 兜底分单（asset_bu_info）
  │  ├─ [6d] 攻击链提取（必然执行）
  │  └─ [6e] pa_code/bu_name 一级字段设置
  ▼
StopEvent(JudgeAnalysisRes)
  │  → model_dump() → 完整 JSON 输出
  │    alert_title, alert_action, alert_rationale,
  │    disposal_action, warning_flag, attack_detail, evaluation, disposal,
  │    follow_up, pa_code, bu_name, attack_chain,
  │    disposition_results, asset_bu_info
```

---

## 九、关键 Prompt 索引

### 碎片化 Prompt（组装模式）

| Prompt | 文件位置 | 用途 | 对应 event_type |
|--------|----------|------|-----------------|
| `HEAD_PART` | `hids_prompts.py:5` | 通用头部（角色+背景+字段+输出格式） | 所有 |
| `TAIL_PART` | `hids_prompts.py:5` | 通用尾部（综合推理+输出格式+限制+待研判告警） | 所有 |
| `PROCESS_PART_MALIC_OPERA` | `hids_prompts.py:5` | 可疑操作 | malic_opera |
| `PROCESS_PART_BACKDOOR_DIAGNOSE` | `hids_prompts.py:5` | 后门检测 | backdoor_diagnose, backdoor_diagnose_win |
| `PROCESS_PART_BOUNCE_SHELL` | `hids_prompts.py:5` | 反弹Shell | bounce_shell, win_bounce_shell |
| `PROCESS_PART_WEB_COMMEND` | `hids_prompts.py:5` | Web命令执行 | web_command, web_command_win |
| `PROCESS_PART_BRUTE_FORCE` | `hids_prompts.py:5` | 暴力破解 | bruteforce_ext, bruteforce_inter |
| `PROCESS_PART_VIRUS_DETECT` | `hids_prompts.py:5` | 病毒检测 | anti_virus_detect |
| `PROCESS_PART_PRIVILEGE_ESCALATION` | `hids_prompts.py:5` | 系统提权 | privilege_escalation |
| `PROCESS_PART_HONEYPOT` | `hids_prompts.py:5` | 踩蜜罐 | honeypot |
| `PROCESS_PART_WEBSHELL` | `hids_prompts.py:5` | WebShell | webshell |

### 通用兜底 Prompt

| Prompt | 文件位置 | 用途 |
|--------|----------|------|
| `HIDS_WITH_BACKGROUND_PROMPT_0415` | `hids_prompts.py:788` | 通用兜底（10 个过程，最新完整版） |

### 历史版本

| Prompt | 文件位置 | 用途 |
|--------|----------|------|
| `HIDS_WITH_BACKGROUND_PROMPT_0327` | `hids_prompts.py:9` | 初始版（8 个过程） |
| `HIDS_WITH_BACKGROUND_PROMPT_0410` | `hids_prompts.py:192` | v2（增加 webshell） |
| `HIDS_WITH_BACKGROUND_PROMPT_0411` | `hids_prompts.py:371` | v3（扩展后门检测） |
| `HIDS_WITH_BACKGROUND_PROMPT_0414` | `hids_prompts.py:571` | v4（扩展 web 命令执行） |

### 其他共享 Prompt

| Prompt | 文件位置 | 用途 |
|--------|----------|------|
| `ZEUS_DISPOSAL_TEMPLATE` | `disposal_template.py:10` | 处置模板推荐 |
| `FOLLOW_UP_EXTRACTION_PROMPT` | `follow_up_prompts.py:5` | 后续动作提取 |
| `ATTACK_CHAIN_GENERATION_PROMPT` | `attack_chain_prompts.py:5` | 攻击链报告生成 |
| `ALERT_DESCRIPTION_GENERATION_PROMPT` | `alert_description_prompt.py:8` | 攻击详情生成 |
| `ALERT_DESCRIPTION_MERGE_PROMPT` | `alert_description_prompt.py:128` | 攻击详情合并 |

---

## 附录：PROCESS_PART_* 碎片 Prompt 完整内容

### 附录 A：PROCESS_PART_MALIC_OPERA（可疑操作）

```
### 过程1： datatype:malic_opera 可疑操作告警
#### 1. 需要关注的字段
- detail_time：告警事件发生时间
- detail_src_ip：操作登录的来源IP（远程登录方式）
- detail_login_user：detail_src_ip 所使用的登录账号
- detail_uname：执行 detail_cmd 命令使用的用户名
- detail_cmd：实际执行的命令
- detail_hit_rule_names：命中的青藤云的规则名
- detail_rule：同 detail_hit_rule_names

#### 2. 研判逻辑
- detail_uname 为非 root 用户清理痕迹 → 忽略
- event_content 为从 pkg-proxy.yun.paic.com.cn:8080 下载安装软件 → 忽略
- event_content 为从 github 下载和提交代码 → 忽略
- detail_rule 是"Linux 远程下载并执行_a"且请求 *.paic.com.cn → 忽略
- 其他 → 结合 group_name 和 event_content 判断
```

### 附录 B：PROCESS_PART_BACKDOOR_DIAGNOSE（后门检测）

```
### 过程2： datatype:backdoor_diagnose & datatype:backdoor_diagnose_win 后门检测
#### 1. 需要关注的字段
- detail_uname：执行命令使用的用户名
- detail_comment：实际执行的命令
- detail_backdoor_type：青藤云判断的后门类型

#### 2. 研判逻辑
- event_level 为 info → 忽略
- detail_backdoor_type 为"映像劫持"且 event_level=alert → 忽略
- detail_backdoor_type 为"查看共享场景 net share"且 event_level=danger → 忽略
- detail_backdoor_type 为"可疑进程参数"且脚本含 *.paic.com.cn → 忽略
- detail_backdoor_type 为"可疑进程参数"且脚本内容是运维/健康检查 → 忽略
- 其他 → 结合 group_name 和 event_content 判断
```

### 附录 C：PROCESS_PART_BOUNCE_SHELL（反弹Shell）

```
### 过程3： datatype:bounce_shell & datatype:win_bounce_shell 反弹shell告警
#### 1. 需要关注的字段
- detail_uname：执行命令使用的用户名
- detail_process_tree：实际进程调用链
- detail_ppname：父进程名字
- detail_pcmd：父进程命令
- detail_pname：执行的进程名字
- detail_cmd：实际执行的命令

#### 2. 研判逻辑
- detail_cmd 中脚本路径为运维功能脚本或 miniconda3 的 jupyter lab → 忽略
- group_name 为"证券/总部/基础设施及信息安全组"且 detail_ppname="lizhu_exec" → 忽略
- group_name 为"平安科技/GBD..."且 detail_pname="jupyter-lab" → 忽略
- 其他 → 结合 group_name 和 event_content 判断
```

### 附录 D：PROCESS_PART_WEB_COMMEND（Web命令执行）

```
### 过程4： datatype:web_command 和 datatype:web_command_win web命令执行
#### 1. 需要关注的字段
- detail_hit_rule_name：命中青藤HIDS的内置规则名
- detail_process_name：执行web 命令的进程名
- detail_process_tree：进程链
- detail_ppname：父进程名字

#### 2. 研判逻辑
- detail_hit_rule_name="查看主机用户信息"且 event_content 含 /tmp/CVU_19_resource → 忽略
- event_content 含"net localgroup administrators /add ecsuser" → 忽略
- group_name=终端安全组且 "[web命令执行]tomcat执行系统命令_b" → 忽略
- event_content 执行 systeminfo → 忽略
- group_name 为"证券/总部/基础设施及信息安全组"且含 lizhu_agent/lizhu_exec → 忽略
- event_content 是添加 ada 用户 → 忽略
- detail_cmd 涉及 /usr/bin/chattr +a → 忽略
- nmap 扫描 -p 指定具体端口 → 忽略
- 其他 → 结合 group_name 和 event_content 判断
```

### 附录 E：PROCESS_PART_BRUTE_FORCE（暴力破解）

```
### 过程5： datatype:bruteforce_ext外网暴力破解 和 datatype:bruteforce_inter内网暴力破解
#### 1. 需要关注的字段
- event_type：告警类型
- detail_src_ip：发起爆破的源IP
- internal_ip：被爆破的目的IP

#### 2. 研判逻辑
- event_type 为"bruteforce_inter"（内网暴力破解）→ 基本都是运维工具或员工触发，**直接忽略**
- bruteforce_ext（外网暴力破解）→ 进入综合研判
```

### 附录 F：PROCESS_PART_VIRUS_DETECT（病毒检测）

```
### 过程6： datatype:anti_virus_detect 病毒检测
#### 1. 需要关注的字段
- detail_path：告警的病毒和木马的文件路径
- detail_rule：触发的告警规则

#### 2. 研判逻辑
- group_name="基础设施及信息安全组" → 内部病毒测试，忽略
- detail_path 含 IntelligenceAgentService/FileZilla/pbrc → 杀软误报，忽略
- 其他 → 结合 group_name 和 event_content 判断
```

### 附录 G：PROCESS_PART_PRIVILEGE_ESCALATION（系统提权）

```
### 过程7： datatype:privilege_escalation 系统提权告警
#### 1. 需要关注的字段
- detail_uname：提权前的用户名
- detail_pname：提权进程或提权手段
- init_priv：提权的目标权限

#### 2. 研判逻辑
- group_name="平安科技/GBD 数据基础工具部/GBD 平台运营组"，root/hadoop 通过 container-execu 提权 → 忽略
- group_name="健康险"，dluser 通过 sudo 提权到 root → 忽略
- 其他 → 结合 group_name 和 event_content 判断
```

### 附录 H：PROCESS_PART_HONEYPOT（踩蜜罐）

```
### 过程8： datatype:honeypot 踩蜜罐告警
#### 研判逻辑
- group_name="证券/总部/基础设施及信息安全组" → 安全正常行为，忽略
- event_content 来自 26.38.4.2 的蜜罐触发 → 忽略
- detail_process_name=SearchIndexer.exe → Windows 文件搜索引擎触发，忽略
```

### 附录 I：PROCESS_PART_WEBSHELL（WebShell）

```
### 过程10： datatype:webshell webshell告警
#### 研判逻辑
- group_name="证券/总部/基础设施及信息安全组" → 安全测试，忽略
- 青藤自研检测出的告警 → 基本都是误报，忽略
```

---

## 附录：HIDS_WITH_BACKGROUND_PROMPT_0415 完整内容（通用兜底）

这是目前最新的通用兜底 Prompt，包含 **10 个过程**的完整研判逻辑，作为事件类型不在 prompt_dict 中的兜底策略。

### 背景信息

```
- 青藤HIDS：安装在平安集团内部云服务器的主机安全监控软件
- 操作系统：Linux 各类发行版 + Windows 各类服务器发行版
- 三类环境：
  - 生产（prd）→ security_qthids 告警
  - 测试（stg）→ security_qthids-stg 告警
  - 开发（dev）→ security_qthids-stg 告警
- 数据中心机房：观澜(gl)、宝信(bx)、东莞(dg)、廊坊(lf)、光明(gm)、外高桥(wgq)、重庆两江(lj)
- 内部域名：*.paic.com.cn（互联网不可访问）
- 内部网段：10.0.0.0/8、30.0.0.0/8、172.16.0.0/16、192.168.0.0/16

角色：作为数据中心安全事件监控与研判分析专家，负责对青藤云HIDS平台产生的各种类型的安全事件告警进行深入分析和研判，提供客观、准确的结论。

分析方法和依据：需要结合平安的运维习惯和操作特征排除内部人员操作触发导致的告警事件。
```

### 字段说明

| 字段 | 含义 | 示例 |
|------|------|------|
| host_name | 主机名 | stsz031429 |
| group_name | 所属组织架构 | 云桌面分组 |
| internal_ip | 告警机器IP | 192.167.3.109 |
| event_type | 事件类型 | web_command |
| event_level | 告警等级 | info/alert/danger |
| event_content | 事件完整描述 | java进程发现异常执行行为... |
| detail_time | 告警时间 | 1742903321 |
| detail_src_ip | 来源IP | 1、主机远程登录方式 |
| detail_login_user | 登录账号 | escuser |
| detail_uname | 执行命令用户名 | escuser |
| detail_cmd | 实际执行的命令 | net user /domain wanghaob33 |
| detail_hit_rule_names | 命中规则名 | [web命令执行]spring执行系统命令 |
| detail_backdoor_type | 后门类型 | 枚举域内操作 |
| detail_process_tree | 进程调用链 | systemd(1)->java(3752814)->chattr(1243278) |
| detail_pname | 进程名字 | 279120ecf1f623d |
| detail_path | 病毒文件路径 | /app/secker_agent/... |
| detail_puname | 父进程调用者 | root |
| detail_ppname | 父进程名字 | cmd.exe |
| detail_ppath | 父进程路径 | C:\Windows\System32\cmd.exe |
| detail_pcmd | 父进程命令 | cmd.exe |
| init_priv | 提权目标权限 | 1243278 |

### 工作流程

```
* Step 1：获取 datatype 告警类型，依据类型执行对应的子过程研判
* Step 2：结合子过程的研判结果，按照综合推理过程进行最终结果输出
```

### 10 个过程详细研判逻辑

（与 §四.5 相同，此处不再重复，详见各过程描述）

### 综合推理

- **行为分析**：分析进程链中所有进程及对应命令行，观察是否包含攻击入侵相关的恶意命令参数
- **恶意可能性评估**：根据进程链中每个进程完整路径和完整命令行，逐个识别是否存在攻击工具特征
- **核心推导**：一旦进程链或参数出现攻防相关软件或工具 → 判断为恶意，转交
- **判定原则**：
  - 进程来源未知 + 程序用户权限可控 + 存在攻击特征行为 → 转交
  - 即使有疑似测试关键字，如果是攻击工具 → 转交
  - 进程或命令行中涉及目录为用户可读写时视为危险

### 输出格式

```json
{
    "action": "转交" | "忽略",
    "rationale": "执行了过程x，核心推导依据是：xxx"
}
```
