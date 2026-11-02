# 第6步J：人工样本隔离串联

6I内网已回传ready：3条人工样本、含重发共4次计划提交；文件摘要为503dd5b8d7636a6b99bf49b8d628cfe0f58705ea7aa802e4d7c208693bc2bbe2。6J使用该固定文件；内网已回传计数4→3/1个信号、SDK1次/6.247秒且清理成功，但analysis_failed/invalid_model_output使交付blocked，整体未通过。下一步使用6K诊断命令，不直接反复重试本命令。

交付命令是`CHECK-SYNTHETIC-CHAIN.sh`的完整内容，可直接粘贴，不要求传文件。工具解压到`~/sec-rule-refine-data/synthetic-chain-check-*`，不覆盖项目源码或配置。校验LOCAL环境、人工文件及既有模块摘要后，通过只读`/dev/tty`要求输入LIVE；其后使用原Redis和本机模型代理。调用前取得原共享模型槽，SDK重试0、超时240秒，代理内部尝试次数未知。业务数据库不读写，不经过HTTP或Celery派发。

流程：demo随机Redis命名空间、最低3条（保留1小时窗口）、3次人工接收及1次重发、一次分析、原结果存档/模拟交付、按信号确认、再次消费为空。模型返回证据不足/无需修改也可代表流程完成，但不代表已生成有效过滤建议。建议只在本地模拟接收，不应用到ZEUS。

输出目录中的`manifest.json`保存模型配置、模块摘要和来源；`result/`保存提示词、输出Schema、计数、消费记录、完整分析、summary.json及REPORT.md。仅保留模型结构化结果及传输审计，不导出令牌或推理文本。模型返回后若租约校验失败，已取得的结果仍保留。测试结束或失败均只清理捕获的3个demo计数键，共享模型槽及熔断状态由原组件维护。失败不会由后台续跑，不自动再次请求模型；请保留整个目录用于诊断。

本地验证：probe 8项、runner 11项测试通过；全部使用人工客户端，无真实模型或Redis调用。完整复制命令经过真实PTY取消测试，3个嵌入文件摘要一致、文件权限0600，21个既有模块摘要与交付源匹配；取消时未创建调用manifest、未连接服务。真实业务验收仍待实际样本和后续ZEUS联调。

维护命令：

```bash
backend/.venv/bin/python validation/rule_refine/implementation/step06j_synthetic_chain/test_probe.py
backend/.venv/bin/python validation/rule_refine/implementation/step06j_synthetic_chain/test_runner.py
backend/.venv/bin/python validation/rule_refine/implementation/step06j_synthetic_chain/build_command.py
bash -n validation/rule_refine/implementation/step06j_synthetic_chain/CHECK-SYNTHETIC-CHAIN.sh
```

最后两条仅重建并检查复制命令，不调用模型。修改runner/probe/pins后必须重新构建交付命令。
