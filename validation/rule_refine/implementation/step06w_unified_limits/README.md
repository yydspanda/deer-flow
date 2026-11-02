# 6W：一次安装预算与失败恢复修复

适用于内网 `~/sec_know_model`，兼容已确认的6M版本，以及已经安装6V的情况。不需要重新寻找旧诊断目录。安装只执行离线测试、更新本次源码和测试；小文件备份保存在 `~/sec-rule-refine-data/unified-limits-install-*`。

内网进展（2026-10-29）：已回传 `installed`，230项检查通过、33个文件更新、安装状态核对通过；默认limits已写入，小文件备份为 `unified-limits-install-pkmzt5m9`，安装的模型/Redis/数据库/业务提交计数为0。用户随后确认重启后显示 `idle`，后台恢复轮询。[6X历史试跑](../step06x_historical_review/README.md)也已回传：传输与输出校验通过，模型返回证据不足；11条告警实际入模。无需重复安装或重跑，取样预算与分批覆盖问题仍待后续改进。

## 1. 安装

在原来运行 `local_run.sh` 的终端按一次 Ctrl+C，等后台退出、回到命令提示符。

打开 [INSTALL-RULE-REFINE-FIX.sh](INSTALL-RULE-REFINE-FIX.sh)，复制全文到内网终端执行。脚本自带本次代码，不需要传文件。测试通过才安装，不调用模型、Redis或业务数据库，不压缩整个项目。遇到未识别的本地源码修改会显示文件名和摘要并停止，保留原文件。

看到末尾 `status: installed` 或 `already_installed` 后启动后台：

```bash
cd "$HOME/sec_know_model"
env env_profile=LOCAL ENV_PROFILE=LOCAL bash local_run.sh
```

API与模型代理沿用原进程。请贴回安装末尾JSON，以及重启后的第一条 `rule_refine_background` 日志。`idle` 表示本次没有处理待办，不表示安装失败。

## 2. 查看与重试旧失败

新开终端查看已保存的待处理或失败记录；此命令不连接Redis、不调用模型：

```bash
cd "$HOME/sec_know_model"
.venv/bin/python test/rule_refine/retry_rule_refine.py
```

需要重试时执行以下命令。命令安排新尝试；已启用的后台将在后续轮询中处理，届时可能调用模型：

```bash
cd "$HOME/sec_know_model"
.venv/bin/python test/rule_refine/retry_rule_refine.py --retry --all
```

只重试某条规则时使用：

```bash
.venv/bin/python test/rule_refine/retry_rule_refine.py --retry --rule RPAADM_002627
```

规则跨企业或topic重名时，按列表中的值追加 `--company` 和 `--topic`。旧分析、旧建议和失败原因都会保留。分析已经成功、只是交付失败时，补投使用原结果。旧独立历史试跑目录不属于后台待办，不会被这个命令自动重跑。

结果为 `requires_allow_unconfirmed` 表示旧进程曾开始调用，但没有保存完整结果。确认要重新尝试该条时，显式添加 `--allow-unconfirmed`；普通批量重试跳过这类记录。`newer_pending_preserved` 表示该规则已有另一条待办，命令保留它。

只想保留失败记录、结束旧待办而不重新分析，可执行：

```bash
.venv/bin/python test/rule_refine/retry_rule_refine.py --release --rule RPAADM_002627
```

新分析遇到明确失败，默认最多尝试3次，间隔至少60秒。次数用尽后先保存失败记录再释放待办，避免同一规则永远被旧失败堵住。已开始但结果未知的调用不会自动重发。安装本身不会自动重新调用旧失败任务的模型。

只是任务材料尚未到齐时会继续等待，不消耗模型失败次数；部分样本已经可用时可以继续分析并说明缺少哪些材料。

## 3. 修改大小、超时和重试设置

只修改这个文件：

```text
~/sec_know_model/config/rule_refine.limits.local.json
```

默认内容：

```json
{
  "input_kib": 128,
  "output_kib": 64,
  "timeout_seconds": 240,
  "retry_attempts": 3,
  "retry_delay_seconds": 60
}
```

