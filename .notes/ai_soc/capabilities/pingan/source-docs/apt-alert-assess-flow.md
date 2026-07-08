# APT 研判流程详细文档

> 核心文件：`my_workflows/zeus/flows/apt_alert_assess.py`
> 引擎：LlamaIndex `Workflow` + `@step` 异步方法
> 大模型：平安内部 LLM（OpenAI compatible 接口）

---

## 一、整体架构

APT 研判是一个**基于攻击类型的 Prompt 分流 + 漏斗决策**的两阶段流水线：

```
第一阶段：研判决策（忽略 / 转交）
┌──────────────────────────────────────────────────────────────────┐
│ StartEvent                                                       │
│   │ 原始告警数据 (AptRoot)                                        │
│   ▼                                                              │
│ [1] 数据预处理 → JudgeDataEvent    提取ID、日志、类型、关联预警    │
│   │                                                              │
│ [2] 关联预警历史判断              ← 能快速判定的立刻返回           │
│   ├─ 能判断 → FinalEvent（直接结束）                              │
│   └─ 不能判断                                                      │
│       │                                                          │
│ [3] 知识库研判 → MainStartEvent    （预留扩展，当前透传）          │
│   │                                                              │
│ [4] 主流程 LLM 研判                                               │
│   │  根据 attack_types[0] 选择对应 Prompt 进入 LLM 研判            │
│   │  → 23 种攻击类型 × 23 种专属 Prompt（告警/预警两种模式）       │
│   │                                                              │
│   ▼ 产出 FinalEvent ← 研判结论：忽略 或 转交                      │
└──────────────────────────────────────────────────────────────────┘
                             │
                             ▼
第二阶段：处置闭环（转交后执行）
┌──────────────────────────────────────────────────────────────────┐
│ [5] 生成攻击详情 + 资产定位 + 研判合并                             │
│   ├─ 5a. 资产归属定位（AssetExtractor → search_asset_info → 工作流）│
│   ├─ 5b. 攻击详情生成子工作流                                      │
│   └─ 5c. alert_action_merge 研判合并                              │
│   │                                                              │
│ [6] IP 隔离处置                                                   │
│   ├─ 规则封堵（RPAADM_002267, RPAADM_000558 直接封堵）             │
│   ├─ 渗透测试名单检查（可能改为关闭）                               │
│   ├─ attacker IP 风险评分（ZEUS 威胁情报）                         │
│   └─ IP 封堵（R≥85→30天, 75-85→7天, 65-75→24h）                   │
│   │                                                              │
│ [7] 后续动作提取                                                  │
│   ├─ FollowUp 提取（pa_code + bu_name）                           │
│   ├─ 兜底分单（asset_bu_info）                                    │
│   ├─ attack_chain 提取（必然执行）                                │
│   └─ pa_code/bu_name 一级字段设置                                 │
│   │                                                              │
│   ▼ StopEvent → JudgeAnalysisRes（完整研判结果）                   │
└──────────────────────────────────────────────────────────────────┘
```

**与 EDR 的核心差异**：

| 维度 | EDR | APT |
|------|-----|-----|
| 告警来源 | 联软 EDR 终端安全 | 奇安信 APT 流量检测 |
| Prompt 策略 | 通用 Prompt + 路径分流 | 23 种攻击类型 × 23 种专属 Prompt |
| 处置手段 | UM 封禁 + IP 隔离 | IP 封堵（基于风险评分） |
| 攻击详情 | 独立子工作流 | 独立子工作流（共享） |
| 资产定位 | 共享 | 共享 |

---

## 二、模型配置

| 参数 | 值 |
|------|------|
| 默认模型 | `Deepseek_Qwen_32B_My` |
| LLM 客户端 | `OpenAI(base_url=PINGANGPT_OPENAI_SERVER, api_key=APP_KEY_DICT["sec_model_online"])` |
| 模型参数（预警） | `top_p=0.5, temperature=0.85, frequency_penalty=0.2` |
| 模型参数（告警） | `top_p=0.5`（覆盖） |
| 调用超时 | 240s（主流程 LLM）、7min（攻击详情子工作流） |
| need_trans | `False` |

---

## 三、支持的攻击类型

### 3.1 完整攻击类型列表

```
{'APT事件', 'SQL注入', 'URL跳转', 'webshell上传', 'webshell利用',
 '代理工具', '代码执行', '信息泄露', '其他', '命令执行', '弱口令',
 '挖矿病毒', '敏感信息/重要文件泄漏', '文件上传', '文件下载',
 '文件读取', '暴力猜解', '权限许可和访问控制', '目录遍历',
 '系统/服务配置不当', '跨站脚本攻击（XSS）', '配置不当/错误',
 '非授权访问/权限绕过', '黑市工具', '默认配置不当'}
```

### 3.2 告警 vs 预警 Prompt 模式

代码中定义了两套 Prompt 字典，根据 `execute_type == 3`（告警）还是预警（其他值）选择不同模式：

