# 6X：安装修复后的历史规则试跑

6W已在内网安装成功（230项检查、33个文件），用户确认重启后台后显示 `idle`。现在重新用历史样本验证规则 `RPAADM_002627` 的建议生成，不需要再安装补丁。

内网结果（2026-10-29）：本次试跑已完成，`status=passed`、`analysis_status=insufficient_evidence`，格式与配置一致性检查通过，无传输或校验错误。50条目标告警全部符合材料条件；128KiB预算下实际11条告警/11条日志入模，输入127026字节，1次SDK请求耗时78.075秒。报告正文已收到：模型指出9条样本集中于同一签名和目标IP/端口，另两条为其他签名；因业务属性未确认及样本范围有限，建议先核查集中场景，再考虑组合排除。

这是模型主动返回证据不足，代码没有要求资产/EDR证据齐全或样本全部覆盖才可提出带前提的建议。`filter=null`及空试算来自非proposal状态，没有发生条件校验失败。当前保留原结果，优先核查目标服务及相关行为是否预期；不据9条重复样本断言正常业务，不自动扩大预算或重调模型。查看原报告的命令仍为：

```bash
cat "$HOME/sec-rule-refine-data/historical-rule-review-v2-_wwm6uu1/REPORT.md"
```

原回答和新报告已保存，当前无需重新执行下面的试跑命令。

模型路由核对：6F安装配置默认写入`DeepSeek_V32`，本工具读取`config/rule_refine.analysis.local.json`，不是自动选择DeepSeek官方最新版。上传源码包中该别名经本机4001 LiteLLM、8003内部适配器转入平安EAGW PRD；适配器移除请求体的model字段，由对应scene_id选择网关模型服务。因此不能单凭别名和返回`default`证明实际部署仍为V3.2，官方V3.2规格只用于区分token与字节单位，不能替代内网端点容量核对。此次只检查配置来源，没有更换模型或调用模型。

## 1. 执行

保持现有服务运行。打开 [REVIEW-HISTORICAL-RULE.sh](REVIEW-HISTORICAL-RULE.sh)，复制全文到内网新终端执行。

脚本先显示取样数量、完整请求字节数和当前预算。看到提示后输入 `LIVE`，本次最多发起一次SDK请求；代理内部可能重试。输入其他内容则退出，未调用模型。

结果保存到新的目录：

```text
~/sec-rule-refine-data/historical-rule-review-v2-随机后缀/
```

把最后的JSON贴回来。建议正文在该目录的 `REPORT.md`；原模型回答在 `assistant-content.txt`（仅在传输返回了符合接收边界的正文时保存）。无需导出原始告警。

## 2. 这次使用什么

- 原历史文件 `~/sec_know_model/nas/original/full_alert_2026_month_forth_sample_200.pkl`，SHA `7dcf39081920a8531f7458149f1c98c9d256e297c62c10ee9d385c39a091f2cf`，检查前200行。
- 原CSV `~/sec-rule-refine-data/rules.csv`，SHA `729b0892ce3bd455d09f8d8b93d40afcf2d78a6f28d25e7753b640c1ac7e1976`，按 `ptp-nids + RPAADM_002627` 唯一匹配配置。
- 已安装6W的编码、合同、数字比较、条件检查和模型适配器；本地配置 `config/rule_refine.limits.local.json`。默认输入128KiB、输出64KiB、超时设置240秒；本历史试跑不使用后台3次重试设置。
- 本工具是独立历史分析，不制造突增信号或接收时间窗口。原模型研判回答继续不输入，以保持与上次历史试跑相同的材料类型；原ID和日志值保留。

8KiB日志、32KiB规则、60KiB上下文及旧节点数门槛已移除，完整messages连同提示词和JSON转义按当前输入预算选取整条日志。`eligible_unique_alerts` 是符合材料条件的数量，`included_alerts/included_logs` 才是模型实际看到的数量；`budget_omitted_*`、`selection_omitted_*`、`invalid_logs` 分别说明预算省略、数量限制省略及无效日志。

为控制单次请求仍保留前200行、最多20个样本、每个最多3条模型可见日志；单告警2MiB、每告警最多1000个hit及100条扫描日志。样本按来源顺序选取，不按原研判结论筛选。

用户已指出这些demo抽样与字节预算影响实际验证。128KiB是脚本自定的UTF-8请求预算，不是用户指定值，也不是按内网模型容量测定的上限；20个样本、3条日志、前200行同样属于工具抽样策略。当前程序仍只发一次请求，没有对省略样本继续分批分析，因此单纯调大字节预算不等于覆盖全部目标告警。

官方[DeepSeek V3.2模型卡](https://fe-static.deepseek.com/chat/transparency/deepseek-v3.2-model-card-0414-EN.pdf)标称上下文128K，单位为token；不能与128KiB字节等同。此次接口返回输入48333、输出8342、合计56675tokens，请求JSON为127026字节，这只是本次两种计量的结果，不能作为固定换算比例。请求别名DeepSeek_V32、返回标签default也不足以证明内网端点配置的实际上下文上限。后续修正应依据端点及模型的token容量安排输入和输出，分批覆盖选定范围并保留来源；不能把采样上限解释成模型限制。本次只核对原因，尚未修改该策略或发起新模型调用。

## 3. 结果含义

| 输出 | 含义 |
|---|---|
| `status=passed` | 本次传输、输出检查和规则配置一致性核对完成；具体可能是proposal、no_change或insufficient_evidence等 |
| `status=manual_review` | 建议已保存，其中有本地尚不能试算的比较方式，需人工核对 |
| `status=blocked/failed` | 看 `error_code`、`validation_stage`、`validation_error` 及 `model_error_code`；原回答和分析结果尽可能保留 |
| `sample_match_counts` | 仅模型可见历史日志的匹配数，不是误报率、线上降量率或全部历史告警的效果 |

脚本不提交告警、不写业务库、不回调ZEUS、不更改实时计数或线上规则，不覆盖旧报告。请求模型时沿用原项目模型容量槽和熔断机制，因此会连接该用途的Redis；它不是完全离线执行。测试建议仅保存本地，未提交正式接收接口。

## 4. 本地验证

48项测试通过：材料16项、建议及适配22项、完整命令与调用编排10项。包括100KB整日志、60KB规则、超过旧节点限额的字段密集JSON、数字等值、长中文回答、未引用条件、组合不跨日志、人工核对、预算冻结、取消0调用、一次SDK编排、配置漂移和版本错误诊断。测试只使用人工数据、临时文件及替身模型/服务，没有真实SDK、Redis、数据库或内网请求。

```bash
backend/.venv/bin/python validation/rule_refine/implementation/step06x_historical_review/test_materials.py
backend/.venv/bin/python validation/rule_refine/implementation/step06x_historical_review/test_review.py
backend/.venv/bin/python validation/rule_refine/implementation/step06x_historical_review/test_command.py
backend/.venv/bin/python validation/rule_refine/implementation/step06x_historical_review/build_command.py
bash -n validation/rule_refine/implementation/step06x_historical_review/REVIEW-HISTORICAL-RULE.sh
```

原6R及6T产物保持不变。新工具源码摘要、安装模块摘要、预算、提示词、输出合同和结果一起保存在新目录，后续可离线解释同一份回答。