`input_kib` 计入完整messages，包括规则、样本、提示词和JSON转义；`output_kib` 是回答接收上限。它们按文本字节计量，不是token数或模型能力上限。输入可设64～2048KiB，输出16～1024KiB，超时1～600秒，尝试总次数1～5，间隔1～3600秒。已有合法配置在重复安装时保留。

后台后续处理读取新设置。建议在没有模型请求进行时修改；已保存建议仍按其原提示词和配置进行核对。SDK不自动重试，代理内部尝试次数仍由代理决定；超时设置不是整个流程严格的墙钟耗时上限。

## 4. 这次修复与仍保留的边界

| 内容 | 安装后的行为 |
|---|---|
| 单条日志8KiB、规则投影32KiB、旧回答入模2KiB限制 | 移除这些提前淘汰门槛，统一在完整请求预算内选择完整内容；日志优先，历史研判使用剩余预算 |
| 字段很多但文件不大的JSON | 移除固定10000节点限制，使用字节预算、深度和合法JSON检查；保存前也做包装预检 |
| 较大的历史研判与候选材料 | 单告警/旧回答最多2MiB，候选材料32MiB，保存包40MiB；100KB文本可以进入候选，不保证每份都在本次模型可见范围内 |
| 模型回答 | 接收上限默认64KiB，可配置；JSON代码框、数字equal支持统一；未使用的条件别名不再错误要求证据 |
| 本地无法试算的条件 | 确有样本字段依据时保存为 `manual_review`，保留问题、建议及条件，供人工核对；不把未知当成已经匹配 |
| 失败恢复 | 新尝试独立保存，交付重试复用成功结果，明确失败有次数上限，结果未知需显式决定 |
| 尚保留的取样边界 | 最多20个告警样本、每个最多3条模型可见日志；任务查询最多100行，总字节32MiB。超出模型请求预算会说明省略，不截断字段 |
| 尚保留的合同检查 | 身份、规则版本、引用样本、字段依据仍核对；表达式最多32条件、2048字符，描述/条件值各4096字符。无效JSON、缺字段或无样本支持仍会失败并标明原因 |
| 本次未改的监测与环境 | 监测仍沿用1小时/1000条/3倍等默认参数；入口的8规则、1秒软预算和降级行为仍在。后台仍为LOCAL、CSV配置和本地模拟接收；不自动改ZEUS规则 |

旧历史报告保持原样；旧6R脚本固定旧代码及8KiB取样，不能用于新版历史试跑。先确认本次安装结果，再用适配新版的历史试跑命令继续。

## 5. 本地验证与维护

安装预检使用人工数据，在临时项目中阻断网络、SQLite连接和子进程服务调用。开发侧另有临时SQLite读取测试与完整安装命令验收。内网安装、真实历史材料的新模型结果、真实突增自动链路和ZEUS兼容仍需后续验证。

本地验证已通过：230项安装功能预检、12项临时SQLite读取测试、21项安装事务测试、4项完整命令验收。完整命令覆盖6M直接升级、可选6V升级、重复执行、用户配置保留、未知修改不覆盖和Python3.11/Bash语法。实际CSV中3条原先超过32KiB的规则均可构建新请求，未调用模型。

```bash
backend/.venv/bin/python validation/rule_refine/implementation/step06w_unified_limits/run_payload_tests.py
backend/.venv/bin/python validation/rule_refine/implementation/step06w_unified_limits/test_install.py
backend/.venv/bin/python validation/rule_refine/implementation/step06w_unified_limits/test_delivery.py
backend/.venv/bin/python validation/rule_refine/implementation/step06w_unified_limits/build_command.py
bash -n validation/rule_refine/implementation/step06w_unified_limits/INSTALL-RULE-REFINE-FIX.sh
```

安装文件及兼容摘要见 `install-manifest.json`；后续版本核对使用 `current-pins.json`。旧步骤的代码和安装产物保持原样。