```python
if execute_type == 3:  # 告警模式
    # 使用 APT_MIDDLE_SPECIFIC_*_SCENARIO_EVALUATION_STEP + 组装模式
    prompt_dict = {
        "弱口令": APT_MIDDLE_SPECIFIC_WEAK_PASSWORD_SCENARIO_EVALUATION_STEP,
        "命令执行": APT_MIDDLE_SPECIFIC_COMMAND_EXE_SCENARIO_EVALUATION_STEP,
        "文件读取": APT_MIDDLE_SPECIFIC_FILE_READ_SCENARIO_EVALUATION_STEP,
        "目录遍历": APT_MIDDLE_SPECIFIC_DIR_TRAVERSE_SCENARIO_EVALUATION_STEP,
        "非授权访问/权限绕过": APT_MIDDLE_SPECIFIC_NOT_CERTIFY_SCENARIO_EVALUATION_STEP,
        "SQL注入": APT_MIDDLE_SPECIFIC_SQL_INJECT_SCENARIO_EVALUATION_STEP,
        "黑市工具": APT_MIDDLE_SPECIFIC_HACKER_TOOL_SCENARIO_EVALUATION_STEP,
        "代理工具": APT_MIDDLE_SPECIFIC_PROXY_TOOL_SCENARIO_EVALUATION_STEP,
        "webshell上传": APT_MIDDLE_SPECIFIC_WEBSHELL_UPLOAD_SCENARIO_EVALUATION_STEP,
        "webshell利用": APT_MIDDLE_SPECIFIC_WEBSHELL_UTIL_SCENARIO_EVALUATION_STEP,
        "文件上传": APT_MIDDLE_SPECIFIC_FILE_UPLOAD_SCENARIO_EVALUATION_STEP,
        "文件下载": APT_MIDDLE_SPECIFIC_FILE_DOWNLOAD_SCENARIO_EVALUATION_STEP,
        "代码执行": APT_MIDDLE_SPECIFIC_CODE_EXE_SCENARIO_EVALUATION_STEP,
        "敏感信息/重要文件泄漏": APT_MIDDLE_SPECIFIC_SENSE_FILE_SCENARIO_EVALUATION_STEP,
        "跨站脚本攻击（XSS）": APT_MIDDLE_SPECIFIC_SHELL_XSS_SCENARIO_EVALUATION_STEP,
        "信息泄露": APT_MIDDLE_SPECIFIC_MES_LEAK_SCENARIO_EVALUATION_STEP,
        "默认配置不当": APT_MIDDLE_SPECIFIC_SYS_SERVICE_SCENARIO_EVALUATION_STEP,
        "挖矿病毒": APT_MIDDLE_SPECIFIC_MINING_VIRUS_SCENARIO_EVALUATION_STEP,
        "后门程序": APT_MIDDLE_SPECIFIC_BACKEND_PROGRAMING_SCENARIO_EVALUATION_STEP,
        "暴力猜解": APT_MIDDLE_SPECIFIC_BRUTE_FORCE_SCENARIO_EVALUATION_STEP,
        "权限许可和访问控制": APT_MIDDLE_SPECIFIC_CERTIFY_CONTROL_SCENARIO_EVALUATION_STEP,
        "系统/服务配置不当": APT_MIDDLE_SPECIFIC_SYS_SERVICE_SCENARIO_EVALUATION_STEP,
        "配置不当/错误": APT_MIDDLE_SPECIFIC_SYS_SERVICE_SCENARIO_EVALUATION_STEP,
        "其他": APT_MIDDLE_COMMON_SCENARIO_EVALUATION_STEP,
        "APT事件": APT_MIDDLE_SPECIFIC_EVENT_TCP_SCENARIO_EVALUATION_STEP,
    }
else:  # 预警模式
    # 使用独立的完整 Prompt（旧版模式）
    prompt_dict = {
        "弱口令": APT_WEAK_PASSWORD_PROMPT,
        "命令执行": APT_COMMAND_EXE_PROMPT,
        "文件读取": APT_FILE_READ_PROMPT,
        "目录遍历": APT_DIR_TRAVERSE_PROMPT,
        "非授权访问/权限绕过": APT_NOT_CERTIFY_PROMPT,
        "SQL注入": APT_SQL_INJECT_PROMPT,
        "黑市工具": APT_HACKER_TOOL_PROMPT,
        "代理工具": APT_PROXY_TOOL_PROMPT,
        "webshell上传": APT_WEBSHELL_UPLOAD_PROMPT,
        "webshell利用": APT_WEBSHELL_UTIL_PROMPT,
        "文件上传": APT_FILE_UPLOAD_PROMPT,
        "文件下载": APT_FILE_DOWNLOAD_PROMPT,
        "代码执行": APT_CODE_EXE_PROMPT,
        "敏感信息/重要文件泄漏": APT_SENSE_FILE_PROMPT,
        "跨站脚本攻击（XSS）": APT_SHELL_XSS_PROMPT,
        "信息泄露": APT_MES_LEAK_PROMPT,
        "默认配置不当": APT_SYS_SERVICE_PROMPT,
        "挖矿病毒": APT_MINING_VIRUS_PROMPT,
        "后门程序": APT_BACKEND_PROGRAMING_PROMPT,
        "暴力猜解": APT_BRUTE_FORCE_PROMPT,
        "权限许可和访问控制": APT_CERTIFY_CONTROL_PROMPT,
        "系统/服务配置不当": APT_SYS_SERVICE_PROMPT,
        "配置不当/错误": APT_SYS_SERVICE_PROMPT,
        "其他": APT_WITH_BACKGROUND_PROMPT,
        "APT事件": APT_EVENT_TCP_PROMPT,
    }
```

**组装模式**（告警模式）：
每个 Prompt = `APT_WARNING_HEAD` + `APT_MIDDLE_COMMON_SCENARIO_EVALUATION_START_STEP` + `[场景专属步骤]` + `APT_MIDDLE_COMMON_SCENARIO_EVALUATION_END_STEP` + `TAIL`

---

## 四、研判决策阶段（第一阶段）

### 4.1 步骤 1：数据预处理

**代码位置**：`AptAlertWorkflow.prepare_msgs()` → `my_workflows/zeus/flows/apt_alert_assess.py:191`

**职责**：从原始 hit log 中解出研判所需的全部必要数据。

**处理步骤**：

| 步骤 | 操作 | 产出 |
|------|------|------|
| 1 | 提取 `alert_id` | alert_id |
| 2 | 获取关联预警情况 | `related_status_count` |
| 3 | 获取研判入模数据 | `flow_datas`：从 hitLog 中提取 zeusRawLogs，截断 rsp_body/rsp_header/req_body/req_header（各±1000字符） |
| 4 | 提取 3 级预警类型 ID | `alter_full_type_id` |
| 5 | 提取攻击类型 | `attack_types`：从第一个 hit_log 的 zeusRawLogs[0].attack_type |
| 6 | 提取执行类型 | `execute_type`：=3 为告警，其他为预警 |

**日志截断**（`get_alert_main_data`）：

```python
# 如果字段长度 > 2000，截断为[:1000] + 末尾1000
cut_alert_log["rsp_body"] = cut_alert_log["rsp_body"][:1000] + cut_alert_log["rsp_body"][-1000:]
cut_alert_log["rsp_header"] = cut_alert_log["rsp_header"][:1000] + cut_alert_log["rsp_header"][-1000:]
cut_alert_log["req_body"] = cut_alert_log["req_body"][:1000] + cut_alert_log["req_body"][-1000:]
cut_alert_log["req_header"] = cut_alert_log["req_header"][:1000] + cut_alert_log["req_header"][-1000:]
# 然后调用 truncate_strings() 对所有字符串字段做截断
```

**产出**：`JudgeDataEvent` 包含 `alert_id`, `related_status_count`, `flow_datas`, `alter_full_type_id`, `execute_type`, `attack_types`, `evaluation_trace`

---

### 4.2 步骤 2：关联预警历史判断

**代码位置**：`AptAlertWorkflow.judge_by_related_alert()` → `my_workflows/zeus/flows/apt_alert_assess.py:235`
**核心逻辑**：`judge_by_related_data_adjustment()` → `my_workflows/zeus/utils/util_tools.py:184`

**职责**：基于历史关联预警的处置结论，尝试直接得出结论，避免不必要的 LLM 调用。

**前置处理**（`get_related_alerts_dict`）：

1. 筛选**半年内**、**同三级类型**的关联预警
2. 按时间升序排序
3. 统计状态分布（与 EDR 相同）
4. 状态合并：将所有进行中的状态合并到"已关闭"

**判定逻辑**（与 EDR 完全相同）：

