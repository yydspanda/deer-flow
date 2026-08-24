# PingAn Legacy Knowledge Migration Matrix

> Updated: 2026-08-24
>
> Source: `validation/original_works/security-log-analysis/security-log-analysis/`
>
> Scope: PingAn legacy alert integration; no generic Runtime policy or action authority

本表是旧 PingAn Prompt、案例和 Skill Demo 的逐项迁移台账。它回答三件事：旧知识去了哪里、当前是否
真正启用、还缺什么。旧文档继续作为来源证据，但不再作为生产 Runtime 依赖。

## Status Legend / 状态说明

| Status | Meaning |
|---|---|
| `active_c_context` | 已评审并通过 canonical typed selector 投影为只读 `C-*` |
| `generic_method` | 方法已进入 public Skill / 通用 Runtime，不迁移 PingAn 结论 |
| `governed_dynamic` | 必须由带 owner、scope、validity 的动态事实或 Provider 提供 |
| `memory_or_eval` | 适合作为审核后的 Memory 或离线真值，不是静态知识 |
| `blocked_canonical_gap` | 原始信息存在，但尚未进入足够精确的 canonical contract |
| `needs_operator_review` | 技术选择器可实现，但业务事实尚未由当前项目所有者确认 |
| `no_representative_corpus` | 旧案例存在，当前 4343 条语料无匹配样本，暂不激活 |
| `rejected_static` | 明确禁止静态化或直接迁移 |

## Activated First-Alert Playbooks / 已启用首见告警知识

所有条目位于 `backend/soc_agent/integrations/pingan/knowledge/endpoint-playbooks-v1.json`，只在
`integration_name=pingan_legacy_alert_platform` 生效，固定 `decision_authority=none`。

| Fact | Canonical gate | Corpus evidence | Negative control | Status |
|---|---|---|---|---|
| `pa.endpoint-group-policy-logon-script` | EDR/HIDS + `gpscript.exe` + `Map_Drive.ps1` | `2444022`；同类候选约 80 条 | 只有 rule text 或执行其他脚本不命中 | `active_c_context` |
| `pa.endpoint-sccm-powershell-deployment` | 同一事件的连通进程片段包含 `Ccm32BitLauncher -> [cmd] -> PowerShell`，并命中 `start.ps1` 或 `ccmcache/install.ps1` 参数组 | 13 条精确命中：`2492577`、`2515982` 等 | `1965794` 虽含 SCCM launcher，但执行 `cacls` 改权限，不命中 | `active_c_context` |
| `pa.endpoint-pycharm-wmic-av-inventory` | 同一 canonical process observation 含 `pycharm64.exe + WMIC.exe`，命令为 SecurityCenter2 AntivirusProduct 只读查询 | 21 条精确命中，代表 `2451633` | DataGrip/IDEA、PyCharm PowerShell 和只有 rule text 的样本不命中 | `active_c_context` |
| `pa.endpoint-notepad-memory-map` | `explorer.exe` 交互启动 + 已评审 Notepad++ 名称/路径组合 | `2478371`、`2529831` | 两条 Windows Notepad 调命令的 HIDS 告警不命中 | `active_c_context` |
| `pa.endpoint-net-share-list` | HIDS observation 中完整规范化命令严格等于 `net share` | `2503147` | `net share d$ /delete` 三条样本均不命中 | `active_c_context` |
| `pa.endpoint-fdmee-unc-script` | HIDS + `cscript.exe` + 已审核 FDMEE UNC server/share/event-script 组合 | 12 条全部命中：`2448147` 至 `2565458` 的候选集 | 服务器或脚本层级变化的合成反例不命中 | `active_c_context` |
| `pa.endpoint-office-assistant-nsis-update` | 连通的 Office Assistant Setup / `old-uninstaller.exe` 更新链 + 产品参数 + Temp `System.dll` action target | `1968376` | 其他产品目标或其他 DLL 不命中；不复用旧 agent-updater 身份 | `active_c_context` |
| `pa.endpoint-msi-startup-shortcut` | 标准路径 `msiexec -Embedding` + 标准父 `msiexec /V` + Startup `observed_artifact` `.lnk` | `1976406`、`1976564`、`1986762` | 非标准进程路径、父进程/参数变化、非 Startup 或非 `.lnk` 均不命中 | `active_c_context` |

选择器不会跨独立日志拼接行为。EDR 同一原始传感器事件内被拆开的 process edge 只有在“规范化名称
+ 非空 PID”均相同、形成连通分量时才可合并匹配；缺失 PID 的同名常见进程不作为连接依据。

## Endpoint / Host Case Matrix

