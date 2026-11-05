# 完整回答与失败报告修复

基于已安装的第9步新操作符版本，小范围更新突增生成/复核的输出约束和失败报告。
不覆盖旧安装包，不修改队列、模型配置、日志材料、历史报告或小规则提交链路。

用户回传的 `surge-real-csv-runtime-nt34ideo` 生成回答仅有 `status/issue/recommendation/certainty`，
缺少 `filter` 和样本引用；其中 `certainty` 在半句话结束。模型返回 `finish_reason=stop`，
但这不证明回答完整。程序返回 `missing_reply_field`，具体路径是 `filter`。
缺失检测发生在操作符检查之前，不能将本次失败归因于 `Eq/Contains`。
没有证据证明服务实际截断的原因，也没有条件或引用可以从已保存答案直接恢复。

## 修改内容

- 模板先输出 `status/filter/sample_alert_ids`，再输出解释；长材料之后追加简短的完整输出要求。
  新增消息计入输入估算与请求摘要，不扩大预算、不设置输出上限。
- 新生成/复核提示词分别为 `rule-surge-runtime-independent-generation-v6` 和
  `rule-surge-runtime-review-v7`，输出约束版本为 `complete-advisory-json-v1`。
  仍使用六个新操作符，字段用途和行为范围限制保持。
- 缺少条件或引用的 proposal 仍不可采纳；不从 prose 构造条件，不补造引用，不把缺失条件设为
  null 后伪装成功。可选说明字段缺失与必需字段缺失分开报告。
- 新请求失败时，摘要和报告直接列出错误路径、缺失字段，并在本地报告展示已保存回答。
  回答按文字显示、隐藏推理片段、过长时标记显示截断；原始 `response.json` 始终保留。
- 旧冻结请求按原版本读取，旧完成报告不改写。新输出要求不倒灌成旧请求执行时已经存在的约束。
- 调用次数不增加：生成失败最多一次SDK请求，完整生成与复核最多两次；代理内部重试情况未知。

这些改动降低结构遗漏的机会并便于直接排障，不保证模型每次输出正确；结构完整也不证明条件业务上安全。

## 内网执行

打开 [FIX-AND-RETEST.sh](FIX-AND-RETEST.sh)，全选复制到内网Mac终端。
默认项目为 `$HOME/sec_know_model`。先校验当前第9步源码并执行离线回归，通过后备份更新。
安装失败则整段命令停止；成功后进入原真实CSV手工工具，输入 `LIVE` 才发起新一轮生成及复核。
原失败报告保留，新结果另存，不执行过滤或提交规则。

仅安装可复制 [FIX-COMPLETE-OUTPUT.sh](FIX-COMPLETE-OUTPUT.sh)。安装不启动或重启服务；
手工重测的新进程读取新源码，已运行的后台worker需后续重启才加载修改。

## 离线验证

```bash
backend/.venv/bin/python validation/rule_refine/implementation/step09a_complete_output/offline_preflight.py
```

该预检在临时目录还原已安装第9步的源码，实际执行完整粘贴安装命令，核对目标摘要、未修改文件和
重复安装的幂等性。离线模型替身不能证明内网模型会遵守新版输出要求。

2026-11-05离线验证：308项回归通过，实际粘贴安装更新临时项目8文件（2运行模块、6测试文件），
第二次执行为already_installed，目标摘要与未修改源码核对通过。另2项已有冻结历史兼容测试
在本地通过；该大型夹具不额外传到内网。组合命令在未知源码夹具上确认失败即停止，未进入CSV
阶段。两份Shell命令语法通过；模型、Redis和业务数据库调用均为0。

## 内网手工验收已回传

用户回传安装成功：308项回归、8个更新文件，备份为 `complete-output-install-p3m5idtn`。
随后 `surge-real-csv-runtime-qbiqd34t` 完成新一轮生成与复核，结果为
`received/proposal`、`ai_review_completed=true`、`recommendation_ready=true`，实际2次SDK请求。
生成展示26条当前和11条历史；复核展示26条当前（23新增、3复用）和同11条历史，
两阶段累计49条不同当前告警，不是把全部2205条交给模型。

最终JSON使用 `detail_process_name Contains "net1.exe" && event_content Contains "net1 accounts"`，
没有检测结果名称条件；本轮证明新操作符及完整回答的手工链路可用。
生成输入83857/输出489 tokens、25.340秒；复核输入84646/输出973 tokens、22.634秒。
用户确认本轮结果可以，不再修改或重复请求。正文将26条写成23条、引用ID笔误以及子串匹配的
范围限制仍保留在原回答中，没有静默修正。实际条件不限制完整命令、额外参数或可信任务来源。

以上为用户回传证据，助手未访问内网文件。建议仅本地保存，未执行全量过滤、提交或应用规则，
小规则网关接受性和引擎字段可用性仍未在本轮验证。已运行后台worker加载本补丁仍需后续重启。