```
if ignore_status == {"已关闭"}:
    → 转交 + 调用处置推荐
elif ignore_status == {"已忽略"}:
    if 忽略数 >= 4:  ← 注意：APT 的 default_ignore_parameter=4（预警时）
        → 忽略
    elif 理由⊆误报:
        → 忽略
    else:
        → continue
elif ignore_status == {"已忽略", "已关闭"}:
    if 忽略数 > 5 且 最近2次均为忽略:  ← APT: default_ignore_parameter+1=5
        → 忽略
    elif 理由⊆准确:
        → 转交 + 调用处置推荐
    else:
        → continue
else:
    → continue
```

**注意**：APT 场景的 `default_ignore_parameter=4`（告警=3，预警=4），比 EDR 的 3 略高。

**产出**：能判断 → `FinalEvent`；不能判断 → `KnowledgeEvent`（带 `related_reason_descriptions`）

---

### 4.3 步骤 3：知识库研判（预留）

**代码位置**：`AptAlertWorkflow.judge_by_knowledge()` → `my_workflows/zeus/flows/apt_alert_assess.py:285`

**当前状态**：未实现 LLM 调用，直接透传数据到 `MainStartEvent`，标记为预留扩展节点。

---

### 4.4 步骤 4：主流程 LLM 研判

**代码位置**：`AptAlertWorkflow.judge_by_main()` → `my_workflows/zeus/flows/apt_alert_assess.py:304`

**职责**：根据 `attack_types[0]` 选择对应的专属 Prompt，调用 LLM 进行研判。这是 APT 场景的核心研判节点。

**Prompt 选择映射**（与 §三.2 相同，此处不再赘述）。

**核心调用**：`judge_by_llm()` → `my_workflows/zeus/utils/util_tools.py:316`（与 EDR 共享）

**Prompt 通用结构**（每个攻击类型的 Prompt 都包含）：

```
## 角色：平安集团 APT `{攻击类型}`攻击告警分析专家
## 背景：
  1. 奇安信 APT 系统（通用背景）
  2. 安全环境（安全流量特征 / 安全行为 / 标识字段）
  3. 攻击时间（7×24，晚10点至早6点扫描多）
  4. 告警特征
## 字段描述：（完整字段表）
## 历史关联预警处理结论：`{{related_history}}`
## 输出格式：JSON {action, summary, rationale[]}
## 分析步骤：（每个攻击类型不同）
## 待研判告警：`{{alert_event}}`
```

**输出格式**：

```json
{
  "action": "转交" | "忽略",
  "summary": "核心总结，一句话概括。严格控制在30字以内",
  "rationale": [
    {"key": "风险点/研判维度名称", "value": "该维度的具体描述或理由，严格控制在30字以内"}
  ]
}
```

**处置动作判断**：

| action | 说明 |
|--------|------|
| "转交" | 存在风险：攻击成功/恶意特征/恶意代码/敏感信息泄露/异常行为 |
| "忽略" | 无风险：攻击未成功/无恶意特征/正常业务/无异常行为 |

**LLM 调用流程**（与 EDR 相同，见 §五.3）

---

### 4.5 各攻击类型 Prompt 差异要点

以下列出各攻击类型 Prompt 中的**关键研判步骤**差异（告警模式 `APT_MIDDLE_*` + 预警模式 `APT_*`）：

| 攻击类型 | 关键研判特征 | 特殊忽略条件 |
|----------|-------------|-------------|
| **APT事件** | 提取 `packet_data` → 检查 `CC1AFAEBF380D0` 特征 | sip 非内网 / sip==dip |
| **弱口令** | 明文传输(login/pwd) / rsp_status 200+OK/Success/Token / agent 含 python/spider | host_state=失败 / attack_result=0 / 3XX/4XX/5XX |
| **命令执行** | cmd=... / command=... / eval/exec/system / rsp_body 含 localhost/root/C:\Windows | URI 含 GPT / health 路径 / iobs 主机 |
| **文件读取** | path=xxx / 路径穿越 `../` / 目标 /etc/passwd 等敏感文件 | URI 含 GPT / iobs 主机 |
| **目录遍历** | `../` / `%2e%2e%2f` / 读取敏感文件 | URI 含 GPT / iobs 主机 |
| **SQL注入** | UNION/SELECT/DROP / `1' OR '1'='1` / SLEEP(5) / 数据库错误信息 | URI 含 GPT / health 路径 |
| **非授权访问/权限绕过** | Spring Actuator(`/actuator`) / Swagger(`swagger-ui.html`) / Elasticsearch(`/ _cat`) / Kibana / Hadoop | URI 含 GPT / iobs 主机 |
| **黑市工具** | Nmap Agent / sqlmap / Metasploit / BurpSuite / XFF 异常 / Cookie 异常 / 注入 payload | - |
| **代理工具** | 提取 `packet_data` → 检查 Frp 特征(version/os/arch/privilege_key) | sip 非内网 / sip==dip |
| **webshell上传** | URI/rsp_body 含 `.asp/.jsp/.jspx/.php/.aspx/.asa` | iobs.pingan.com.cn / iobs-sf-super |
| **webshell利用** | req_body 含 `eval/assert/base64_decode/@ini_set/_0x*=...` / rsp_body 含 `.asp/.jsp/.php/password` | iobs.pingan.com.cn / iobs-sf-super |
| **文件上传** | UploadFileData / upload.php / add_images / method=POST/PUT | host_state=失败 / attack_result=0 |
| **文件下载** | 恶意文件下载特征 / rsp_status 200 + OK/Success/Token | host_state=失败 / uri含 yum/Latest/scripts/ |
| **代码执行** | cmd=... / command=... / eval/exec/system / rsp_body 含系统信息 | URI 含 GPT / iobs 主机 |
| **敏感信息/重要文件泄漏** | 路径穿越 / file=xxx / 目标敏感文件 / rsp_body 含文件内容 | URI 含 GPT / iobs 主机 |
| **跨站脚本攻击(XSS)** | script/img/src/onerror/prompt/alert / rsp_body 含成功标志 | - |
| **信息泄露** | 敏感路径(`.svn/nacos/swagger/api-docs`) / 路径穿越 / rsp_body 含敏感文件 | URI 含 GPT / iobs 主机 |
| **系统/服务配置不当** | rsp_body 含 `Index of /` | URI 含 GPT / iobs 主机 |
| **挖矿病毒** | proto=dns + repeat_count>5 | host_state=失败 / sip 非内网 |
| **后门程序** | req_header 含 `Cobalt Strike Beacon` / req_body 含 PHP eval / rsp_status=200 | host_state=失败 / method 空且 rsp_body_len 空 |
| **暴力猜解** | 明文传输 / rsp_status 200+OK/Success / agent python/spider | host_state=失败 / uri含 p=login |
| **权限许可和访问控制** | 敏感路径 `/restapi/sasinfo/` `/omm/v2/http/mop/` / rsp_body 含成功+敏感文件 | URI 含 GPT / iobs 主机 |
| **其他** | req_body/parameter/uri 含恶意风险 / rsp_body 含敏感内容 | 90% 告警需要忽略 / 信息缺失多 |

---

### 4.6 研判阶段总结