| Legacy case | Correct destination | Current status | Evidence or blocker |
|---|---|---|---|
| Group Policy PowerShell login script | endpoint Playbook `C-*` | `active_c_context` | 当前项目所有者已确认；真实 replay 已通过 |
| SCCM PowerShell deployment | endpoint Playbook `C-*` | `active_c_context` | 13 条真实 canonical match；权限修改反例不命中 |
| PyCharm WMIC AV inventory | endpoint Playbook `C-*` | `active_c_context` | HIDS Adapter 已从 `event_content` 提取可追踪命令；21 条 match |
| Notepad++ mapped-file behavior | endpoint Playbook `C-*` | `active_c_context` | 两种已观测路径分别验证，不扩展到普通 Notepad |
| Exact `net share` listing | endpoint Playbook `C-*` | `active_c_context` | exact-command gate；删除共享不命中 |
| MSI `msiexec -Embedding` startup shortcut | typed artifact + endpoint Playbook `C-*` | `active_c_context` | 项目所有者已确认；三个真实样本均命中完整多信号 gate |
| Office Assistant NSIS `old-uninstaller` update | endpoint Playbook `C-*` | `active_c_context` | 项目所有者已确认；仅 `1968376` 的 Office Assistant 产品链生效，不泛化全部 NSIS |
| Historical agent-updater NSIS update | endpoint Playbook or Memory | `no_representative_corpus` | 旧案来源保留，但当前语料没有该产品代表样本；不会借用 Office Assistant 结论 |
| aTrust SCCM/NSIS install | endpoint Playbook or governed software fact | `no_representative_corpus` | 当前语料未发现 aTrust 样本 |
| Agent builder `.aimax-builder` | endpoint Playbook | `no_representative_corpus` | 当前语料未发现对应 canonical path/command |
| Google Chrome cron key refresh | endpoint Playbook | `no_representative_corpus` | 当前 Chrome 样本均为 Windows 其他行为，不是 Linux cron/keyring 案例 |
| Huawei ICSLite startup registration | endpoint Playbook | `no_representative_corpus` | 当前语料未发现 ICSLite 进程链 |
| FinalShell automatic inventory | session/command-set Playbook | `no_representative_corpus` | 需 SSH session + separator + bounded command-set contract；当前语料未命中 |
| cscript Oracle FDMEE/ETL UNC script | endpoint Playbook `C-*` | `active_c_context` | 项目所有者已确认；12 条真实候选全部命中受控 server/share/event-script gate |
| explorer + TrustedInstaller `.rbf` | tool evidence / Memory | `no_representative_corpus` | 旧案例自身缺模块路径；静态身份不足以证明无风险 |
| ubiops-agent installer | internal-system identity + governed authorization | `governed_dynamic` | `C-*` 已能识别 ubiops 路径/进程；是否授权安装必须按事件时间确认 |
| EDR safe-path workbook | software-path Provider + Tenant Policy | `governed_dynamic` | 已有独立 catalog/provider/policy，禁止复制成静态白名单 |

## Network / APT / NIDS Matrix

| Legacy case group | Current destination | Status |
|---|---|---|
| Source/destination, reverse connection, proxy/CDN role separation | network/asset public Skills + `pingan.network_direction` | `generic_method` + `active_c_context` |
| Internal ranges, GeoIP caveat, corporate domains | versioned network knowledge Profile | `active_c_context` |
| CodePilot/AskBob/IOBS/data-manager/Palo identity | `pingan.internal_systems` identity facts | `active_c_context`; identity alone never means benign |
| SQL injection, WebShell, command execution, DNSLog, FRP, Stratum, Sunlogin | generic Network/APT/Web/Endpoint triage method and eval cases | `generic_method` / `memory_or_eval` |
| Scanner, red team, purple-team, maintenance and authorized tests | `AuthorizedActivityFact` / `security_tag.lookup` | `governed_dynamic` |
| Fixed scanner IP, environment-name ignore, rule-code auto-ignore | none | `rejected_static` |
| Detector success fields such as host state | current `E-*` + Adapter semantics | `generic_method`; not duplicated into tenant knowledge |

## Review Required / 需要项目所有者确认

当前本表没有待确认的代表语料条目。2026-08-24，项目所有者分别确认启用 FDMEE、Office Assistant
NSIS 和 MSI Startup；三者以独立 Fact 进入 `pingan.endpoint_playbooks@1.4.0`。本次确认没有覆盖旧
agent-updater、全部 NSIS、全部 `cscript` 或全部 `msiexec` 行为，后续新增产品/路径仍需单独评审。

## Verification / 验证

```bash
cd backend
.venv/bin/pytest -q tests/test_soc_agent_tenant_knowledge_context.py
.venv/bin/pytest -q tests/test_soc_pingan_message_parsing.py
```

真实语料选择审计必须记录候选数、精确命中、相邻反例和代表 alert ID。运营历史标签只用于发现样本，
不能单独把条目升级为 `reviewed`。
