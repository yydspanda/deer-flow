# 6L：完整JSON代码框兼容与原回答离线复核

6K内网结果明确：已保存模型回答SHA256为10dc131e79722ae54191572a0518cd9523b2e8b597c01c5b2b92c788faa69000，JSONDecodeError发生在第1行第1列，fenced=true。该标记证明正文以代码框开头，不单独证明代码框完整、内部JSON及建议有效。该次SDK调用1次/1.833秒/967+332=1299 tokens；计数及测试键清理正确，整体仍blocked。

修复候选位于payload/my_workflows/zeus/rule_refine/analysis.py，仅增加_json_document并改一处json.loads入参。接受整个回答是单个、完整的json/JSON/无语言标签代码框；前后只能有JSON空白。保留16KiB整段上限、原正文SHA、严格JSON、样本支持、条件表达式及全部原校验。不从混合说明中抓取片段，不自动修复内容，不重试模型。修复候选SHA为c8e73780c98a0ae6e02611220965050be07e0dcb3b30b133d4419a03176f00e0。

本步先交付REPLAY-SAVED-ANSWER.sh的完整内容，直接复制到内网执行。它读取synthetic-chain-diagnostic-o4olbtkt，校验固定正文摘要、原模型记录摘要、材料及提示词摘要，使用候选解析器的独立内存副本处理原回答。proposal再经过原建议校验与本地模拟接收；不提建议的合法状态须为not_applicable。没有模型、Redis或数据库调用，不改项目源码、原记录或原报告。

新的结果保存在fenced-json-replay-*。response_source的synthetic_reply仅表示本次通过离线回调回放，另标记response_origin=saved_model_response；不得把原SDK调用1次算成本次调用，也不得把离线成功当作新的真实模型生成或完整后台验收。回放失败则输出细项，不强行转成成功。

18项解析与原分析回归测试通过，3项真实存档/校验/模拟交付测试通过：完整代码框成功，缺字段仍blocked，错误正文摘要拒绝；项目与原报告逐字不变。内网已回传passed/proposal/received/local_checks_passed，模型/Redis/数据库调用0，目录fenced-json-replay-6nth7amy。正式应用解析器及对应测试、后续版本清单和后台重启进入6M；离线报告的application_code_updated=false保留原义。

维护：

```bash
backend/.venv/bin/python validation/rule_refine/implementation/step06l_fenced_json/run_tests.py
backend/.venv/bin/python validation/rule_refine/implementation/step06l_fenced_json/test_replay.py
backend/.venv/bin/python validation/rule_refine/implementation/step06l_fenced_json/build_command.py
bash -n validation/rule_refine/implementation/step06l_fenced_json/REPLAY-SAVED-ANSWER.sh
```