```
数据预处理 → 关联预警判断(能直接判就返回) → 知识库(透传) → 主流程 LLM 研判
    │
    │  根据 attack_types[0] 选择 23 种专属 Prompt 之一
    │  告警模式: APT_WARNING_HEAD + 通用步骤 + 场景专属步骤 + 结束 + TAIL
    │  预警模式: 独立完整 Prompt
    │
    ▼
FinalEvent ← 研判结论: 忽略 或 转交
含: evaluation_conclusion + disposal_conclusion + alert_type
```

---

## 五、处置闭环阶段（第二阶段）

### 5.1 步骤 5a：资产归属定位

**代码位置**：`AptAlertWorkflow.generate_alert_description()` → `my_workflows/zeus/flows/apt_alert_assess.py:447`
**核心函数**：`locate_asset_bu()` → `my_workflows/zeus/flows/disposition_tools/asset_locator.py:221`

**职责**：与 EDR 共享，三层降级策略（AssetExtractor → search_asset_info → asset_to_bu → ums）。

**产出**：`AssetBuInfo`（与 EDR 相同，见 §七.2）

---

### 5.2 步骤 5b：攻击详情生成

**代码位置**：`AptAlertWorkflow.generate_alert_description()` → `my_workflows/zeus/flows/apt_alert_assess.py:460`
**核心类**：`AlertDescriptionWorkflow` → `my_workflows/zeus/flows/alert_description_generation.py`

**职责**：日志去重 → 切割(≤12000 tokens) → LLM 逐条生成 → 合并。
**产出**：`{action, merged_result, summary}`

---

### 5.3 步骤 5c：研判结果合并

**代码位置**：`alert_action_merge()` → `my_workflows/zeus/process/alert_helper.py:12`

**合并逻辑**（与 EDR 相同）：

| evaluation_action | alert_detail_action | 最终 alert_action |
|-------------------|---------------------|-------------------|
| 转交 | 转交 | **转交** |
| 忽略 | 忽略 | **忽略** |
| 转交 | 忽略 | **忽略** |
| 忽略 | 转交 | **忽略** |

**特殊处理**：APT 场景中，仅"弱口令"和"非授权访问/权限绕过"告警时可以转生产预警，其他告警类型设置 `warning_flag = 0`（不转生产预警）。

```python
if judge_analysis_res.alert_type not in ["弱口令", "非授权访问/权限绕过"] and execute_type == 3:
    judge_analysis_res.warning_flag = 0
```

---

### 5.4 步骤 6：IP 隔离处置

**代码位置**：`AptAlertWorkflow.ip_isolation_disposal()` → `my_workflows/zeus/flows/apt_alert_assess.py:477`

**触发条件**：`alert_action == "转交"`

**处置流程**：

```
[1] 规则封堵检查
    │  特定规则码 RPAADM_002267, RPAADM_000558 → 直接封堵预警
    │    → alert_action 改为"转交" + 追加理由
    │
    ▼
[2] 提取 attacker IP
    │  从 asset_bu_info.role_result.role_assignments["attacker"] 提取
    │  仅取 type=="IP" 的值
    ▼
[3] 渗透测试名单检查
    │  BlackWhiteTagClient.search_content(keywords=attacker_ips, label="渗透测试")
    │  if has_active == True:
    │      → 将 alert_action 改为"关闭"
    ▼
[4] IP 风险评分（ZEUS 威胁情报）
    │  ZeusClient.query_ip_intelligence(first_ip)
    │  → 获取 ipAnalyseReport + ipReputationReport
    │  → IPScorer.score_with_info(reputation_data, analysis_data, ip)
    ▼
[5] IP 封堵执行
    │  IPScorer 评分模型：
    │    Final_Score = min(100, Base_Score × Geo_Weight × Time_Decay)
    │
    │  基础分：取威胁标签最高分（C2=100, 僵尸网络=90, 钓鱼=80, 代理=20 等）
    │  地理权重：境外 ×1.5, 境内 ×1.0
    │  时效衰减：
    │    境内：7天→1.0, 30天→0.7, 90天→0.3, 180天→0.1
    │    境外：7天→1.0, 30天→0.85, 90天→0.6, 180天→0.5
    │  境内白名单强制归零：白名单/CDN/移动基站
    │
    │  封堵策略（risk_score ≥ 65 才执行）：
    │    R ≥ 85 → 30天/永久封堵 (strategyId=118)
    │    75 ≤ R < 85 → 7天 (strategyId=119)
    │    65 ≤ R < 75 → 24小时 (strategyId=120)
    ▼
    请求体格式：
    {
        "name": "攻击IP封堵",
        "status": 0,
        "operateType": 0,
        "invokeParam": {
            "strategyIdList": [策略ID],
            "ipInfoList": [{"domainIp": "IP", "reason": "恶意IP，{alertCode}，{alertName}"}]
        },
        "followUpUms": []
    }
    ▼
    执行 execute_and_end_contain()
    → 全部成功才结束抑制状态
```

**IP 提取方法**（`_extract_ips_by_disposal_target`）：

```python
disposal_target = judge_analysis_res.asset_bu_info.disposal_target
# 严格按 disposal_target 驱动，不 fallback
if disposal_target in ("-", ""):
    → 不隔离，返回空列表
else:
    → 从 role_result.role_assignments[disposal_target] 中提取 type=="IP" 的值
```

---

### 5.5 步骤 7a：后续动作提取（FollowUpExtractor）

**代码位置**：`AptAlertWorkflow.auto_disposal_step()` → `my_workflows/zeus/flows/apt_alert_assess.py:638`
**核心类**：`FollowUpExtractor` → `my_workflows/zeus/utils/follow_up_extractor.py:88`

**触发条件**：所有转交告警都会提取，但最终 `follow_up` 字段仅在 `alert_action == "转交"` 时设置。

**业务限制**（APT 特有）：

```python
# 不是 弱口令 非授权访问/权限绕过 文件读取 系统/服务配置不当 不要后续转BU了
if judge_analysis_res.alert_type in ["弱口令", "非授权访问/权限绕过", "文件读取", "系统/服务配置不当", ""] and follow_up and follow_up.action_code != 0:
    follow_up.if_ignore = 0  # 保持转BU
```

**兜底逻辑**：如果 FollowUp 提取失败，使用 `asset_bu_info` 兜底分单。

---

### 5.6 步骤 7b：攻击链提取

**代码位置**：`AptAlertWorkflow.auto_disposal_step()` → `my_workflows/zeus/flows/apt_alert_assess.py:674`
**核心类**：`AttackChainExtractor` → `my_workflows/zeus/utils/attack_chain_extractor.py:19`

**触发条件**：**必然执行**，与转交/忽略无关。

**输入数据**：原始日志 + 关联告警日志（`include_related_alerts=True`）
**输出**：Markdown 格式攻击链报告（与 EDR 相同，见 §四.8）

---

### 5.7 处置闭环阶段总结

