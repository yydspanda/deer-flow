# 6N：后台处理函数的隔离材料验证

前置证据：6M内网已安装解析修复，18项测试通过；重启local_run.sh后2026-10-29 10:16:50、10:17:20、10:17:50三次tick正常。此前空业务库不提供带材料验证，6J的消费函数试调用因代码框解析失败，6L已用原回答离线证明修复有效。

本步使用已安装的run_tick、LocalWorkflowTaskSource和SignalProcessor，手动调用一次带材料后台轮询，再确认下一次idle。现有Celery注册、定时派发和空轮询已有独立证据；本步不宣称在真实worker中完成了带材料处理，也不验证HTTP入口。

运行完整的CHECK-BACKGROUND-MATERIALS.sh内容。修正版仅复用内网synthetic-chain-diagnostic-o4olbtkt中的runner.py、probe.py、pins.json三份已匹配基础工具；新probe.py、diagnostics.py和entry.py由命令内完整压缩包提供，先验证摘要/大小/文件名/语法，再于新的background-materials-check-*目录运行。不读取或执行旧诊断helper，不改项目源码。版本清单采用6M已安装摘要。保留3条人工健康探测样本加1次重发，使用随机独立Redis命名空间和临时SQLite，仅在该目录创建3行任务；规则源为人工规则，业务CSV、数据库及监测配置不变。计数器mode=live表示沿用后台接收时间语义，不代表真实业务数据；材料从临时任务表读取，外层来源明确标记synthetic_fixture。

用户输入LIVE后最多1次SDK请求，max_retries=0、timeout=240，沿用现有共享模型并发槽及熔断器；代理内部尝试次数未知。不另起Celery或HTTP服务，不调用Zeus，不自动修改规则。结果和有界模型正文保存在本次私有目录，若输出失败保留具体校验诊断，不自动重新调用模型。无论成功与否只清理本次跟踪的3个计数键和临时SQLite，保留报告；不调用FLUSHDB或删除业务键。

通过预期：submitted=4、unique_count=3、new_signal_count=1、duplicate_added=false，database_reads=1、analysis_calls=1、sdk_request_calls=1，processed_per_tick=[1,0]，第一次acknowledged、下一次idle；proposal对应received，其他合法分析结论允许not_applicable。insufficient_evidence表示功能串联完成但材料不足，不能声称产出了过滤建议。cleanup_ok和temporary_database_removed均须为true。

本地验证：9项probe测试使用真实后台函数、解析器和SQLite只读读取器，Redis和模型替换为测试实现；19项命令/运行前检查覆盖版本拒绝、非LOCAL拒绝、LIVE取消、共享槽占用、单次模型请求和资源关闭。此记录不代表内网执行已通过。

维护命令：

```bash
backend/.venv/bin/python validation/rule_refine/implementation/step06n_background_materials/build_command.py
backend/.venv/bin/python validation/rule_refine/implementation/step06n_background_materials/test_probe.py
backend/.venv/bin/python validation/rule_refine/implementation/step06n_background_materials/test_command.py
```

后续仍需实际业务样本验证告警入口、后台取样/分析以及建议质量；Zeus两个接口准备好后再联调。用户无需再执行旧6J/6K固定版本命令。

内网首次预检：提示“原验证工具版本不符：diagnostics.py”，尚未进入临时目录创建或服务请求。先执行check_tool_version.py的完整内容，只读比较原manifest与实际字节摘要、跨3.11/3.12规范化AST摘要和entry.py摘要；它不导入旧诊断代码。不根据manifest单方记录接受未知代码，不删除版本校验，不覆写旧证据。本地源与6K命令嵌入字节一致，后续实际摘要已回传，见下段。

2026-10-29内网只读结果：旧diagnostics.py 6499字节，SHA=e6f1c99153f70a9ff12b20fc642fb95181c5f83f0e2ef31baad85338cb9b8490，与原manifest一致；AST摘要3a662d32448e752975c9a840967ef69205f0df03535182f341327d89bc49071b，与本地工具不同。旧entry.py SHA=b73c36ce82ece9df7595c0ae361ed1fc270875dcc0efb68ec7098a53e6fdcf68，也与本地不同。没有取得旧helper全文，不能据此宣称差异仅为排版或已确认行为等价；不把这些新摘要加入可执行白名单。改用当前已检查的新helper，保留原文件与历史证据。新增完整命令回归：旧helper置为不可执行内容仍不读取；三基础文件任一变更在创建目录前停止；压缩包损坏/超限/名单或语法错误拒绝；完整组装的六个文件内容和0600/0700权限正确；取消LIVE不调用服务。内网修正版已通过，结果见下段。

2026-10-29内网验证通过：目录~/sec-rule-refine-data/background-materials-check-b_1iqpsj/result；submitted=4、unique_count=3、new_signal_count=1、duplicate_added=false，database_reads=1、analysis_calls=1、sdk_request_calls=1。analysis_status=proposal、processing_status=received、delivery_status=received、check_reason=local_checks_passed；consumer_status=acknowledged、acknowledged=true、processed_per_tick=[1,0]，next_tick_status=idle。3个测试键清理成功，临时SQLite已删除，业务数据库调用0、automatic_apply=false。

本次SDK记录耗时61852.902ms，请求模型DeepSeek_V32、返回标签default，finish_reason=stop；prompt/completion/total tokens=1045/1117/2162，代理内部尝试次数未知。正文摘要cfce8adcfd54d4a058d2a84376c36bd9d1b322e5cf9e5360bb9286a48d068b57，validation_error=null，正文已保存；记录ID a802273c6aaeb021dab9bcb9fe9e9a91a725b695e912f9fab7adc966e7670b35。matched=3/not_matched=0/unknown=0只表示人工样本本地条件试算，不是生产降量率或误报率。此步为真实Redis和一次真实SDK请求下的函数级后台串联；没有通过实际HTTP接收或Celery worker携带材料完成处理，不能把这两项记为已验收。

2026-10-29用户已回传SHOW-RECOMMENDATION.sh输出：问题说明与人工规则path包含“/”相符，建议文字和JSON均为user_agent包含RuleRefineSyntheticProbe时新增排除；正常业务需运营确认，原报告没有自动应用。没有格式或文字/JSON不一致的问题，但排除范围不限于已展示的/health场景。三个正样本全部匹配不能证明其他路径不会被影响。

本地以现有filter_evaluation.evaluate_filter做零模型、零Redis、零数据库的人工反例试算：相同UA+/health和相同UA+/login均匹配原条件；其他UA+/health不匹配；缺UA为unknown。人工拟议的“A && B”，其中B为path equal /health，仅改变第二例为not_matched，其他三例不变。该组合是范围收窄示例，不是新模型结果、不是已安装的生成策略，也不能证明请求正常。未修改原报告、运行代码或内网配置。无需用户再跑一遍相同正样本；下一阶段为实际HTTP入口及Celery带材料处理验收，建议质量需真实场景及相邻反例验证。
