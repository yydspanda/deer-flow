# Topic 最终回答诊断

打开 [DIAGNOSE-TOPIC-REPLY.sh](DIAGNOSE-TOPIC-REPLY.sh)，复制整个文件到内网 Mac 终端执行。
默认项目目录为 `$HOME/sec_know_model`，也支持 `SEC_KNOW_MODEL_ROOT`。
命令先核对已安装 Step10C 的模块摘要、预览材料，再从终端读取 `LIVE`。

这不是安装补丁。沿用当前生成/复核提示词、取样、模型参数和严格解析器，不修改应用、配置或旧结果，不启动服务。
最多两次 SDK 请求，生成失败不继续复核，无 SDK 自动重试。代理内部尝试次数仍未知。
只使用现有模型容量/熔断 Redis；材料读取沿用临时 SQLite 回放与只读项目历史，不补数。

诊断继承已安装的 TopicProcessor，仅覆盖整个 `_deliver` 入口：验证结果保存链后结束，不进入提交逻辑。
同时传入禁止提交的函数作为兜底；不用共享交付记录目录。即使两轮通过，也不创建 ZEUS 小规则。
它不是正式后台运行或全量过滤覆盖率验收。

结果位于 `root_config.DATA_DIR/rule_refine/topic_reply_diagnostics/diagnostic-*/analysis/`：

- `replies/generate.final.content.txt`、`review.final.content.txt`：解析前保存的最终 content，每阶段上限 64 KiB；缺失 content 不创建该文本文件。
- `replies/*.reply.json`：完整回答哈希、大小、保存字节数/哈希及是否截断，finish_reason、tokens、时间等经过白名单处理的返回信息。
- `diagnostic-summary.json`：运行结论和错误摘要。终端输出同一份摘要，可以回传诊断。
- `source-audit.json` 与原流程的小记录：源码/材料摘要、选择统计、条件和少量引用。

不会另存完整请求/历史日志、鉴权信息或独立 reasoning 字段。最终 content 本身可能复述日志，原文文件应留在内网。
文件以 0600 创建，新诊断目录以 0700 创建，不覆盖旧文件。每次运行创建独立目录；重跑会再次调用模型。
两轮 final content 总上限 128 KiB，其余为有界小记录；它不改变后台 NAS 精简存储策略。

旧失败回答未保存，无法恢复。这次是采集新回答，不保证复现此前59次重复键冲突，也不证明已修复模型输出。
重复键在原文中原样保留；不使用先解析后序列化，不选择冲突键的首值/末值，不提交任何可执行修正。

本地验证：

```bash
backend/.venv/bin/python -B validation/rule_refine/implementation/step10d_topic_reply_diagnostic/test_diagnostic.py
backend/.venv/bin/python -B validation/rule_refine/implementation/step10d_topic_reply_diagnostic/build_command.py
backend/.venv/bin/python -B validation/rule_refine/implementation/step10d_topic_reply_diagnostic/manual_preflight.py
```

使用临时安装目录、人工响应与禁止网络的测试；本地不访问内网模型、Redis、业务数据库或 ZEUS。
`manifest.json` 记录命令和内嵌工具摘要；冻结的 Step10/10A/10B/10C 交付文件不变。

2026-11-05 用户回传 `diagnostic-fai0u98o`：两轮完成，proposal、recommendation_ready=true，
2次SDK、0次ZEUS提交，源码快照未变。生成/复核最终content为1798/2965字节，完整保存、无截断；
两轮均stop，实际输入85092/85400 tokens、输出481/759 tokens，总耗时约32.97秒。
这次通过了现有校验，没有复现阻断；单次成功无法证明模型输出稳定或解释旧59次冲突原因。
随后用户回传只读展示结果：两阶段相同/冲突重复计数均为0；最终仅
`detail_process_name Eq net1.exe`，没有正文依据的 `net1 accounts` 操作条件。
该候选会匹配此topic下该进程的其他活动，正文要求运营确认命令并不会收窄实际JSON。
业务范围审阅不通过；不得将结构ready或这次成功当作自动提交链路验收。
两轮各显示27当前，复核新增18/复用9，累计45个不同当前；模型对2205条全部相同的说法
没有全量证据支持（正文2201与材料2205也不一致）。11条历史未被当成正常标签。
当前就绪判断依赖结构/字段检查和模型submit；提示词已有范围提醒但这次复核未落实。
仍未提交ZEUS，未修改提示词或业务源码，不能凭这次结果断言上一轮重复键的成因。

[SHOW-DIAGNOSTIC-RECOMMENDATION.sh](SHOW-DIAGNOSTIC-RECOMMENDATION.sh)只读展示该次结果中的
最终条件、解释和两轮选择/JSON诊断。默认读取上述目录，可用 `TOPIC_DIAGNOSTIC_DIR` 指定其他诊断目录。
不调用模型、Redis或ZEUS，不读取原始材料、不修改文件；无需重跑诊断或生成新的建议。