```
FinalEvent ← 研判结论(忽略/转交)
    │
    ▼ generate_alert_description（子流程合并）
    │  ├─ 资产归属定位（AssetExtractor → search_asset_info → 工作流 → ums）
    │  ├─ 攻击详情生成（子工作流）
    │  └─ alert_action_merge 合并（有一个忽略则忽略）
    │      + APT 特有：仅弱口令/非授权访问/权限绕过 可转生产预警
    │
    ▼ ip_isolation_disposal（IP 隔离处置）
    │  ├─ 规则封堵（RPAADM_002267, RPAADM_000558）
    │  ├─ 渗透测试名单检查 ← 可能改为关闭
    │  ├─ attacker IP 风险评分（ZEUS 威胁情报）
    │  └─ IP 封堵（R≥85→30天, 75-85→7天, 65-75→24h）
    │
    ▼ auto_disposal_step（后续动作提取）
    │  ├─ FollowUp 提取（pa_code + bu_name）← 转交才设置
    │  │    + APT 特有：仅弱口令/非授权访问/文件读取/系统配置不当 才转BU
    │  ├─ 兜底分单（asset_bu_info）
    │  ├─ 攻击链提取 ← 必然执行
    │  └─ pa_code/bu_name 一级字段设置
    │
    ▼ StopEvent → JudgeAnalysisRes（完整结果）
```

---

## 六、核心工具函数详解

### 六.1 关联预警处理（`get_related_alerts_dict`）

**代码位置**：`my_workflows/zeus/utils/util_tools.py:85`

**与 EDR 完全相同**，见 §五.1。

---

### 六.2 关联预警判定微调（`judge_by_related_data_adjustment`）

**代码位置**：`my_workflows/zeus/utils/util_tools.py:184`

**与 EDR 相同**，差异点：
- APT 的 `default_ignore_parameter=4`（告警=3，预警=4）
- APT 的混合情况判断阈值：`忽略数 > default_ignore_parameter + 1 = 5`

---

### 六.3 LLM 研判调用（`judge_by_llm`）

**代码位置**：`my_workflows/zeus/utils/util_tools.py:316`

**与 EDR 完全相同**，见 §五.3。

---

### 六.4 处置模板推荐（`disposal_processing` / `disposal_recommend`）

**代码位置**：`my_workflows/zeus/utils/util_tools.py:361` / `:389`

**与 EDR 完全相同**，见 §五.4。

---

## 七、核心抑制手段详解

### 七.1 IP 风险评分系统（`IPScorer`）

**代码位置**：`my_workflows/zeus/flows/disposition_tools/ip_risk_scorer_zeus.py:422`

**评分模型**：

```
Final_Score = min(100, Base_Score × Geo_Weight × Time_Decay)
```

**基础威胁标签权重**：

| 分值 | 标签 |
|------|------|
| 100 | C2, 远控, Sinkhole C2, 安全机构接管 C2, C2 Panel, 远控管理面板, 木马家族, Trojan Family |
| 90 | Botnet, 僵尸网络, Hijacked, 劫持, Malware, 恶意软件, Zombie, 傀儡机, MiningPool, 公共矿池, CoinMiner, 私有矿池 |
| 80 | Phishing, 钓鱼, Fake Website, 仿冒网站, Exploit, 漏洞利用, Scanner, 扫描, 恶意扫描, Spam, 垃圾邮件, Brute Force, 暴力破解, SSH/FTP/SMTP/HTTP Brute Force, 撞库, 攻击源, Attack |
| 50 | Compromised, 失陷主机 |
| 30 | Reverse Proxy, 反向代理, Fake Software Downloader, 仿冒软件下载站, Commercial Rat, 商用远程控制工具 |
| 20 | Suspicious, 可疑, Suspicious Application, 潜在有害应用程序, Suspicious Website, 潜在有害站点, 协议代理, Proxy, VPN, Tor |

**官方标签分类权重**：

| 分值 | 标签 |
|------|------|
| 100 | 威胁行为者, ThreatActor, APT组织, APT |
| 90 | 犯罪团伙, Gang, 僵木蠕, Worm, 病毒, Virus |

**境内白名单强制归零**：

| 类型 | 标签 |
|------|------|
| 标签 | 白名单, Whitelist, CDN服务器, CDN, 移动基站, Mobile |
| 场景 | CDN, Mobile Network, 移动网络, Company, 企业专线 |

**地理权重**：境外 ×1.5，境内 ×1.0

**时效衰减**：

| 时间 | 境内 | 境外 |
|------|------|------|
| ≤7天 | 1.0 | 1.0 |
| ≤30天 | 0.7 | 0.85 |
| ≤90天 | 0.3 | 0.6 |
| ≤180天 | 0.1 | 0.5 |
| >180天 | 0.1 | 0.1 |

**分析实验室加成**：Analysis 数据源 ×1.2

---

### 七.2 IP 封堵动作（`IpIsolationDispatcher`）

**代码位置**：`my_workflows/zeus/flows/disposition_tools/ip_isolation_disposition_tool.py:140`

**封堵策略 ID**：

| 环境 | R≥85 | 75≤R<85 | 65≤R<75 |
|------|------|---------|---------|
| LOCAL/STG | 118 | 119 | 120 |
| PRD | 16 | 15 | 14 |

**请求体格式**：

```json
{
    "name": "攻击IP封堵",
    "status": 0,
    "operateType": 0,
    "invokeParam": {
        "strategyIdList": [策略ID],
        "ipInfoList": [
            {"domainIp": "1.1.1.1", "reason": "恶意IP ，{alertCode}，{alertName}"}
        ]
    },
    "followUpUms": []
}
```

---

### 七.3 黑白名单查询（`BlackWhiteTagClient`）

**代码位置**：`my_workflows/zeus/flows/disposition_tools/black_white_tag_client.py:23`

**接口**：`POST /public/searchTagContent`
**默认 label**：`"渗透测试"`

**查询结果**：

| 字段 | 含义 |
|------|------|
| `found` | 是否查到记录 |
| `is_valid` | 至少有一条有效记录 |
| `has_active` | 有查到且有效（最终判断） |
| `summary` | "未查到" / "查到X条且有效" / "查到X条但已过期" |

---

### 七.4 ZEUS API 客户端（`ZeusClient`）

**代码位置**：`my_workflows/zeus/flows/disposition_tools/ip_risk_scorer_zeus.py:243`

**接口**：`POST /public/indicatorSearch`
**返回结构**：

```json
{
    "data": {
        "intelligenceInformation": {
            "ipAnalyseReport": {"data": {"<IP>": {...}}},
            "ipReputationReport": {"data": {"<IP>": {...}}}
        }
    }
}
```

---

## 八、关键数据模型

### JudgeAnalysisRes

