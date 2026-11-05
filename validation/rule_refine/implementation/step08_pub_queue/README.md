# 告警队列合并：Pub 27

> **旧安装方案已停用，请勿执行下方历史记录中的安装命令，也不要据此重建安装包。** 用户已改为手工逐步修改，并明确要求STG配置不动。下方旧方案中的“Qwen复用R队列”和自动安装范围不再适用。

2026-11-05归档时已禁用Shell安装命令、Python安装器和构建器的直接执行入口，立即退出且不写文件。
历史载荷及变换算法保留用于审阅，不代表当前部署方案；不得绕过退出保护或运行旧安装回归来重新部署。

当前范围：PRD/LOCAL告警统一使用Pub，生产并发27；STG保留原V32队列和容量，由现有控制器把V32转换为用户设置的Qwen38_27B，原优先级0走Pub的例外不改。原共享`config/celery_task.conf`和`config/celery_task.detector.conf`保留，生产另用PRD专用worker配置，不共改STG。保留第7G生产禁用标记。

已回传的验证：10项Python语法检查、4个worker的静态配置检查、Mac/Flower启动，以及`get_selected_model`源码人工核对。用户已确认生产部署成功，当前Pod Pub worker在线且有19成功/0失败；这不证明逐条研判、回调或27槽满载已验证。

独立模型接口测试已回传：27并发、每个输入5045 tokens、仅回复OK，26成功/1次HTTP502，成功请求平均3.109秒。见[说明](../model_concurrency_probe/README.md)和[脚本](../model_concurrency_probe/probe.py)。直接请求模型不能证明Celery容量或完整告警链路吞吐。

## 以下为已停用方案的历史记录

用户确认下线 `DeepSeek_V32`、`DeepSeek_V4_Flash_My`；PRD/LOCAL 告警统一使用 `Pub_DeepSeek_V4_Flash`，STG 按用户最新确认继续调用 `Qwen38_27B`，复用现有 `PingAnGPT_R_queue` 和共享容量6，不新增worker。这是一份对内网旧项目 `sec_know_model` 的增量修改，不修改 DeerFlow Runtime。

## 内网执行

内网首次安装在写入前因版本差异停止，没有修改源码。用户已回传全部22文件诊断：18份与已审阅版本一致，PRD代理YAML仅新增6行模型登记，另有适配器、控制器、模型测试文件3份Python版本不同。用户随后说明控制器已将STG转换目标改为Qwen38_27B；仅重建这一个字符串替换就得到与内网一致的完整摘要 `d24a93c167428804d1ac606f3a034b168592e3fd1e692d6cf1e26b1104329022`，因此该控制器版本已经精确核对。新版同时对齐STG入队模型、策略和默认值为Qwen，并继续支持其它已说明的局部差异；使用下面更新后的安装命令。

除了上述已精确重建的控制器版本，新版不会直接把未知整文件摘要加入可安装清单：代理YAML核对两个退役模型块与登记区外内容，适配器核对PRD字典中两个目标条目，控制器核对默认模型/原STG分支/导入，模型测试只处理明确的字面量名单。其它字节保留，新增模型登记保留；模糊结构、直接重写、重复键或目标片段变化仍会停止。回执中的 `entire_file_reviewed=false` 表示只核对本次修改范围，不是对整个内网版本的审计。另18份文件继续完整摘要检查。

复制 [INSTALL-PUB-QUEUE-27.sh](INSTALL-PUB-QUEUE-27.sh) **全文**到 Mac 终端执行，或把文件带入内网后执行：

```bash
bash INSTALL-PUB-QUEUE-27.sh
```

默认项目是 `$HOME/sec_know_model`，优先使用其 `.venv/bin/python`，仅依赖 Python 3.11+ 标准库和 bash。不安装依赖，不访问模型、数据库或 Redis，不启动/重启服务。可用 `SEC_KNOW_PROJECT_ROOT`、`SEC_KNOW_BACKUP_PARENT` 指定其它项目和项目外备份目录。

脚本会先核对源码摘要或上述目标片段、在临时副本运行 33 个离线测试方法、检查目标配置和 shell 语法，全部通过才备份并修改文件。测试不会导入未知适配器、执行未知控制器/模型测试脚本；模型选择验证只执行已经核对的表达式与人工测试函数。未通过检查或混合安装状态会停止，不覆盖整份文件。备份、测试日志与回执保存在项目外的 `$HOME/sec-rule-refine-data/queue-pub27-*/`，避免随应用镜像发布。未知整文件的重复安装还须匹配同项目上次成功回执及安装后摘要，不能只凭旧模型条目缺失判断已安装；此时 `tests_passed=0`。

