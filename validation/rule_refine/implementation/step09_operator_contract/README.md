# 第9步：统一小规则操作符

本次以用户2026-11-05上传、确认昨晚成功部署的 `validation/sec_know_model.zip`
为基线，SHA-256 为 `2a523efcc64df8cf5750ff2721f3a5bfd7658b7c1ba898ca9bee0e53185b4406`。
用户要求整个相关流程改用新写法，包括提示词，不只在提交前转换。

## 更新范围

- 共享操作符定义统一为 `Eq / Ne / Contains / NotContains / StartsWith / EndsWith`。
- 普通分析、突增生成、复核的提示词和示例使用新值；新模型回答不接受旧拼写。
- 条件结构、单条件比较、组合比较、建议保存回读和相关人工测试全部使用新值。
- 提交客户端同步、异步路径都检查新值；即使关闭普通预校验，也不能发送旧值。
  先验证再对最终请求体签名，鉴权、地址、扁平条款结构和批量请求方式沿用基线。
- `Ne / NotContains` 不把缺失字段或空值判为匹配。字符串保持区分大小写，
  `StartsWith / EndsWith` 是字面前后缀；数字只支持严格十进制 `Eq / Ne`。
- 当前操作符契约为 `small-rule-operators-v2`，新突增复核提示词为
  `rule-surge-runtime-review-v6`，独立生成为 `rule-surge-runtime-independent-generation-v5`。

## 内网执行

用户也可直接复制 `PASTE-OPERATORS.sh` 的整段命令，无需下载文件。该粘贴版使用经哈希校验的
源码差异重建相同业务源码，仍由同一安装器先测试、再备份写入。为缩短命令，只不携带
`test_legacy_completed_operator.py` 中的大型人工冻结历史夹具及其2项测试：这两项已在本地
和完整包中验证。粘贴版更新23文件、运行288项回归；完整包更新24文件、运行290项回归。
`compact-manifest.json` 明确列出两种交付的差别；所有业务文件的目标哈希完全一致。
粘贴版从内网项目读取未修改测试时同样核对固定基线哈希，不接受未经核对的本地替代测试。

把本目录的 `INSTALL-OPERATORS.sh` 传到 Mac，然后执行：

```bash
bash /实际保存位置/INSTALL-OPERATORS.sh
```

默认项目位置为 `$HOME/sec_know_model`；其他位置可明确指定：

```bash
SEC_KNOW_MODEL_ROOT=/实际项目目录 bash /实际保存位置/INSTALL-OPERATORS.sh
```

脚本先按新生产包核对涉及的源码，在隔离目录运行离线回归，通过后备份并更新。
备份位于 `$HOME/sec-rule-refine-data/operator-contract-install-*`；源码不同则停止，
重复安装可识别，写入失败会恢复备份。不会启动/重启服务、调用模型、写业务数据库、
清理队列或调用小规则接口。PRD/LOCAL合并队列、STG配置、生产规则监测禁用状态保持原样。
后台进程需要在用户安排的重启后读取新版源码；脚本执行成功不表示内网接口联调通过。

## 已保存材料

旧原始回答、请求、报告不批量替换。突增旧保存链按冻结请求版本读取，保留原文、摘要和
报告语义；用旧草案发起新的复核时，只在新请求的草案副本里转换操作符，并记录源摘要。
已冻结但尚未执行的旧提示词不会在升级后被继续发送，需新建任务使用新版提示词。
普通分析旧产物仍由既有提示词摘要检查阻止冒充新分析结果，不自动重调模型。

旧值只允许出现在明确的历史兼容代码和对应测试中。之前交付的步骤目录是冻结安装版本，
不覆盖其源码或压缩载荷；不要重新运行旧安装器以覆盖当前版本。

## 接口待核实事项

新文档参数表给出了六种新操作符，但错误码69208仍写旧值；示例同时出现顶层 `A` 和
`terms.A`。本次按新枚举更新，保留现有顶层条款请求结构，不臆测另一种结构。
接口成功响应、实际规则引擎语义和表达式支持范围仍需要内网联调确认。
本次不接通建议自动提交、不增加模型请求、不修改已保存业务日志。

## 离线构建与验证

```bash
backend/.venv/bin/python validation/rule_refine/implementation/step09_operator_contract/build_command.py
backend/.venv/bin/python -m unittest discover -s validation/rule_refine/implementation/step09_operator_contract -p 'test_*.py' -v
backend/.venv/bin/python validation/rule_refine/implementation/step09_operator_contract/offline_preflight.py
bash -n validation/rule_refine/implementation/step09_operator_contract/INSTALL-OPERATORS.sh
```

构建器仅打包本次改动源码与人工测试，完整私有生产包、鉴权配置、真实日志和生成报告不进入交付载荷。

2026-11-05本地验证：24个更新文件；23个回归测试文件共290项通过，安装器事务测试8项通过，
全部修改的Python源码通过Python 3.10语法解析，安装脚本通过 `bash -n`。
已在基线ZIP构造的临时目录实际安装并核对最终文件摘要；没有修改原ZIP。
用户已回传内网粘贴版安装结果：288项回归通过、23文件更新，备份目录为
`operator-contract-install-9ap5b7lx`；随后确认Mac DEV重启。这是用户回传证据，
不是助手直接检查内网进程；新小规则接口仍未联调。

重启后真实CSV测试 `surge-real-csv-runtime-nt34ideo` 在生成阶段返回
`missing_reply_field`：模型请求正常结束，输入85688、输出376 tokens，未进入复核。
该错误发生在操作符检查之前；具体缺失键须读取保存的 `generate/parsed.json`
的 `error_path`，不能据此归因于新操作符。
复制 [READ-GENERATE-ERROR.sh](READ-GENERATE-ERROR.sh) 到内网终端，可只读展示本次
错误路径、版本及模型最终回答；隐藏独立/内嵌推理，不打印请求原日志，不改保存文件，
不导入应用或调用模型。默认定位上述任务，可用 `SEC_RULE_REFINE_DIAG_JOB` 指定其他job目录。

后续[完整回答修复及内网手工验收](../step09a_complete_output/README.md#内网手工验收已回传)
已完成：用户回传两阶段正常返回，最终候选使用新 `Contains`，确认本轮结果可以。
本目录已交付源码和安装包保持原样；后续改进作为增量包，不覆盖旧安装版本。