| 字段 | 类型 | 说明 |
|------|------|------|
| `alert_type` | Optional[str] | 告警类型标记 |
| `warning_flag` | Optional[int] | 0=忽略/不转生产，1=转交/转生产 |
| `evaluation_conclusion` | Optional[Dict] | 研判结论：`{action, rationale, summary, trace}` |
| `disposal_conclusion` | Optional[Dict] | 处置结论：`{action, rationale, trace}` |
| `alert_description` | Optional[Dict] | 攻击详情：`{action, merged_result, summary}` |
| `final_conclusion` | Optional[Dict] | 最终结论：`{alert_action, alert_rationale}` |
| `follow_up` | Optional[FollowUpAction] | 后续动作 |
| `asset_bu_info` | Optional[AssetBuInfo] | 资产归属定位结果 |
| `disposition_results` | Optional[DispositionResults] | 抑制动作执行结果 |
| `attack_chain` | Optional[AttackChainDetails] | 攻击链分析结果 |
| `ip_risk_info` | Optional[IPRiskInfo] | IP 风险评分 |
| `pa_code` | str | 一级字段，始终有值 |
| `bu_name` | str | 一级字段，始终有值 |

### FollowUpAction (APT 特有：业务限制)

| 字段 | 类型 | 说明 |
|------|------|------|
| `action_code` | int | 1=转BU, 0=无 |
| `if_ignore` | int | APT 特有：仅弱口令/非授权访问/文件读取/系统配置不当 = 0，其他 = 1（忽略转BU） |

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

## 九、完整事件流转

```
StartEvent(AptRoot)
  │
  ▼
[1] prepare_msgs()
  │ → 提取 alert_id, related_status_count, flow_datas (截断±1000),
  │   alter_full_type_id, attack_types, execute_type
  ▼
JudgeDataEvent
  │
  ▼
[2] judge_by_related_alert()
  │ → default_ignore_parameter=4（预警）/ 3（告警）
  │ → 判定逻辑同 EDR
  ├─ ignore_status == {"已关闭"} → 转交
  ├─ ignore_status == {"已忽略"} 且 >=4 → 忽略
  ├─ ignore_status == {"已忽略"} 且 理由⊆误报 → 忽略
  ├─ ignore_status == {"已忽略","已关闭"} 且 忽略>5且最近2次忽略 → 忽略
  ├─ ignore_status == {"已忽略","已关闭"} 且 理由⊆准确 → 转交
  └─ 其他 → continue
  ▼
KnowledgeEvent → MainStartEvent（透传）
  │
  ▼
[4] judge_by_main() → 根据 attack_types[0] 选择 Prompt
  │
  │  告警模式 (execute_type=3):
  │    prompt = APT_WARNING_HEAD + 通用步骤 + 场景专属步骤 + 结束 + TAIL
  │  预警模式 (execute_type!=3):
  │    prompt = 独立完整 Prompt
  │
  │  23 种攻击类型映射表（见 §三.2）
  ▼
  judge_by_llm() → LLM 研判
  ▼
FinalEvent（含 evaluation_conclusion + disposal_conclusion）
  │
  ▼
[5] generate_alert_description()
  │  ├─ 资产归属定位 locate_asset_bu()
  │  ├─ 攻击详情生成 sub_workflow.run()
  │  └─ alert_action_merge() 合并
  │      + APT 特有：仅弱口令/非授权访问/权限绕过 可转生产预警
  ▼
IpIsolationEvent
  │
  ▼
[6] ip_isolation_disposal()
  │  ├─ 规则封堵（RPAADM_002267, RPAADM_000558）
  │  ├─ 渗透测试名单检查（BlackWhiteTagClient）
  │  ├─ attacker IP 风险评分（ZeusClient + IPScorer）
  │  │    Final_Score = min(100, Base×Geo×Time)
  │  │    基础分: 威胁标签最高分
  │  │    地理权重: 境外×1.5, 境内×1.0
  │  │    时效衰减: 境内快衰减, 境外慢衰减
  │  │    境内白名单强制归零
  │  └─ IP 封堵（R≥85→30天, 75-85→7天, 65-75→24h）
  ▼
DisposalEvent
  │
  ▼
[7] auto_disposal_step()
  │  ├─ [7a] 渗透名单检查（已在上一步完成）
  │  ├─ [7b] FollowUp 提取
  │  │    + APT 特有：仅弱口令/非授权访问/文件读取/系统配置不当 才转BU
  │  ├─ [7c] 兜底分单（asset_bu_info）
  │  ├─ [7d] 攻击链提取（AttackChainExtractor，必然执行）
  │  └─ [7e] pa_code/bu_name 一级字段设置
  ▼
StopEvent(JudgeAnalysisRes)
  │  → model_dump() → 完整 JSON 输出
  │    alert_title, alert_action, alert_rationale,
  │    disposal_action, warning_flag, attack_detail, evaluation, disposal,
  │    follow_up, pa_code, bu_name, ip_risk_info, attack_chain,
  │    disposition_results, asset_bu_info
```

---

## 十、关键 Prompt 索引

### 预警模式 Prompt（旧版）

| Prompt | 文件位置 | 用途 |
|--------|----------|------|
| `APT_EVENT_TCP_PROMPT` | `apt_prompts.py:9` | APT事件（TCP流量） |
| `APT_CERTIFY_CONTROL_PROMPT` | `apt_prompts.py:184` | 权限许可和访问控制 |
| `APT_BRUTE_FORCE_PROMPT` | `apt_prompts.py:368` | 暴力猜解 |
| `APT_SYS_SERVICE_PROMPT` | `apt_prompts.py:529` | 系统/服务配置不当 |
| `APT_BACKEND_PROGRAMING_PROMPT` | `apt_prompts.py:700` | 后门程序 |
| `APT_MINING_VIRUS_PROMPT` | `apt_prompts.py:855` | 挖矿病毒 |
| `APT_MES_LEAK_PROMPT` | `apt_prompts.py:1001` | 信息泄露 |
| `APT_SHELL_XSS_PROMPT` | `apt_prompts.py:1202` | 跨站脚本攻击（XSS） |
| `APT_SENSE_FILE_PROMPT` | `apt_prompts.py:1362` | 敏感信息/重要文件泄漏 |
| `APT_CODE_EXE_PROMPT` | `apt_prompts.py:1562` | 代码执行 |
| `APT_FILE_DOWNLOAD_PROMPT` | `apt_prompts.py:1749` | 文件下载 |
| `APT_FILE_UPLOAD_PROMPT` | `apt_prompts.py:1907` | 文件上传 |
| `APT_WEBSHELL_UTIL_PROMPT` | `apt_prompts.py:2067` | webshell利用 |
| `APT_WEBSHELL_UPLOAD_PROMPT` | `apt_prompts.py:2255` | webshell上传 |
| `APT_PROXY_TOOL_PROMPT` | `apt_prompts.py:2439` | 代理工具 |
| `APT_HACKER_TOOL_PROMPT` | `apt_prompts.py:2619` | 黑市工具（黑客工具） |
| `APT_SQL_INJECT_PROMPT` | `apt_prompts.py:2787` | SQL注入 |
| `APT_NOT_CERTIFY_PROMPT` | `apt_prompts.py:2975` | 非授权访问/权限绕过 |
| `APT_DIR_TRAVERSE_PROMPT` | `apt_prompts.py:3245` | 目录遍历 |
| `APT_FILE_READ_PROMPT` | `apt_prompts.py:3441` | 文件读取 |
| `APT_COMMAND_EXE_PROMPT` | `apt_prompts.py:3639` | 命令执行 |
| `APT_WEAK_PASSWORD_PROMPT` | `apt_prompts.py:3826` | 弱口令 |
| `APT_WITH_BACKGROUND_PROMPT` | `apt_prompts.py:4012` | 其他（兜底） |