安装只改变磁盘源码。发布前停止向旧队列投递，等待旧 worker 的待执行和在执行任务结束，再切换启动配置；本工具不搬迁或删除任务。已安装的第7G `RULE_REFINE_MONITOR_ENABLED=0` 保持原样，LOCAL 配置中的 enabled 也不改变。旧版安装命令的哈希会与合并后源码不同，不要为了重装旧补丁强行跳过版本核对。

## 改动范围

| 路径 | 改动 |
| --- | --- |
| `config/celery_config.py` | 删除两个旧模型/队列登记；Pub 并发 27；Qwen38_27B映射现有R队列/6；保留规则后台定时注册 |
| `config/model_limits.py` | 删除旧模型策略；Pub共享告警容量27；STG Qwen与R共用原R槽/6；按环境选择默认模型 |
| `model/db/crud/workflow_task.py` | 告警停止三路分配，PRD/LOCAL 固定 Pub、STG 固定 Qwen（R队列）；保留优先级、pending/meta、熔断及失败清理 |
| `tasks/task.py`、`my_workflows/zeus/flows/alert_controller.py` | 默认模型与环境一致；进入并发控制前解析；拒绝已退役模型 |
| `service/io_models.py` | 输入模型目录替换为 Pub；避免旧名称静默落入其它队列 |
| `config/celery_task.conf`、`config/celery_task.detector.conf` | 删除两个旧 worker；两套生产 Pub worker 并发均为 27 |
| `local_run.sh`、`local_run.detector.sh` | 删除两个旧 worker；Mac Pub 保留原并发 2 |
| `scripts/detector_worker.py` | Detector 仅保留 pub 入口；其余检测流程保持 |
| `llm_service/openai_completion.py` | 删除 PRD 两个旧模型登记；保留其它模型和 STG 登记 |
| `llm_service/litellm_proxy_config.local.yaml`、`llm_service/litellm_proxy_config.prd.yaml` | 删除两个旧别名；其它模型、代理参数不变 |
| `run.sh`、`validation/verify_stg_distribution.py` | 停用旧 STG 分流测试注入及其自动清理；保留生产原有启动/清理流程和第7G禁用标记 |
| `validation/cleanup_priority9_tasks.py` | 手工清理工具目标目录与新路由一致；本安装器不执行清理 |
| `my_workflows/util/check_celery_queue.sh`、`util/diagnose_model_state.py`、`test/test_all_models.py` | 更新诊断目录、并发说明及模型测试名单 |
| `my_workflows/zeus/rule_refine/background_service.py`、`test/rule_refine/test_background_service.py` | 规则建议缺省模型切 Pub，继续共用容量控制 |
| `config/rule_refine.analysis.local.json`（如果存在且仍选旧模型） | 仅把退役模型改 Pub，其余配置及开关保留 |

固定修改 22 个目标文件；可选本地 JSON 最多额外 1 个。`start_proxy.sh` 使用动态健康检查，无需修改；STG 的 `DeepSeek_V4_Flash` 是另一个名字，不等于退役的 `_My`，保持原样。备份、历史报告、历史定价与未被线上引用的旧实验可能仍出现旧名称，不能全仓替换。

“27”指每个生产 Pub worker 的并发及告警 `agent_task` 共享 Redis 槽容量，不能解释为所有通用任务/直接 API 调用的总限额。内网模型实际可用额度、连接、worker 启动和运行状态仍需发布后核对。

## 本地验证与构建

```bash
python3 -B validation/rule_refine/implementation/step08_pub_queue/build_command.py
python3 -B -W ignore::DeprecationWarning -m unittest discover \
  -s validation/rule_refine/implementation/step08_pub_queue -p 'test_*.py'
```

构建仅读取私有 ZIP 和既有 6F/7G/7 后台源码版本，输出变换算法、测试及前后哈希；交付包不含项目完整源码或凭据。离线测试用替代的 Redis/Celery/DB 对象验证路由、优先级、共享容量、失败清理、6F注册保留；安装测试覆盖备份、权限、回滚、未知版本拒绝、幂等以及脱离仓库的完整命令执行。不会执行原来的启动或清理脚本。

本地共78个测试方法通过（含23项局部校验、9项只读诊断及安装故障测试）。4份文件同时存在无关改动的模拟安装、内容保留、回执幂等、写回执失败回滚均已验证，完整安装命令也脱离仓库验证。模拟数据不等于已读取内网3份Python完整内容；实际目标片段由脚本在内网核对，当前新版内网安装及生产运行尚未回传。

先前的 [DIAGNOSE-SOURCE-DIFFERENCES.sh](DIAGNOSE-SOURCE-DIFFERENCES.sh) 仍为只读差异工具，输出完整摘要、行号及脱敏结构，既不写文件，也不自动批准未知版本。