### 告警模式 Prompt（组装模式）

| Prompt | 文件位置 | 用途 |
|--------|----------|------|
| `APT_WARNING_HEAD` | `apt_prompts.py:4200` | 通用头部（角色+Schema+历史+输出格式） |
| `APT_MIDDLE_COMMON_SCENARIO_EVALUATION_START_STEP` | `apt_prompts.py:4270` | 通用研判步骤（步骤1：快速筛查） |
| `APT_MIDDLE_COMMON_SCENARIO_EVALUATION_END_STEP` | （未找到完整定义，应为空或极简） | 结束 |
| `TAIL` | （未找到完整定义，应为空或极简） | 尾部 |
| `APT_MIDDLE_SPECIFIC_*_SCENARIO_EVALUATION_STEP` | `apt_prompts.py:4327-4842` | 各攻击类型专属步骤（步骤2/步骤3） |

### 其他 Prompt

| Prompt | 文件位置 | 用途 |
|--------|----------|------|
| `ZEUS_DISPOSAL_TEMPLATE` | `disposal_template.py:10` | 处置模板推荐 |
| `FOLLOW_UP_EXTRACTION_PROMPT` | `follow_up_prompts.py:5` | 后续动作提取 |
| `ATTACK_CHAIN_GENERATION_PROMPT` | `attack_chain_prompts.py:5` | 攻击链报告生成 |
| `ALERT_DESCRIPTION_GENERATION_PROMPT` | `alert_description_prompt.py:8` | 攻击详情生成 |
| `ALERT_DESCRIPTION_MERGE_PROMPT` | `alert_description_prompt.py:128` | 攻击详情合并 |

---

## 附录：全部 Prompt 原文

### 附录 A：APT 预警模式 Prompt 概览

APT 预警模式包含 23 个独立 Prompt 文件常量，每个 Prompt 均为完整的 Markdown 格式文档，包含：

1. **角色**：平安集团 APT `{攻击类型}`攻击告警分析专家
2. **背景**：奇安信 APT 系统 / 安全环境 / 攻击时间 / 告警特征
3. **字段描述**：完整字段表（rule_name, attack_result, timestamp, uri, attack_type, sip, dip, host, host_state, req_header, req_body, rsp_body, rsp_status, agent 等）
4. **历史关联预警处理结论**：`{{related_history}}`
5. **输出格式**：JSON `{action, summary, rationale[]}`
6. **分析步骤**：每个攻击类型独特（见 §四.5）
7. **待研判告警**：`{{alert_event}}`

### 附录 B：APT 告警模式 Prompt 组装结构

```
完整 Prompt = APT_WARNING_HEAD + APT_MIDDLE_COMMON_SCENARIO_EVALUATION_START_STEP + [场景专属步骤] + APT_MIDDLE_COMMON_SCENARIO_EVALUATION_END_STEP + TAIL

APT_WARNING_HEAD 包含：
  - 角色：APT攻击告警分析专家
  - 告警 JSON Schema
  - 历史关联告警处理结论
  - 输出格式

APT_MIDDLE_COMMON_SCENARIO_EVALUATION_START_STEP 包含：
  - 步骤1. 快速筛查：
    - 检查响应状态码（200→分析rsp_body, 3XX/4XX/5XX→忽略）
    - 检查 host_state（企图/失败→忽略）
    - URI 路径关键词过滤（pingangpt/chatgpt/gpt→忽略）
    - 请求路径排除（/health /chatgpt/dialog → 忽略）
    - HOST 检查（iobs-sf-super / pingancode-accelerated-prd / *iobs* → 忽略）
    - 特殊端口检测（JDWP端口）
    - 信息缺失处理

[场景专属步骤] 各攻击类型不同：
  - APT事件: 提取packet_data → CC1AFAEBF380D0
  - 弱口令: 明文传输 + 响应特征 + 风险评估
  - 命令执行: cmd/command 参数 + 响应特征
  - 文件读取: 路径穿越 + 敏感文件 + 响应内容
  - SQL注入: SQL语法 + 特殊攻击模式 + 参数异常 + 错误响应
  - 非授权访问: Spring Actuator/Swagger/Elasticsearch/Kibana/Hadoop
  - 代理工具: packet_data → Frp特征
  - webshell上传: .asp/.jsp/.php扩展名
  - webshell利用: eval/assert/base64_decode扩展名
  - 文件上传: UploadFileData/upload.php + POST/PUT
  - 黑市工具: Nmap/sqlmap/Metasploit/BurpSuite + XFF + Cookie
  - 挖矿病毒: 源IP内网检查 + DNS+repeat_count>5
  - ... 等

APT_MIDDLE_COMMON_SCENARIO_EVALUATION_END_STEP + TAIL
  - 综合判断
  - 输出格式要求
```

### 附录 C：各攻击类型 Prompt 详细分析步骤

#### C.1 APT事件（APT_EVENT_TCP_PROMPT / APT_MIDDLE_SPECIFIC_EVENT_TCP_SCENARIO_EVALUATION_STEP）

**步骤1 快速筛查**：
- 内网 IP 判断（10.x, 30.x, 172.16-31.x, 192.168.x）
- sip 非内网 → 忽略
- sip == dip → 忽略（非外部攻击）

**步骤2 提取 packet_data**：
- 提取 `packet_data` 多行文本，解码后组合

**步骤3 特征分析**：
- 检查 `CC1AFAEBF380D0` 字符串 → 转交
- 未发现 → 忽略

---

#### C.2 弱口令（APT_WEAK_PASSWORD_PROMPT / APT_MIDDLE_SPECIFIC_WEAK_PASSWORD_SCENARIO_EVALUATION_STEP）

**步骤1 快速筛查**：
- host_state 包含"攻击失败"或"尝试" → 忽略
- attack_result=0 → 攻击企图
- rsp_status 200 → 分析 rsp_body，3XX/4XX/5XX → 忽略
- URI 含 pingangpt/chatgpt/gpt → 忽略
- 特殊路径 /health → 忽略
- host 含 iobs-sf-super → 忽略

**步骤2 攻击行为分析**：
- 检查明文传输（login/password/pwd/toLogin）
- 响应特征（200/302 + OK/Success/Token）
- User-Agent 检查（python/spider → 高风险；KJAQ-PENTEST/isas_radar → 忽略）

**步骤3 综合判断**：
- 高风险条件：host_state=成功 + 明文传输 + rsp_status 200/302 + OK/Success/Token + agent 含 python/spider → 转交
- 中低风险：部分满足 → 转交（待观察）
- 误报条件：host_state=失败 / attack_result=0 → 忽略

---

#### C.3 命令执行（APT_COMMAND_EXE_PROMPT / APT_MIDDLE_SPECIFIC_COMMAND_EXE_SCENARIO_EVALUATION_STEP）

**步骤1 快速筛查**：
- 响应状态码检查（同弱口令）
- URI 路径过滤（同弱口令）
- 检查 whoami/ls/pwd/ifconfig/ipconfig 命令关键字 → 转交
- 特殊路径 /health → 忽略
- host 含 iobs-sf-super → 忽略

**步骤2 请求特征分析**：
- cmd=... / command=...
- 可疑编码（Base64 / URL编码）
- 可疑脚本执行（eval/exec/system）

**步骤3 响应特征分析**：
- 本地主机信息（localhost/127.0.0.1）
- 系统用户（root/administrator）
- 系统路径（/root/C:\Windows）
- 命令执行输出（"Command executed successfully"）

---

#### C.4 文件读取（APT_FILE_READ_PROMPT / APT_MIDDLE_SPECIFIC_FILE_READ_SCENARIO_EVALUATION_STEP）

**步骤1 快速筛查**：
- 响应状态码检查
- URI 路径过滤（同弱口令）
- 特殊路径 /health → 忽略
- host 含 iobs-sf-super → 忽略

**步骤2 请求特征分析**：
- uri 检查敏感参数（file=xxx / path=xxx）
- 路径穿越模式（`../` / `..\` / `/etc/passwd`）
- 目标文件包括 /etc/passwd / /etc/shadow / C:\Windows\win.ini / .env 等

**步骤3 响应特征分析**：
- /etc/passwd：`username:password:UID:GID:...`
- /etc/shadow：`username:encrypted_password:...`
- .env：`DB_PASSWORD=secret123`
- SSH密钥：`-----BEGIN RSA PRIVATE KEY-----`

---

#### C.5 SQL注入（APT_SQL_INJECT_PROMPT / APT_MIDDLE_SPECIFIC_SQL_INJECT_SCENARIO_EVALUATION_STEP）

**步骤1 快速筛查**：
- 响应状态码检查
- URI 路径过滤
- 特殊路径 /health → 忽略
- host 含 iobs-sf-super → 忽略

**步骤2 关键检测指标**：
- 异常SQL语法（UNION/SELECT/DROP/EXEC/XP_）
- 特殊攻击模式（AND 1=1 / SLEEP(5) / EXTRACTVALUE()）
- 参数异常值（非数字字符/超长/编码字符）
- 错误响应模式（MySQL server version / Syntax error）

**步骤3 响应内容分析**：
- 普通SQL执行失败 → 忽略
- 有危害 → 转交

---

#### C.6 非授权访问/权限绕过（APT_NOT_CERTIFY_PROMPT / APT_MIDDLE_SPECIFIC_NOT_CERTIFY_SCENARIO_EVALUATION_STEP）

**步骤2 请求特征分析**：

| 攻击目标 | URI/req_header 特征 | rsp_body 特征 |
|----------|---------------------|---------------|
| Spring Boot Actuator | actuator/env/beans/dump/trace | `{"_links":{"self":` / java.vm.name / `{"loggers":` |
| Swagger | swagger/swagger-ui.html/api-docs | `swagger` |
| Elasticsearch | / _cat/ _node | `You Konw` / `/_cat/` / `cluster_name` |
| Hadoop | cluster/ /cluster/info/ /cluster/apps | `All Applications` / `clusterInfo` |
| Kibana | /app/kibana | `Kibana` |
| Spring Eureka | pafa-cloud-eureka-server | /cluster/apps |

---

#### C.7 黑市工具（APT_HACKER_TOOL_PROMPT / APT_MIDDLE_SPECIFIC_HACKER_TOOL_SCENARIO_EVALUATION_STEP）

**步骤2 请求特征分析**：

| 工具 | User-Agent 特征 | 其他特征 |
|------|-----------------|----------|
| Nmap | `Mozilla/5.0 (compatible; Nmap)` / `Nmap Scripting Engine` | - |
| sqlmap | `sqlmap/<version>` | req_body 含 `1' OR '1'='1` / UNION SELECT / SLEEP(5) |
| Metasploit | `Mozilla/4.0 (compatible; MSIE)` | req_body 含 `/msf` / Base64编码命令 |
| BurpSuite | `BurpSuite` | req_body 含 `<script>alert(1)</script>` |

**X-Forwarded-For 异常**：非法IP格式 / 回环地址127.0.0.1 / 广播地址255.255.255.255

**Cookie 异常**：非法字符(注入字符串) / 超长(>4096字节) / 非标准分隔符 / 脚本代码

---

#### C.8 代理工具（APT_PROXY_TOOL_PROMPT / APT_MIDDLE_SPECIFIC_PROXY_TOOL_SCENARIO_EVALUATION_STEP）

**与 APT事件 类似，检查 packet_data 中的 Frp 特征**：
- 版本信息：`"version":"0.xx.0"`
- 操作系统和架构：`"os":"windows"` / `"arch":"amd64"`
- 用户和认证信息：`"privilege_key":"xxxx"`
- 时间戳和运行 ID：`"timestamp":1234567890`

---

#### C.9 webshell 上传/利用

**webshell上传（APT_WEBSHELL_UPLOAD_PROMPT）**：
- 请求特征：uri/api/req_header 含 `.asp/.jsp/.jspx/.php/.aspx/.asa`
- 响应特征：rsp_status=200 + rsp_body 含上述扩展名

**webshell利用（APT_WEBSHELL_UTIL_PROMPT）**：
- 请求特征：req_body 含 `eval/assert/base64_decode/@ini_set/_0x*=...`
- 响应特征：rsp_status=200 + rsp_body 含 `.asp/.jsp/.php/password`

---

#### C.10 信息泄露（APT_MES_LEAK_PROMPT / APT_MIDDLE_SPECIFIC_MES_LEAK_SCENARIO_EVALUATION_STEP）

**步骤2 请求特征**：

| 敏感路径 | rsp_body 特征 |
|----------|---------------|
| `/api/v4/projects` | `"description":` |
| `/debug/pprof/` | `profile` |
| `/.svn` | `/.svn/entries` |
| `/nacos/v1/cs/configs` | `jdbc` |
| `swagger-ui.html` / `swagger-resources` | - |
| `/*/api-docs` | `"swagger":"` |

---

#### C.11 挖矿病毒（APT_MINING_VIRUS_PROMPT）

**关键条件**：`proto=dns` 且 `repeat_count > 5` → 频繁访问挖矿域名 → 转交

**其他**：sip 非内网 → 忽略 / host_state=失败 → 忽略

---

### 附录 D：处置模板推荐场景映射

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
